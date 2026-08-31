"""경로 시각화 — ★판정에 쓰는 값 그대로★ 원본 프레임 위에 그린다.

대상 노드도 `/lane/debug` 를 그리지만, 그건 그 노드가 보여 주고 싶은 것이다.
여기서는 ★테스트베드가 판정에 실제로 쓴 숫자★(signals.csv 의 폴리핏 계수)로
다시 그린다. 그래서 "리포트의 θ 가 왜 이 값인지"를 그림에서 바로 확인할 수 있다.

그리는 것 (BEV 와 원본 양쪽에):
    · 좌/우 차선 폴리핏          x = a·y² + b·y + c
    · ★중심선(경로 차선)★        xc(y) = (xL + xR) / 2
    · 근점 y_near, 전방점 y_look
    · ★θ 를 만드는 시컨트★        두 점을 잇는 직선 — θ 는 이 직선의 각이다
    · 접선(비교용)                곡선에서 시컨트와 얼마나 다른지 눈으로 보인다
    · cte                        중심선 근점과 BEV 중앙의 가로 거리

계약의 `render:` 블록이 어느 신호가 좌/우 계수인지 알려 준다 —
이 파일에는 워크스페이스의 신호 이름이 없다.

★거리 판정 노드용 화면 (`render.bev_dist`)★
    차선 폴리핏 대신 ★BEV 위의 가로선 하나★ 로 판정하는 노드가 있다(정지선까지
    몇 px 처럼). 그런 노드는 디버그 이미지를 안 낼 수도 있는데, 그러면 숫자만 남고
    "그 숫자가 가리키는 곳에 정말 그것이 있나"를 볼 방법이 없어진다 — 검출률이
    좋아도 엉뚱한 것을 잡고 있으면 소용이 없으므로 그 확인이 판정의 절반이다.
    그래서 계약에 `render.bev_dist` 가 있으면 아래를 그린다:
        · BEV 의 기준선(거리 0)과 문턱들          ← 캘리브 대상에서 그대로
        · ★노드가 발행한 거리★ 가 놓이는 가로선   ← 자홍색
        · 그 선을 ★원본 화면으로 되돌린 것★       ← 노면 위 실제 위치와 대조
"""
from __future__ import annotations

import math

from pathlib import Path

import cv2
import numpy as np

from .geometry import draw_rows, ipm_matrices, put_text, undistorter

C_LEFT = (235, 150, 60)     # BGR 파랑
C_RIGHT = (70, 80, 220)     # 빨강
C_CENTER = (60, 175, 245)   # 주황 — 경로
C_SECANT = (80, 255, 255)   # 노랑 — θ 를 만드는 직선
C_TANGENT = (150, 150, 150)  # 회색 — 접선(비교)
C_AXIS = (120, 120, 120)
C_MEAS = (230, 90, 230)     # 자홍 — 노드가 잰 거리
C_WARN = (60, 200, 255)     # 주황 — 이 그림을 믿으면 안 되는 이유


def _poly_x(fit, y):
    a, b, c = fit
    return a * y * y + b * y + c


def _dx(fit, y):
    a, b, _ = fit
    return 2.0 * a * y + b


def _valid(fit):
    return fit is not None and (abs(fit[0]) + abs(fit[1]) + abs(fit[2])) > 1e-9


def _target_value(t, params):
    """캘리브 대상 하나의 현재 값 — ★언제나 리스트★ 로 돌려준다(없으면 None).

    `tb.calibrate.Calib._param_value` 와 ★같은 규칙★ 이다. 여기만 규칙이 달라서
    (값이 여럿이어도 첫 개만 돌려줬다) 값이 8개인 `quad` 대상을 params 로 받는
    순간 4×2 로 못 펴고 죽었다:

        ValueError: cannot reshape array of size 1 into shape (4,2)

    ★캘리브 화면에서 «저장» 한 사다리꼴이 시나리오에 들어오는 순간 걸리는 자리★
    였다 — 아무도 그 값을 넣은 적이 없어서 여태 안 드러났을 뿐이다. 두 함수의
    규칙이 갈리면 또 이렇게 되므로 붙여 둔다.

    파라미터 이름은 계약에만 있으므로 이 함수도 워크스페이스를 모른다.
    """
    names = t.get("params") or ([t["param"]] if t.get("param") else [])
    for nid in t.get("nodes", []):
        kv = (params or {}).get(nid, {})
        out = []
        for n in names:
            if n in kv:
                v = kv[n]
                out.extend(v if isinstance(v, (list, tuple)) else [v])
        if out:
            return out
    return None


