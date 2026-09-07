"""보정 스튜디오 서버 — ★화면은 web/ 를 그대로 쓴다★

★왜 서버가 다시 생겼나★ [2026-09-07]
브라우저는 `mp4v`(MPEG-4 Part 2)를 열지 못한다 — mp4 안에서 받아 주는 것은
H.264·AV1 뿐이다. 그런데 이 기계의 녹화(cam_record)가 전부 그 코덱이라,
정적 페이지만으로는 「가진 영상을 하나도 못 여는」 도구가 됐다.
실측(2026-09-07): http 로 그대로 내려 줘도 `<video>` 는 `error code 4` 다.
★파일을 어디서 주느냐의 문제가 아니라 브라우저 디코더의 한계다.★

서버는 그 벽을 우회한다 — cv2 로 디코드해 ★프레임 한 장★ 을 그림으로 넘긴다.
브라우저가 영상을 만지지 않으므로 코덱이 무엇이든 상관없어진다.
실측(2.2GB mp4v): 임의 탐색 13ms + 디코드 3ms + JPEG 5ms = 한 장 21ms,
이어지는 프레임은 1ms.

★그림은 여전히 브라우저가 그린다★
예전 서버는 BEV 까지 그려 합성 JPEG 을 내려보냈다(조작마다 71ms 왕복).
지금은 ★원본 프레임만★ 준다 — 왜곡보정·BEV·오버레이는 `web/geom.js` 와
`web/render.js` 가 GPU 에서 한다. 그래서 드래그가 즉시 반응하고, 「화면의 기하가
cv2 와 같다」는 증명(`tb/selftest.py` 의 `t_geom_js`)도 그대로 유효하다.

★이 서버가 하지 않는 것★
 · 쓰기가 없다 — 어떤 요청도 파일을 만들거나 고치지 않는다(내보내기는 브라우저가
   내려받기로 한다). 그래서 이 서버로 망가뜨릴 수 있는 것이 없다.
 · 실행이 없다 — 예전의 `COMMANDS` 표처럼 임의 명령을 감싸는 통로를 두지 않는다.
 · 업로드가 없다 — 파일은 이미 이 기계에 있다.
 · 인증이 없다 — 127.0.0.1 에만 바인딩한다. 밖에서 들어올 길이 없기 때문이다.
   ★다른 기기에서 열려고 host 를 바꾸는 순간 이 전제가 깨진다★ — 그때는
   인증을 먼저 만들어야 한다.

    python3 -m tb.run studio            # 저장소 안에서 (local.yaml 의 browse_roots)
    python3 studio.py                   # ★혼자서도 돈다★ — 남의 기계에 건네는 꾸러미

★테스트베드가 없어도 돈다★ [2026-09-07]
이 파일은 `tb/` 의 다른 모듈을 하나도 import 하지 않는다. 필요한 것은 cv2 와
표준 라이브러리뿐이라, `web/` 폴더와 이 파일만 있으면(140KB) 남의 기계에서
`pip install opencv-python` 한 번으로 돈다. 시험 도구(`tb.run`)는 `rclpy` 를
import 하므로 ROS 가 없는 기계에서는 뜨지 않는다 — 그래서 스튜디오는 그 길을
지나지 않는다.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import cv2

#  ★화면 파일이 어디 있나★ 두 가지 배치를 모두 받는다:
#    저장소 안 :  tb/studio.py  →  ../web/
#    꾸러미     :  studio.py     →  ./web/
_here = Path(__file__).resolve().parent
WEB = _here / "web" if (_here / "web" / "index.html").is_file() else _here.parent / "web"

#  ★훑어도 되는 뿌리★ 부르는 쪽이 정한다 — 이 파일은 local.yaml 을 모른다.
#  저장소 안에서는 tb.run 이 local.yaml 의 browse_roots 를 넘겨 주고,
#  혼자 돌 때는 홈 하나(또는 --root)다. 없는 폴더는 조용히 버린다 — USB 를
#  뽑아 두면 목록에서 사라질 뿐 스튜디오가 안 뜨면 곤란하다.
_ROOTS = [Path.home()]


def set_roots(paths):
    global _ROOTS
    out = []
    for r in (paths or []):
        try:
            q = Path(str(r)).expanduser().resolve()
        except OSError:
            continue
        if q.is_dir() and q not in out:
            out.append(q)
    _ROOTS = out or [Path.home()]
    return _ROOTS


#  화면이 쓰는 파일만 내보낸다. 폴더를 통째로 열어 주면 저장소의 다른 파일까지
#  (local.yaml 을 포함해) 주소만 알면 읽히게 된다.
STATIC = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "geom.js": "application/javascript; charset=utf-8",
    "render.js": "application/javascript; charset=utf-8",
    "tuning.js": "application/javascript; charset=utf-8",
    "reference.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}

MEDIA_EXT = {".mp4": "video", ".avi": "video", ".mkv": "video", ".mov": "video",
             ".m4v": "video", ".webm": "video",
             ".png": "image", ".jpg": "image", ".jpeg": "image", ".bmp": "image"}

JPEG_Q = 85

#  ★열어 둔 VideoCapture 를 재사용한다★ 파일을 다시 열면 2.2GB 짜리에서
#  매번 수십 ms 가 더 든다. 프레임을 이어서 읽을 때는 탐색조차 건너뛴다(1ms).
_caps: dict = {}
_caps_lock = threading.Lock()
_CAP_MAX = 3


def media_kind(name) -> str:
    return MEDIA_EXT.get(Path(name).suffix.lower(), "")


# ══════════════════════════════════════════════════════════════════════
#  경로 — ★훑는 길과 여는 길이 같은 규칙 하나를 지난다★
# ══════════════════════════════════════════════════════════════════════
#  예전 웹앱에서 배운 것: 훑기만 막고 열기를 안 막으면 뿌리는 화면의 장식일
#  뿐이다(경로만 알면 프레임 요청으로 바로 열린다). 그래서 두 길이 이 함수를
#  같이 지난다.
def roots():
    return list(_ROOTS)


def in_roots(p, rs=None) -> bool:
    try:
        p = Path(p).expanduser().resolve()
    except OSError:
        return False
    for r in (rs if rs is not None else roots()):
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def check_allowed(path):
    if in_roots(path):
        return
    raise ValueError(f"열 수 있는 폴더 밖입니다: {path}\n"
                     "  local.yaml 의 browse_roots: 에 그 폴더를 넣으면 열립니다.")


def browse(d=""):
    """폴더 하나 — 하위 폴더와 ★열 수 있는 파일★ 만.

    뿌리 밖을 가리키면 조용히 첫 뿌리로 되돌린다. 「없는 폴더」와 「막힌 폴더」를
    구분해 알려 주면 그 대답 자체가 밖에 무엇이 있는지 흘리는 정보가 된다.
    """
    rs = roots()
    base = Path(d).expanduser() if d else rs[0]
    if not base.is_dir() or not in_roots(base, rs):
        base = rs[0]
    base = base.resolve()
    #  뿌리보다 위로는 못 올라간다 — 「상위 폴더」가 탈출구가 되지 않게.
    up = str(base.parent) if in_roots(base.parent, rs) else str(base)
    dirs, files = [], []
    try:
        for e in sorted(base.iterdir(), key=lambda x: x.name.lower()):
            if e.name.startswith("."):
                continue
            try:
                if e.is_dir():
                    dirs.append({"name": e.name, "path": str(e)})
                elif media_kind(e.name):
                    files.append({"name": e.name, "path": str(e),
                                  "kind": media_kind(e.name),
                                  "mb": round(e.stat().st_size / 1e6, 1)})
            except OSError:
                continue
    except PermissionError:
        return {"dir": str(base), "up": up, "roots": [str(r) for r in rs],
                "error": "열 수 없는 폴더입니다", "dirs": [], "files": []}
    return {"dir": str(base), "up": up, "roots": [str(r) for r in rs],
            "dirs": dirs, "files": files}


# ══════════════════════════════════════════════════════════════════════
#  프레임
# ══════════════════════════════════════════════════════════════════════
def _cap(path):
    """열려 있는 VideoCapture 를 준다(없으면 연다). ★_caps_lock 안에서만.★"""
    ent = _caps.get(path)
    if ent is None:
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise ValueError(f"영상을 열 수 없습니다: {Path(path).name}")
        ent = {"cap": cap, "pos": -1}
        #  오래된 것부터 닫는다 — 파일을 여러 개 열어 두면 메모리와 fd 가 는다
        while len(_caps) >= _CAP_MAX:
            k = next(iter(_caps))
            try:
                _caps.pop(k)["cap"].release()
            except (KeyError, AttributeError):
                break
        _caps[path] = ent
    return ent


def info(path):
    check_allowed(path)
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"파일이 없습니다: {p}")
    kind = media_kind(p.name)
    if kind == "image":
        img = cv2.imread(str(p))
        if img is None:
            raise ValueError(f"사진을 열 수 없습니다: {p.name}")
        return {"kind": "image", "w": img.shape[1], "h": img.shape[0],
                "frames": 1, "fps": 0.0, "name": p.name}
    with _caps_lock:
        cap = _cap(str(p))["cap"]
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    return {"kind": "video", "w": w, "h": h, "frames": max(0, n),
            "fps": round(fps, 3), "name": p.name}


def frame_jpeg(path, i=0) -> bytes:
    """그 프레임 한 장을 JPEG 으로. ★원본 그대로 — 아무것도 그리지 않는다.★"""
    check_allowed(path)
    p = Path(path)
    if media_kind(p.name) == "image":
        img = cv2.imread(str(p))
        if img is None:
            raise ValueError(f"사진을 열 수 없습니다: {p.name}")
    else:
        with _caps_lock:
            ent = _cap(str(p))
            cap = ent["cap"]
            i = max(0, int(i))
            #  ★이어지는 프레임이면 탐색하지 않는다★ 탐색 13ms vs 그냥 읽기 1ms.
            #  프레임 이동 버튼을 연타할 때 이 차이가 그대로 반응 속도가 된다.
            if i != ent["pos"] + 1:
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ok, img = cap.read()
            ent["pos"] = i if ok else -1
        if not ok or img is None:
            raise ValueError(f"{i}번 프레임을 읽지 못했습니다")
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
    if not ok:
        raise ValueError("JPEG 인코딩 실패")
    return buf.tobytes()


# ══════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    server_version = "cam-studio"

    def log_message(self, fmt, *args):          # 요청마다 한 줄씩 찍지 않는다
        pass

    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        #  화면 코드를 고쳐 새로고침했는데 예전 것이 나오면 원인을 못 찾는다
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _err(self, code, msg):
        self._json({"error": str(msg)}, code)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        one = lambda k, d="": q.get(k, [d])[0]          # noqa: E731
        try:
            if u.path.startswith("/api/"):
                return self._api(u.path[5:], one)
            name = unquote(u.path).lstrip("/") or "index.html"
            if name in STATIC:
                f = WEB / name
                if not f.is_file():
                    return self._err(404, f"{name} 이 없습니다")
                return self._send(200, f.read_bytes(), STATIC[name])
            return self._err(404, "없는 주소입니다")
        except BrokenPipeError:
            return None
        except ValueError as e:
            return self._err(400, e)
        except Exception as e:                          # noqa: BLE001
            return self._err(500, f"{type(e).__name__}: {e}")

    def _api(self, route, one):
        if route == "ping":
            #  화면이 「서버가 있나」를 이걸로 판단한다
            return self._json({"studio": 1, "roots": [str(r) for r in roots()]})
        if route == "browse":
            return self._json(browse(one("dir")))
        if route == "info":
            return self._json(info(one("path")))
        if route == "frame":
            return self._send(200, frame_jpeg(one("path"), int(one("i", "0") or 0)),
                              "image/jpeg")
        return self._err(404, "없는 주소입니다")

    #  ★POST 가 없다★ 이 서버는 아무것도 쓰지 않는다. 내보내기는 브라우저가
    #  파일로 내려받는다 — 서버에 쓰기 통로를 두지 않는 것이 제일 싼 방어다.


#  ★인자 이름을 roots 로 두지 않는다★ 그러면 아래 roots() 함수를 가려 버려
#  "list object is not callable" 로 죽는다 — 저장소 안에서는 tb.run 이 값을
#  넘겨 줘서 안 드러났고, 꾸러미를 남의 기계처럼 돌려 보고서야 나왔다.
def serve(host="127.0.0.1", port=8770, root_dirs=None, open_browser=False):
    if root_dirs:
        set_roots(root_dirs)
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("거부: 이 서버는 인증이 없다 — 127.0.0.1 밖으로 열지 않는다.\n"
              "  다른 기기에서 쓰려면 인증을 먼저 만들어야 한다(README §4).")
        return 2
    if not (WEB / "index.html").is_file():
        print(f"⛔ 화면 파일이 없다: {WEB}/index.html")
        return 2
    #  ★포트가 물려 있으면 다음 것으로★ 「이미 쓰는 중」이라고 죽으면 사람은
    #  무엇을 어떻게 하라는 건지 모른다. 열 수 있는 자리를 찾아 알려 준다.
    srv = None
    for p in range(int(port), int(port) + 20):
        try:
            srv = ThreadingHTTPServer((host, p), Handler)
            port = p
            break
        except OSError:
            continue
    if srv is None:
        print(f"⛔ {port}~{port + 19} 사이에 빈 포트가 없다")
        return 2
    url = f"http://{host}:{port}"
    print("카메라 보정 스튜디오")
    print(f"  → {url}")
    print(f"  훑는 곳: {', '.join(str(r) for r in roots())}"
          "   (local.yaml 의 browse_roots: 로 넓힌다)")
    print("  ★이 기계의 영상을 코덱 상관없이 연다★ — 서버가 프레임만 넘긴다")
    print("  Ctrl-C 로 종료")
    if open_browser:
        #  ★열어 놓고 기다리지 않는다★ 브라우저가 없는 기계(서버·원격 셸)도 있다.
        import webbrowser                                 # noqa: PLC0415
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    finally:
        srv.server_close()
        with _caps_lock:
            for ent in _caps.values():
                try:
                    ent["cap"].release()
                except AttributeError:
                    pass
            _caps.clear()
    return 0


def main(argv=None):
    """혼자 돌 때의 진입점 — 꾸러미의 `python3 studio.py` 가 여기로 온다."""
    import argparse                                       # noqa: PLC0415
    ap = argparse.ArgumentParser(
        prog="studio.py", description="카메라 보정 스튜디오 (이 기계의 브라우저)")
    ap.add_argument("--root", action="append", default=[],
                    help="영상을 찾을 폴더 (여러 번 줄 수 있다. 기본: 홈)")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true",
                    help="브라우저를 자동으로 열지 않는다")
    a = ap.parse_args(argv)
    return serve(port=a.port, root_dirs=a.root or None,
                 open_browser=not a.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
