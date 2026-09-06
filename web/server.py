"""카메라 보정 스튜디오 — 표준 라이브러리만 쓴다(외부 의존성 0).

★왜 표준 라이브러리인가★
대회 현장에서 pip 이 막히거나 가상환경이 꼬여도 그냥 돈다. 필요한 건 GET 몇 개와
정적 파일, 그리고 이미지 한 장이라 프레임워크가 할 일이 없다.

★이 서버가 하는 일은 하나다★ [2026-09-04]
영상이나 이미지 한 장을 받아, ★대상 노드가 하는 것과 같은 변환★(`tb.geometry`)으로
BEV 를 만들어 돌려준다. 사람이 화면에서 사각형을 끌면 그 값으로 다시 그린다.
알고리즘 시험(실행·비교·기록)은 여기 없다 — 그건 CLI 와 클로드 스킬이 한다.
예전에는 이 파일이 `tb.run` 서브커맨드를 폼으로 감싸 subprocess 로 띄우는
작업 실행기이기도 했다(COMMANDS 표). 그 화면이 통째로 사라졌으므로 임의 명령을
실행하는 통로도 같이 없앴다 — 지금 서버가 띄우는 외부 프로세스는 ★둘뿐★ 이고
(워크스페이스 파라미터 읽기·BEV 대조) 인자를 사용자가 정하지 못한다.

★쓰는 자리는 이 기계가 아니다★ [2026-09-06]
이 기계는 영상·GPU·워크스페이스가 있는 쪽이고, 사람은 그 앞에 앉아 있지 않다.
그래서 스튜디오는 ★같은 공유기의 다른 기기★ 브라우저로 들어와 쓴다. 화면이 원격이라
달라지는 것은 없다 — 기하는 전부 여기서 계산해 PNG 로 보내기 때문이다(경계 3).
달라지는 것은 둘뿐이고 그 둘이 아래에 있다: ★인증★(`TB_WEB_TOKEN`)과
★파일을 넣는 길★(`/api/upload` — 다른 기기에 있는 영상은 경로로 부를 수 없다).

    TB_WEB_TOKEN=… python3 -m tb.run web --host 0.0.0.0     # 다른 기기에서 접속
    python3 -m tb.run web                                   # 127.0.0.1 (손으로 확인할 때만)

평소에는 사람이 이 명령을 치지 않는다 — `deploy/install.sh` 가 systemd 에 맡긴다.
"""
from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent      # testbed/
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT))

#  ★프로필이 있는 곳★ 계약(워크스페이스에 붙은 것)과 독립 프로필 둘 다 본다.
#  파일 안에 `calibration:` 블록이 있으면 프로필이다 — 그 판단만 하고,
#  무엇을 맞추는지(대상·파라미터 이름)는 전부 그 파일이 말한다.
PROFILE_DIRS = ("contracts", "calib")

_SAFE = re.compile(r"^[A-Za-z0-9._@-]+$")

#  ★터널로 노출하면 이 토큰이 유일한 방어선이다★
WEB_TOKEN = os.environ.get("TB_WEB_TOKEN", "")

#  OpenCV 의 VideoCapture 는 스레드 안전하지 않다(ThreadingHTTPServer 다)
_CAP_LOCK = threading.Lock()


def check_basic_auth(header, token):
    """Authorization: Basic 헤더가 토큰과 맞는가. token 이 비면 항상 통과(로컬).

    비번만 본다(사용자명 아무거나) — 브라우저 기본 로그인 창을 그대로 쓰려는 것.
    hmac.compare_digest 로 비교해 타이밍 누출을 막는다.
    """
    if not token:
        return True
    if not header or not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header[6:]).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(raw.partition(":")[2], token)


# ══════════════════════════════════════════════════════════════════════
#  프로필 — 「이 카메라를 어떻게 펴고, 무엇을 맞추는가」
# ══════════════════════════════════════════════════════════════════════
def _profile_files():
    for d in PROFILE_DIRS:
        for f in sorted((ROOT / d).glob("*.yaml")):
            yield f


def profile_id(path):
    """`contracts/black_vote.yaml` 처럼 저장소 기준 상대경로가 곧 id 다."""
    return str(Path(path).resolve().relative_to(ROOT))


def profile_path(pid):
    """id → 실제 파일. ★디렉터리 탈출을 막는다★ (id 는 화면에서 온다)."""
    pid = unquote(str(pid or "")).strip()
    if not pid:
        return None
    p = (ROOT / pid).resolve()
    if p.parent.parent != ROOT or p.parent.name not in PROFILE_DIRS:
        raise ValueError(f"프로필 위치가 잘못됐습니다: {pid}")
    if p.suffix != ".yaml" or not p.is_file():
        raise ValueError(f"그런 프로필이 없습니다: {pid}")
    return p


def list_profiles():
    """보정할 수 있는 프로필 전부. `calibration:` 이 없는 계약은 빠진다."""
    from tb.contract import load as load_contract          # noqa: PLC0415
    out = []
    for f in _profile_files():
        try:
            c = load_contract(f)
        except Exception as e:                             # noqa: BLE001
            out.append({"id": profile_id(f), "name": f.stem, "error": str(e)})
            continue
        cal = c.raw.get("calibration") or {}
        if not cal:
            continue          # 보정 대상이 아닌 계약 — 목록에 넣으면 눌러도 안 열린다
        u = cal.get("undistort") or {}
        out.append({
            "id": profile_id(f), "name": c.name, "file": f.name,
            "kind": f.parent.name,
            "workspace": str(c.workspace or ""),
            "targets": len(cal.get("targets") or {}),
            "size": [int(x) for x in (u.get("size") or [0, 0])],
            "has_k": bool(u.get("K") or u.get("file")),
            "verify": bool(cal.get("verify")),
        })
    return out


CALIB_TMPL = """\
# ══════════════════════════════════════════════════════════════════
#  보정 프로필 — 워크스페이스 없이 ★카메라만★ 맞출 때 쓰는 파일
# ══════════════════════════════════════════════════════════════════
#  계약(contracts/*.yaml)의 `calibration:` 블록과 같은 형식이다. 값을 다 맞춘 뒤
#  스튜디오의 «내보내기» 로 노드 파라미터 형태로 뽑아 쓴다.
#
#  ⚠️ K/D 는 ★이 카메라의 실제 값★ 이어야 한다. 아래는 화각 60° 를 가정한
#     자리표시자다 — 체스보드 사진이 있으면 스튜디오의 «체스보드 보정» 이
#     실측해서 이 자리를 채워 준다.
version: 1
name: {name}

calibration:
  undistort:
    size:  [{w}, {h}]
    K:     [{fx}, {fy}, {cx}, {cy}]
    D:     [0.0, 0.0, 0.0, 0.0, 0.0]
    alpha: 0.0
  bev: {{w: 640, h: 1000}}

  targets:
    ipm_src:
      kind: quad
      nodes: []
      param: ipm_src_pts
      hint: "좌우 변을 ★차선 위에★ 올려라. 그러면 BEV 에서 차선이 수직이 된다."
    px2m:
      kind: scale
      nodes: []
      param: pixel_to_meter_bev
      hint: "BEV 에서 실측 길이를 아는 두 점을 찍어라(차선폭이 제일 쉽다)."
"""


def new_profile(name, width, height):
    """빈 프로필 하나. 초점거리는 화각 60° 가정의 ★자리표시자★ 다."""
    from tb.config import NAME_RE                          # noqa: PLC0415
    if not NAME_RE.match(str(name)):
        raise ValueError("이름은 영문·숫자·_·-·. 만 쓸 수 있습니다")
    w, h = int(width), int(height)
    if not (16 <= w <= 20000 and 16 <= h <= 20000):
        raise ValueError("해상도가 이상합니다")
    d = ROOT / "calib"
    d.mkdir(exist_ok=True)
    f = d / f"{name}.yaml"
    if f.exists():
        raise ValueError(f"이미 있습니다: calib/{name}.yaml")
    #  f ≈ (w/2) / tan(30°) — 초점거리를 0 으로 두면 undistorter 가 죽는다.
    fx = round(w / 2 / 0.5774, 2)
    f.write_text(CALIB_TMPL.format(name=name, w=w, h=h, fx=fx, fy=fx,
                                   cx=round(w / 2, 1), cy=round(h / 2, 1)))
    _CALIB_ENV["stamp"] = None
    return {"id": profile_id(f), "path": f"calib/{name}.yaml"}


# ══════════════════════════════════════════════════════════════════════
#  소스 고르기 — ★등록하지 않는다★
# ══════════════════════════════════════════════════════════════════════
#  예전에는 영상을 쓰려면 local.yaml 에 논리 이름으로 등록하고 그 이름을
#  시나리오에 적어야 했다. 지금은 파일을 직접 고른다.
#
#  ★길이 둘인 이유★ [2026-09-06]
#  · 훑기(browse) — 이 기계에 이미 있는 영상. 복사되지 않고 경로만 쓴다.
#    실차에서 방금 딴 영상은 대부분 여기 있으므로 이쪽이 기본이다.
#  · 올리기(upload) — 다른 기기에 있는 영상. 화면이 원격이 되면서 생긴 길이다.
#    경로로는 부를 수 없으니 바이트를 받아 이 기계에 놓는 수밖에 없다.
VIDEO_EXT = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def media_kind(path):
    s = Path(path).suffix.lower()
    return "video" if s in VIDEO_EXT else ("image" if s in IMAGE_EXT else "")


