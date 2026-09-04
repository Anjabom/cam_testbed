"""캘리브레이션 엔진 — 보정 스튜디오(웹앱)가 쓰는 계산부.

대상 노드가 하는 변환을 `tb.geometry` 로 그대로 재현하므로, 스튜디오에서 맞춘
BEV 가 실제 노드가 만드는 BEV 와 같다(실측 확인: 에지 일치율 0.85 — 남는 차이는
노드가 BEV 위에 그리는 오버레이뿐이다. `verify()` 가 그 대조를 한다).

맞추는 대상과 파라미터 이름은 전부 ★프로필★ 의 `calibration.targets` 에 있다.
이 파일에는 대상 워크스페이스의 파라미터 이름이 없다. 프로필은 두 종류다:
계약 파일(`contracts/*.yaml` — 워크스페이스에 붙은 것)과 독립 프로필
(`calib/*.yaml` — 워크스페이스 없이 카메라만 실험할 때).

★화면은 여기 없다★ [2026-09-04] 예전에는 이 파일이 cv2 창을 띄우는 대화형
도구이기도 했다. 웹 스튜디오와 같은 일을 두 벌로 갖고 있었던 셈이라, 한쪽만
고쳐지는 일이 반복됐다. 지금 UI 는 `web/` 한 곳뿐이고 여기는 계산만 한다.

★핵심 요령★ IPM 사각형의 좌우 변을 차선 위에 올려라. 지면은 평면이므로
그렇게 놓으면 BEV 에서 차선이 정확히 수직·평행으로 선다. 수직이 아니면
사각형이 틀린 것이다 — 격자가 그 자다.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .geometry import undistorter, warp_bev


# ══════════════════════════════════════════════════════════════════════
class Calib:
    """계약이 선언한 캘리브레이션 대상들의 현재 값."""

    def __init__(self, contract, params, ws_params=None):
        #  ws_params : `tb.run params` 로 받아 둔 ★워크스페이스 기본값★
        #    우선순위 = 시나리오/local params → 이것 → 계약의 default
        #    (계약에 옮겨 적은 값은 갈라지므로 노드가 말한 값을 더 믿는다)
        self.ws_params = ws_params or {}
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
            got = self._param_value(params, t, self.ws_params)
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
    def _param_value(params, t, ws_params=None):
        """이 대상의 현재 값 — 앞에서부터 처음 있는 것을 쓴다.

            ① 시나리오/local.yaml params  (사람이 이 시험을 위해 정한 값)
            ② 워크스페이스 기본값 캐시    (노드가 스스로 선언한 값)
            ③ 계약의 default:            (①②가 없을 때의 마지막 폴백)
        """
        names = t.get("params") or ([t["param"]] if t.get("param") else [])
        for src in (params, ws_params or {}):
            out = []
            for nid in t.get("nodes", []):
                kv = (src or {}).get(nid, {})
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


def verify(cal, profile, video, dbg_path, start=0, n=7, out_png=""):
    """내가 그리는 BEV 와 ★노드가 실제로 만든 BEV★ 가 같은지 대조한다.

    노드는 BEV 위에 피팅 곡선·HUD 를 그리므로 픽셀이 같을 수는 없다.
    그래서 에지가 겹치는 비율로 ★기하★만 본다. 0.75 이상이면 같은 변환이다.

    `dbg_path` 는 대상 노드가 남긴 디버그 mp4 (`tb.run run` 이 만든다).
    그 영상의 어느 부분이 BEV 판인지는 프로필의 `calibration.verify.bev_pane` 이
    말한다 — 판 수가 계약마다 다르므로 여기 박지 않는다.
    """
    cfg = (profile.raw.get("calibration") or {}).get("verify") or {}
    dbg_path = Path(dbg_path)
    if not dbg_path.is_file():
        raise ValueError(f"디버그 영상이 없습니다: {dbg_path}")
    lo, hi = cfg.get("bev_pane", [0.5, 1.0])
    log = []

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
    log.append(f"프레임 정렬 오프셋 {off:+d} (일치율 {off_sc:.3f})")

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
        raise ValueError("대조할 프레임을 하나도 못 읽었습니다")

    med = float(np.median(scores))
    log.append(f"프레임 {len(scores)}장 대조 — 에지 일치율 중앙값 {med:.3f} "
               f"(최소 {min(scores):.3f} / 최대 {max(scores):.3f})")
    if med >= 0.75:
        verdict = "✅ 같은 변환이다 — 여기서 맞춘 값이 노드에 그대로 적용된다."
    elif med >= 0.5:
        verdict = ("⚠️ 어긋난다. 프로필의 calibration.undistort 가 노드의 값과 같은지, "
                   "그 런의 IPM 사각형이 지금 값과 같은지 확인할 것.")
    else:
        verdict = "❌ 전혀 다르다. 왜곡보정 계수나 프레임 정렬(start)이 틀렸을 가능성이 크다."
    log.append(verdict)
    png = ""
    if out_png and best:
        #  왼쪽=내가 재현한 BEV, 오른쪽=노드가 실제로 만든 BEV
        cv2.imwrite(str(out_png), np.hstack(
            [best[3], np.full((best[2].shape[0], 6, 3), 255, np.uint8), best[2]]))
        png = str(out_png)
    return {"median": med, "min": min(scores), "max": max(scores),
            "n": len(scores), "offset": off, "ok": med >= 0.75,
            "verdict": verdict, "log": log, "png": png}
