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

README = """카메라 보정 스튜디오 — 읽어보기
════════════════════════════════════════════════════════════════════════

0. 이게 뭔가
   카메라 영상을 열어 ★IPM 사각형·ROI·픽셀↔미터·BEV 기준선★ 을 눈으로 맞추고,
   그 값을 노드 파라미터(params.yaml)로 내보내는 도구다.
   맞춘 값이 맞는지는 오른쪽 BEV 화면이 말해 준다 — 차선이 수직이면 맞은 것이다.


1. 실행
────────────────────────────────────────────────────────────────────────
   윈도우      : 실행하기.bat 을 두 번 누른다
   리눅스·맥   : 터미널에서  bash 실행하기.sh
   직접 하려면 : pip install opencv-python   →   python3 studio.py

   브라우저가 저절로 열린다. 안 열리면 주소창에 http://127.0.0.1:8770

   ※ 처음 한 번은 opencv 를 받느라 1~2분 걸린다(약 60MB). 그 다음부터는 즉시.
   ※ 파이썬이 없으면 python.org 에서 받는다(윈도우는 설치 중 "Add to PATH" 체크).

   옵션
     python3 studio.py --root D:/영상 --root E:/촬영   영상을 찾을 폴더 지정
     python3 studio.py --port 9000                     포트 바꾸기
     python3 studio.py --no-browser                    브라우저 자동 열기 끄기


2. 화면
────────────────────────────────────────────────────────────────────────
   왼쪽 위  「원본」  왜곡보정을 마친 카메라 화면. 여기서 사각형과 ROI 를 끈다.
   왼쪽 아래「BEV」   위에서 내려다본 그림. 노드가 만드는 것과 같은 변환이다.
   오른쪽    맞출 항목 단추들 · 설명 · 현재 값 표

   맨 아래 오른쪽에 "기하 대조 0.028px" 이 ★초록★ 으로 떠 있으면 이 화면의 계산이
   OpenCV 와 같다는 뜻이다. 붉은 띠가 뜨면 그 화면으로 맞춘 값은 쓰지 않는다.


3. 맞추는 순서
────────────────────────────────────────────────────────────────────────
   ① 카메라 확인
      오른쪽 «카메라 (크기 · K · D)» 를 펼친다. 기본값은 이 도구를 만든 차량의
      실측값이다. ★다른 카메라면 반드시 자기 값으로 바꾼다★ — 안 바꾸면 화면이
      보여 주는 BEV 와 실제 노드가 만드는 BEV 가 달라진다.
      D 를 전부 0 으로 두면 "왜곡보정 안 함" 이다.

   ② 영상 열기
      «영상·사진 열기» → 폴더 목록에서 고른다. 목록에 없으면 맨 위 칸에 폴더
      경로를 직접 친다(Enter). 영상이든 사진 한 장이든 된다.
      아래 ◀ ▶ 로 한 프레임씩, 막대로 크게 이동한다.
      ★직선 구간·차선이 잘 보이는 프레임★ 을 고르는 것이 중요하다.

   ③ IPM 사각형   (제일 중요하다)
      «IPM 사각형» 을 누르고 네 점(TL·TR·BR·BL)을 끈다.
      ★좌우 변을 차선 위에 올린다.★ 지면은 평면이라 그렇게 놓으면 BEV 에서
      차선이 정확히 수직으로 선다. 수직이 아니면 사각형이 틀린 것이다.
      · 윗변은 멀리, 아랫변은 차 앞. 아랫점이 화면을 조금 벗어나도 괜찮다.
      · ★숫자를 직접 쳐도 된다★ 오른쪽에 TL/TR/BR/BL 의 x·y 칸이 나온다.
        끌면 숫자가 따라 움직이고, 숫자를 치면 그림이 따라 움직인다.
        (지난번 값을 그대로 다시 넣을 때는 이쪽이 빠르다)
      · 화살표키로 1px, Shift+화살표로 10px 씩 사각형 전체를 민다.

   ④ 픽셀↔미터
      «픽셀↔미터» 를 누르고 BEV 에서 ★길이를 아는 두 점★ 을 찍는다(차선 폭이
      제일 쉽다). 오른쪽 칸에 실제 길이(m)를 넣으면 값이 계산된다.
      이미 아는 값이 있으면 «픽셀↔미터 (직접)» 칸에 그 값을 바로 쳐도 된다.
      맞으면 BEV 왼쪽의 0.5m 격자가 실제 간격과 맞아떨어진다.

   ⑤ ROI (차선 / 신호등)
      해당 단추를 누르고 원본 화면에서 두 모서리를 끈다.
      여기도 «좌상 x·y / 우하 x·y» 칸에 숫자를 직접 칠 수 있다.
      하늘·차체를 빼면 추론이 빨라진다.

   ⑥ BEV 범퍼행 · 정지 문턱   (정지선 판단을 쓰는 경우만)
      «BEV 범퍼행» = 거리 0 의 기준선. 앞범퍼 바로 앞 노면이 BEV 의 몇 번째
      행인가. BEV 에서 그 높이를 누르면 선이 그리로 간다.
      «1단/2단 문턱» 은 그 기준선에서의 거리다 — 기준선을 옮기면 같이 따라온다.
      괄호 안에 미터로 환산돼 보인다(픽셀↔미터를 먼저 맞춰야 뜬다).


4. 내보내기 — 이 도구의 산출물
────────────────────────────────────────────────────────────────────────
   «내보내기» 를 누르면 세 가지가 나온다.

   · params.yaml   노드 파라미터. local.yaml 의 params: 아래에 그대로 붙이거나
                   --params-file 로 준다.
                   ★쓰지 않는 노드 블록은 지운다★ — 없는 파라미터를 주면 노드가
                   기동하지 않는다.
   · 설정 JSON     지금 화면 상태 한 벌(카메라 값 포함). 다음에 «불러오기» 로
                   그대로 이어서 작업한다. ★백업은 이걸로 한다.★
   · 복사          params.yaml 내용을 클립보드로.

   맞추는 중인 값은 그 브라우저에 저장되므로 새로고침해도 살아 있다.
   다만 브라우저를 바꾸거나 기록을 지우면 사라진다 — 중요한 값은 JSON 으로 받아 둔다.


5. 문제가 생기면
────────────────────────────────────────────────────────────────────────
   영상 목록에 아무것도 없다
       기본으로 홈(사용자) 폴더 아래만 훑는다.
       → python3 studio.py --root <영상이 있는 폴더>
       또는 목록 맨 위 칸에 폴더 경로를 직접 입력한다.

   "이 영상을 브라우저가 열지 못합니다" 가 뜬다
       파일 선택으로 연 경우다(서버를 통하지 않으면 브라우저가 직접 디코드한다).
       «영상·사진 열기» 의 폴더 목록으로 열면 코덱과 상관없이 열린다.

   그림이 안 나오고 값만 보인다
       그 브라우저에서 WebGL 을 못 쓰는 것이다. 크롬·엣지·파이어폭스 최신판으로
       열거나, 하드웨어 가속을 켠다.

   붉은 띠 "기하가 cv2 와 어긋납니다"
       화면의 계산이 기준과 다르다는 뜻이다. ★그 상태로 맞춘 값은 쓰지 않는다.★
       도구를 준 사람에게 알린다.

   포트가 쓰이는 중이라고 나온다
       알아서 다음 포트(8771…)로 뜬다. 주소창의 포트를 확인한다.


6. 밖으로 나가는 것이 없다
────────────────────────────────────────────────────────────────────────
   · 이 도구는 자기 컴퓨터(127.0.0.1)에서만 열린다 — 다른 사람은 접속할 수 없다.
   · 영상은 ★읽기만★ 한다. 파일을 고치거나 지우거나 어디로 보내지 않는다.
   · 인터넷이 없어도 된다(opencv 를 처음 설치할 때만 필요하다).
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
