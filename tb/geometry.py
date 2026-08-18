"""BEV 기하 — 대상 노드가 하는 변환을 그대로 재현한다.

캘리브레이션 도구가 화면에 그리는 BEV 와 실제 노드가 만드는 BEV 가 같아야
캘리브레이션이 의미를 가진다. 그래서 변환 순서를 여기 한 곳에 두고
`tb.calibrate` 와 `--check` 렌더가 같은 함수를 쓴다.

순서 (perception.cb 와 동일):
    원본 → cv2.remap(어안 보정) → getOptimalNewCameraMatrix ROI 로 crop
         → 원래 크기로 resize → getPerspectiveTransform → warpPerspective

★주의★ 어안 보정 계수는 대상 노드가 파라미터가 아니라 소스에 박아 둔 값이다.
계약의 calibration.undistort 에 같은 값을 적어야 하고, 노드 쪽이 바뀌면 여기도
같이 바꿔야 한다. `tb.calibrate --verify` 가 실제 노드 출력과 대조해 확인해 준다.
"""
from __future__ import annotations

import cv2
import numpy as np


class Undistorter:
    """어안 왜곡 보정 + ROI crop + 원래 크기 복원."""

    def __init__(self, size, K, D, alpha=0.0):
        self.w, self.h = int(size[0]), int(size[1])
        fx, fy, cx, cy = [float(v) for v in K]
        self.K = np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]],
                          dtype=np.float64)
        self.D = np.array([float(v) for v in D], dtype=np.float64)
        self.alpha = float(alpha)
        self.new_K, self.roi = cv2.getOptimalNewCameraMatrix(
            self.K, self.D, (self.w, self.h), self.alpha, (self.w, self.h))
        self.map1, self.map2 = cv2.initUndistortRectifyMap(
            self.K, self.D, None, self.new_K, (self.w, self.h), cv2.CV_16SC2)

    def __call__(self, frame):
        if frame.shape[1] != self.w or frame.shape[0] != self.h:
            frame = cv2.resize(frame, (self.w, self.h))
        out = cv2.remap(frame, self.map1, self.map2,
                        interpolation=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_CONSTANT)
        rx, ry, rw, rh = self.roi
        if rw > 0 and rh > 0:
            out = out[ry:ry + rh, rx:rx + rw]
            out = cv2.resize(out, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
        return out


def quad_to_pts(flat):
    """[x0,y0,x1,y1,x2,y2,x3,y3] → (4,2) float32. 순서는 TL,TR,BR,BL."""
    a = np.asarray(flat, dtype=np.float32).reshape(4, 2)
    return a


def pts_to_quad(pts):
    return [float(v) for v in np.asarray(pts, dtype=np.float32).reshape(-1)]


def ipm_matrices(src_pts, bev_w, bev_h):
    src = np.asarray(src_pts, dtype=np.float32).reshape(4, 2)
    dst = np.float32([[0, 0], [bev_w, 0], [bev_w, bev_h], [0, bev_h]])
    return (cv2.getPerspectiveTransform(src, dst),
            cv2.getPerspectiveTransform(dst, src))


def warp_bev(img, src_pts, bev_w, bev_h):
    M, _ = ipm_matrices(src_pts, bev_w, bev_h)
    return cv2.warpPerspective(img, M, (bev_w, bev_h))


def bev_to_src(pt, src_pts, bev_w, bev_h):
    _, Minv = ipm_matrices(src_pts, bev_w, bev_h)
    p = np.array([[[float(pt[0]), float(pt[1])]]], dtype=np.float32)
    q = cv2.perspectiveTransform(p, Minv)
    return (float(q[0][0][0]), float(q[0][0][1]))


def src_to_bev(pt, src_pts, bev_w, bev_h):
    M, _ = ipm_matrices(src_pts, bev_w, bev_h)
    p = np.array([[[float(pt[0]), float(pt[1])]]], dtype=np.float32)
    q = cv2.perspectiveTransform(p, M)
    return (float(q[0][0][0]), float(q[0][0][1]))


def quad_is_sane(pts, w, h):
    """되돌릴 수 없는 사각형(뒤집힘·자기교차·퇴화)을 잡아낸다.

    반환: (ok, 사유 목록). 경고만 하고 막지는 않는다 — 극단적인 배치가
    필요한 경우가 있어서다.
    """
    p = np.asarray(pts, dtype=np.float64).reshape(4, 2)
    why = []
    # 볼록성 + 방향(시계) 확인 — TL,TR,BR,BL 순이면 부호가 일정해야 한다
    cross = []
    for i in range(4):
        a, b, c = p[i], p[(i + 1) % 4], p[(i + 2) % 4]
        cross.append(np.cross(b - a, c - b))
    if not (all(x > 0 for x in cross) or all(x < 0 for x in cross)):
        why.append("사각형이 볼록하지 않다(점 순서가 TL→TR→BR→BL 인지 확인)")
    area = 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], -1))
                     - np.dot(p[:, 1], np.roll(p[:, 0], -1)))
    if area < (w * h) * 0.002:
        why.append("면적이 너무 작다")
    top = np.linalg.norm(p[1] - p[0])
    bot = np.linalg.norm(p[2] - p[3])
    if top < 4 or bot < 4:
        why.append("윗변 또는 아랫변이 거의 0")
    elif top > bot:
        why.append("윗변이 아랫변보다 길다 — 지면 평면이면 보통 반대다")
    return (not why), why