def _roots():
    """훑어도 되는 뿌리들. 못 읽으면 홈 하나 — 지금까지와 같다."""
    try:
        import tb.config as cfg                            # noqa: PLC0415
        return cfg.browse_roots()
    except Exception:                                      # noqa: BLE001
        return [Path.home().resolve()]


def in_roots(path, roots):
    """이 경로가 뿌리들 ★안★ 인가.

    ★왜 이게 생겼나★ [2026-09-06]
    예전에는 홈 아래를 자유롭게 훑었다. 그때는 이 화면을 보는 사람이 그 기계
    앞에 앉아 있었으므로 목록에 새로 보이는 것이 없었다. 지금은 다른 기기에서
    들어온다 — ★폴더 목록 자체가 밖으로 나가는 정보★ 다.

    `resolve()` 한 뒤에 본다. 심볼릭 링크와 `..` 은 문자열로는 안 잡힌다.
    """
    try:
        p = Path(path).expanduser().resolve()
    except OSError:
        return False
    return any(p == r or r in p.parents for r in roots)


#  ★올린 파일이 가는 곳은 한 군데뿐이다★
#  경로를 요청이 정하게 하면 그것이 곧 「아무 데나 쓰기」다. 이름만 받고
#  자리는 서버가 정한다 — 그래서 여기 상수 하나가 업로드의 전체 권한이다.
UPLOAD_DIR = Path(os.environ.get("TB_UPLOAD_DIR")
                  or Path.home() / "cam_testbed_uploads").expanduser()
#  기본 8GiB. 블랙박스 영상 한 편이 몇 GB 인 일이 흔해 넉넉히 잡는다.
UPLOAD_MAX = int(os.environ.get("TB_UPLOAD_MAX") or 8 * 1024 ** 3)
#  다 받고 나서도 이만큼은 남아 있어야 한다. ★실차가 곧 이 기계★ 라
#  디스크를 꽉 채우면 다음 주행의 녹화가 죽는다.
UPLOAD_KEEP_FREE = 2 * 1024 ** 3
_UP_CHUNK = 1 << 16

#  이름에서 남길 글자 — 한글을 남긴다(영상 이름이 한글인 일이 흔하다).
_UP_KEEP = re.compile(r"[^0-9A-Za-z._가-힣ㄱ-ㅎㅏ-ㅣ()\[\] +-]+")


def safe_upload_name(raw):
    """올라온 이름에서 ★파일명 하나★만 남긴다.

    이름은 남의 기기가 준 문자열이다. 경로 구분자(`/` 와 윈도의 `\\`)를 먼저
    자르고 마지막 조각만 쓴다 — 그래야 `../../.ssh/authorized_keys` 가
    `authorized_keys` 로 납작해진다. 앞의 점도 지운다(`..` 과 숨김 파일).
    확장자 검사는 여기서 하지 않는다 — 부르는 쪽이 `media_kind` 로 본다.
    """
    name = unquote(str(raw or "")).replace("\\", "/").rpartition("/")[2]
    name = _UP_KEEP.sub("_", name).strip().lstrip(".")
    if len(name) > 120:                       # 앞을 자르고 ★확장자는 지킨다★
        stem, dot, ext = name.rpartition(".")
        name = (stem[:110] + dot + ext[:9]) if dot else name[:120]
    return name


def upload_dest(name, exists=None):
    """겹치면 덮지 않고 `-2`, `-3` 을 붙인다 — 남이 올린 것을 지우지 않는다."""
    exists = exists or (lambda p: Path(p).exists())
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    for i in range(1, 1000):
        cand = UPLOAD_DIR / (name if i == 1 else f"{stem}-{i}{dot}{ext}")
        if not exists(cand):
            return cand
    raise ValueError(f"같은 이름이 너무 많습니다: {name}")


def check_source_allowed(path):
    """이 파일을 열어도 되는가 — ★뿌리 안이거나 올린 것★.

    훑기만 막고 열기를 안 막으면 뿌리는 화면의 장식일 뿐이다(경로만 알면
    `/api/calib/view` 로 바로 열린다). 그래서 고르는 길과 여는 길이 같은
    규칙 하나를 지난다.
    """
    if in_roots(path, _roots() + [UPLOAD_DIR]):
        return
    raise ValueError(
        f"열 수 있는 폴더 밖입니다: {path}\n"
        "  local.yaml 의 browse_roots: 에 그 폴더를 넣으면 열립니다.")


def browse(d=""):
    """폴더 하나를 훑는다 — 하위 폴더와 ★열 수 있는 파일★ 만.

    뿌리 밖을 가리키면 조용히 첫 뿌리로 되돌린다. 「없는 폴더」와 「막힌 폴더」를
    구분해 알려 주면 그 자체가 밖에 무엇이 있는지 흘리는 답이 된다.
    """
    roots = _roots()
    base = Path(d).expanduser() if d else roots[0]
    if not base.is_dir() or not in_roots(base, roots):
        base = roots[0]
    base = base.resolve()
    #  뿌리에서 더 위로는 못 올라간다 — 「상위 폴더」가 탈출구가 되지 않게.
    up = str(base.parent) if in_roots(base.parent, roots) else str(base)
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
                                  "size": e.stat().st_size})
            except OSError:
                continue
    except PermissionError:
        return {"dir": str(base), "up": up, "error": "열 수 없는 폴더입니다",
                "dirs": [], "files": []}
    return {"dir": str(base), "up": up, "roots": [str(r) for r in roots],
            "dirs": dirs, "files": files}


def source_info(path):
    """고른 파일이 무엇인가 — 해상도·프레임 수. 이미지면 프레임 1장이다."""
    import tb.config as cfg                                # noqa: PLC0415
    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise ValueError(f"그런 파일이 없습니다: {p}")
    check_source_allowed(p)
    kind = media_kind(p)
    if not kind:
        raise ValueError(f"영상도 이미지도 아닙니다: {p.name}")
    info = cfg.video_info(str(p))
    info.update({"path": str(p), "kind": kind, "name": p.name})
    cfg.push_recent(str(p))
    return info


_CALIB = {}                                  # 영상별 VideoCapture 캐시
_CALIB_ENV = {"stamp": None, "val": {}}      # 프로필 해석 결과 캐시


def _calib_stamp():
    """설정 파일들의 (경로, mtime) — 이게 그대로면 다시 읽지 않는다."""
    out = []
    for f in _profile_files():
        out.append((str(f), f.stat().st_mtime))
    lp = ROOT / "local.yaml"
    out.append((str(lp), lp.stat().st_mtime if lp.exists() else 0.0))
    return tuple(out)


def _calib_env(pid):
    """프로필 하나를 푼다 — 왜곡보정 맵·BEV 크기·맞출 대상과 그 현재 값.

    캐시하는 이유: 프레임 한 장마다 YAML 을 다시 읽으면 34ms+ 다. 실제 영상 처리가
    10ms 라, 재생을 막고 있던 건 파일 읽기였다.

    값의 출처 순서는 `Calib._param_value` 가 정한다 —
      ① local.yaml 의 params (이 기계에서 사람이 정한 값)
      ② 워크스페이스 기본값 캐시 (`tb.run params` 가 노드에게 물어 둔 것)
      ③ 프로필의 default:
    """
    stamp = _calib_stamp()
    if _CALIB_ENV["stamp"] != stamp:
        _CALIB_ENV.update({"stamp": stamp, "val": {}})
    hit = _CALIB_ENV["val"].get(pid)
    if hit is not None:
        return hit

    from tb.calibrate import Calib                  # noqa: PLC0415
    from tb.contract import load as _load           # noqa: PLC0415
    from tb.run import load_ws_params, local_overrides   # noqa: PLC0415

    f = profile_path(pid)
    profile = _load(f)
    if not (profile.raw.get("calibration") or {}):
        raise ValueError(f"{Path(f).name} 에는 calibration: 블록이 없습니다")
    loc = local_overrides()
    params = loc.get("params") or {}
    ws = load_ws_params(profile) if profile.nodes else {}
    cal = Calib(profile, params, ws)
    #  왜곡보정을 켜고 볼지 — 노드가 이미 편 영상을 다시 펴면 이중보정이 된다.
    und = True
    if cal.und_param and profile.nodes:
        und = bool(params.get(profile.nodes[0]["id"], {}).get(cal.und_param, True))
    env = {"cal": cal, "profile": profile, "id": pid, "file": str(f),
           "undistort": und, "ws_params": ws}
    _CALIB_ENV["val"][pid] = env
    return env


