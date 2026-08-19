"""캘리브레이션 — 영상을 보면서 카메라 기본 세팅(BEV ROI 등)을 맞춘다.

대상 노드가 하는 변환을 `tb.geometry` 로 그대로 재현하므로, 여기서 맞춘 BEV 가
실제 노드가 만드는 BEV 와 같다(실측 확인: 에지 일치율 0.85 — 남는 차이는
노드가 BEV 위에 그리는 오버레이뿐이다).

맞추는 대상과 파라미터 이름은 전부 계약의 `calibration.targets` 에 있다.
이 파일에는 대상 워크스페이스의 파라미터 이름이 없다.

    python3 -m tb.calibrate --scenario scenarios/regression.yaml
    python3 -m tb.calibrate --scenario ... --check out.png   # 창 없이 렌더만
    python3 -m tb.calibrate --scenario ... --verify <런디렉토리>  # 실제 노드와 대조

★핵심 요령★ IPM 사각형의 좌우 변을 차선 위에 올려라. 지면은 평면이므로
그렇게 놓으면 BEV 에서 차선이 정확히 수직·평행으로 선다. 수직이 아니면
사각형이 틀린 것이다 — 격자(`g`)가 그 자다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

from .contract import load as load_contract
from .geometry import (draw_grid, draw_rows, put_text, quad_is_sane,
                       undistorter, verticality, warp_bev)

HELP = [
    "1 IPM 사각형   2 차선 ROI   3 신호등 ROI   4 BEV 측정   5 BEV 가로선",
    "드래그: 가장 가까운 점 이동   방향키: 1px  Shift+방향키: 10px",
    "[ ] 프레임 +-30   , . 프레임 +-1   g 격자   u 보정 on/off",
    "+ - 실측길이 조정   c 측정 지우기   r 되돌리기   s 저장   q 종료",
]


# ══════════════════════════════════════════════════════════════════════
class Calib:
    """계약이 선언한 캘리브레이션 대상들의 현재 값."""

    def __init__(self, contract, params):
        cal = contract.raw.get("calibration") or {}
        if not cal:
            raise SystemExit("[calibrate] 계약에 calibration: 블록이 없다.")
        u = cal["undistort"]
        self.und, self.und_size = undistorter(u, Path(contract.path).parent)
        if self.und is None:
            raise SystemExit("[calibrate] 계약의 calibration.undistort 에 K/D 도 "
                             "file 도 없다.")
        self.und_param = u.get("param", "")
        self.bev_w = int(cal["bev"]["w"])
        self.bev_h = int(cal["bev"]["h"])
        self.targets = cal["targets"]

        w, h = self.und_size
        self.quad = None
        self.rects = {}
        self.px2m = 0.006
        self.length_m = 3.0
        self.quad_key = self.px2m_key = self.len_key = None
        #  ★BEV 위의 가로선들★ [bev_row = 기준선 / bev_dist = 그 선에서의 거리]
        #  원근이 펴진 BEV 에서는 가로선 하나가 곧 '차에서 얼마'다. 기준선(범퍼)을
        #  먼저 놓고, 문턱들은 그 선에서의 ★거리★ 로 잡는다 — 기준선을 옮기면
        #  문턱이 통째로 따라오게 하려는 것이다(따로 잡으면 반드시 어긋난다).
        self.bev_rows = {}        # key -> 값 (bev_row 는 행, bev_dist 는 거리)
        self.bev_kinds = {}       # key -> 'bev_row' | 'bev_dist'
        self.bumper_key = None

        for key, t in self.targets.items():
            kind = t["kind"]
            got = self._param_value(params, t)
            if kind == "quad":
                self.quad_key = key
                self.quad = (np.asarray(got, np.float32).reshape(4, 2) if got
                             else np.float32([[w * .32, h * .60], [w * .68, h * .60],
                                              [w, h], [0, h]]))
            elif kind == "rect":
                self.rects[key] = (np.asarray(got, np.float32).reshape(2, 2)
                                   if got else np.float32([[0, 0], [w, h]]))
            elif kind == "scale":
                self.px2m_key = key
                if got:
                    self.px2m = float(got[0])
            elif kind == "length_m":
                self.len_key = key
                if got:
                    self.length_m = float(got[0])
            elif kind in ("bev_row", "bev_dist"):
                self.bev_kinds[key] = kind
                if kind == "bev_row" and self.bumper_key is None:
                    self.bumper_key = key
                dflt = float(self.bev_h) if kind == "bev_row" else self.bev_h * 0.25
                self.bev_rows[key] = float(got[0]) if got else dflt

    @staticmethod
    def _param_value(params, t):
        """시나리오 params 에 이미 값이 있으면 그걸 출발점으로 삼는다."""
        names = t.get("params") or ([t["param"]] if t.get("param") else [])
        out = []
        for nid in t.get("nodes", []):
            kv = params.get(nid, {})
            for n in names:
                if n in kv:
                    v = kv[n]
                    out.extend(v if isinstance(v, (list, tuple)) else [v])
            if out:
                return out
        #  params 에 없으면 계약이 적어 둔 노드 기본값 — 그것도 없으면 None
        d = t.get("default")
        if d is None:
            return None
        return list(d) if isinstance(d, (list, tuple)) else [d]

    # ── 저장 ────────────────────────────────────────────────────────
    def to_params(self):
        """노드별 파라미터 dict — 시나리오 params: 에 그대로 붙는다."""
        out = {}

        def put(t, values):
            names = t.get("params") or [t["param"]]
            for nid in t.get("nodes", []):
                d = out.setdefault(nid, {})
                if len(names) == 1:
                    d[names[0]] = values if len(values) > 1 else values[0]
                else:
                    for n, v in zip(names, values):
                        d[n] = v

        for key, t in self.targets.items():
            k = t["kind"]
            if k == "quad":
                put(t, [round(float(v), 1) for v in self.quad.reshape(-1)])
            elif k == "rect":
                (x0, y0), (x1, y1) = self.rects[key]
                put(t, [int(round(x0)), int(round(y0)),
                        int(round(x1)), int(round(y1))])
            elif k == "scale":
                put(t, [round(float(self.px2m), 6)])
            elif k == "length_m":
                put(t, [round(float(self.length_m), 3)])
            elif k in ("bev_row", "bev_dist"):
                put(t, [round(float(self.bev_rows.get(key, 0.0)), 1)])
        return out

    # ── BEV 가로선 ──────────────────────────────────────────────────
    def bumper_y(self):
        """거리 0 의 기준행. 기준선을 안 두면 BEV 밑변이 기준이다."""
        if self.bumper_key is None:
            return float(self.bev_h)
        return float(self.bev_rows.get(self.bumper_key, self.bev_h))

    def row_y(self, key):
        """그 대상이 BEV 의 몇 번째 행에 그려지는가."""
        v = float(self.bev_rows.get(key, 0.0))
        return v if self.bev_kinds.get(key) == "bev_row" else self.bumper_y() - v

    def set_row_y(self, key, y):
        """BEV 에서 y 행을 찍었을 때 그 대상의 값이 얼마가 되는가."""
        k = self.bev_kinds.get(key)
        if k is None:
            return
        self.bev_rows[key] = (float(y) if k == "bev_row"
                              else self.bumper_y() - float(y))

    def row_label(self, key):
        v = self.bev_rows.get(key, 0.0)
        unit = "행" if self.bev_kinds.get(key) == "bev_row" else "px"
        m = f" ({v * self.px2m:.2f}m)" if (self.px2m > 0
                                           and self.bev_kinds.get(key) == "bev_dist") else ""
        return f"{key} {v:.0f}{unit}{m}"

    # ── 편집 ────────────────────────────────────────────────────────
    def handles(self, mode):
        """현재 모드에서 잡을 수 있는 점들 → [(라벨, np.array 참조, 인덱스)]"""
        if mode == "quad":
            return [(f"P{i}", self.quad, i) for i in range(4)]
        r = self.rects.get(mode)
        return [] if r is None else [("A", r, 0), ("B", r, 1)]

    def bev_keys(self):
        """기준선 먼저, 그 다음 거리들 — 화면에 그리는 순서이자 `5` 의 순환 순서."""
        return ([self.bumper_key] if self.bumper_key else []) + \
               [k for k in self.bev_rows if k != self.bumper_key]


# ══════════════════════════════════════════════════════════════════════
def render(frame_raw, cal, mode, sel, use_und, grid, meas, real_m, msg,
           disp_w=760):
    """SRC 패널 + BEV 패널 + HUD 를 한 장으로 합성."""
    W, H = cal.und_size
    src = cal.und(frame_raw) if use_und else cv2.resize(frame_raw, (W, H))

    bev = warp_bev(src, cal.quad, cal.bev_w, cal.bev_h)
    vdev, vn = verticality(bev)
    if grid:
        bev = draw_grid(bev, cal.px2m)
    #  ★거리 판정의 기준선과 문턱★ — BEV 위의 가로선이 곧 '차에서 얼마'다
    if cal.bev_rows:
        bev = draw_rows(bev, [(k, cal.row_y(k), cal.row_label(k))
                              for k in cal.bev_keys()], mode)

    left = cv2.resize(src, (disp_w, int(round(disp_w * H / W))))
    k = disp_w / W

    # ROI 사각형
    for key, r in cal.rects.items():
        on = (mode == key)
        col = (60, 200, 255) if on else (110, 110, 110)
        (x0, y0), (x1, y1) = (r * k).astype(int)
        cv2.rectangle(left, (x0, y0), (x1, y1), col, 2 if on else 1)
        cv2.putText(left, key, (x0 + 5, max(14, y0 + 16)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.44, col, 1, cv2.LINE_AA)

    # IPM 사각형
    q = (cal.quad * k).astype(int)
    qcol = (70, 160, 255) if mode == "quad" else (150, 150, 150)
    cv2.polylines(left, [q.reshape(-1, 1, 2)], True, qcol, 2, cv2.LINE_AA)
    for i, (x, y) in enumerate(q):
        hot = (mode == "quad" and i == sel)
        cv2.circle(left, (x, y), 7 if hot else 5,
                   (0, 255, 255) if hot else qcol, -1, cv2.LINE_AA)
        lab = "TL TR BR BL".split()[i]
        lx = x + 9 if x < left.shape[1] - 34 else x - 32
        ly = max(12, y - 8) if y > 14 else y + 18
        cv2.putText(left, lab, (lx, ly),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, qcol, 1, cv2.LINE_AA)

    # 측정선
    if meas:
        for p in meas:
            bx, by = int(p[0]), int(p[1])
            cv2.circle(bev, (bx, by), 4, (0, 255, 255), -1, cv2.LINE_AA)
        if len(meas) >= 2:
            cv2.line(bev, tuple(map(int, meas[0])), tuple(map(int, meas[1])),
                     (0, 255, 255), 2, cv2.LINE_AA)

    ph = max(left.shape[0], bev.shape[0])
    canvas = np.zeros((ph + 120, left.shape[1] + 8 + bev.shape[1], 3), np.uint8)
    canvas[:] = (28, 28, 32)
    canvas[0:left.shape[0], 0:left.shape[1]] = left
    canvas[0:bev.shape[0], left.shape[1] + 8:] = bev

    ok, why = quad_is_sane(cal.quad, W, H)
    hud = [
        f"[{mode}]  sel={sel}  frame={msg.get('frame', 0)}  "
        f"undistort={'ON' if use_und else 'OFF'}  grid={'ON' if grid else 'OFF'}",
        f"px2m={cal.px2m:.6f} m/px   BEV 폭={cal.bev_w * cal.px2m:.2f}m   "
        f"lane_width={cal.length_m:.2f}m   실측기준={real_m:.2f}m",
    ]
    if vn:
        verdict = ("사각형이 맞다" if vdev < 2.0 else
                   "거의 맞다" if vdev < 5.0 else "좌우 변을 차선에 더 붙여라")
        hud.append(f"수직도 {vdev:.1f}° (선 {vn}개) — {verdict}"
                   "   ※ 직선 구간에서만 의미 있다")
    else:
        hud.append("수직도: 선을 못 찾았다 — 차선이 보이는 프레임으로 이동할 것")
    if cal.bev_rows:
        hud.append("BEV 가로선: " + "   ".join(cal.row_label(k)
                                              for k in cal.bev_keys()))
    if len(meas) >= 2:
        d = float(np.linalg.norm(np.array(meas[0]) - np.array(meas[1])))
        hud.append(f"측정 {d:.1f}px = {real_m:.2f}m  ->  px2m={real_m / max(d, 1e-6):.6f}"
                   f"   (Enter 로 적용)")
    elif msg.get("note"):
        hud.append(msg["note"])
    if not ok:
        hud.append("! " + " / ".join(why))

    y = ph + 22
    for t in hud:
        col = (90, 120, 255) if t.startswith("!") else (215, 215, 215)
        put_text(canvas, t, (12, y), 15, col)
        y += 23
    return canvas, k


# ══════════════════════════════════════════════════════════════════════
def _load_scenario(path):
    return yaml.safe_load(open(path)) if path else {}


def verify(cal, contract, video, run_dir, start, n=7, out_png=""):
    """내가 그리는 BEV 와 ★노드가 실제로 만든 BEV★ 가 같은지 대조한다.

    노드는 BEV 위에 피팅 곡선·HUD 를 그리므로 픽셀이 같을 수는 없다.
    그래서 에지가 겹치는 비율로 ★기하★만 본다. 0.8 이상이면 같은 변환이다.
    """
    cfg = (contract.raw.get("calibration") or {}).get("verify") or {}
    dbg_path = Path(run_dir) / cfg.get("video", "lane_debug.mp4")
    if not dbg_path.exists():
        raise SystemExit(f"[verify] 디버그 영상이 없다: {dbg_path}\n"
                         f"         `tb.run run --record-debug` 로 먼저 만들 것.")
    lo, hi = cfg.get("bev_pane", [0.5, 1.0])

    dbg = cv2.VideoCapture(str(dbg_path))
    cap = cv2.VideoCapture(str(video))
    ndbg = int(dbg.get(cv2.CAP_PROP_FRAME_COUNT))

    def pane(i):
        dbg.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, d = dbg.read()
        if not ok:
            return None
        W = d.shape[1]
        return d[:, int(W * lo):int(W * hi)]

    def mine_at(v):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(v))
        ok, f = cap.read()
        return warp_bev(cal.und(f), cal.quad, cal.bev_w, cal.bev_h) if ok else None

    def score(ref, mine):
        """에지가 ±2px 안에서 겹치는 비율.

        노드는 BEV 위에 곡선·HUD 를 덧그리므로 픽셀 비교는 못 한다.
        Canny 는 부드러운 경계에서 1~2px 흔들리므로 ★기준 쪽을 부풀려서★ 잰다 —
        그렇게 해야 "같은 기하냐"만 남고 렌더링 차이가 빠진다.
        """
        if mine is None or ref is None:
            return 0.0
        if mine.shape[:2] != ref.shape[:2]:
            mine = cv2.resize(mine, (ref.shape[1], ref.shape[0]))
        a = cv2.Canny(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), 60, 150)
        b = cv2.Canny(cv2.cvtColor(mine, cv2.COLOR_BGR2GRAY), 60, 150)
        a = cv2.dilate(a, np.ones((5, 5), np.uint8), iterations=1).astype(bool)
        b = b.astype(bool)
        return (a & b).sum() / max(1, b.sum())

    # ★프레임 정렬을 먼저 찾는다★ — 디버그 녹화는 노드가 뜬 뒤에 시작되므로
    #   mp4 의 0번이 영상의 start 번이라는 보장이 없다. 어긋난 채로 비교하면
    #   변환이 맞아도 점수가 바닥이라 오진한다.
    probe_i = int(ndbg * 0.5)
    ref0 = pane(probe_i)
    off, off_sc = 0, -1.0
    for cand in range(-40, 41):
        sc = score(ref0, mine_at(start + probe_i + cand))
        if sc > off_sc:
            off, off_sc = cand, sc
    print(f"프레임 정렬 오프셋 {off:+d} (일치율 {off_sc:.3f})")

    scores, best = [], None
    for i in np.linspace(ndbg * 0.15, ndbg * 0.85, n).astype(int):
        ref = pane(int(i))
        mine = mine_at(start + int(i) + off)
        if ref is None or mine is None:
            continue
        sc = score(ref, mine)
        scores.append(sc)
        if best is None or sc > best[0]:
            best = (sc, int(i), ref, mine)
    dbg.release()
    cap.release()
    if not scores:
        raise SystemExit("[verify] 대조할 프레임을 못 읽었다")

    med = float(np.median(scores))
    print(f"프레임 {len(scores)}장 대조 — 에지 일치율 중앙값 {med:.3f} "
          f"(최소 {min(scores):.3f} / 최대 {max(scores):.3f})")
    if med >= 0.75:
        print("✅ 같은 변환이다 — 여기서 맞춘 값이 노드에 그대로 적용된다.")
    elif med >= 0.5:
        print("⚠️  어긋난다. 계약의 calibration.undistort 가 노드의 하드코딩 값과 "
              "같은지, 런의 ipm_src_pts 가 지금 값과 같은지 확인할 것.")
    else:
        print("❌ 전혀 다르다. undistort 계수나 프레임 정렬(start)이 틀렸을 가능성이 크다.")
    if out_png and best:
        cv2.imwrite(out_png, np.hstack(
            [best[3], np.full((best[2].shape[0], 6, 3), 255, np.uint8), best[2]]))
        print(f"비교 이미지 → {out_png}  (왼쪽=재현, 오른쪽=노드 실제)")
    return 0 if med >= 0.75 else 1


def main(argv=None):
    from .run import _deep_merge, _resolve_contract, local_overrides, resolve_video

    ap = argparse.ArgumentParser(prog="tb.calibrate")
    ap.add_argument("--scenario", default="")
    ap.add_argument("--contract", default="")
    ap.add_argument("--video", default="")
    ap.add_argument("--frame", type=int, default=-1, help="-1=시나리오 start")
    ap.add_argument("--out", default="", help="저장할 YAML 경로")
    ap.add_argument("--check", default="", help="창 없이 이 PNG 로 렌더만")
    ap.add_argument("--verify", default="",
                    help="이 런 디렉터리의 디버그 영상과 대조 (--record-debug 필요)")
    ap.add_argument("--verify-png", default="")
    ap.add_argument("--disp-width", type=int, default=760)
    args = ap.parse_args(argv)

    sc = _load_scenario(args.scenario)
    loc = local_overrides()
    contract = load_contract(_resolve_contract(args.contract or sc.get("contract")))
    params = _deep_merge(sc.get("params", {}), loc.get("params", {}))
    cal = Calib(contract, params)

    video = args.video or resolve_video(sc, loc)
    if not video or not Path(video).exists():
        raise SystemExit(f"[calibrate] 영상이 없다: {video}")
    cap = cv2.VideoCapture(video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fno = args.frame if args.frame >= 0 else int(sc.get("start", 0))

    def read(n):
        n = max(0, min(total - 1, n))
        cap.set(cv2.CAP_PROP_POS_FRAMES, n)
        ok, f = cap.read()
        return (n, f) if ok else (n, None)

    fno, frame = read(fno)
    if frame is None:
        raise SystemExit("[calibrate] 프레임을 읽을 수 없다")

    use_und = bool(params.get(contract.nodes[0]["id"] if contract.nodes else "",
                              {}).get(cal.und_param, True)) if cal.und_param else True
    mode, sel, grid, meas = "quad", 0, True, []
    real_m = cal.length_m
    note = cal.targets.get(cal.quad_key, {}).get("hint", "")

    if args.verify:
        return verify(cal, contract, video, args.verify,
                      int(sc.get("start", 0)), out_png=args.verify_png)

    # ── 창 없이 렌더만 ──────────────────────────────────────────────
    if args.check:
        canvas, _ = render(frame, cal, mode, sel, use_und, grid, meas, real_m,
                           {"frame": fno, "note": note}, args.disp_width)
        cv2.imwrite(args.check, canvas)
        print(f"렌더 → {args.check}  (frame {fno})")
        print(yaml.safe_dump({"params": cal.to_params()}, allow_unicode=True,
                             sort_keys=False, default_flow_style=None))
        return 0

    # ── 대화형 ──────────────────────────────────────────────────────
    win = "tb.calibrate"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    state = {"drag": False, "k": 1.0, "split": 0}

    def on_mouse(ev, x, y, flags, _p):
        split = state["split"]
        k = state["k"]
        nonlocal sel, meas
        if x > split:                      # BEV 패널
            if mode in cal.bev_rows:       # — 가로선 끌기
                if ev == cv2.EVENT_LBUTTONDOWN:
                    state["drag"] = True
                if state["drag"] and ev in (cv2.EVENT_LBUTTONDOWN,
                                            cv2.EVENT_MOUSEMOVE):
                    cal.set_row_y(mode, y)
                elif ev == cv2.EVENT_LBUTTONUP:
                    state["drag"] = False
                return
            if ev == cv2.EVENT_LBUTTONDOWN and y < cal.bev_h:   # — 측정
                if len(meas) >= 2:
                    meas = []
                meas.append((x - split, y))
            return
        sx, sy = x / k, y / k              # SRC 패널 — 점 이동
        hs = cal.handles(mode)
        if not hs:
            return
        if ev == cv2.EVENT_LBUTTONDOWN:
            d = [np.hypot(arr[i][0] - sx, arr[i][1] - sy) for _, arr, i in hs]
            j = int(np.argmin(d))
            if d[j] < 90 / k:
                sel = j
                state["drag"] = True
        elif ev == cv2.EVENT_MOUSEMOVE and state["drag"]:
            _, arr, i = hs[sel]
            arr[i] = (sx, sy)
        elif ev == cv2.EVENT_LBUTTONUP:
            state["drag"] = False

    cv2.setMouseCallback(win, on_mouse)
    print("\n".join(HELP))

    while True:
        canvas, k = render(frame, cal, mode, sel, use_und, grid, meas, real_m,
                           {"frame": fno, "note": note}, args.disp_width)
        state["k"] = k
        state["split"] = int(cal.und_size[0] * k) + 8
        cv2.imshow(win, canvas)
        key = cv2.waitKeyEx(20)
        if key == -1:
            continue
        ch = key & 0xFF

        if ch == ord("q"):
            break
        elif ch == ord("1"):
            mode, sel = "quad", 0
            note = cal.targets.get(cal.quad_key, {}).get("hint", "")
        elif ch in (ord("2"), ord("3")):
            keys = list(cal.rects)
            j = ch - ord("2")
            if j < len(keys):
                mode, sel = keys[j], 0
                note = cal.targets.get(mode, {}).get("hint", "")
        elif ch == ord("4"):
            mode = "measure"
            note = cal.targets.get(cal.px2m_key, {}).get("hint", "")
        elif ch == ord("5"):
            keys = cal.bev_keys()
            if keys:
                mode = keys[(keys.index(mode) + 1) % len(keys)] \
                    if mode in keys else keys[0]
                note = cal.targets.get(mode, {}).get("hint", "")
        elif ch == ord("g"):
            grid = not grid
        elif ch == ord("u"):
            use_und = not use_und
        elif ch == ord("c"):
            meas = []
        elif ch in (ord("+"), ord("=")):
            real_m = round(real_m + 0.05, 2)
        elif ch == ord("-"):
            real_m = round(max(0.05, real_m - 0.05), 2)
        elif ch in (13, 10):               # Enter — 측정값 적용
            if len(meas) >= 2:
                d = float(np.linalg.norm(np.array(meas[0]) - np.array(meas[1])))
                if d > 1:
                    cal.px2m = real_m / d
                    cal.length_m = real_m
                    note = f"적용: px2m={cal.px2m:.6f}, lane_width={real_m:.2f}m"
                    meas = []
        elif ch == ord("["):
            fno, f2 = read(fno - 30)
            frame = f2 if f2 is not None else frame
        elif ch == ord("]"):
            fno, f2 = read(fno + 30)
            frame = f2 if f2 is not None else frame
        elif ch == ord(","):
            fno, f2 = read(fno - 1)
            frame = f2 if f2 is not None else frame
        elif ch == ord("."):
            fno, f2 = read(fno + 1)
            frame = f2 if f2 is not None else frame
        elif ch == ord("r"):
            cal = Calib(contract, params)
            note = "되돌렸다"
        elif ch == ord("s"):
            out = args.out or "calibration_out.yaml"
            body = yaml.safe_dump({"params": cal.to_params()}, allow_unicode=True,
                                  sort_keys=False, default_flow_style=None)
            Path(out).write_text(
                "# tb.calibrate 결과 — 시나리오의 params: 에 붙이거나\n"
                "# local.yaml 의 params: 로 쓴다.\n"
                f"# 영상 {video}  frame {fno}\n" + body)
            note = f"저장 → {out}"
            print(note)
            print(body)
        else:
            step = 10 if (key & 0x10000) else 1   # Shift
            arrows = {0xFF51: (-1, 0), 0xFF52: (0, -1),
                      0xFF53: (1, 0), 0xFF54: (0, 1),
                      81: (-1, 0), 82: (0, -1), 83: (1, 0), 84: (0, 1)}
            d = arrows.get(key) or arrows.get(ch)
            if d and mode in cal.bev_rows:
                # 위 = 멀어짐. bev_row 는 행이 줄고, bev_dist 는 거리가 는다.
                cal.set_row_y(mode, cal.row_y(mode) + d[1] * step)
                continue
            hs = cal.handles(mode)
            if d and hs and sel < len(hs):
                _, arr, i = hs[sel]
                arr[i] = (arr[i][0] + d[0] * step, arr[i][1] + d[1] * step)

    cap.release()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
