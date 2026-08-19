"""테스트베드를 ★별도의 창★으로 띄운다 — 여전히 표준 라이브러리만 쓴다.

★왜 창인가★
주소를 외워 브라우저에 치고, 열어 둔 탭 스무 개 사이에서 찾는 것은 도구가 아니다.
크롬의 앱 모드(`--app=`)는 주소창·탭·북마크가 없는 전용 창을 띄운다. 전용 프로필을
같이 주면 작업표시줄에 별도 항목으로 잡히고 창 크기·줌을 자기가 기억한다.

★왜 Electron 도 PyQt 도 아닌가★
UI 는 이미 `web/` 에 다 있다. 필요한 것은 그 화면을 담을 창 하나뿐이고,
그건 이미 깔려 있는 브라우저가 프레임워크 없이 해 준다. pip 설치가 0 이라는
이 저장소의 약속을 창 하나 때문에 깨지 않는다.

★UI 코드는 한 줄도 다르지 않다★
같은 서버, 같은 `app.js` 다. 렌더링 엔진도 브라우저로 볼 때와 같으므로
영상 재생(H.264)·플롯이 지금 검증된 그대로 동작한다.

    python3 -m tb.run app              # 별도 창
    python3 -m tb.run app --page exec  # 바로 '테스트 실행' 화면으로
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import server as srv_mod          # 같은 폴더. tb.run 이 sys.path 에 web/ 을 넣는다

# 앱 모드를 지원하는 크로미움 계열을 순서대로 찾는다. 첫 번째로 걸리는 것을 쓴다.
_BROWSERS = ("google-chrome-stable", "google-chrome", "chromium",
             "chromium-browser", "microsoft-edge", "brave-browser")

_PAGE_OK = re.compile(r"^[A-Za-z0-9/_-]+$")     # URL 조립에 들어가는 값이라 좁게 막는다


def _profile_dir():
    """크롬 프로필 자리.

    `runs/` 안에 두지 않는다 — 수십 MB 짜리 브라우저 프로필은 실행 결과가 아니고,
    거기 섞이면 「실행 기록」의 의미도 `du -sh runs/` 도 흐려진다.
    """
    cache = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(cache) / "cam-testbed" / "window"


def find_browser():
    for name in _BROWSERS:
        p = shutil.which(name)
        if p:
            return p
    return None


def _port_busy(host, port):
    """그 포트에서 이미 서버가 돌고 있는가. 돌고 있으면 창만 붙인다."""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def launch(host="127.0.0.1", port=8770, page="", size="1600,1000"):
    browser = find_browser()
    if not browser:
        print("[app] 크로미움 계열 브라우저를 찾지 못했다 "
              f"({', '.join(_BROWSERS)}).", flush=True)
        print("[app] 별도 창 대신 기본 브라우저로 연다.", flush=True)
        return srv_mod.serve(host, port, True)

    url = f"http://{host}:{port}"
    if page and _PAGE_OK.match(page):
        url += "/#/" + page.lstrip("/")

    srv = None
    if _port_busy(host, port):
        print(f"[app] {url} 에 서버가 이미 떠 있다 — 창만 붙인다.", flush=True)
    else:
        srv = ThreadingHTTPServer((host, port), srv_mod.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"테스트베드 → {url}")
        print(f"  실행 {len(srv_mod.list_runs())}개 · "
              f"베이스라인 {len(srv_mod.list_baselines())}개", flush=True)

    prof = _profile_dir()
    first = not prof.exists()
    prof.mkdir(parents=True, exist_ok=True)

    argv = [browser, f"--app={url}", f"--user-data-dir={prof}",
            "--class=cam-testbed", "--no-first-run",
            "--no-default-browser-check"]
    if first:
        argv.append(f"--window-size={size}")     # 그 뒤로는 프로필이 기억한다

    print("  창을 닫으면 종료된다", flush=True)
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n종료")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    finally:
        if srv:
            srv.shutdown()
            srv.server_close()
    return 0


if __name__ == "__main__":
    launch()