def _cal_dump(cal):
    """Calib 의 현재 값 → JSON 으로 보낼 수 있는 형태."""
    return {
        "quad": [round(float(v), 1) for v in cal.quad.reshape(-1)],
        "rects": {k: [int(round(float(x))) for x in r.reshape(-1)]
                  for k, r in cal.rects.items()},
        "px2m": round(float(cal.px2m), 6),
        "length_m": round(float(cal.length_m), 3),
        # BEV 가로선 — 값과 '그래서 몇 번째 행인가' 를 함께 보낸다.
        # 화면은 행만 있으면 그릴 수 있고, 값은 입력칸이 쓴다.
        "bev_rows": {k: round(float(v), 1) for k, v in cal.bev_rows.items()},
        "bev_row_y": {k: round(float(cal.row_y(k)), 1) for k in cal.bev_rows},
    }


def _cal_work(env, body):
    """파일에서 읽은 값은 그대로 두고, 화면이 보낸 값을 얹은 ★사본★을 만든다.

    통째로 새로 만들지 않는 이유는 `Undistorter` 다 — 1920×1080 remap 맵을
    다시 계산하는 데만 7ms 라, 재생 중이면 그게 프레임 예산의 절반이다.
    """
    import copy                                     # noqa: PLC0415
    import numpy as np                              # noqa: PLC0415
    cal = copy.copy(env["cal"])                     # remap 맵은 공유한다
    cal.quad = env["cal"].quad.copy()
    cal.rects = {k: v.copy() for k, v in env["cal"].rects.items()}
    cal.bev_rows = dict(env["cal"].bev_rows)
    cal.ws_params = env["cal"].ws_params

    q = body.get("quad")
    if q:
        if len(q) != 8:
            raise ValueError("quad 는 값 8개여야 합니다")
        cal.quad = np.asarray([float(v) for v in q], np.float32).reshape(4, 2)
    for k, v in (body.get("rects") or {}).items():
        if k not in cal.rects:
            raise ValueError(f"계약에 없는 ROI 입니다: {k}")
        if len(v) != 4:
            raise ValueError(f"{k} 는 값 4개여야 합니다")
        cal.rects[k] = np.asarray([float(x) for x in v], np.float32).reshape(2, 2)
    for k, v in (body.get("bev_rows") or {}).items():
        if k not in cal.bev_rows:
            raise ValueError(f"계약에 없는 BEV 가로선입니다: {k}")
        cal.bev_rows[k] = float(v)
    if body.get("px2m") is not None:
        cal.px2m = max(1e-9, float(body["px2m"]))
    if body.get("length_m") is not None:
        cal.length_m = float(body["length_m"])
    return cal


def _grab(entry, n):
    """캐시된 VideoCapture 로 프레임 하나. 매 요청마다 파일을 새로 열지 않는다.

    OpenCV 의 VideoCapture 는 스레드 안전하지 않으므로 락으로 감싼다
    (ThreadingHTTPServer 라 썸네일 요청이 동시에 들어온다).
    """
    import cv2                                      # noqa: PLC0415
    #  ★사진 한 장도 소스가 된다★ [2026-09-04] 보정에 필요한 것은 프레임 하나다.
    #  VideoCapture 로도 열리기는 하지만 seek 동작이 백엔드마다 달라 조용히
    #  빈 프레임을 주는 일이 있다 — 확장자로 갈라서 imread 로 읽는다.
    if media_kind(entry["video"]) == "image":
        img = entry.get("still")
        if img is None:
            img = cv2.imread(str(entry["video"]))
            entry["still"] = img
        return img
    with _CAP_LOCK:
        cap = entry.get("cap")
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(str(entry["video"]))
            entry["cap"] = cap
            entry["pos"] = -1
        n = int(n)
        # 바로 다음 프레임이면 seek 없이 읽는다 (seek 가 제일 비싸다)
        if entry.get("pos") != n - 1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, n)
        ok, img = cap.read()
        entry["pos"] = n if ok else -1
        return img if ok else None


def calib_view(video, frame, cal, undist=True, width=1400, meas=None,
               mode="", grid=True):
    """★원본(사각형·ROI 표시) + BEV + 수직도★ 를 한 장으로.

    meta 의 좌표는 전부 ★돌려주는 JPEG 기준★이다. 예전에는 리사이즈 전 값을
    보내서 클릭 위치가 6~7% 어긋났다 — 「직접 맞추는」 화면에서는 치명적이다.
    """
    import cv2                                      # noqa: PLC0415
    import numpy as np                              # noqa: PLC0415
    from tb.geometry import (draw_grid, draw_rows,  # noqa: PLC0415
                             put_text, quad_is_sane, verticality, warp_bev)

    key = "calib:" + str(video)
    ent = _CALIB.get(key)
    if ent is None:
        ent = {"video": video, "cap": None, "pos": -1}
        _CALIB[key] = ent
    img = _grab(ent, frame)
    if img is None:
        return None, {}
    img = cal.und(img) if undist else cv2.resize(img, tuple(cal.und_size))

    bw, bh = cal.bev_w, cal.bev_h
    q = cal.quad
    bev = warp_bev(img, q, bw, bh)
    dev, nline = verticality(bev)
    if grid:
        bev = draw_grid(bev, cal.px2m)
    if cal.bev_rows:                                # 거리 판정의 기준선·문턱
        bev = draw_rows(bev, [(k, cal.row_y(k), cal.row_label(k))
                              for k in cal.bev_keys()], mode)

    src = img.copy()
    for k, r in cal.rects.items():                  # ROI — 고르는 중인 것만 밝게
        on = (mode == k)
        col = (60, 200, 255) if on else (120, 120, 120)
        (x0, y0), (x1, y1) = r.astype(int)
        cv2.rectangle(src, (x0, y0), (x1, y1), col, 4 if on else 2)
        put_text(src, k, (x0 + 10, max(0, y0 + 8)), 24, col)
        if on:
            for pt in ((x0, y0), (x1, y1)):
                cv2.circle(src, pt, 13, (0, 255, 255), -1, cv2.LINE_AA)

    qon = mode in ("", "quad", "measure")
    qcol = (70, 160, 255) if qon else (140, 140, 140)
    cv2.polylines(src, [q.astype(np.int32).reshape(-1, 1, 2)], True, qcol,
                  3 if qon else 2, cv2.LINE_AA)
    for i, lab in enumerate(("TL", "TR", "BR", "BL")):
        pt = (int(q[i][0]), int(q[i][1]))
        cv2.circle(src, pt, 13 if qon else 7,
                   (0, 255, 255) if qon else qcol, -1, cv2.LINE_AA)
        put_text(src, lab, (pt[0] + 16, pt[1] - 8), 22,
                 (0, 255, 255) if qon else qcol)

    for pt in (meas or []):                          # 측정 점 — BEV 위에
        cv2.circle(bev, (int(pt[0]), int(pt[1])), 5, (0, 255, 255), -1, cv2.LINE_AA)
    if meas and len(meas) >= 2:
        cv2.line(bev, (int(meas[0][0]), int(meas[0][1])),
                 (int(meas[1][0]), int(meas[1][1])), (0, 255, 255), 2, cv2.LINE_AA)

    ok, why = quad_is_sane(q, cal.und_size[0], cal.und_size[1])
    sc = bh / float(src.shape[0])
    src_small = cv2.resize(src, (max(1, int(round(src.shape[1] * sc))), bh))
    both = np.hstack([src_small, bev])
    shrink = 1.0
    if width and both.shape[1] > width:
        shrink = width / float(both.shape[1])
        both = cv2.resize(both, (width, int(round(both.shape[0] * shrink))))
    okj, buf = cv2.imencode(".jpg", both, [cv2.IMWRITE_JPEG_QUALITY, 84])
    meta = {"verticality_deg": None if dev != dev else round(dev, 2),
            "lines": nline, "sane": ok, "why": why,
            "src_w": src.shape[1], "src_h": src.shape[0],
            "bev_w": bw, "bev_h": bh,
            "bev_width_m": round(bw * cal.px2m, 3),
            "bumper_y": round(float(cal.bumper_y()), 1),
            # ★아래 다섯은 돌려주는 JPEG 좌표계★ — 클릭 환산은 이것만 쓴다
            "disp_w": both.shape[1], "disp_h": both.shape[0],
            "split_x": round(src_small.shape[1] * shrink, 2),
            "src_scale": sc * shrink,               # 원본 1px → 화면 몇 px
            "bev_scale": shrink}
    return (buf.tobytes() if okj else None), meta


