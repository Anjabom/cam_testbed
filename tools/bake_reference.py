"""대조점 굽기 — cv2 가 낸 값을 `web/reference.js` 에 박는다.

★왜 필요한가★
스튜디오가 정적 페이지로 옮겨 오면서 기하가 `web/geom.js` 에 한 벌 더 생겼다.
두 벌이 갈라지면 화면이 보여 주는 BEV 와 노드가 만드는 BEV 가 달라지고,
그러면 거기서 맞춘 값이 실차에서 틀린다. 그래서 ★cv2 를 정답으로 두고★
JS 를 그것과 대조한다 — 그 정답표가 이 파일이 굽는 `web/reference.js` 다.

    python3 tools/bake_reference.py        # 다시 굽는다
    python3 -m tb.selftest                 # t_geom_js 가 그것으로 JS 를 검사한다

★여기 카메라는 지어낸 것이다★ 재려는 것이 「이 차의 값이 맞나」가 아니라
「같은 입력에 같은 답을 내나」라서 그렇다. 실측 K·D 로 재면 그 값이 바뀔 때마다
정답표를 다시 구워야 하고, 그러면 대조가 캘리브 변경에 끌려다닌다.

★끝에서 끝까지 재는 요령★ — 좌표를 색으로 칠한 그림을 파이프라인에 통과시킨다.
R 채널에 x, G 채널에 y 를 넣어 두면(둘 다 1차 램프라 이중선형 보간이 정확하다)
BEV 의 한 픽셀을 읽는 것만으로 「이 자리는 원본의 어디였나」를 되읽을 수 있다.
그래서 중간 단계를 파이썬에 다시 쓰지 않고도 합성 전체를 잰다 — 파이썬에 같은
식을 또 적으면 그 식이 틀렸을 때 양쪽이 사이좋게 틀려 대조가 통과해 버린다.

★이 방법의 잡음 바닥은 0.03px 이다★ float32 로 올려도 남는다. cv2 의 remap·
warpPerspective 가 좌표를 1/32 픽셀 고정소수점으로 반올림하기 때문이다(실측:
같은 파이썬 값끼리 대조해도 0.028px 이 남는다). 그래서 허용치를 그보다 넉넉한
0.1px 로 잡는다 — 순서·반픽셀 규약이 틀리면 0.5px 이상으로 벌어지므로 그건 잡힌다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tb.geometry import (Undistorter, ipm_matrices,           # noqa: E402
                         quad_is_sane, warp_bev)

#  지어낸 카메라 둘. ①은 흔한 광각(배럴 왜곡), ②는 왜곡이 없는 판 —
#  D 가 0 이면 코드가 다른 길로 새기 쉬워서(나눗셈·반복문) 일부러 넣는다.
CASES = [
    {
        "name": "wide",
        "size": [1280, 720],
        "K": [640.0, 642.5, 651.3, 358.7],
        "D": [-0.28, 0.06, 0.0004, -0.0012, 0.0],
        "alpha": 0.0,
        "quad": [420.0, 400.0, 860.0, 400.0, 1240.0, 715.0, 40.0, 715.0],
        "bev": [640, 1000],
    },
    {
        #  ★ROI 가 크게 잘리는 판이 반드시 하나 있어야 한다★ alpha=0 인 판은
        #  ROI 가 화면과 거의 같아(1픽셀 차이) crop·resize 단계가 사실상 안 재진다.
        #  실측: 그 상태에선 반픽셀 규약을 통째로 빼도 오차가 0.0004px 이라
        #  대조를 그냥 통과했다. 여기는 roi=(15,88,1252,545) 로 잘린다.
        "name": "wide_a1",
        "size": [1280, 720],
        "K": [640.0, 642.5, 651.3, 358.7],
        "D": [-0.28, 0.06, 0.0004, -0.0012, 0.0],
        "alpha": 1.0,
        "quad": [430.0, 380.0, 850.0, 380.0, 1150.0, 660.0, 130.0, 660.0],
        "bev": [640, 1000],
    },
    {
        "name": "nodist",
        "size": [960, 540],
        "K": [500.0, 500.0, 480.0, 270.0],
        "D": [0.0, 0.0, 0.0, 0.0, 0.0],
        "alpha": 0.0,
        "quad": [300.0, 300.0, 660.0, 300.0, 940.0, 535.0, 20.0, 535.0],
        "bev": [640, 480],
    },
]

MAP_SAMPLES = 60      # 보정 맵 표본 (격자)
BEV_SAMPLES = 60      # 끝에서 끝까지 표본


def _map_samples(K, D, new_K, size):
    """보정된 픽셀 (u,v) → 원본 픽셀. cv2 의 맵을 그대로 읽는다."""
    w, h = size
    kk = np.array([[K[0], 0, K[2]], [0, K[1], K[3]], [0, 0, 1]], dtype=np.float64)
    nk = np.array([[new_K[0], 0, new_K[2]], [0, new_K[1], new_K[3]], [0, 0, 1]],
                  dtype=np.float64)
    #  ★CV_32FC1 로 뽑는다★ 기본인 CV_16SC2 는 고정소수점이라 1/32 픽셀로
    #  양자화된다 — 그걸 정답이라고 두면 JS 가 맞아도 어긋난 것처럼 보인다.
    m1, m2 = cv2.initUndistortRectifyMap(kk, np.array(D, dtype=np.float64), None,
                                         nk, (w, h), cv2.CV_32FC1)
    out = []
    n = int(round(MAP_SAMPLES ** 0.5))
    for i in range(n):
        for j in range(n):
            u = int(round((w - 1) * i / (n - 1)))
            v = int(round((h - 1) * j / (n - 1)))
            out.append([u, v, float(m1[v, u]), float(m2[v, u])])
    return out


def _bev_samples(case, und, quad):
    """BEV 픽셀 → 원본 픽셀. 좌표를 색으로 칠해 파이프라인에 통과시킨다."""
    w, h = case["size"]
    bw, bh = case["bev"]
    ramp = np.zeros((h, w, 3), dtype=np.float32)
    ramp[:, :, 0] = np.arange(w, dtype=np.float32)[None, :]           # R = x
    ramp[:, :, 1] = np.arange(h, dtype=np.float32)[:, None]           # G = y
    #  ★B 채널은 「여기가 원본에서 왔다」는 표시★ 램프의 x=0 열과 화면 밖에서
    #  온 0 을 구별할 길이 달리 없다 — 그 둘을 섞으면 원점 근처가 통째로 틀린다.
    ramp[:, :, 2] = 1.0
    bev = warp_bev(und(ramp), quad, bw, bh)
    out = []
    n = int(round(BEV_SAMPLES ** 0.5))
    for i in range(n):
        for j in range(n):
            #  가장자리는 보간이 바깥의 0 과 섞여 값이 무너진다 — 5% 안쪽만 본다
            bx = int(round(bw * (0.05 + 0.9 * i / (n - 1))))
            by = int(round(bh * (0.05 + 0.9 * j / (n - 1))))
            px = bev[min(by, bh - 1), min(bx, bw - 1)]
            if float(px[2]) < 0.999:      # 원본 밖에서 온 자리 — 대조에서 뺀다
                continue
            out.append([bx, by, float(px[0]), float(px[1])])
    return out


#  사각형 건전성 — ★파이썬과 JS 가 같은 답을 내야 한다★ 화면 밖·뒤집힌 사각형을
#  한쪽만 거절하면, 스튜디오는 통과시킨 값을 노드가 검은 띠로 받는다.
QUADS = [
    ("정상", [600, 650, 1300, 650, 1900, 1070, 20, 1070]),
    ("화면 밖", [600, 650, 1300, 650, 4000, 1070, 20, 1070]),
    ("꼬임", [600, 650, 1300, 650, 20, 1070, 1900, 1070]),
    ("위아래 뒤집힘", [600, 1070, 1300, 1070, 1900, 650, 20, 650]),
    ("한 직선", [600, 650, 601, 650, 602, 651, 599, 651]),
]


def bake():
    cases = []
    for c in CASES:
        und = Undistorter(c["size"], c["K"], c["D"], c["alpha"])
        quad = np.array(c["quad"], dtype=np.float32).reshape(4, 2)
        M, _ = ipm_matrices(quad, c["bev"][0], c["bev"][1])
        cases.append({
            "name": c["name"],
            "size": c["size"], "K": c["K"], "D": c["D"], "alpha": c["alpha"],
            "quad": c["quad"], "bev": c["bev"],
            "newK": [float(und.new_K[0, 0]), float(und.new_K[1, 1]),
                     float(und.new_K[0, 2]), float(und.new_K[1, 2])],
            "roi": [int(v) for v in und.roi],
            "H": [[float(v) for v in row] for row in M],
            "map": _map_samples(c["K"], c["D"], [float(und.new_K[0, 0]),
                                                 float(und.new_K[1, 1]),
                                                 float(und.new_K[0, 2]),
                                                 float(und.new_K[1, 2])], c["size"]),
            "bevToSrc": _bev_samples(c, und, quad),
        })
    quads = [{"name": n, "quad": q, "sane": bool(quad_is_sane(q, 1920, 1080)[0])}
             for n, q in QUADS]
    body = json.dumps({"cv2": cv2.__version__, "cases": cases, "quads": quads},
                      ensure_ascii=False, indent=1)
    out = ROOT / "web" / "reference.js"
    out.write_text(
        "/* cv2 가 낸 정답표 — ★손으로 고치지 않는다★\n"
        " * 다시 구우려면: python3 tools/bake_reference.py\n"
        " * 무엇에 쓰나: web/geom.js 가 cv2 와 같은 값을 내는지 대조한다\n"
        " *   · 페이지는 열 때 스스로 대조하고 어긋나면 띠를 띄운다\n"
        " *   · tb/selftest.py 의 t_geom_js 가 node 로 같은 대조를 한다\n"
        " * 카메라 값은 ★지어낸 것★ 이다 — 재는 것은 「같은 입력에 같은 답인가」다. */\n"
        "window.GEOM_REF = " + body + ";\n", encoding="utf-8")
    n = sum(len(c["map"]) + len(c["bevToSrc"]) for c in cases)
    print(f"구웠다: {out.relative_to(ROOT)}  판 {len(cases)}개 · 대조점 {n}개")


if __name__ == "__main__":
    bake()