def _scalar(v, default=None):
    """`_target_value` 의 리스트에서 스칼라 하나 — 없으면 계약의 default."""
    if v is None:
        v = default
    if v is None:
        return None
    return float(v[0] if isinstance(v, (list, tuple)) else v)


class Renderer:
    """계약의 render 블록으로 설정을 읽어 프레임에 오버레이를 그린다."""

    def __init__(self, contract, params=None):
        cal = contract.raw.get("calibration") or {}
        r = contract.raw.get("render") or {}
        u = cal.get("undistort") or {}
        base = Path(contract.path).parent if contract.path != "mem" else None
        self.und, self.size = undistorter(u, base)
        # ★그 런이 실제로 보정을 켜고 돌았는가★ 껐다면 여기서도 끄지 않으면
        #   '노드가 본 그림' 과 다른 것을 그리게 되고, 거리선이 엉뚱한 곳에 얹힌다.
        pnm = u.get("param")
        if pnm:
            for nid, kv in (params or {}).items():
                if pnm in kv and not kv[pnm]:
                    self.und = None
        self.bev_w = int((cal.get("bev") or {}).get("w", 640))
        self.bev_h = int((cal.get("bev") or {}).get("h", 480))

        self.bottom_ratio = float(r.get("bottom_ratio", 0.92))
        self.lookahead_ratio = float(r.get("lookahead_ratio", 0.45))
        sg = r.get("signals") or {}
        self.k_left = sg.get("left", ["fit_aL", "fit_bL", "fit_cL"])
        self.k_right = sg.get("right", ["fit_aR", "fit_bR", "fit_cR"])
        self.k_half = sg.get("half_width", "half_width_px")
        self.readout = r.get("readout", [])

        # ── 거리 판정 화면 (있을 때만) ────────────────────────────
        #   기준선·문턱의 ★현재 값★ 은 그 런이 실제로 쓴 파라미터에서 온다.
        #   기본값으로 그리면 화면과 판정이 어긋나므로 params 를 먼저 본다.
        self.bd = r.get("bev_dist") or None
        self.bd_rows = []
        self.bd_px2m = 0.0
        if self.bd:
            for key, t in (cal.get("targets") or {}).items():
                kind = t.get("kind")
                if kind not in ("bev_row", "bev_dist"):
                    continue
                v = _scalar(_target_value(t, params), t.get("default"))
                if v is None:
                    v = float(self.bev_h) if kind == "bev_row" else None
                if v is None:
                    continue
                self.bd_rows.append((key, kind, v))
            # 기준선 먼저, 그 다음 먼 문턱 → 가까운 문턱
            self.bd_rows.sort(key=lambda x: (x[1] != "bev_row", -x[2]))
            #  참고 미터 — ★판정에는 안 쓴다★. 노드에서도 0 이면 화면에도 안 붙는다.
            for t in (cal.get("targets") or {}).values():
                if t.get("kind") == "scale":
                    self.bd_px2m = _scalar(_target_value(t, params),
                                           t.get("default")) or 0.0

        # IPM 사각형 — 그 런이 실제로 쓴 params 가 있으면 그걸 쓴다
        quad = None
        #  ★계약의 default 로 떨어졌는가★ [2026-08-25]
        #  이 그림은 '노드가 판정에 쓴 그 사다리꼴' 이어야 뜻이 있다. default 는
        #  계약이 적어 둔 ★문서★ 일 뿐이고 노드가 실제로 든 값이 아니다 — 실제로
        #  어긋난 적이 있다(night_b: 노드 (750,650)(1170,650)(1810,1080)(260,1080)
        #  ↔ 계약 default (750,560)(1170,560)(1920,1080)(0,1080)). 그때 자홍색
        #  거리선이 205px(≈1.2m) 먼 곳에 얹혀 ★멀쩡한 판정값이 틀려 보였다★.
        #  그림 없이 원본만 나오는 것보다는 낫지만, 조용히 틀린 그림이 제일 나쁘다.
        self.quad_guessed = False
        tgt = (cal.get("targets") or {})
        for t in tgt.values():
            if t.get("kind") != "quad":
                continue
            v = _target_value(t, params)
            #  런의 params 에 없으면 계약이 적어 둔 ★노드 기본값★ 을 쓴다.
            #  (예전에는 여기서 None 이 되어 BEV 를 아예 못 그렸다 — 그러면 오버레이
            #   없이 원본만 나와서 '왜 안 그려지지' 를 한참 찾게 된다)
            quad = t.get("default") if v is None else v
            self.quad_guessed = v is None and quad is not None
        if quad is None:
            quad = r.get("ipm_src_pts")
        self.quad = (np.asarray(quad, np.float32).reshape(4, 2)
                     if quad is not None else None)
        self.M, self.Minv = (ipm_matrices(self.quad, self.bev_w, self.bev_h)
                             if self.quad is not None else (None, None))

    # ── 좌표 ────────────────────────────────────────────────────────
    @property
    def y_near(self):
        return self.bev_h * self.bottom_ratio

    @property
    def y_look(self):
        return self.bev_h * self.lookahead_ratio

    def fits(self, row):
        lf = [row.get(k) for k in self.k_left]
        rf = [row.get(k) for k in self.k_right]
        num = lambda v: isinstance(v, (int, float))       # noqa: E731
        lf = lf if all(num(v) for v in lf) else None
        rf = rf if all(num(v) for v in rf) else None
        return (lf if _valid(lf) else None), (rf if _valid(rf) else None)

    def center_x(self, lf, rf, y, half_px):
        """중심선 — 양쪽이 있으면 평균, 한쪽이면 법선방향 반폭 이동."""
        if lf and rf:
            return 0.5 * (_poly_x(lf, y) + _poly_x(rf, y))
        fit = lf or rf
        if not fit:
            return None
        sgn = +1.0 if lf else -1.0
        m = _dx(fit, y)
        return _poly_x(fit, y) + sgn * half_px / math.sqrt(1.0 + m * m)

    # ── 그리기 ──────────────────────────────────────────────────────
    def _in_bev(self, x):
        """BEV 폭 밖으로 나간 곡선은 버린다.

        폴리핏은 y 의 이차식이라 BEV 밖에서 급격히 발산한다. 그걸 그대로
        역-IPM 하면 원본에서 ★지평선 너머로 선이 뻗는다★ — 실제 차선이
        거기 있는 게 아니라 외삽이 폭주한 것이다.
        """
        return -40.0 <= x <= self.bev_w + 40.0

    def _segments(self, pts):
        """BEV 안에 있는 구간만 끊어서 돌려준다."""
        out, cur = [], []
        for x, y in pts:
            if self._in_bev(x):
                cur.append((x, y))
            elif cur:
                out.append(cur)
                cur = []
        if cur:
            out.append(cur)
        return [s2 for s2 in out if len(s2) > 1]

    def _curve_bev(self, img, fit, color, th=2):
        pts = [(_poly_x(fit, y), float(y)) for y in range(0, self.bev_h, 4)]
        for seg in self._segments(pts):
            cv2.polylines(img, [np.array([(int(x), int(y)) for x, y in seg],
                                         np.int32)], False, color, th, cv2.LINE_AA)

    def _project(self, img, pts, color, th=2):
        """BEV 좌표 목록을 원본으로 되돌려 그린다 (BEV 안쪽 구간만)."""
        if self.Minv is None:
            return
        H, W = img.shape[:2]
        for seg in self._segments(pts):
            arr = np.array([[[x, y] for x, y in seg]], np.float32)
            out = cv2.perspectiveTransform(arr, self.Minv)[0]
            keep = [(int(round(p[0])), int(round(p[1]))) for p in out
                    if -W < p[0] < 2 * W and -H < p[1] < 2 * H]
            if len(keep) > 1:
                cv2.polylines(img, [np.array(keep, np.int32)], False, color, th,
                              cv2.LINE_AA)

    def _curve_src(self, img, fit, color, th=2):
        self._project(img, [(_poly_x(fit, y), float(y))
                            for y in range(0, self.bev_h, 6)], color, th)

    def _center_pts(self, lf, rf, half_px, step=6):
        out = []
        for y in range(0, self.bev_h, step):
            x = self.center_x(lf, rf, float(y), half_px)
            if x is not None:
                out.append((x, float(y)))
        return out

    # ── 거리 판정 화면 ──────────────────────────────────────────────
    def _bumper_y(self):
        for key, kind, v in self.bd_rows:
            if kind == "bev_row":
                return v
        return float(self.bev_h)

    def _row_y(self, kind, v):
        return v if kind == "bev_row" else self._bumper_y() - v

    def draw_bev_dist(self, frame_raw, row):
        """★노드가 발행한 거리★ 를 BEV 와 원본 양쪽에 그린다."""
        px2m = self.bd_px2m
        src = self.und(frame_raw) if self.und else frame_raw
        if self.M is None:
            return src
        bev = cv2.warpPerspective(src, self.M, (self.bev_w, self.bev_h))
        src = src.copy()

        q = self.quad.astype(np.int32)
        cv2.polylines(src, [q.reshape(-1, 1, 2)], True, (90, 90, 90), 2, cv2.LINE_AA)

        # 기준선·문턱 — BEV 에 그리고, 원본으로도 되돌려 그린다
        lines = []
        for i, (key, kind, v) in enumerate(self.bd_rows):
            y = self._row_y(kind, v)
            unit = "" if kind == "bev_row" else f" {v:.0f}px"
            m = f" ({v * px2m:.2f}m)" if (px2m > 0 and kind != "bev_row") else ""
            lines.append((key, y, key + unit + m))
            self._project(src, [(0.0, y), (float(self.bev_w), y)],
                          [(120, 220, 90), (60, 170, 255), (60, 60, 240),
                           (200, 120, 240)][i % 4], 2)
        draw_rows(bev, lines)

        # ★판정값★ — 노드가 낸 거리가 놓이는 자리
        key = (self.bd or {}).get("distance")
        d = row.get(key)
        miss = float((self.bd or {}).get("missing", -1.0))
        seen = isinstance(d, (int, float)) and float(d) > miss + 1e-9
        if seen:
            y = self._bumper_y() - float(d)
            cv2.line(bev, (0, int(y)), (self.bev_w, int(y)), C_MEAS, 3, cv2.LINE_AA)
            put_text(bev, f"{key} {float(d):.0f}px", (8, max(14, int(y) - 8)),
                     16, C_MEAS)
            self._project(src, [(0.0, y), (float(self.bev_w), y)], C_MEAS, 3)

        lines_txt = [f"frame {int(row.get('frame', -1))}"]
        for k in (self.readout or [key]):
            v = row.get(k)
            lines_txt.append(f"{k:<16}" + (f"{v:+.1f}" if isinstance(v, float)
                                           else f"{v}"))
        lines_txt += ["", ("★ 자홍색 선이 노드가 잰 거리다 — 노면의 그것과 "
                           "겹치는가" if seen else "★ 미검출 — 자홍색 선이 없다")]
        if self.quad_guessed:
            lines_txt += ["⚠ 사다리꼴을 계약 default 로 그렸다 — 이 런이 실제로",
                          "⚠ 쓴 값이 아니다. 선 위치를 믿지 말 것"]
        panel_h = 22 * len(lines_txt) + 14
        cv2.rectangle(src, (0, 0), (560, panel_h), (24, 24, 28), -1)
        for i, t in enumerate(lines_txt):
            put_text(src, t, (10, 24 + 22 * i), 16,
                     C_WARN if t.startswith("⚠") else
                     (C_MEAS if t.startswith("★") else (225, 225, 225)))

        sh = src.shape[0]
        scale = sh / float(self.bev_h)
        return np.hstack([src, cv2.resize(bev, (int(self.bev_w * scale), sh))])

    def draw(self, frame_raw, row, px2m=0.006, show_tangent=True):
        """원본 프레임 + BEV 를 가로로 붙인 오버레이 한 장을 만든다."""
        if self.bd:
            return self.draw_bev_dist(frame_raw, row)
        src = self.und(frame_raw) if self.und else frame_raw
        if self.M is None:
            return src
        bev = cv2.warpPerspective(src, self.M, (self.bev_w, self.bev_h))
        src = src.copy()

        # ── IPM 사각형을 원본에 그린다 ──
        #   원본에서 곡선이 멀리까지 뻗어 보이면 대개 이 사각형이 트랙 밖까지
        #   걸쳐 있기 때문이다. 그리면 그게 바로 보인다(캘리브레이션 단서).
        q = self.quad.astype(np.int32)
        cv2.polylines(src, [q.reshape(-1, 1, 2)], True, (90, 90, 90), 2, cv2.LINE_AA)
        for i, lab in enumerate(("TL", "TR", "BR", "BL")):
            put_text(src, lab, (int(q[i][0]) + 6, int(q[i][1]) - 6), 14, (90, 90, 90))

        lf, rf = self.fits(row)
        half = row.get(self.k_half)
        half_px = float(half) if isinstance(half, (int, float)) and half > 1 else 200.0
        yn, yl = self.y_near, self.y_look

        # 근점·전방점 기준선
        for y, lab in ((yn, "y_near"), (yl, "y_look")):
            cv2.line(bev, (0, int(y)), (self.bev_w, int(y)), C_AXIS, 1, cv2.LINE_AA)
            put_text(bev, lab, (6, int(y) - 4), 12, C_AXIS)
        cv2.line(bev, (self.bev_w // 2, 0), (self.bev_w // 2, self.bev_h),
                 C_AXIS, 1, cv2.LINE_AA)

        for y, col in ((yn, C_CENTER), (yl, C_AXIS)):
            self._project(src, [(0.0, y), (float(self.bev_w), y)], col, 1)

        if lf:
            self._curve_bev(bev, lf, C_LEFT)
            self._curve_src(src, lf, C_LEFT)
        if rf:
            self._curve_bev(bev, rf, C_RIGHT)
            self._curve_src(src, rf, C_RIGHT)

        note = []
        if lf or rf:
            # ── 중심선 = 경로 차선 ──
            cpts = self._center_pts(lf, rf, half_px)
            for seg in self._segments(cpts):
                cv2.polylines(bev, [np.array([(int(x), int(y)) for x, y in seg],
                                             np.int32)], False, C_CENTER, 4, cv2.LINE_AA)
            self._project(src, cpts, C_CENTER, 3)

            xc_n = self.center_x(lf, rf, yn, half_px)
            xc_l = self.center_x(lf, rf, yl, half_px)
            if xc_n is not None and xc_l is not None:
                # ── θ 를 만드는 시컨트 ──
                p1, p2 = (int(xc_n), int(yn)), (int(xc_l), int(yl))
                cv2.line(bev, p1, p2, C_SECANT, 2, cv2.LINE_AA)
                cv2.circle(bev, p1, 5, C_SECANT, -1, cv2.LINE_AA)
                cv2.circle(bev, p2, 5, C_SECANT, -1, cv2.LINE_AA)
                th_sec = -math.degrees(math.atan2(xc_l - xc_n, yn - yl))
                note.append(f"세컨트 θ = {th_sec:+.2f}°")

                # ── 접선 (비교용) ──
                if show_tangent and lf and rf:
                    m = 0.5 * (_dx(lf, yn) + _dx(rf, yn))
                    dy = yn - yl
                    xt = xc_n - m * dy
                    cv2.line(bev, p1, (int(xt), int(yl)), C_TANGENT, 2, cv2.LINE_AA)
                    th_tan = -math.degrees(math.atan(m))
                    note.append(f"접선 θ = {th_tan:+.2f}°  (차이 {th_tan - th_sec:+.2f}°)")

                # ── cte ──
                cx = self.bev_w * 0.5
                cv2.arrowedLine(bev, (int(cx), int(yn)), p1, C_CENTER, 2,
                                cv2.LINE_AA, tipLength=0.25)
                note.append(f"cte(근점) = {(xc_n - cx) * px2m:+.3f} m "
                            f"[화면좌표, 발행값은 부호 반전]")
            if lf and rf:
                w = (_poly_x(rf, yn) - _poly_x(lf, yn)) * px2m
                note.append(f"차선폭 = {w:.3f} m")
        else:
            note.append("차선 미검출")

        # ── 판정에 쓴 값 ──
        keys = self.readout or ["theta_deg", "cte_rear_m", "conf_eff",
                                "lane_width_m", "flags"]
        lines = [f"frame {int(row.get('frame', -1))}"]
        for k in keys:
            v = row.get(k)
            lines.append(f"{k:<14}" + (f"{v:+.4f}" if isinstance(v, (int, float))
                                       else f"{v}"))
        lines += ["", "— 테스트베드가 판정에 쓴 값으로 다시 그린 것 —"] + note

        panel_h = 20 * len(lines) + 14
        cv2.rectangle(src, (0, 0), (520, panel_h), (24, 24, 28), -1)
        for i, t in enumerate(lines):
            col = (C_SECANT if "θ" in t else
                   C_CENTER if "cte" in t or "폭" in t else (225, 225, 225))
            put_text(src, t, (10, 22 + 20 * i), 15, col)

        # 범례
        leg = [("좌 차선", C_LEFT), ("우 차선", C_RIGHT), ("중심선(경로)", C_CENTER),
               ("θ 시컨트", C_SECANT), ("접선", C_TANGENT)]
        lh = 18 * len(leg) + 10
        cv2.rectangle(bev, (0, self.bev_h - lh), (150, self.bev_h),
                      (24, 24, 28), -1)
        for i, (t, c) in enumerate(leg):
            y = self.bev_h - 12 - 18 * (len(leg) - 1 - i)
            cv2.line(bev, (8, y - 4), (26, y - 4), c, 3, cv2.LINE_AA)
            put_text(bev, t, (32, y), 13, c)

        sh = src.shape[0]
        scale = sh / float(self.bev_h)
        bev_big = cv2.resize(bev, (int(self.bev_w * scale), sh))
        return np.hstack([src, bev_big])