def calib_state(pid=""):
    """보정 화면이 통째로 그리는 데 필요한 것."""
    import tb.config as cfg                                # noqa: PLC0415
    profs = list_profiles()
    if not pid:
        pid = _suggest_profile(profs)
    if not pid:
        return {"profiles": profs, "id": "", "recent": cfg.recent_videos(),
                "upload": _upload_state(),
                "error": "보정 프로필이 하나도 없습니다"}
    env = _calib_env(pid)
    cal, p = env["cal"], env["profile"]
    out = {"profiles": profs, "id": pid, "name": p.name,
           "file": Path(env["file"]).name, "kind": Path(env["file"]).parent.name,
           "bev": {"w": cal.bev_w, "h": cal.bev_h},
           "size": list(cal.und_size),
           "undistort": env["undistort"],
           "recent": cfg.recent_videos(),
           "snapshots": sorted(_snapshots().get(pid, {})),
           "targets": {k: {"kind": v.get("kind"), "hint": v.get("hint", ""),
                           "param": v.get("param"), "params": v.get("params"),
                           "nodes": v.get("nodes", [])}
                       for k, v in cal.targets.items()},
           #  ★워크스페이스 기본값★ — 있으면 화면이 «불러오기» 를 띄운다
           "ws_params": env.get("ws_params") or {},
           "ws_stamp": _ws_params_stamp(p),
           "verify": bool((p.raw.get("calibration") or {}).get("verify")),
           "upload": _upload_state(),
           "workspace": str(p.workspace or "")}
    out.update(_cal_dump(cal))
    #  같은 프로필을 ★워크스페이스 값만★ 으로 읽은 것 — «불러오기» 가 이 값으로 되돌린다
    try:
        from tb.calibrate import Calib as _C                # noqa: PLC0415
        out["ws_values"] = _cal_dump(_C(p, {}, env.get("ws_params") or {}))
    except Exception:                                       # noqa: BLE001
        out["ws_values"] = None
    return out


def _upload_state():
    """화면이 ★보내기 전에★ 알아야 하는 것 — 한도와 남은 자리.

    8GB 를 다 올리고 나서 「너무 큽니다」를 듣는 것은 도구가 아니다.
    자리 정보는 실패했을 때 사람이 무엇을 지워야 하는지도 알려 준다.
    """
    try:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(UPLOAD_DIR).free
    except OSError:
        free = 0
    return {"dir": str(UPLOAD_DIR), "max": UPLOAD_MAX,
            "free": free, "keep_free": UPLOAD_KEEP_FREE}


def _suggest_profile(profs):
    """처음 열 프로필 — local.yaml 의 default_contract 가 있으면 그것."""
    from tb.run import local_overrides                     # noqa: PLC0415
    good = [p for p in profs if not p.get("error")]
    if not good:
        return ""
    dc = Path(str(local_overrides().get("default_contract") or "")).name
    if dc:
        for p in good:
            if p.get("file") == dc:
                return p["id"]
    return good[0]["id"]


def _ws_params_stamp(profile):
    """워크스페이스 파라미터 캐시를 언제 받아 왔나 (없으면 빈 문자열)."""
    from tb.run import params_cache_path                   # noqa: PLC0415
    f = params_cache_path(profile)
    if not f.exists():
        return ""
    import datetime as _dt                                 # noqa: PLC0415
    return _dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes")


# ══════════════════════════════════════════════════════════════════════
#  스냅샷 — 값 세트를 이름 붙여 두고 오간다
# ══════════════════════════════════════════════════════════════════════
#  ★왜 필요한가★ 보정은 "이게 나은가 저게 나은가" 를 몇 번씩 오가는 일이다.
#  되돌리기 한 단계로는 30분 전 값으로 못 돌아가고, 그러면 사람이 숫자를
#  종이에 적어 두게 된다. 프로필 파일이 아니라 local.yaml 에 두는 이유는
#  이 값이 ★이 기계의 이 카메라★ 것이기 때문이다(계약 파일은 저장소에 올라간다).
SNAP_KEY = "calib_snapshots"


def _snapshots():
    from tb.run import local_overrides                     # noqa: PLC0415
    return local_overrides().get(SNAP_KEY) or {}


SNAP_NOTE = "# 보정 스냅샷 — 스튜디오가 관리한다(손으로 안 고쳐도 된다)"


def _write_snapshots(all_snaps):
    import yaml                                            # noqa: PLC0415
    import tb.config as cfg                                # noqa: PLC0415
    #  ★표식 주석까지 함께 걷어낸다★ 안 그러면 저장할 때마다 한 줄씩 쌓인다.
    keep = cfg.strip_block(cfg._local_text().splitlines(), SNAP_KEY, SNAP_NOTE)
    body = yaml.safe_dump({SNAP_KEY: all_snaps}, allow_unicode=True,
                          sort_keys=False, default_flow_style=None)
    keep += ["", SNAP_NOTE, body.rstrip()]
    cfg._write_local("\n".join(keep) + "\n")


def snapshot_save(pid, name, body):
    """지금 값을 이름 붙여 저장. 같은 이름이면 덮어쓴다."""
    name = str(name or "").strip()
    if not name or len(name) > 40 or "\n" in name:
        raise ValueError("스냅샷 이름이 잘못됐습니다")
    cal = _cal_work(_calib_env(pid), body)
    all_snaps = dict(_snapshots())
    per = dict(all_snaps.get(pid) or {})
    per[name] = _cal_dump(cal)
    all_snaps[pid] = per
    _write_snapshots(all_snaps)
    return {"names": sorted(per), "values": per[name]}


def snapshot_load(pid, name):
    per = (_snapshots().get(pid) or {})
    if name not in per:
        raise ValueError(f"그런 스냅샷이 없습니다: {name}")
    return per[name]


def snapshot_delete(pid, name):
    all_snaps = dict(_snapshots())
    per = dict(all_snaps.get(pid) or {})
    per.pop(str(name), None)
    all_snaps[pid] = per
    _write_snapshots(all_snaps)
    return {"names": sorted(per)}


# ══════════════════════════════════════════════════════════════════════
#  자동 미세조정 — 「수직도」를 목적함수로 사각형을 국소 탐색한다
# ══════════════════════════════════════════════════════════════════════
#  ★왜 되는가★ 지면은 평면이다. IPM 사각형의 좌우 변이 실제 차선 위에 정확히
#  놓이면 BEV 에서 차선이 수직으로 선다 — 그래서 `verticality()` 의 기울기
#  편차가 곧 "사각형이 얼마나 맞는가" 다. 사람이 1px 씩 밀어 보는 그 일을
#  기계가 대신한다.
#
#  ★여러 프레임에서 잰다★ 한 장에만 맞춘 사각형은 그 장면에만 맞는다(과적합).
#  실제로 곡선 구간 한 장으로 맞춰 놓고 직선에서 어긋난 적이 있다.
def _vert_cost(cal, imgs):
    """이 사각형이 얼마나 나쁜가 — 작을수록 좋다. 잴 수 없으면 무한대.

    돌려주는 것 : (비용, 잰 프레임 수, 찾은 선 수)
    """
    import numpy as np                                     # noqa: PLC0415
    from tb.geometry import verticality, warp_bev          # noqa: PLC0415
    devs, lines = [], 0
    for img in imgs:
        bev = warp_bev(img, cal.quad, cal.bev_w, cal.bev_h)
        dev, nline = verticality(bev)
        if nline >= 2 and dev == dev:      # NaN 은 선을 못 찾은 것
            devs.append(float(dev))
            lines += nline
    if not devs:
        return float("inf"), 0, 0
    return float(np.mean(devs)), len(devs), lines


def _quad_ok(quad, w, h):
    """★탐색이 밟으면 안 되는 자리★ — 이걸 안 막으면 목적함수가 뚫린다.

    실측한 실패다: 사각형을 화면 ★밖★ 으로 밀면 BEV 에 원본이 없는 검은 띠가
    생기고, 그 띠의 경계는 완벽히 수직이다. 수직도가 0.00° 로 떨어지므로
    탐색은 그것을 「완벽한 답」으로 고른다 — 그림은 아무것도 안 남는데.
    그래서 ① 네 점이 화면 안에 있고 ② 사각형이 온전해야만 후보로 본다.
    """
    from tb.geometry import quad_is_sane                   # noqa: PLC0415
    for i in range(4):
        x, y = float(quad[i][0]), float(quad[i][1])
        if not (0 <= x <= w and 0 <= y <= h):
            return False
    return quad_is_sane(quad, w, h)[0]