def draw_grid(bev, px2m, step_m=0.5, color=(90, 90, 90)):
    """BEV 위에 실측 격자. 차선이 수직·등간격으로 보이는지 확인하는 자다."""
    out = bev.copy()
    h, w = out.shape[:2]
    if px2m <= 0:
        return out
    step_px = step_m / px2m
    if step_px < 4:
        return out
    y = h
    i = 0
    while y > 0:
        y_i = int(round(y))
        cv2.line(out, (0, y_i), (w, y_i), color, 1, cv2.LINE_AA)
        if i:
            cv2.putText(out, f"{i * step_m:.1f}m", (4, max(11, y_i - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.34, color, 1, cv2.LINE_AA)
        y -= step_px
        i += 1
    x = w * 0.5
    while x < w:
        cv2.line(out, (int(x), 0), (int(x), h), color, 1, cv2.LINE_AA)
        x += step_px
    x = w * 0.5 - step_px
    while x > 0:
        cv2.line(out, (int(x), 0), (int(x), h), color, 1, cv2.LINE_AA)
        x -= step_px
    cv2.line(out, (w // 2, 0), (w // 2, h), (0, 200, 255), 1, cv2.LINE_AA)
    return out


# ══════════════════════════════════════════════════════════════════════
#  한글 텍스트 — cv2.putText 는 한글을 그리지 못한다(전부 ? 로 나온다).
#  PIL 로 그리고, 한글 폰트를 못 찾으면 조용히 cv2 로 떨어진다.
# ══════════════════════════════════════════════════════════════════════
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/unfonts-core/UnDotum.ttf",
]
_font_cache = {}


def _font(size):
    if size in _font_cache:
        return _font_cache[size]
    f = None
    try:
        from PIL import ImageFont
        import os
        for p in _FONT_CANDIDATES:
            if os.path.exists(p):
                f = ImageFont.truetype(p, size)
                break
    except Exception:   # noqa: BLE001
        f = None
    _font_cache[size] = f
    return f


def put_text(img, text, org, size=15, color=(230, 230, 230)):
    """한글이 되는 putText. color 는 BGR."""
    f = _font(size)
    if f is None:
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, size / 32.0,
                    color, 1, cv2.LINE_AA)
        return img
    from PIL import Image, ImageDraw
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    ImageDraw.Draw(pil).text((org[0], org[1] - size), text,
                             font=f, fill=(color[2], color[1], color[0]))
    img[:] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    return img


def verticality(bev, min_len_ratio=0.25):
    """BEV 의 밝은 선들이 얼마나 수직인가 [deg]. IPM 사각형이 맞았는지의 자.

    지면은 평면이므로 사각형의 좌우 변을 차선 위에 올리면 BEV 에서 차선은
    정확히 수직이 된다. 직선 구간에서 이 값이 0 에 가까우면 사각형이 맞은 것이다.
    ★곡선 구간에서는 의미가 없다★ — 차선 자체가 휘어 있으므로.

    반환: (중앙값 편차[deg], 검출된 선 개수)
    """
    g = cv2.cvtColor(bev, cv2.COLOR_BGR2GRAY) if bev.ndim == 3 else bev
    h, w = g.shape[:2]
    edges = cv2.Canny(cv2.GaussianBlur(g, (5, 5), 0), 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360,
                            threshold=40,
                            minLineLength=int(h * min_len_ratio),
                            maxLineGap=int(h * 0.06))
    if lines is None:
        return (float("nan"), 0)
    devs = []
    for x1, y1, x2, y2 in lines[:, 0]:
        dx, dy = float(x2 - x1), float(y2 - y1)
        if abs(dy) < 1e-6:
            continue
        ang = abs(np.degrees(np.arctan2(dx, dy)))   # 0 = 수직
        if ang > 90:
            ang = 180 - ang
        if ang < 45:                                # 가로선(격자·경계)은 뺀다
            devs.append(ang)
    if not devs:
        return (float("nan"), 0)
    return (float(np.median(devs)), len(devs))
