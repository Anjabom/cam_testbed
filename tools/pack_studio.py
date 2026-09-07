"""보정 스튜디오를 ★남의 기계에 건네는 꾸러미★ 로 굽는다.

    python3 tools/pack_studio.py                  # → dist/cam-studio-<날짜>.zip
    python3 tools/pack_studio.py --dir            # zip 없이 폴더만 (시험용)

★왜 저장소를 통째로 주지 않나★
`tb/run.py` 가 `rclpy` 를 import 한다 — ROS 가 없는 기계에서는 그 길이 아예
막힌다. 그리고 상대에게 필요한 것은 보정 화면뿐인데 시험 엔진·계약·런 기록까지
같이 가면 「무엇을 봐야 하는지」가 흐려진다.

그래서 꾸러미에는 ★스튜디오가 실제로 쓰는 것만★ 넣는다(140KB):

    cam-studio/
      studio.py        tb/studio.py 그대로 — 이 파일은 tb 를 import 하지 않는다
      web/             화면 (index.html · app.js · geom.js · render.js
                             tuning.js · reference.js · style.css)
      실행하기.sh / .bat
      읽어보기.txt

상대가 하는 일은 둘뿐이다: `pip install opencv-python` 한 번, 그리고 실행.

★기하 대조표(reference.js)도 같이 간다★ 그래야 상대의 브라우저에서도 화면이
스스로 「이 기하가 cv2 와 같은가」를 확인하고, 어긋나면 붉은 띠를 띄운다.
"""
from __future__ import annotations

import argparse
import shutil
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "cam-studio"

WEB_FILES = ["index.html", "app.js", "geom.js", "render.js",
             "tuning.js", "reference.js", "style.css"]

RUN_SH = """#!/usr/bin/env bash
#  카메라 보정 스튜디오 — 이 파일을 두 번 눌러 실행하거나, 터미널에서 bash 실행하기.sh
cd "$(dirname "$0")" || exit 1
command -v python3 >/dev/null || { echo "python3 이 없습니다. 먼저 설치하세요."; exit 1; }
python3 -c "import cv2" 2>/dev/null || {
  echo "opencv 를 설치합니다 (처음 한 번만)…"
  python3 -m pip install --user opencv-python || exit 1
}
exec python3 studio.py "$@"
"""

RUN_BAT = """@echo off
rem  카메라 보정 스튜디오 — 이 파일을 두 번 누르세요.
cd /d "%~dp0"
where python >nul 2>nul || (echo python 이 없습니다. python.org 에서 설치하세요. & pause & exit /b 1)
python -c "import cv2" 2>nul || (
  echo opencv 를 설치합니다 ^(처음 한 번만^)...
  python -m pip install --user opencv-python || (pause & exit /b 1)
)
python studio.py %*
pause
"""

README = """카메라 보정 스튜디오
════════════════════════════════════════════════════════════════════

 실행
   윈도우 : 실행하기.bat 을 두 번 누른다
   리눅스·맥 : bash 실행하기.sh
   (직접 하려면: pip install opencv-python  →  python3 studio.py)

 브라우저가 저절로 열린다. 안 열리면 http://127.0.0.1:8770 로 들어간다.

 무엇을 하는 도구인가
   카메라 영상을 열어 IPM 사각형·ROI·픽셀↔미터·BEV 기준선을 맞추고,
   그 값을 노드 파라미터(params.yaml)로 내보낸다.

 ★어떤 mp4 든 열린다★
   브라우저는 cv2 의 기본 코덱(mp4v)을 열지 못하지만, 이 도구는 파이썬이
   프레임을 디코드해 그림으로 넘기므로 코덱을 가리지 않는다.

 영상이 목록에 없으면
   기본으로 홈 폴더 아래만 훑는다. 다른 곳을 보려면:
       python3 studio.py --root /경로/하나 --root /경로/둘
   또는 화면의 폴더 목록에서 경로를 직접 입력한다.

 밖으로 나가는 것이 없다
   서버는 이 기계(127.0.0.1)에서만 열리고, 파일을 ★읽기만★ 한다.
   맞춘 값은 브라우저에 남고, «내보내기» 로 파일을 내려받는다.

 카메라가 다르면
   화면 오른쪽 «카메라 (크기 · K · D)» 를 펴서 값을 바꾼다.
   기본값은 이 도구를 만든 차량의 실측값이다.
"""


def build(dest: Path) -> Path:
    out = dest / NAME
    if out.exists():
        shutil.rmtree(out)
    (out / "web").mkdir(parents=True)

    shutil.copy2(ROOT / "tb" / "studio.py", out / "studio.py")
    for f in WEB_FILES:
        src = ROOT / "web" / f
        if not src.is_file():
            raise SystemExit(f"⛔ 화면 파일이 없다: {src}")
        shutil.copy2(src, out / "web" / f)

    (out / "실행하기.sh").write_text(RUN_SH, encoding="utf-8")
    (out / "실행하기.sh").chmod(0o755)
    (out / "실행하기.bat").write_text(RUN_BAT, encoding="utf-8")
    (out / "읽어보기.txt").write_text(README, encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 tools/pack_studio.py")
    ap.add_argument("--out", default="dist", help="어디에 만들까 (기본: dist/)")
    ap.add_argument("--dir", action="store_true", help="zip 없이 폴더만")
    a = ap.parse_args(argv)

    dest = Path(a.out).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    folder = build(dest)
    n = sum(1 for _ in folder.rglob("*") if _.is_file())
    kb = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1024
    print(f"폴더: {folder}  ({n}개 파일 · {kb:.0f}KB)")

    if a.dir:
        return 0
    zp = dest / f"{NAME}-{date.today():%Y%m%d}.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f"{NAME}/{f.relative_to(folder)}")
    print(f"묶음: {zp}  ({zp.stat().st_size / 1024:.0f}KB)")
    print("  건네받은 사람은: 압축을 풀고 → 실행하기.bat(윈도우) / 실행하기.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