def optimize_quad(pid, body, video, frames, rounds=4):
    """지금 사각형 주변에서 더 나은 것을 찾는다 (좌표하강 + 반씩 줄이는 보폭).

    전역 탐색이 아니다 — ★사람이 대충 놓은 자리★ 에서 출발해 다듬는 것이다.
    출발이 엉뚱하면 결과도 엉뚱하므로, 나빠지면 원래 값을 그대로 돌려준다.
    """
    env = _calib_env(pid)
    cal = _cal_work(env, body)
    w, h = cal.und_size
    imgs = []
    for n in frames:
        ent = _CALIB.setdefault("calib:" + str(video),
                                {"video": video, "cap": None, "pos": -1})
        img = _grab(ent, int(n))
        if img is None:
            continue
        imgs.append(cal.und(img) if body.get("undistort", True) else img)
    if not imgs:
        raise ValueError("프레임을 하나도 읽지 못했습니다")

    best = cal.quad.copy()
    base, used, _ln = _vert_cost(cal, imgs)
    if base == float("inf"):
        raise ValueError("BEV 에서 선을 하나도 못 찾았습니다 — 사각형을 대강이라도 "
                         "차선 위에 올린 뒤, 차선이 보이는 구간에서 다시 누르세요")
    #  ★한 장으로는 맞추지 않는다★ 실측한 실패다: 야간 영상에서 세 장 중 한 장만
    #  선이 잡혔고, 탐색은 그 한 장의 ★연석★ 을 수직으로 세우는 자리로 갔다.
    #  차선이 아니라 연석에 맞춘 사각형인데 숫자는 0.00° 라 완벽해 보인다.
    #  근거가 한 장뿐이면 다듬지 않고 그 사실을 말한다.
    if used < 2:
        raise ValueError(f"{len(imgs)}장 중 {used}장에서만 선이 잡혔습니다 — "
                         "한 장에 맞춘 사각형은 다른 장면에서 무너집니다. "
                         "차선이 뚜렷한 구간으로 옮겨 다시 누르세요")
    start, start_used = base, used
    step = 12.0
    for _ in range(int(rounds)):
        moved = False
        for i in range(4):                 # 네 꼭짓점을
            for axis in (0, 1):            # x, y 각각
                for d in (+step, -step):
                    trial = best.copy()
                    trial[i][axis] += d
                    if not _quad_ok(trial, w, h):
                        continue
                    cal.quad = trial
                    c, u, _l = _vert_cost(cal, imgs)
                    #  ★잰 프레임이 줄면 개선이 아니다★ 선을 못 찾게 만들어
                    #  평균을 낮추는 길이 열려 있으면 탐색은 그 길로 간다.
                    if u < start_used or c >= base - 1e-6:
                        continue
                    base, best, used, moved = c, trial, u, True
        if not moved:
            step /= 2.0                    # 못 움직였으면 더 잘게
            if step < 1.0:
                break
    cal.quad = best
    improved = bool(base < start - 1e-6)
    return {"quad": [round(float(v), 1) for v in best.reshape(-1)],
            "before": round(start, 3), "after": round(base, 3),
            "frames": used,
            "improved": improved,
            "note": (f"수직도 편차 {start:.2f}° → {base:.2f}° "
                     f"({len(imgs)}장 중 {used}장 평균) — 저장하지 않았습니다. "
                     f"«4장 한꺼번에 보기» 로 확인한 뒤 저장하세요"
                     if improved else
                     "더 나은 자리를 못 찾았다 — 이 근처에서는 지금 값이 최선이다")}


def multiview(pid, body, video, frames, width=1200):
    """여러 프레임의 BEV 를 한 장에 나란히 — ★과적합 확인용★.

    한 프레임에서 완벽해 보이는 사각형이 다른 장면에서 무너지는 일이 흔하다.
    여기서 네 장을 같이 보면 그게 바로 눈에 띈다.
    """
    import cv2                                             # noqa: PLC0415
    import numpy as np                                     # noqa: PLC0415
    from tb.geometry import (draw_grid, put_text,          # noqa: PLC0415
                            verticality, warp_bev)
    env = _calib_env(pid)
    cal = _cal_work(env, body)
    und = bool(body.get("undistort", True))
    panes, devs = [], []
    for n in frames:
        ent = _CALIB.setdefault("calib:" + str(video),
                                {"video": video, "cap": None, "pos": -1})
        img = _grab(ent, int(n))
        if img is None:
            continue
        img = cal.und(img) if und else cv2.resize(img, tuple(cal.und_size))
        bev = warp_bev(img, cal.quad, cal.bev_w, cal.bev_h)
        dev, nline = verticality(bev)
        if body.get("grid", True):
            bev = draw_grid(bev, cal.px2m)
        txt = (f"{n}   수직도 {dev:.2f}°  선 {nline}" if dev == dev
               else f"{n}   선을 못 찾음")
        put_text(bev, txt, (10, 14), 20, (60, 220, 255))
        panes.append(bev)
        if dev == dev and nline >= 2:
            devs.append(float(dev))
    if not panes:
        raise ValueError("프레임을 하나도 읽지 못했습니다")
    sep = np.full((panes[0].shape[0], 4, 3), 255, np.uint8)
    both = panes[0]
    for p in panes[1:]:
        both = np.hstack([both, sep, p])
    if width and both.shape[1] > width:
        s = width / float(both.shape[1])
        both = cv2.resize(both, (width, int(round(both.shape[0] * s))))
    okj, buf = cv2.imencode(".jpg", both, [cv2.IMWRITE_JPEG_QUALITY, 84])
    return {"img": base64.b64encode(buf.tobytes()).decode() if okj else "",
            "devs": [round(d, 2) for d in devs],
            "spread": round(max(devs) - min(devs), 2) if len(devs) > 1 else None}


# ══════════════════════════════════════════════════════════════════════
#  체스보드 실측 보정 — K/D 를 ★사진에서★ 구한다
# ══════════════════════════════════════════════════════════════════════
#  ★왜 필요한가★ 지금까지 K/D 는 워크스페이스 소스에 박힌 값을 사람이 프로필로
#  옮겨 적은 것이었다. 그 값이 어디서 왔는지 아는 사람이 없어지면 카메라를
#  바꿔 달았을 때 손댈 방법이 없다. 여기서 사진 몇 장으로 다시 잰다.
#
#  ★어안이냐 일반이냐★ 프로필의 undistort 가 D 를 5개(k1 k2 p1 p2 k3)로 쓰면
#  일반 모델, 4개면 어안 모델이다 — 그 형태를 그대로 유지해서 채운다.
#  (형태가 바뀌면 노드 쪽 언디스토트와 어긋나 조용히 다른 그림이 된다.)
def chessboard_calibrate(pid, folder, cols=9, rows=6, square_mm=25.0, log=None):
    import cv2                                             # noqa: PLC0415
    import numpy as np                                     # noqa: PLC0415
    say = log if log is not None else (lambda *_a: None)

    d = Path(str(folder)).expanduser()
    if not d.is_dir():
        raise ValueError(f"그런 폴더가 없습니다: {d}")
    files = [f for f in sorted(d.iterdir()) if media_kind(f.name) == "image"]
    if len(files) < 5:
        raise ValueError(f"이미지가 {len(files)}장뿐입니다 — 최소 5장, "
                         "각도를 바꿔 가며 12장 이상이 좋습니다")
    cols, rows = int(cols), int(rows)
    if not (3 <= cols <= 30 and 3 <= rows <= 30):
        raise ValueError("코너 수가 이상합니다 (칸 수가 아니라 ★내부 코너★ 수다)")

    env = _calib_env(pid)
    u = (env["profile"].raw.get("calibration") or {}).get("undistort") or {}
    fisheye = len(u.get("D") or []) == 4
    say(f"모델: {'어안(fisheye)' if fisheye else '일반(pinhole)'} · "
        f"코너 {cols}×{rows} · 칸 {square_mm}mm · 사진 {len(files)}장")

    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * (float(square_mm) / 1000.0)
    objpoints, imgpoints, used, size = [], [], [], None
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
    for f in files:
        img = cv2.imread(str(f))
        if img is None:
            say(f"  · {f.name} — 못 읽음")
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if size is None:
            size = gray.shape[::-1]
        elif gray.shape[::-1] != size:
            say(f"  · {f.name} — 해상도가 다르다, 건너뜀")
            continue
        ok, corners = cv2.findChessboardCorners(
            gray, (cols, rows),
            cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE)
        if not ok:
            say(f"  · {f.name} — 코너를 못 찾음")
            continue
        corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), crit)
        objpoints.append(objp.reshape(-1, 1, 3) if fisheye else objp)
        imgpoints.append(corners)
        used.append(f.name)
        say(f"  ✓ {f.name}")
    if len(objpoints) < 5:
        raise ValueError(f"코너를 찾은 사진이 {len(objpoints)}장뿐입니다 — "
                         "보드 전체가 또렷하게 나오도록 다시 찍으세요")

    if fisheye:
        K = np.zeros((3, 3))
        D = np.zeros((4, 1))
        flags = (cv2.fisheye.CALIB_RECOMPUTE_EXTRINSIC
                 + cv2.fisheye.CALIB_FIX_SKEW)
        rms, K, D, _r, _t = cv2.fisheye.calibrate(
            objpoints, imgpoints, size, K, D, flags=flags,
            criteria=(cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1e-6))
        dist = [round(float(x), 6) for x in D.reshape(-1)]
    else:
        rms, K, D, _r, _t = cv2.calibrateCamera(objpoints, imgpoints, size, None, None)
        dist = [round(float(x), 6) for x in D.reshape(-1)][:5]
    fx, fy = float(K[0][0]), float(K[1][1])
    cx, cy = float(K[0][2]), float(K[1][2])
    say(f"재투영 오차(RMS) {rms:.4f} px  —  "
        + ("좋다" if rms < 0.5 else "0.5px 를 넘는다. 흐린 사진을 빼고 다시 재라"))
    return {"rms": round(float(rms), 4), "used": used, "n": len(used),
            "size": [int(size[0]), int(size[1])], "fisheye": fisheye,
            "K": [round(fx, 5), round(fy, 5), round(cx, 5), round(cy, 5)],
            "D": dist,
            "ok": rms < 1.0}


