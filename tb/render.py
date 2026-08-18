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
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from .geometry import Undistorter, ipm_matrices, put_text

C_LEFT = (235, 150, 60)     # BGR 파랑
C_RIGHT = (70, 80, 220)     # 빨강
C_CENTER = (60, 175, 245)   # 주황 — 경로
C_SECANT = (80, 255, 255)   # 노랑 — θ 를 만드는 직선
C_TANGENT = (150, 150, 150)  # 회색 — 접선(비교)
C_AXIS = (120, 120, 120)


def _poly_x(fit, y):
    a, b, c = fit
    return a * y * y + b * y + c


def _dx(fit, y):
    a, b, _ = fit
    return 2.0 * a * y + b


def _valid(fit):
    return fit is not None and (abs(fit[0]) + abs(fit[1]) + abs(fit[2])) > 1e-9


class Renderer:
    """계약의 render 블록으로 설정을 읽어 프레임에 오버레이를 그린다."""

    def __init__(self, contract, params=None):
        cal = contract.raw.get("calibration") or {}
        r = contract.raw.get("render") or {}
        u = cal.get("undistort") or {}
        self.und = (Undistorter(u["size"], u["K"], u["D"], u.get("alpha", 0.0))
                    if u else None)
        self.size = u.get("size", [1920, 1080])
        self.bev_w = int((cal.get("bev") or {}).get("w", 640))
        self.bev_h = int((cal.get("bev") or {}).get("h", 480))

        self.bottom_ratio = float(r.get("bottom_ratio", 0.92))
        self.lookahead_ratio = float(r.get("lookahead_ratio", 0.45))
        sg = r.get("signals") or {}
        self.k_left = sg.get("left", ["fit_aL", "fit_bL", "fit_cL"])
        self.k_right = sg.get("right", ["fit_aR", "fit_bR", "fit_cR"])
        self.k_half = sg.get("half_width", "half_width_px")
        self.readout = r.get("readout", [])

        # IPM 사각형 — 시나리오 params 가 덮어썼으면 그걸 쓴다
        quad = None
        tgt = (cal.get("targets") or {})
        for t in tgt.values():
            if t.get("kind") == "quad":
                nm = t.get("param")
                for nid in t.get("nodes", []):
                    v = (params or {}).get(nid, {}).get(nm)
                    if v:
                        quad = v
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

    def draw(self, frame_raw, row, px2m=0.006, show_tangent=True):
        """원본 프레임 + BEV 를 가로로 붙인 오버레이 한 장을 만든다."""
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
