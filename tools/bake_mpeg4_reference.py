"""mp4v 해독 정답표 굽기 — cv2 가 읽은 그림을 `tools/fixtures/` 에 박는다.

★왜 필요한가★ [2026-10-06]
스튜디오가 github.io 로 다시 나가면서 mp4v 해독이 브라우저(wasm)에 한 벌 생겼다
(`web/mp4frames.js` + `web/vendor/mpeg4dec.js`). 서버 모드는 cv2 로 읽으므로,
두 길이 ★같은 프레임 번호에 같은 그림★ 을 내야 한다 — 어긋나면 「프레임 300 에서
맞췄다」는 말이 길마다 다른 장면을 가리킨다. 그래서 cv2 를 정답으로 두고 대조한다.

    python3 tools/bake_mpeg4_reference.py   # 다시 굽는다 (cv2 · ffmpeg 필요)
    python3 -m tb.selftest                  # t_mpeg4_js 가 그것으로 JS 를 검사한다

★그림마다 다른 무늬를 넣는다★ 재려는 것은 「색이 정확한가」보다 「n 번째를
달라고 했을 때 n 번째가 오는가」다. 프레임마다 막대 위치와 바탕색을 바꿔 두면
한 장만 어긋나도 차이가 수십 단계로 벌어진다. 색 변환(BT.601)·색차 보간의 차이는
1~3 단계라 그 둘은 문턱 하나로 갈린다.

★클립은 둘이다★
  · cv2 가 직접 쓴 mp4v  — 이 기계의 녹화와 같은 길(GOP 12, B 프레임 없음)
  · ffmpeg 로 B 프레임을 넣은 mp4v — 해독 순서 ≠ 표시 순서(ctts)인 경우.
    녹화에는 없지만 남이 건넨 영상에는 있을 수 있다. ffmpeg 가 없으면 건너뛴다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tools" / "fixtures"
W, H, N = 176, 144, 40
SIG = (11, 9)          # 대조용으로 줄인 크기 — 16×16 칸의 평균


def pattern(i: int) -> np.ndarray:
    """프레임 i 의 그림 — i 마다 막대 위치·바탕색이 다르다(BGR)."""
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = ((i * 37) % 200 + 30, (i * 71) % 200 + 30, (i * 13) % 200 + 30)
    x = (i * 9) % (W - 24)
    img[:, x:x + 24] = (240, 240, 240)
    y = (i * 7) % (H - 16)
    img[y:y + 16, :] = (20, 20, 20)
    return img


def signature(bgr: np.ndarray) -> list:
    """RGB 로 바꿔 칸 평균 — JS 쪽(tools/mpeg4_check.js)과 같은 줄이기"""
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float64)
    sw, sh = SIG
    cw, ch = W // sw, H // sh
    out = rgb[:sh * ch, :sw * cw].reshape(sh, ch, sw, cw, 3).mean(axis=(1, 3))
    return [round(float(v), 2) for v in out.ravel()]


def read_all(path: Path) -> list:
    cap = cv2.VideoCapture(str(path))
    sigs = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        sigs.append(signature(f))
    cap.release()
    return sigs


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    clips = {}

    p = OUT / "mp4v_cv2.mp4"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), 30, (W, H))
    for i in range(N):
        w.write(pattern(i))
    w.release()
    clips[p.name] = read_all(p)

    ff = shutil.which("ffmpeg")
    if ff:
        src = OUT / "_src.mp4"
        w = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"mp4v"), 30, (W, H))
        for i in range(N):
            w.write(pattern(i))
        w.release()
        p = OUT / "mp4v_bframes.mp4"
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(src), "-an",
                        "-c:v", "mpeg4", "-bf", "2", "-g", "10", "-q:v", "3",
                        "-tag:v", "mp4v", str(p)], check=True)
        src.unlink()
        clips[p.name] = read_all(p)
    else:
        print("ffmpeg 가 없어 B 프레임 클립은 건너뛴다", file=sys.stderr)

    for name, sigs in clips.items():
        if len(sigs) != N:
            print(f"{name}: cv2 가 {len(sigs)}장만 읽었다 (기대 {N})", file=sys.stderr)
            return 1

    ref = {"cv2": cv2.__version__, "size": [W, H], "sig": list(SIG), "clips": clips}
    (OUT / "mp4v_ref.json").write_text(json.dumps(ref, separators=(",", ":")) + "\n")
    for f in sorted(OUT.iterdir()):
        print(f"{f.name}: {f.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