def apply_intrinsics(pid, K, D, size):
    """실측한 K/D 를 프로필 파일에 쓴다 — ★그 네 줄만★ 갈아 끼운다(주석 보존)."""
    import tb.config as cfg                                # noqa: PLC0415
    f = profile_path(pid)
    lines = f.read_text().splitlines()

    def fmt(v):
        return "[" + ", ".join(f"{float(x):g}" for x in v) + "]"

    want = {"size": fmt(size), "K": fmt(K), "D": fmt(D)}
    #  undistort: 블록 안의 그 키만 고친다. 다른 곳의 같은 이름(bev 의 w/h 등)을
    #  건드리지 않도록 블록 범위를 먼저 잡는다.
    try:
        ui = next(i for i, ln in enumerate(lines)
                  if re.match(r"^\s+undistort\s*:", ln))
    except StopIteration:
        raise ValueError("프로필에 calibration.undistort 블록이 없습니다") from None
    indent = len(lines[ui]) - len(lines[ui].lstrip())
    end = cfg._block_end(lines, ui, indent)
    done = []
    for key, val in want.items():
        pat = re.compile(rf"^(\s+){re.escape(key)}(\s*):(\s*).*$")
        for i in range(ui + 1, end):
            m = pat.match(lines[i])
            if m:
                lines[i] = cfg._keep_comment(
                    lines[i], f"{m.group(1)}{key}:{m.group(3) or ' '}{val}")
                done.append(key)
                break
    missing = [k for k in want if k not in done]
    if missing:
        raise ValueError(f"undistort 블록에 {', '.join(missing)} 줄이 없습니다 — "
                         "프로필을 직접 확인하세요")
    out = "\n".join(lines) + "\n"
    import yaml                                            # noqa: PLC0415
    got = ((yaml.safe_load(out) or {}).get("calibration") or {}).get("undistort") or {}
    for key, val in (("K", K), ("D", D), ("size", size)):
        if [float(x) for x in (got.get(key) or [])] != [float(x) for x in val]:
            raise ValueError(f"{key} 를 제대로 넣지 못했다 — 파일을 바꾸지 않았다")
    f.write_text(out)
    _CALIB_ENV["stamp"] = None
    return {"path": profile_id(f), "K": K, "D": D, "size": size}


def calib_yaml(pid, body):
    """지금 값을 YAML 로. ★저장하기 전에 눈으로 본다★ — 무엇이 어디로 가는지."""
    import yaml                                     # noqa: PLC0415
    cal = _cal_work(_calib_env(pid), body)
    vals = cal.to_params()
    if vals:
        return yaml.safe_dump({"params": vals}, allow_unicode=True,
                              sort_keys=False, default_flow_style=None)
    #  노드가 없는 독립 프로필 — 값이 프로필의 default 로 들어간다
    return yaml.safe_dump({"targets(default)": _target_defaults(cal)},
                          allow_unicode=True, sort_keys=False,
                          default_flow_style=None)


def calib_export(pid, body):
    """맞춘 값을 ★실차에서 그대로 쓸 수 있는 형태★ 로 만든다.

    ★왜 필요한가★ 보정 결과가 테스트베드 안(local.yaml)에만 남으면, 실차에
    반영하는 일이 사람의 손 옮겨 적기로 남는다 — 거기서 실측이 흐지부지된다.
    기본은 ★붙여 넣을 것만★ 만들어 준다(대상 저장소를 말없이 고치지 않는다).
    파일에 직접 쓰는 것은 `export_write` 가 따로 하고, 그건 확인을 한 번 더 받는다.

    돌려주는 것 : launch(런치 명령 한 줄) · params_yaml(--params-file 용 한 장)
    """
    env = _calib_env(pid)
    cal = _cal_work(env, body)
    c = env["profile"]
    vals = cal.to_params()                       # {노드id: {파라미터: 값}}

    def fmt(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (list, tuple)):
            return "\"[" + ", ".join(f"{float(x)}" for x in v) + "]\""
        return f"{v}"

    #  런치 파일 이름은 계약이 알려 준다(없으면 노드 실행 명령으로 떨어진다)
    lk = (c.raw.get("deploy") or {}).get("launch")
    lines, pkg = [], (c.nodes[0]["package"] if c.nodes else "")
    if lk:
        args = " ".join(f"{k}:={fmt(v)}" for kv in vals.values() for k, v in kv.items())
        lines.append(f"ros2 launch {pkg} {lk} \\\n     {args}")
    for n in c.nodes:
        kv = vals.get(n["id"]) or {}
        if not kv:
            continue
        args = " ".join(f"-p {k}:={fmt(v)}" for k, v in kv.items())
        lines.append(f"ros2 run {pkg} {n['executable']} --ros-args {args}")

    import yaml as _y                                       # noqa: PLC0415
    ros = {}
    for n in c.nodes:
        kv = vals.get(n["id"]) or {}
        if kv:
            ros[f"/{n.get('node_name') or n['executable']}"] = {"ros__parameters": kv}
    py = ("# 테스트베드에서 맞춘 카메라 설정 — `--params-file` 로 그대로 먹인다\n"
          f"#   ros2 run {pkg} <노드> --ros-args --params-file <이 파일>\n"
          "# ⚠️ 이 값은 ★맞출 때 쓴 영상의 카메라 설정★ 이다. 그 영상이 실차 카메라로\n"
          "#    찍힌 것이 아니면 실차에 그대로 쓰면 안 된다.\n"
          + _y.safe_dump(ros, allow_unicode=True, sort_keys=False,
                         default_flow_style=None))
    return {"launch": "\n\n".join(lines), "params_yaml": py, "params": vals}


def export_write(pid, body, path):
    """★워크스페이스 YAML 파일에 직접 쓴다★ — 옮겨 적기가 끊기는 지점을 없앤다.

    쓰는 것은 `--params-file` 형식 한 장이다(노드 이름 → ros__parameters).
    ★새로 만들거나, 이미 있으면 통째로 갈아 끼운다★ — 남의 저장소 파일이므로
    부분 병합으로 조용히 섞지 않는다. 화면이 먼저 미리보기를 보여 주고,
    사람이 «이 파일에 쓴다» 를 눌렀을 때만 여기 온다.
    """
    p = Path(str(path)).expanduser()
    if p.suffix not in (".yaml", ".yml"):
        raise ValueError("YAML 파일에만 쓸 수 있습니다")
    if not p.parent.is_dir():
        raise ValueError(f"그런 폴더가 없습니다: {p.parent}")
    if ROOT in p.parents:
        raise ValueError("테스트베드 안에는 쓰지 않습니다 — "
                         "저장은 «저장» 이, 여기는 ★대상 워크스페이스★ 용입니다")
    r = calib_export(pid, body)
    before = p.read_text() if p.is_file() else ""
    p.write_text(r["params_yaml"])
    return {"path": str(p), "existed": bool(before),
            "bytes": len(r["params_yaml"]), "replaced": before}


def calib_save(pid, body):
    """맞춘 값을 저장한다 — ★어디에 쓰는지는 프로필 종류가 정한다★.

    · 계약 프로필(워크스페이스에 붙은 것) → local.yaml 의 params:
      계약 파일은 저장소에 올라가고 여러 기계가 함께 쓴다. 카메라 값은 기계에
      묶이므로 거기 굳히면 다른 차의 값을 덮어쓰게 된다.
    · 독립 프로필(calib/*.yaml) → 그 파일의 targets[].default
      노드가 없으니 params: 로 쓸 자리가 없다. 그 파일이 곧 값의 집이다.
    """
    env = _calib_env(pid)
    cal = _cal_work(env, body)
    vals = cal.to_params()
    if vals:
        import tb.config as cfg                            # noqa: PLC0415
        path = cfg.set_params(vals, "local")
    else:
        path = _save_defaults(pid, cal)
    _CALIB_ENV["stamp"] = None                 # 파일이 바뀌었으니 다시 읽는다
    return {"path": path, "params": vals}


def _save_defaults(pid, cal):
    """독립 프로필의 targets[].default 를 갈아 끼운다 (주석 보존)."""
    import yaml                                            # noqa: PLC0415
    import tb.config as cfg                                # noqa: PLC0415
    f = profile_path(pid)
    lines = f.read_text().splitlines()
    want = _target_defaults(cal)
    for key, val in want.items():
        try:
            ki = next(i for i, ln in enumerate(lines)
                      if re.match(rf"^\s+{re.escape(key)}\s*:\s*$", ln))
        except StopIteration:
            raise ValueError(f"프로필에서 대상 `{key}` 를 못 찾았습니다") from None
        indent = len(lines[ki]) - len(lines[ki].lstrip())
        end = cfg._block_end(lines, ki, indent)
        step = cfg._child_indent(lines, ki, indent)
        body = " " * (indent + step) + f"default: {cfg._yv(val)}"
        hit = next((i for i in range(ki + 1, end)
                    if re.match(r"^\s+default\s*:", lines[i])), None)
        if hit is not None:
            lines[hit] = cfg._keep_comment(lines[hit], body)
        else:
            lines.insert(end, body)
    out = "\n".join(lines) + "\n"
    got = ((yaml.safe_load(out) or {}).get("calibration") or {}).get("targets") or {}
    for key, val in want.items():
        g = (got.get(key) or {}).get("default")
        if [float(x) for x in (g if isinstance(g, list) else [g])] != \
                [float(x) for x in (val if isinstance(val, list) else [val])]:
            raise ValueError(f"{key} 를 제대로 넣지 못했다 — 파일을 바꾸지 않았다")
    f.write_text(out)
    return profile_id(f)


def _target_defaults(cal):
    """Calib 의 현재 값 → {대상 이름: default 로 쓸 값}."""
    out = {}
    for key, t in cal.targets.items():
        k = t["kind"]
        if k == "quad":
            out[key] = [round(float(v), 1) for v in cal.quad.reshape(-1)]
        elif k == "rect":
            (x0, y0), (x1, y1) = cal.rects[key]
            out[key] = [int(round(x0)), int(round(y0)),
                        int(round(x1)), int(round(y1))]
        elif k == "scale":
            out[key] = round(float(cal.px2m), 6)
        elif k == "length_m":
            out[key] = round(float(cal.length_m), 3)
        elif k in ("bev_row", "bev_dist"):
            out[key] = round(float(cal.bev_rows.get(key, 0.0)), 1)
    return out


# ══════════════════════════════════════════════════════════════════════
#  오래 걸리는 일 — 한 번에 하나만
# ══════════════════════════════════════════════════════════════════════
#  ★임의 명령 실행기가 아니다★ 예전 웹앱은 `tb.run` 서브커맨드를 폼으로 감싸
#  사용자가 고른 인자로 subprocess 를 띄웠다(COMMANDS 표). 지금 여기서 도는
#  것은 아래 세 가지뿐이고, 무엇을 돌릴지는 화면이 고르지 못한다 — 이름으로만
#  부르고 인자는 서버가 만든다.
TASK = {"name": "", "started": 0.0, "done": False, "log": [], "result": None,
        "error": ""}
TASK_LOCK = threading.Lock()


def task_running():
    return bool(TASK["name"]) and not TASK["done"]


def task_status():
    with TASK_LOCK:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in TASK.items()}


def _task_log(msg):
    with TASK_LOCK:
        TASK["log"].append(str(msg))
        del TASK["log"][:-400]


def start_task(name, fn):
    """fn(log) 을 딴 스레드로. 결과는 /api/task 가 물어 간다."""
    if task_running():
        raise ValueError(f"«{TASK['name']}» 이(가) 아직 돌고 있습니다")
    with TASK_LOCK:
        TASK.update({"name": name, "started": time.time(), "done": False,
                     "log": [], "result": None, "error": ""})

    def body():
        try:
            r = fn(_task_log)
            with TASK_LOCK:
                TASK["result"] = r
        except Exception as e:                             # noqa: BLE001
            with TASK_LOCK:
                TASK["error"] = f"{type(e).__name__}: {e}"
        finally:
            with TASK_LOCK:
                TASK["done"] = True

    threading.Thread(target=body, daemon=True).start()
    return task_status()


def task_ws_params(pid):
    """대상 노드를 한 번 띄워 ★노드가 스스로 선언한 값★ 을 받아 적는다(20~40초)."""
    env = _calib_env(pid)
    if not env["profile"].nodes:
        raise ValueError("이 프로필에는 노드가 없습니다 — 워크스페이스에 붙은 "
                         "계약에서만 할 수 있습니다")
    cf = env["file"]

    def fn(log):
        log(f"노드를 띄워 파라미터를 묻는다 — {Path(cf).name}")
        p = subprocess.Popen(
            ["python3", "-m", "tb.run", "params", "--contract", str(cf)],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=dict(os.environ))
        for line in p.stdout:
            log(line.rstrip())
        rc = p.wait()
        _CALIB_ENV["stamp"] = None          # 캐시가 새 값을 읽게 한다
        if rc != 0:
            raise ValueError(f"받아 오지 못했다 (종료코드 {rc}) — "
                             "가중치 경로·GPU 문제일 수 있다")
        return {"rc": rc}

    return start_task("워크스페이스 파라미터 읽기", fn)


def task_verify(pid, body, video, dbg_path):
    """내가 그리는 BEV 와 ★노드가 실제로 만든 BEV★ 를 대조한다."""
    from tb.calibrate import verify                        # noqa: PLC0415
    env = _calib_env(pid)
    cal = _cal_work(env, body)
    start = int(body.get("start", 0) or 0)
    #  runs/ 는 git 에 없다 — 갓 클론한 기계에는 폴더가 아예 없고,
    #  cv2.imwrite 는 그럴 때 ★예외 없이 False 만 돌려준다★(그림이 조용히 사라진다).
    from tb.run import runs_dir                            # noqa: PLC0415
    out_png = runs_dir() / "_calib_verify.png"

    def fn(log):
        r = verify(cal, env["profile"], video, dbg_path, start, out_png=str(out_png))
        for ln in r["log"]:
            log(ln)
        return r

    return start_task("BEV 대조", fn)


def task_chessboard(pid, folder, cols, rows, square_mm):
    def fn(log):
        return chessboard_calibrate(pid, folder, cols, rows, square_mm, log)

    return start_task("체스보드 보정", fn)


# ══════════════════════════════════════════════════════════════════════
#  HTTP
# ══════════════════════════════════════════════════════════════════════
#  정적으로 내보낼 파일은 ★화이트리스트★로 고정한다.
#  확장자 기반으로 열어 두면 server.py 같은 소스까지 나간다.
_STATIC = {"index.html": "text/html; charset=utf-8",
           "app.js": "text/javascript; charset=utf-8",
           "style.css": "text/css; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    server_version = "testbed-studio"

    def log_message(self, fmt, *args):        # 조용히
        pass

    # ── 응답 헬퍼 ───────────────────────────────────────────────────
    def _send(self, code, body=b"", ctype="application/octet-stream", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self._send(code, body, "application/json; charset=utf-8")

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def _file(self, path: Path, ctype=None):
        if not path.is_file():
            return self._err(404, f"파일이 없습니다: {path.name}")
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self._send(200, path.read_bytes(), ctype)

    # ── 인증 — 토큰이 설정된 경우에만(로컬 전용이면 없음) ────────────
    def _authed(self):
        return check_basic_auth(self.headers.get("Authorization"), WEB_TOKEN)

    def _need_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="testbed"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    # ── 라우팅 ─────────────────────────────────────────────────────
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._authed():
            return self._need_auth()
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        try:
            if p.startswith("/api/"):
                return self._api(p[5:], q)
            if p in ("/", "/index.html"):
                return self._file(WEB / "index.html", "text/html; charset=utf-8")
            name = unquote(p).lstrip("/")
            if name in _STATIC:
                return self._file(WEB / name, _STATIC[name])
            return self._err(404, "없는 주소입니다")
        except BrokenPipeError:
            return None
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:                # noqa: BLE001
            return self._err(500, f"{type(e).__name__}: {e}")

    def _api(self, route, q):
        one = lambda k, d="": q.get(k, [d])[0]            # noqa: E731
        if route == "state":
            return self._json(calib_state(one("profile")))
        if route == "profiles":
            return self._json({"profiles": list_profiles()})
        if route == "browse":
            return self._json(browse(one("dir")))
        if route == "task":
            return self._json(task_status())
        if route == "png":
            #  대조 결과 이미지 하나 — 서버가 방금 만든 것만 준다
            f = ROOT / "runs" / "_calib_verify.png"
            return self._file(f, "image/png")
        return self._err(404, "없는 주소입니다")

    # ── 다른 출처의 페이지가 대신 쏘는 것을 막는다 ──────────────────
    #  브라우저는 Basic 인증을 한 번 통과하면 그 뒤로 ★자동으로★ 붙여 준다.
    #  그래서 다른 사이트의 스크립트가 이 주소로 POST 를 쏘면 인증은 통과한다.
    #  지금은 JSON·커스텀 헤더라 프리플라이트에 막히지만, 그건 요청 모양이
    #  우연히 그런 것이라 근거로 삼지 않는다. Origin 이 있으면 Host 와 맞는지 본다
    #  (curl 처럼 Origin 이 없는 것은 브라우저가 아니므로 이 함정에 안 걸린다).
    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).netloc == (self.headers.get("Host") or "")

    def do_POST(self):
        if not self._authed():
            return self._need_auth()
        if not self._same_origin():
            return self._err(403, "다른 출처에서 온 요청입니다")
        u = urlparse(self.path)
        if not u.path.startswith("/api/"):
            return self._err(404, "없는 주소입니다")
        try:
            #  ★업로드도 같은 그물 안에 둔다★ 밖에 두었더니 중간에 끊긴 업로드가
            #  응답 없이 연결만 닫혔다 — 화면에는 「연결이 끊겼습니다」만 뜨고
            #  얼마나 받다 끊겼는지는 서버 로그에도 안 남았다.
            if u.path == "/api/upload":
                return self._upload()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except (ValueError, json.JSONDecodeError):
                return self._err(400, "요청 본문이 잘못됐습니다")
            return _post(self, u.path[5:], body)
        except ValueError as e:
            return self._err(400, str(e))
        except SystemExit as e:
            return self._err(400, str(e) or "프로필을 열 수 없습니다")
        except BrokenPipeError:
            return None
        except Exception as e:                     # noqa: BLE001
            return self._err(500, f"{type(e).__name__}: {e}")

    # ── 파일 받기 ───────────────────────────────────────────────────
    def _upload(self):
        """다른 기기에 있는 영상을 이 기계에 놓는다.

        ★multipart 를 파싱하지 않는다★
        폼 인코딩은 경계 문자열을 스캔해야 해서 코드가 길고, 그 파서가
        곧 공격면이다. 여기 필요한 것은 「이름 하나 + 바이트 덩어리」뿐이라
        이름은 헤더로 받고 본문은 날바이트로 둔다.

        ★통째로 메모리에 읽지 않는다★
        영상은 GB 단위다. `rfile.read(n)` 한 방이면 그 크기만큼 램을 먹고
        이 기계는 추론도 같이 도는 기계다. 64KB 씩 파일로 흘린다.

        ★끝나기 전에는 `.part` 다★
        중간에 끊긴 파일이 목록에 「영상」으로 보이면 다음 사람이 그걸 열어
        보고 나서야 안다. 다 받고 나서 제자리로 옮긴다.
        """
        name = safe_upload_name(self.headers.get("X-Filename"))
        if not media_kind(name):
            return self._err(400, "영상이나 이미지만 올릴 수 있습니다 "
                                  f"(받은 이름: {name or '없음'})")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return self._err(400, "빈 파일입니다")
        if n > UPLOAD_MAX:
            return self._err(413, f"너무 큽니다 ({_mb(n)} > 한도 {_mb(UPLOAD_MAX)})")
        try:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(UPLOAD_DIR).free
        except OSError as e:
            return self._err(500, f"올릴 폴더를 못 씁니다: {e}")
        if n + UPLOAD_KEEP_FREE > free:
            return self._err(507, f"디스크가 모자랍니다 (남은 자리 {_mb(free)}, "
                                  f"이 파일 {_mb(n)} — 여유분 {_mb(UPLOAD_KEEP_FREE)}는 남긴다)")

        dest = upload_dest(name)
        tmp = dest.with_name(dest.name + ".part")
        got = 0
        try:
            with tmp.open("wb") as f:
                while got < n:
                    chunk = self.rfile.read(min(_UP_CHUNK, n - got))
                    if not chunk:
                        raise ValueError("업로드가 중간에 끊겼습니다 "
                                         f"({_mb(got)} / {_mb(n)})")
                    f.write(chunk)
                    got += len(chunk)
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        #  받은 직후 ★열리는지까지★ 확인해 돌려준다 — 화면은 이 뒤로
        #  경로로 고른 파일과 완전히 같은 길을 간다.
        return self._json({"uploaded": True, **source_info(str(dest))})


def _mb(n):
    """사람이 읽는 크기 — 오류 문구에만 쓴다."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _source(body, need=True):
    """요청이 가리키는 영상/이미지 경로. 없으면 400 으로 떨어진다."""
    v = str(body.get("video") or "")
    if not v:
        if not need:
            return ""
        raise ValueError("영상이나 이미지를 먼저 고르세요")
    p = Path(v).expanduser()
    if not p.is_file():
        raise ValueError(f"그런 파일이 없습니다: {v}")
    check_source_allowed(p)
    return str(p)


def _frames(body, n=4):
    """여러 프레임 검사용 프레임 번호들 — 안 주면 지금 프레임 하나."""
    fr = body.get("frames")
    if isinstance(fr, list) and fr:
        return [int(x) for x in fr[:8]]
    return [int(body.get("frame", 0) or 0)]


def _post(hnd, route, body):
    pid = str(body.get("profile") or "")

    if route == "calib/view":
        cal = _cal_work(_calib_env(pid), body)
        b, meta = calib_view(_source(body), int(body.get("frame", 0)), cal,
                             bool(body.get("undistort", True)),
                             int(body.get("w", 1400)),
                             [(float(a), float(c)) for a, c in (body.get("meas") or [])],
                             str(body.get("mode", "")),
                             bool(body.get("grid", True)))
        if not b:
            return hnd._err(404, "그 프레임을 읽지 못했습니다")
        return hnd._json({"img": base64.b64encode(b).decode(), "meta": meta})

    if route == "calib/multiview":
        return hnd._json(multiview(pid, body, _source(body), _frames(body),
                                   int(body.get("w", 1200))))
    if route == "calib/optimize":
        return hnd._json(optimize_quad(pid, body, _source(body), _frames(body)))
    if route == "calib/yaml":
        return hnd._json({"yaml": calib_yaml(pid, body)})
    if route == "calib/export":
        return hnd._json(calib_export(pid, body))
    if route == "calib/export/write":
        return hnd._json(export_write(pid, body, body.get("path", "")))
    if route == "calib/save":
        if task_running():
            return hnd._err(409, "다른 작업이 도는 중에는 저장하지 않습니다")
        return hnd._json({"ok": True, **calib_save(pid, body)})

    if route == "snapshot/save":
        return hnd._json(snapshot_save(pid, body.get("name", ""), body))
    if route == "snapshot/load":
        return hnd._json(snapshot_load(pid, body.get("name", "")))
    if route == "snapshot/delete":
        return hnd._json(snapshot_delete(pid, body.get("name", "")))

    if route == "source":
        return hnd._json(source_info(body.get("path", "")))
    if route == "profile/new":
        return hnd._json(new_profile(body.get("name", ""),
                                     body.get("width", 0), body.get("height", 0)))

    if route == "task/wsparams":
        return hnd._json(task_ws_params(pid))
    if route == "task/verify":
        dbg = Path(str(body.get("debug") or "")).expanduser()
        if not dbg.is_file():
            raise ValueError("대조할 디버그 영상을 고르세요 "
                             "(테스트 실행이 남긴 mp4)")
        return hnd._json(task_verify(pid, body, _source(body), str(dbg)))
    if route == "task/chessboard":
        return hnd._json(task_chessboard(pid, body.get("folder", ""),
                                         body.get("cols", 9), body.get("rows", 6),
                                         body.get("square_mm", 25.0)))
    if route == "intrinsics/apply":
        return hnd._json(apply_intrinsics(pid, body.get("K") or [],
                                          body.get("D") or [],
                                          body.get("size") or []))
    return hnd._err(404, f"없는 주소입니다: {route}")


def lan_urls(port):
    """다른 기기가 칠 주소들 — mDNS 이름을 앞에 둔다.

    IP 는 공유기가 다시 나눠 주면 바뀌지만 `<호스트>.local` 은 그대로다.
    avahi 가 죽어 있는 망도 있으므로 IP 도 같이 찍는다(둘 다 못 쓰면 주소가 없다).
    """
    out = []
    host = socket.gethostname().split(".")[0]
    if host:
        out.append(f"http://{host}.local:{port}")
    try:
        #  ★밖으로 나가는 인터페이스의 주소★ 를 고른다. gethostbyname 은 /etc/hosts
        #  때문에 127.0.1.1 을 주는 일이 흔하다 — 그 주소로는 아무도 못 들어온다.
        #  UDP 라 실제로 패킷이 나가지는 않는다(연결 없는 소켓).
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.settimeout(0.2)
            sk.connect(("192.0.2.1", 9))        # TEST-NET-1 — 실재하지 않는 주소
            ip = sk.getsockname()[0]
        if ip and not ip.startswith("127."):
            out.append(f"http://{ip}:{port}")
    except OSError:
        pass
    return out


def serve(host="127.0.0.1", port=8770):
    #  로컬이 아닌 host 에 토큰 없이 바인딩하는 것을 막는다(무방비 노출).
    if host not in ("127.0.0.1", "localhost", "::1") and not WEB_TOKEN:
        print("거부: 로컬이 아닌 host 인데 TB_WEB_TOKEN 이 없습니다 — "
              "무방비 노출을 막습니다. 토큰을 주고 다시 띄우세요.")
        return 2
    srv = ThreadingHTTPServer((host, port), Handler)
    profs = list_profiles()
    print("카메라 보정 스튜디오")
    if host in ("127.0.0.1", "localhost", "::1"):
        print(f"  이 기계에서만 → http://{host}:{port}")
        print("  다른 기기에서 열려면: TB_WEB_TOKEN=… --host 0.0.0.0")
    else:
        for u in lan_urls(port):
            print(f"  다른 기기에서 → {u}")
    print(f"  프로필 {len(profs)}개: " + ", ".join(p["id"] for p in profs[:6]))
    print(f"  올린 파일이 가는 곳: {UPLOAD_DIR}")
    if WEB_TOKEN:
        print("  인증: TB_WEB_TOKEN 설정됨 — 아이디는 아무거나, 비밀번호가 토큰")
    print("  Ctrl-C 로 종료")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    serve()
