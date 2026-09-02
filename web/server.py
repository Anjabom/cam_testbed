"""테스트베드 웹 뷰어 — 표준 라이브러리만 쓴다(외부 의존성 0).

★왜 표준 라이브러리인가★
대회 현장에서 pip 이 막히거나 가상환경이 꼬여도 그냥 돈다. 필요한 건 GET 몇 개와
정적 파일, 그리고 영상 스크럽용 Range 뿐이라 프레임워크가 할 일이 없다.

★서버는 얇다★
판정도 계산도 하지 않는다. `runs/` 의 파일을 읽어 JSON 으로 옮길 뿐이고,
모든 판정은 엔진이 만든 summary.json 에서 그대로 온다.

    python3 -m tb.run web            # http://127.0.0.1:8770
"""
from __future__ import annotations

import base64
import csv
import hmac
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent      # testbed/
WEB = ROOT / "web"
RUNS = ROOT / "runs"
BASE = ROOT / "baselines"
TRASH = RUNS / "_trash"          # 삭제는 지우지 않고 여기로 옮긴다
INDEX = RUNS / "_index.json"     # 고정·메모·태그 (실행 결과가 아니라 사람의 정리)

_SAFE = re.compile(r"^[A-Za-z0-9._@-]+$")

#  ★터널로 노출하면 이 토큰이 유일한 방어선이다★ — 서버는 ros2 run 을
#  subprocess 로 띄우므로 토큰 없이 열면 URL 을 아는 누구나 명령을 실행한다.
WEB_TOKEN = os.environ.get("TB_WEB_TOKEN", "")


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


# 정적으로 내보낼 파일은 ★화이트리스트★로 고정한다.
# 확장자 기반으로 열어 두면 server.py 같은 소스까지 나간다.
_STATIC = {"index.html": "text/html; charset=utf-8",
           "app.js": "text/javascript; charset=utf-8",
           "plot.js": "text/javascript; charset=utf-8",
           "style.css": "text/css; charset=utf-8"}


def _safe_run(name):
    """런 이름으로 디렉터리 탈출을 못 하게 한다."""
    name = unquote(name)
    if not _SAFE.match(name) or name.startswith("_"):
        return None
    d = RUNS / name
    return d if d.is_dir() else None


def _read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:      # noqa: BLE001
        return default


def _read_csv(p, limit=0):
    rows = []
    try:
        with open(p) as f:
            for i, r in enumerate(csv.DictReader(f)):
                if limit and i >= limit:
                    break
                o = {}
                for k, v in r.items():
                    if v == "" or v is None:
                        o[k] = None
                        continue
                    try:
                        o[k] = float(v)
                    except ValueError:
                        o[k] = v
                rows.append(o)
    except OSError:
        pass
    return rows


# ══════════════════════════════════════════════════════════════════════
#  런 요약 — 목록 화면이 쓰는 한 줄짜리 정보
# ══════════════════════════════════════════════════════════════════════
def run_brief(d):
    sj = _read_json(d / "summary.json")
    b = {"id": d.name, "has_summary": bool(sj)}
    if not sj:
        b["note"] = "분석 결과 없음"
        return b
    s, checks = sj.get("summary", {}), sj.get("checks", [])
    m = s.get("meta", {})
    b.update({
        "when": m.get("when"), "scenario": m.get("scenario"),
        # 실행할 때 사람이 붙인 이름(`--name`). 옛 런에는 없다 → 빈 문자열.
        "label": m.get("label", ""),
        "variant": m.get("variant"), "mode": m.get("mode"),
        "video": m.get("video"), "video_key": m.get("video_key"),
        "perturb": m.get("perturb"), "contract": m.get("contract"),
        "rows": s.get("rows"), "frames_pushed": s.get("frames_pushed"),
        "drop_rate": s.get("drop_rate"), "valid_rate": s.get("valid_rate"),
        "latency_p95_ms": s.get("latency_p95_ms"),
        "checks_total": len(checks),
        "checks_ok": sum(1 for c in checks if c.get("ok") is True),
        "checks_bad": sum(1 for c in checks if c.get("ok") is False),
        "flag_rate": s.get("flag_rate"),
        "contribution": [{"id": f["id"], "label": f.get("label"),
                          "rate": f.get("rate"), "bottleneck": f.get("bottleneck")}
                         for f in (s.get("funnel") or [])],
        "theta": s.get("theta_quality") or {},
        "drift_bad": sum(1 for x in sj.get("drift", [])
                         if x.get("status") == "drift" and not x.get("optional")),
    })
    cmp_md = d / "compare.md"
    if cmp_md.exists():
        head = cmp_md.read_text()[:400]
        for verdict in ("PASS", "DIFF", "NO_OVERLAP", "NO_SIGNALS"):
            if f"판정: {verdict}" in head:
                b["compare"] = verdict
                break
    b["has_video"] = any((d / n).exists()
                         for n in ("lane_debug.mp4", "debug.mp4"))
    b["has_path_video"] = (d / "path_overlay.mp4").exists()
    b["has_inject"] = (d / "inject.json").exists()
    cf = m.get("code_fingerprint") or {}
    b["code"] = {"sha": cf.get("sha"), "n_files": cf.get("n_files"),
                 "ws": (m.get("workspace") or "").rstrip("/").split("/")[-1],
                 # 사본이 있는 런만 실제 diff 를 볼 수 있다 (예전 런에는 없다)
                 "snapshot": (d / "code.json").exists()}
    return b


def _index():
    """사람이 붙인 정리 정보. 실행 결과와 섞지 않으려고 한 파일에 모아 둔다."""
    return _read_json(INDEX, {}) or {}


def _write_index(ix):
    INDEX.parent.mkdir(exist_ok=True)
    INDEX.write_text(json.dumps(ix, ensure_ascii=False, indent=1))


def _run_dirs(base=RUNS):
    """런 디렉터리만. 밑줄로 시작하는 것(_trash, _index)은 내부용이다."""
    if not base.is_dir():
        return []
    return [d for d in base.iterdir() if d.is_dir() and not d.name.startswith("_")]


def list_runs():
    ix = _index()
    out = []
    for d in _run_dirs():
        b = run_brief(d)
        meta = ix.get(d.name) or {}
        b["pin"] = bool(meta.get("pin"))
        b["memo"] = meta.get("memo", "")
        b["tags"] = meta.get("tags") or []
        b["has_feedback"] = (d / "feedback.md").exists()
        out.append(b)
    return sorted(out, key=lambda x: x["id"], reverse=True)


def code_timeline():
    """워크스페이스별 코드 변천 — 런의 코드 지문이 바뀐 지점이 곧 개발의 마디다.

    ★과거 런도 마디까지는 보인다★ 예전 지문은 mtime 기반이라 내용을 되돌릴 수
    없지만, "이 런과 저 런 사이에 코드가 바뀌었다"는 사실은 그대로 남아 있다.
    사본(code.json)이 있는 구간만 무엇이 바뀌었는지까지 말할 수 있다.
    """
    runs = [r for r in list_runs() if r.get("code", {}).get("sha")]
    runs.sort(key=lambda r: (r.get("when") or "", r["id"]))
    out = {}
    for r in runs:
        ws = r["code"]["ws"] or "(없음)"
        lane = out.setdefault(ws, [])
        if lane and lane[-1]["sha"] == r["code"]["sha"]:
            lane[-1]["runs"].append(r["id"])
            lane[-1]["last"] = r.get("when")
            lane[-1]["snapshot"] = lane[-1]["snapshot"] or r["code"]["snapshot"]
            lane[-1]["scenarios"].add(r.get("scenario") or "?")
            continue
        lane.append({"sha": r["code"]["sha"], "n_files": r["code"]["n_files"],
                     "first": r.get("when"), "last": r.get("when"),
                     "runs": [r["id"]], "snapshot": r["code"]["snapshot"],
                     "scenarios": {r.get("scenario") or "?"}})
    for lane in out.values():
        for i, e in enumerate(lane):
            e["scenarios"] = sorted(e["scenarios"])
            e["changed"] = (code_changes(lane[i - 1]["runs"][-1], e["runs"][0])
                            if i else [])
    return out


def _code_files(rid):
    """그 런이 남긴 파일별 해시. 사본이 없는 예전 런은 빈 dict."""
    return (_read_json(RUNS / rid / "code.json", {}) or {}).get("files") or {}


def _diff_maps(a, b):
    """파일별 해시 두 장을 견준다 — 한쪽이라도 비면 ★아무 말도 하지 않는다★.

    사본이 없는 예전 런을 '전부 추가'로 적으면 개발 이력이 거짓이 된다.
    """
    if not a or not b:
        return []
    out = [{"path": p, "status": "added"} for p in sorted(set(b) - set(a))]
    out += [{"path": p, "status": "removed"} for p in sorted(set(a) - set(b))]
    out += [{"path": p, "status": "changed"}
            for p in sorted(set(a) & set(b)) if a[p] != b[p]]
    return out


def code_changes(old_id, new_id):
    """두 런 사이에 바뀐 파일 — 양쪽 다 사본이 있어야 말할 수 있다."""
    return _diff_maps(_code_files(old_id), _code_files(new_id))


def code_diff(old_id, new_id, rel):
    """두 런의 사본에서 그 파일을 꺼내 unified diff — 표준 라이브러리만 쓴다."""
    import difflib
    import tarfile

    def read(rid):
        tf = RUNS / rid / "code_src.tar.gz"
        if not tf.exists():
            return None
        try:
            with tarfile.open(tf, "r:gz") as t:
                f = t.extractfile(rel)
                return f.read().decode("utf-8", "replace").splitlines() if f else []
        except (OSError, tarfile.TarError, KeyError):
            return None

    a, b = read(old_id), read(new_id)
    if a is None or b is None:
        return None
    return "\n".join(difflib.unified_diff(a, b, f"{old_id}/{rel}",
                                           f"{new_id}/{rel}", lineterm=""))


def prev_code_run(rid):
    """같은 워크스페이스에서 ★바로 앞★ 런 — 코드 변경점을 재는 기준."""
    runs = [r for r in list_runs() if r.get("code", {}).get("sha")]
    runs.sort(key=lambda r: (r.get("when") or "", r["id"]))
    ws = next((r["code"]["ws"] for r in runs if r["id"] == rid), None)
    prev = None
    for r in runs:
        if r["id"] == rid:
            return prev
        if r["code"]["ws"] == ws:
            prev = r["id"]
    return None


def list_trash():
    out = []
    for d in _run_dirs(TRASH):
        st = d.stat()
        out.append({"id": d.name, "when": time.strftime(
            "%Y-%m-%dT%H:%M:%S", time.localtime(st.st_mtime))})
    return sorted(out, key=lambda x: x["id"], reverse=True)


def list_baselines():
    out = []
    for c in sorted(BASE.glob("*.csv")):
        meta = _read_json(c.with_suffix(".json"), {}) or {}
        out.append({"name": c.stem, "video": meta.get("video"),
                    "video_key": meta.get("video_key"),
                    "start": meta.get("start"), "limit": meta.get("limit"),
                    "stride": meta.get("stride"), "mode": meta.get("mode"),
                    "when": meta.get("when"), "scenario": meta.get("scenario")})
    return out


def frame_jpeg(run_dir, n, width=360):
    """원본 영상에서 프레임 한 장을 JPEG 로. cv2 는 여기서만 지연 import 한다.

    (cv2 는 워크스페이스가 이미 쓰는 것이라 새 의존성이 아니지만,
     없어도 나머지 화면은 그대로 돌게 지연 import 로 둔다.)
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from tb.harvest import source_video          # noqa: PLC0415
    import cv2                                    # noqa: PLC0415
    key = "raw:" + str(run_dir)
    ent = _RENDER_CACHE.get(key)
    if ent is None:
        v = source_video(run_dir)
        if not v:
            return None
        ent = {"video": v, "cap": None, "pos": -1}
        _RENDER_CACHE[key] = ent
    img = _grab(ent, n)
    if img is None:
        return None
    if width and img.shape[1] > width:
        sc = width / float(img.shape[1])
        img = cv2.resize(img, (width, int(round(img.shape[0] * sc))))
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return buf.tobytes() if ok else None


_RENDER_CACHE = {}
_CAP_LOCK = threading.Lock()


def _grab(entry, n):
    """캐시된 VideoCapture 로 프레임 하나. 매 요청마다 파일을 새로 열지 않는다.

    OpenCV 의 VideoCapture 는 스레드 안전하지 않으므로 락으로 감싼다
    (ThreadingHTTPServer 라 썸네일 요청이 동시에 들어온다).
    """
    import cv2                                      # noqa: PLC0415
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


def _contract_for(run_dir):
    import sys
    sys.path.insert(0, str(ROOT))
    from tb.contract import load as _load          # noqa: PLC0415
    want = ((_read_json(Path(run_dir) / "summary.json", {}) or {})
            .get("summary", {}).get("meta", {}).get("contract"))
    first = None
    for f in sorted((ROOT / "contracts").glob("*.yaml")):
        try:
            c = _load(f)
        except Exception:      # noqa: BLE001
            continue
        first = first or c
        if want and c.name == want:
            return c
    return first


def overlay_jpeg(run_dir, n, width=900):
    """★판정에 쓴 값 그대로★ 차선·중심선·θ 를 그려 넣은 프레임."""
    import sys
    sys.path.insert(0, str(ROOT))
    import cv2                                      # noqa: PLC0415
    from tb.harvest import (effective_params, read_signals,   # noqa: PLC0415
                            source_video)
    from tb.render import Renderer                  # noqa: PLC0415

    key = str(run_dir)
    ent = _RENDER_CACHE.get(key)
    if ent is None:
        c = _contract_for(run_dir)
        if c is None:
            return None
        #  ★요청값이 아니라 실효값★ (tb.harvest.effective_params 의 주석 참고)
        ent = {"r": Renderer(c, effective_params(run_dir)),
               "rows": {int(x["frame"]): x for x in read_signals(run_dir)
                        if isinstance(x.get("frame"), (int, float))},
               "video": source_video(run_dir)}
        _RENDER_CACHE[key] = ent
    row = ent["rows"].get(int(n))
    if row is None or not ent["video"]:
        return None
    img = _grab(ent, n)
    if img is None:
        return None
    out = ent["r"].draw(img, row)
    if width and out.shape[1] > width:
        sc = width / float(out.shape[1])
        out = cv2.resize(out, (width, int(round(out.shape[0] * sc))))
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else None


#  캘리브레이션 — 영상을 보면서 IPM 사각형·ROI·px2m 을 직접 맞춘다.
#
#  ★「무엇을 맞추는가」도 기하도 엔진이 안다★
#  현재 값 읽기와 저장 형식은 `tb.calibrate.Calib` 에 이미 있다. 서버가 그걸
#  그대로 쓰므로 계약에 대상(quad/rect/scale/length_m)을 하나 더 늘려도
#  이 파일은 고치지 않는다. 여기서 한 벌 더 쓰면 CLI 와 반드시 어긋난다.
# ══════════════════════════════════════════════════════════════════════
_CALIB = {}                                  # 영상별 VideoCapture 캐시
_CALIB_ENV = {"stamp": None, "val": {}}      # 계약·시나리오 해석 결과 캐시


def _calib_stamp():
    """설정 파일들의 (경로, mtime) — 이게 그대로면 다시 읽지 않는다."""
    out = []
    for d in ("contracts", "scenarios"):
        for f in sorted((ROOT / d).glob("*.yaml")):
            out.append((str(f), f.stat().st_mtime))
    lp = ROOT / "local.yaml"
    out.append((str(lp), lp.stat().st_mtime if lp.exists() else 0.0))
    return tuple(out)


def _calib_env(scenario=""):
    """`tb.calibrate --scenario …` 와 ★같은 규칙★으로 계약·파라미터·영상을 푼다.

    캐시하는 이유: 예전에는 프레임 한 장마다 계약과 시나리오 YAML 을 전부 다시
    읽었다(34ms+). 실제 영상 처리는 10ms 라, 재생을 막고 있던 건 파일 읽기였다.
    """
    import sys
    sys.path.insert(0, str(ROOT))
    stamp = _calib_stamp()
    if _CALIB_ENV["stamp"] != stamp:
        _CALIB_ENV.update({"stamp": stamp, "val": {}})
    hit = _CALIB_ENV["val"].get(scenario)
    if hit is not None:
        return hit

    import yaml                                     # noqa: PLC0415
    from tb.calibrate import Calib                  # noqa: PLC0415
    from tb.contract import load as _load           # noqa: PLC0415
    from tb.run import (_deep_merge, _resolve_contract,     # noqa: PLC0415
                        load_ws_params, local_overrides, resolve_video)

    sc = {}
    if scenario:
        f = ROOT / "scenarios" / scenario
        if not f.is_file():
            raise ValueError(f"그런 시나리오가 없습니다: {scenario}")
        sc = yaml.safe_load(f.read_text()) or {}
    loc = local_overrides()
    cf = _resolve_contract(sc.get("contract"))
    if cf is None or not Path(cf).is_file():
        raise ValueError("계약 파일을 찾지 못했습니다")
    contract = _load(cf)
    params = _deep_merge(sc.get("params", {}), loc.get("params", {}))
    #  ★워크스페이스 기본값★ — `tb.run params` 로 노드에게 물어 둔 캐시.
    #  시나리오가 값을 안 정한 항목은 이것으로 출발한다(계약의 default 보다 앞).
    ws = load_ws_params(contract)
    cal = Calib(contract, params, ws)       # 계약에 calibration: 이 없으면 SystemExit
    # 왜곡보정을 켜고 볼지도 시나리오가 정한다 — CLI 와 같은 규칙이다.
    # (perception 이 남긴 영상은 이미 보정된 뒤라 끄고 봐야 이중보정이 안 된다)
    und = True
    if cal.und_param and contract.nodes:
        und = bool(params.get(contract.nodes[0]["id"], {}).get(cal.und_param, True))
    env = {"cal": cal, "contract": contract, "scenario": scenario,
           "video": resolve_video(sc, loc) or "", "undistort": und,
           "start": int(sc.get("start", 0) or 0),
           "ws_params": ws, "scen_params": sc.get("params") or {}}
    _CALIB_ENV["val"][scenario] = env
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


def calib_state(scenario=""):
    """캘리브레이션 화면이 통째로 그리는 데 필요한 것.

    시나리오를 고르면 계약·영상·시작 프레임·현재 파라미터가 전부 따라온다 —
    CLI 의 `tb.calibrate --scenario` 와 같은 해석이다.
    """
    cfg = _cfg()
    snap = cfg.snapshot()
    # 고르지 않았으면 ★평소 돌리는 시나리오★로 연다. 화면의 드롭다운과 실제로
    # 읽은 값이 달라지면 안 되므로, 고르는 일을 화면에 미루지 않는다.
    if not scenario and snap["suggest"]:
        try:
            _calib_env(snap["suggest"])
            scenario = snap["suggest"]
        except (SystemExit, ValueError):
            pass                       # 그 시나리오의 계약에 calibration 이 없으면 그냥 둔다
    env = _calib_env(scenario)
    cal, c = env["cal"], env["contract"]
    videos = dict(snap["videos"])
    # 시나리오가 가리키는 영상이 videos: 에 없는 경로여도 고를 수 있게 넣어 준다
    if env["video"] and env["video"] not in [v.get("path") for v in videos.values()]:
        videos["(시나리오)"] = cfg.video_info(env["video"])

    out = {"contract": c.name, "contract_file": Path(c.path).name,
           "scenario": scenario,
           "scenarios": [s["file"] for s in snap["scenarios"]],
           "bev": {"w": cal.bev_w, "h": cal.bev_h},
           "size": list(cal.und_size),
           "video": env["video"], "start": env["start"],
           "undistort": env["undistort"],
           "videos": videos,
           "targets": {k: {"kind": v.get("kind"), "hint": v.get("hint", ""),
                           "param": v.get("param"), "params": v.get("params"),
                           "nodes": v.get("nodes", [])}
                       for k, v in cal.targets.items()},
           "runs": [d.name for d in sorted(_run_dirs(), reverse=True)
                    if find_video(d) is not None],
           # ★워크스페이스 기본값★ — 있으면 화면이 «불러오기» 를 띄운다
           "ws_params": env.get("ws_params") or {},
           "ws_stamp": _ws_params_stamp(c),
           "workspace": str(c.workspace or "")}
    out.update(_cal_dump(cal))
    #  같은 계약을 ★워크스페이스 값만★ 으로 읽은 것 — «불러오기» 가 이 값으로 되돌린다
    try:
        from tb.calibrate import Calib as _C                # noqa: PLC0415
        out["ws_values"] = _cal_dump(_C(c, {}, env.get("ws_params") or {}))
    except Exception:                                       # noqa: BLE001
        out["ws_values"] = None
    return out


def _ws_params_stamp(contract):
    """워크스페이스 파라미터 캐시를 언제 받아 왔나 (없으면 빈 문자열)."""
    import sys
    sys.path.insert(0, str(ROOT))
    from tb.run import params_cache_path                    # noqa: PLC0415
    f = params_cache_path(contract)
    if not f.exists():
        return ""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="minutes")


def calib_yaml(scenario, body):
    """지금 값을 시나리오 params: 형식의 YAML 로. 저장 전에 눈으로 본다."""
    import yaml                                     # noqa: PLC0415
    cal = _cal_work(_calib_env(scenario), body)
    return yaml.safe_dump({"params": cal.to_params()}, allow_unicode=True,
                          sort_keys=False, default_flow_style=None)


def calib_export(scenario, body):
    """맞춘 값을 ★실차에서 그대로 쓸 수 있는 형태★ 로 만든다.

    ★왜 필요한가★ 캘리브 결과가 테스트베드 안(local.yaml·시나리오)에만 남으면,
    실차에 반영하는 일이 사람의 손 옮겨 적기로 남는다 — 그 지점에서 단계 2 실측이
    흐지부지된다. 워크스페이스 파일은 건드리지 않고, 붙여 넣을 것만 만들어 준다.

    돌려주는 것 : launch(런치 명령 한 줄) · params_yaml(--params-file 용 한 장)
    """
    env = _calib_env(scenario)
    cal = _cal_work(env, body)
    c = env["contract"]
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


def calib_save(scenario, body):
    """맞춘 값을 local.yaml 또는 시나리오의 params: 에 쓴다 (주석 보존)."""
    cal = _cal_work(_calib_env(scenario), body)
    path = _cfg().set_params(cal.to_params(), body.get("target") or "local")
    _CALIB_ENV["stamp"] = None                 # 파일이 바뀌었으니 다시 읽는다
    return {"path": path, "params": cal.to_params()}


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


def _scen_arg(name):
    """시나리오 파일 이름 — 디렉터리 탈출을 막는다. 빈 값은 '고르지 않음'."""
    name = unquote(name or "").strip()
    if not name:
        return ""
    if not _SAFE.match(name) or not name.endswith(".yaml"):
        raise ValueError(f"시나리오 이름이 잘못됐습니다: {name}")
    return name


def _calib_post(hnd, what, body):
    """캘리브레이션 화면의 POST — view / yaml / save / verify."""
    scen = _scen_arg(body.get("scenario", ""))

    if what == "view":
        env = _calib_env(scen)
        cal = _cal_work(env, body)
        video = str(body.get("video") or env["video"])
        if not video or not Path(video).is_file():
            return hnd._err(400, f"영상이 없습니다: {video}")
        meas = [(float(a), float(b)) for a, b in (body.get("meas") or [])]
        b, meta = calib_view(video, int(body.get("frame", 0)), cal,
                             bool(body.get("undistort", True)),
                             int(body.get("w", 1400)), meas,
                             str(body.get("mode", "")),
                             bool(body.get("grid", True)))
        if not b:
            return hnd._err(404, "그 프레임을 읽지 못했습니다")
        import base64                               # noqa: PLC0415
        return hnd._json({"img": base64.b64encode(b).decode(), "meta": meta})

    if what == "yaml":
        return hnd._json({"yaml": calib_yaml(scen, body)})

    if what == "export":
        # ⓑ 맞춘 값을 ★실차에서 쓸 형태★ 로. 워크스페이스 파일은 건드리지 않는다.
        return hnd._json(calib_export(scen, body))

    if what == "wsparams":
        # ⓐ 노드에게 파라미터를 물어 캐시에 받아 둔다(20~40초). 작업으로 돌린다 —
        #    노드를 띄우므로 실행 중이면 도메인이 겹칠 수 있다.
        if job_running():
            return hnd._err(409, "다른 작업이 돌고 있습니다")
        cf = (_calib_env(scen)["contract"]).path
        return hnd._json(start_job("params", ["--contract", str(cf)]))

    if what == "save":
        if job_running():
            return hnd._err(409, "실행 중에는 설정을 바꿀 수 없습니다")
        return hnd._json({"ok": True, **calib_save(scen, body)})

    if what == "verify":
        # 「내가 그리는 BEV 가 노드가 실제로 만드는 BEV 와 같은가」를 대조한다.
        # 계산은 엔진(`tb.calibrate --verify`)이 한다 — 여기서 또 짜지 않는다.
        rid = str(body.get("run", ""))
        if _safe_run(rid) is None:
            return hnd._err(404, "그런 실행이 없습니다")
        argv = ["python3", "-m", "tb.calibrate", "--verify", f"runs/{rid}"]
        if scen:
            argv += ["--scenario", f"scenarios/{scen}"]
        try:
            r = subprocess.run(argv, cwd=str(ROOT), capture_output=True,
                               text=True, timeout=300, env=dict(os.environ))
        except subprocess.TimeoutExpired:
            return hnd._err(504, "대조가 제한 시간 안에 끝나지 않았습니다")
        return hnd._json({"rc": r.returncode,
                          "out": (r.stdout or "") + (r.stderr or "")})

    return hnd._err(404, f"없는 캘리브레이션 항목입니다: {what}")


def contract_ui(contract):
    """웹 화면이 계약에서 읽어 가는 것 — ★프리셋·표의 열·플래그 이름★.

    예전에는 이 셋이 web/app.js 에 박혀 있었다. 그러면 계약을 하나 더 붙일 때마다
    화면 코드를 고쳐야 하고, 신호 이름이 다르면 표가 통째로 빈다(정지선 계약에서
    실제로 그랬다 — θ·cte·차선폭 열이 전부 '—' 였다).
    """
    if contract is None:
        return {}
    raw = contract.raw
    pres = []
    for p in (raw.get("frame_presets") or []):
        pres.append({"label": str(p.get("label", "")),
                     "where": str(p.get("where", "") or ""),
                     "default": bool(p.get("default"))})
    cols = [str(c) for c in (raw.get("frame_columns") or [])]
    if not cols:                       # 선언이 없으면 회귀 비교 대상 앞쪽을 쓴다
        cols = list(contract.compare_signals)[:4]
    fb = raw.get("flag_bits") or {}
    return {
        "frame_presets": pres,
        "frame_columns": cols,
        "flag_signal": fb.get("signal") or "",
        "flag_bits": [[int(k), str(v)] for k, v in sorted((fb.get("bits") or {}).items())],
        "events": [{"signal": e.get("signal"), "label": e.get("label", e.get("signal")),
                    "at": [str(x) for x in (e.get("at") or [])],
                    "why": e.get("why", "")}
                   for e in ([raw["events"]] if isinstance(raw.get("events"), dict)
                             else (raw.get("events") or []))],
    }


def pick_frames(run_dir, where, limit):
    """조건에 맞는 프레임 목록 + 집계. ★열은 계약이 정한다★"""
    import sys
    sys.path.insert(0, str(ROOT))
    from tb.harvest import read_signals, select, summarize   # noqa: PLC0415
    rows = read_signals(run_dir)
    picked = select(rows, where)
    total = len(picked)
    if limit and total > limit:
        step = total / float(limit)
        picked = [picked[int(i * step)] for i in range(limit)]
    contract = _contract_for(run_dir)      # 이름으로 그 런의 계약을 찾는다
    ui = contract_ui(contract)
    cols = ui.get("frame_columns") or []
    fsig = ui.get("flag_signal") or ""
    out = []
    for r in picked:
        if not isinstance(r.get("frame"), (int, float)):
            continue
        item = {"frame": int(r["frame"])}
        if fsig:
            item["flags"] = r.get(fsig)
        for c in cols:
            item[c] = r.get(c)
        out.append(item)
    return {
        "total_rows": len(rows), "matched": total, "shown": len(out),
        "counts": summarize(picked, contract),
        "columns": cols, "flag_signal": fsig,
        "flag_bits": ui.get("flag_bits") or [],
        "frames": out,
    }


#  path_overlay* 은 경로 시각화(별도 패널)이고 *__web.* 은 브라우저 재생용
#  변환 캐시다 — 둘 다 "디버그 영상"이 아니다. 예전에는 glob 이 이걸 집어서
#  디버그 패널에 경로 오버레이가 뜨고 프레임 정렬이 없다고 나왔다.
def _debug_candidates(d):
    for n in ("lane_debug.mp4", "lane_debug.webm", "debug.mp4", "debug.webm"):
        if (d / n).exists():
            yield d / n
    for f in sorted(list(d.glob("*.mp4")) + list(d.glob("*.webm"))):
        if f.stem.startswith("path_overlay") or f.stem.endswith("__web"):
            continue
        yield f


def find_video(d):
    for f in _debug_candidates(d):
        return f
    return None


def find_path_video(d):
    for ext in (".mp4", ".webm"):
        f = d / ("path_overlay" + ext)
        if f.exists():
            return f
    return None


# ══════════════════════════════════════════════════════════════════════
#  작업 실행 — 웹앱은 ★CLI 를 부를 뿐★이고 결과는 파일로 남는다.
#  한 번에 하나만 돌린다(ROS 노드가 겹치면 서로를 방해한다).
# ══════════════════════════════════════════════════════════════════════
JOB = {"proc": None, "kind": None, "args": None, "started": 0.0,
       "log": None, "run_dir": None, "cmd": None}
JOB_LOCK = threading.Lock()

# ── 실행할 수 있는 명령과 그 인자 — ★단 하나의 명세★ ─────────────────
#   화이트리스트(무엇을 허용하는가)와 화면의 입력 폼(무엇을 물어보는가)이
#   같은 곳에서 나온다. 둘을 따로 두면 CLI 에 인자를 하나 더해도 웹은 모르고,
#   반대로 화면에만 있는 인자가 서버에서 거부당한다.
#
#   type  flag(값 없음) · text · name · path · int · float · choice
#   src   choice 의 후보를 어디서 가져오는가 (scenarios/contracts/runs/baselines)
_ARG_SCEN = {"flag": "--scenario", "type": "choice", "src": "scenarios",
             "prefix": "scenarios/", "help": "시나리오 파일"}
_ARG_CONT = {"flag": "--contract", "type": "choice", "src": "contracts",
             "prefix": "contracts/", "help": "계약 파일 (시나리오의 contract: 를 덮어쓴다)"}
_ARG_VIDEO = {"flag": "--video", "type": "path", "help": "원본 영상 (비우면 런이 쓴 것)"}

COMMANDS = {
    "doctor": {
        "title": "환경 점검", "module": ["tb.run", "doctor"], "quick": True,
        "desc": "워크스페이스·계약·영상·가중치가 제대로 물려 있는지 본다. 아무것도 바꾸지 않는다.",
        "pos": [], "args": [_ARG_SCEN, _ARG_CONT]},

    "run": {
        "title": "테스트 실행", "module": ["tb.run", "run"],
        "desc": "시나리오를 돌려 결과를 runs/ 에 남긴다.",
        "pos": [], "args": [
            dict(_ARG_SCEN, required=True), _ARG_CONT,
            {"flag": "--variant", "type": "name", "repeat": True,
             "help": "이 변형만 돌린다 (쉼표로 여러 개)"},
            {"flag": "--tag", "type": "name", "help": "런 이름에 붙일 꼬리표"},
            {"flag": "--name", "type": "text",
             "help": "실행 기록에 표시할 이름 (시나리오와 별개. 공백·한글 가능)"},
            {"flag": "--baseline", "type": "choice", "src": "baselines",
             "help": "끝나고 이 기준과 자동 비교"},
            {"flag": "--domain", "type": "int", "help": "ROS_DOMAIN_ID (0=기본)"},
            {"flag": "--no-record-debug", "type": "flag",
             "help": "디버그 영상을 남기지 않는다 (기본은 남긴다 — 런당 2~7MB)"},
            {"flag": "--watch", "type": "flag",
             "help": "디버그 영상 창을 띄운다 — ★서버가 도는 PC 의 화면★에 뜬다"},
            {"flag": "--keep-going", "type": "flag",
             "help": "변형 하나가 실패해도 나머지를 계속"},
        ]},

    "inject": {
        "title": "주입 검증", "module": ["tb.run", "inject"],
        "desc": "합성 신호로 좌표 변환 수학만 검사한다. 영상도 YOLO 도 쓰지 않아 몇 초면 끝난다.",
        "pos": [], "args": [
            _ARG_SCEN, _ARG_CONT,
            {"flag": "--cases", "type": "path", "help": "검사 케이스 YAML (비우면 기본)"},
            {"flag": "--domain", "type": "int", "help": "ROS_DOMAIN_ID (0=기본)"},
        ]},

    "replay": {
        "title": "다시 돌려 디버그 영상 남기기", "module": ["tb.run", "replay"],
        "desc": "과거 실행을 그때 시나리오·영상·구간·파라미터 그대로 다시 돌린다. "
                "디버그 영상은 실행 중에만 잡히므로 옛 실행의 영상을 보려면 이 길뿐이다.",
        "pos": [{"name": "run", "type": "run", "required": True,
                 "help": "다시 돌릴 실행"}],
        "args": [
            {"flag": "--name", "type": "text", "help": "실행 기록에 표시할 이름"},
            {"flag": "--force", "type": "flag",
             "help": "코드가 그때와 달라도 강행 (그때 그림이 아니게 된다)"},
            {"flag": "--domain", "type": "int", "help": "ROS_DOMAIN_ID (0=기본)"},
        ]},

    "render": {
        "title": "경로 영상 만들기", "module": ["tb.run", "render"],
        "desc": "판정에 쓴 값 그대로 차선·중심선·θ 를 그려 그림이나 mp4 로 남긴다.",
        "pos": [{"name": "run", "type": "run", "required": True, "help": "대상 실행"}],
        "args": [
            {"flag": "--frames", "type": "text", "help": "프레임 번호를 쉼표로 (예: 1090,850)"},
            {"flag": "--where", "type": "text",
             "help": '조건식으로 고르기 (예: int(flags) % 4 >= 2)'},
            {"flag": "--limit", "type": "int", "help": "몇 장까지 (0=전부)"},
            {"flag": "--width", "type": "int", "help": "가로 픽셀"},
            {"flag": "--mp4", "type": "path", "help": "영상으로. 'auto' 면 <런>/path_overlay.mp4"},
            {"flag": "--fps", "type": "float", "help": "재생 속도 (0=원본과 같게)"},
            {"flag": "--out", "type": "path", "help": "저장 폴더 (비우면 <런>/render)"},
            _ARG_VIDEO, _ARG_SCEN, _ARG_CONT,
        ]},

    "harvest": {
        "title": "프레임 추출", "module": ["tb.run", "harvest"],
        "desc": "조건에 맞는 프레임만 원본에서 뽑아 낸다 — 라벨링해 다시 학습시키는 입구(능동 학습).",
        "pos": [{"name": "run", "type": "run", "required": True, "help": "대상 실행"}],
        "args": [
            {"flag": "--where", "type": "text",
             "help": '조건식 (예: int(flags) % 4 >= 2 — 폭 게이트 탈락)'},
            {"flag": "--limit", "type": "int", "help": "균등 샘플링 상한 (0=전부)"},
            {"flag": "--width", "type": "int", "help": "가로 축소 (0=원본)"},
            {"flag": "--out", "type": "path", "help": "저장 폴더 (비우면 <런>/harvest)"},
            {"flag": "--dry-run", "type": "flag", "help": "몇 장이 뽑히는지만 세어 본다"},
            _ARG_VIDEO, _ARG_SCEN, _ARG_CONT,
        ]},

    "publish": {
        "title": "정적 사이트 내보내기", "module": ["tb.run", "publish"],
        "desc": "결과만 보는 읽기 전용 사이트를 docs/ 에 굽는다 — GitHub Pages 로 공유한다. "
                "실행·보정·도구는 서버가 있어야 하므로 그 화면은 빠진다.",
        "pos": [], "args": [
            {"flag": "--run", "type": "run", "repeat": True,
             "help": "공개할 실행 (비우면 ★핀 꽂은 실행만★)"},
            {"flag": "--all", "type": "flag", "help": "핀과 무관하게 전부"},
            {"flag": "--out", "type": "path", "help": "내보낼 폴더 (기본 docs/)"},
        ]},

    "reanalyze": {
        "title": "재분석", "module": ["tb.run", "reanalyze"],
        "desc": "계약을 고친 뒤 raw.jsonl 로 신호·리포트만 다시 만든다. 노드를 다시 돌리지 않는다.",
        "pos": [{"name": "run", "type": "run", "required": True, "help": "대상 실행"}],
        "args": [_ARG_SCEN, _ARG_CONT]},

    "baseline": {
        "title": "기준 등록", "module": ["tb.run", "baseline"],
        "desc": "이 실행을 회귀 비교의 기준으로 등록한다.",
        "pos": [{"name": "run", "type": "run", "required": True, "help": "기준으로 삼을 실행"}],
        "args": [
            {"flag": "--name", "type": "name", "help": "기준 이름 (비우면 시나리오 이름)"},
            {"flag": "--force", "type": "flag", "help": "같은 이름이 있어도 덮어쓴다"},
        ]},

    "compare": {
        "title": "결과 비교", "module": ["tb.run", "compare"],
        "desc": "두 결과(기준 또는 실행)의 신호별 차이를 본다.",
        "pos": [{"name": "a", "type": "any", "required": True, "help": "기준 쪽"},
                {"name": "b", "type": "any", "required": True, "help": "비교할 쪽"}],
        "args": [_ARG_CONT, _ARG_SCEN]},

    "feedback": {
        "title": "피드백 문서", "module": ["tb.run", "feedback"],
        "desc": "실행 결과를 코드 개선 요청문(feedback.md)으로 만든다 — 클로드 코드에 그대로 넘긴다.",
        "pos": [{"name": "run", "type": "run", "required": True, "help": "대상 실행"}],
        "args": [
            {"flag": "--vs", "type": "run", "help": "이전 실행과 개선 전/후를 비교"},
            {"flag": "--note", "type": "text", "help": "사람이 본 것을 함께 적는다"},
        ]},

    "params": {
        "title": "워크스페이스 파라미터 읽기", "module": ["tb.run", "params"],
        "desc": "대상 노드를 한 번 띄워 ★노드가 스스로 선언한 값★ 을 받아 적는다. "
                "카메라 보정의 «워크스페이스 기본값 불러오기» 가 이 결과를 쓴다.",
        "pos": [], "args": [
            _ARG_SCEN, _ARG_CONT,
            {"flag": "--out", "type": "path", "help": "쓸 파일 (비우면 runs/_params/)"},
            {"flag": "--timeout", "type": "float", "help": "몇 초까지 기다릴 것인가"},
        ]},

    "build": {
        "title": "워크스페이스 빌드", "module": ["tb.run", "build"],
        "desc": "대상 워크스페이스를 colcon build 합니다. ★대상 코드를 고쳤으면 이걸 "
                "먼저★ — 이 워크스페이스는 파이썬 모듈이 사본이라, 빌드 전에는 고치기 "
                "전 코드가 돕니다. 빌드할 곳과 패키지는 계약이 정합니다.",
        "pos": [], "args": [
            _ARG_SCEN, _ARG_CONT,
            {"flag": "--all", "type": "flag",
             "help": "계약의 패키지만이 아니라 워크스페이스 전체를 빌드"},
        ]},

    "list": {
        "title": "목록", "module": ["tb.run", "list"], "quick": True,
        "desc": "지금까지의 실행과 리포트를 한 줄씩 훑는다. (같은 내용을 «실행 기록» 탭이 표로 보여 준다)",
        "pos": [], "args": []},

    "selftest": {
        "title": "자체 검사", "module": ["tb.selftest"], "quick": True,
        "desc": "테스트베드 자신이 성한지 본다. ROS 도 영상도 필요 없다.",
        "pos": [], "args": []},

    "discover": {
        "title": "계약 초안", "module": ["tb.discover"], "quick": True,
        "desc": "돌고 있는 ROS 그래프를 읽어 계약 초안을 뽑는다. ★대상 시스템이 떠 있어야 한다.★",
        "pos": [], "args": [
            {"flag": "--seconds", "type": "float", "help": "몇 초 동안 들을 것인가"},
            {"flag": "--name", "type": "name", "help": "계약 이름"},
            {"flag": "--out", "type": "path", "help": "쓸 파일 (비우면 화면에만)"},
            {"flag": "--workspace", "type": "path", "help": "대상 워크스페이스 경로"},
            {"flag": "--include", "type": "text", "help": "이 문자열이 든 토픽만 (쉼표)"},
            {"flag": "--exclude", "type": "text", "help": "이 문자열이 든 토픽 제외 (쉼표)"},
        ]},
}

# 예전 이름 — 화이트리스트가 명세에서 파생된다는 점이 핵심이다.
ALLOWED_JOBS = {k: [a["flag"] for a in v["args"]] for k, v in COMMANDS.items()}

# 셸을 거치지 않으므로(shell=False) 리다이렉션 기호는 무해하다. `< >` 를 막으면
# 조건식(`int(flags) % 4 >= 2`)이 통째로 거부된다 — 그건 문서에 적힌 사용법이다.
_BAD_CHARS = ";|&`$\n\r"
_NAME_OK = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_PATH_OK = re.compile(r"^[A-Za-z0-9_./~-]+$")


def _check_value(spec, v):
    """인자 값 하나를 타입에 맞게 검사한다. 통과하면 문자열, 아니면 예외."""
    v = str(v).strip()
    t = spec.get("type", "text")
    if any(c in v for c in _BAD_CHARS):
        raise ValueError(f"{spec.get('flag') or spec.get('name')}: "
                         "셸 특수문자는 쓸 수 없습니다")
    if t in ("int", "float"):
        try:
            return str(int(v)) if t == "int" else str(float(v))
        except ValueError:
            raise ValueError(f"{spec.get('flag')}: 숫자가 아닙니다: {v}") from None
    if t == "name" and not _NAME_OK.match(v):
        raise ValueError(f"{spec.get('flag') or spec.get('name')}: "
                         f"영문·숫자·_·-·. 만 쓸 수 있습니다: {v}")
    if t == "path":
        if ".." in v or not _PATH_OK.match(v):
            raise ValueError(f"{spec.get('flag') or spec.get('name')}: "
                             f"경로가 잘못됐습니다: {v}")
    if t == "run" and _safe_run(v) is None:
        raise ValueError(f"그런 실행이 없습니다: {v}")
    if t == "any" and not _SAFE.match(v):
        raise ValueError(f"이름이 잘못됐습니다: {v}")
    # text 는 위의 특수문자 검사만 통과하면 된다 (조건식에 공백·괄호가 들어간다)
    return v


def build_argv(kind, argv):
    """화면이 보낸 인자를 명세로 검사해 실제 명령줄로 만든다.

    ★여기를 통과하지 못한 것은 실행되지 않는다★ — 셸을 거치지 않고
    (shell=False) 리스트로 넘기므로, 검사와 실행 사이에 해석이 끼지 않는다.
    """
    spec = COMMANDS.get(kind)
    if spec is None:
        raise ValueError(f"허용되지 않은 작업입니다: {kind}")
    byflag = {a["flag"]: a for a in spec["args"]}
    pos_spec = spec.get("pos") or []
    out, pos, i = [], [], 0
    while i < len(argv):
        a = str(argv[i])
        if a.startswith("--"):
            s = byflag.get(a)
            if s is None:
                raise ValueError(f"허용되지 않은 인자입니다: {a}")
            out.append(a)
            if s.get("type") != "flag":
                if i + 1 >= len(argv):
                    raise ValueError(f"{a} 에 값이 없습니다")
                out.append(_check_value(s, argv[i + 1]))
                i += 1
        else:
            if len(pos) >= len(pos_spec):
                raise ValueError(f"인자가 너무 많습니다: {a}")
            pos.append(_check_value(pos_spec[len(pos)], a))
        i += 1
    for j, s in enumerate(pos_spec):
        if s.get("required") and j >= len(pos):
            raise ValueError(f"{s['name']} 를 지정해야 합니다")
    for s in spec["args"]:
        if s.get("required") and s["flag"] not in out:
            raise ValueError(f"{s['flag']} 를 지정해야 합니다")
    return ["python3", "-m"] + spec["module"] + pos + out


def command_specs():
    """화면이 입력 폼을 그리는 데 쓰는 명세 + 후보 목록."""
    cfg = _cfg()
    snap = cfg.snapshot()
    return {
        "commands": [dict(v, id=k) for k, v in COMMANDS.items()],
        "choices": {
            "scenarios": [s["file"] for s in snap["scenarios"]],
            "contracts": [c["file"] for c in snap["contracts"]],
            # 분석 결과가 없는 런은 뺀다 — render·compare·baseline 전부 signals.csv
            # 를 읽는다. 고를 수 있게 두면 「CSV 를 찾을 수 없다」로만 끝난다.
            "runs": [d.name for d in sorted(_run_dirs(), reverse=True)
                     if (d / "summary.json").exists()],
            "baselines": [b["name"] for b in list_baselines()],
        },
        # 웹에 두지 않는 것과 그 이유 — 화면이 그대로 보여 준다
        "omitted": [
            ["tb.run web / app", "이 웹앱 자신을 띄우는 명령입니다."],
            ["tb.calibrate", "«카메라 보정» 탭이 같은 일을 더 편하게 합니다 "
                             "(대조는 그 탭의 «노드와 대조»)."],
            ["tb.viewer / player / probe", "tb.run 이 내부적으로 띄우는 모듈이라 "
                                           "따로 부를 일이 없습니다."],
        ],
    }


def _cfg():
    """설정 해석·기록은 엔진이 한다 — 서버는 인자만 넘긴다."""
    import sys
    sys.path.insert(0, str(ROOT))
    from tb import config                          # noqa: PLC0415
    return config


def job_running():
    p = JOB.get("proc")
    return p is not None and p.poll() is None


#  터널은 장시간 프로세스라 단일 JOB 슬롯을 쓰면 그동안 테스트가 막힌다 —
#  그래서 자기 슬롯을 따로 둔다.
TUNNEL = {"proc": None, "log": None, "started": 0.0}
TUNNEL_LOCK = threading.Lock()
_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def tunnel_running():
    p = TUNNEL.get("proc")
    return p is not None and p.poll() is None


def tunnel_url():
    #  cloudflared 는 URL 을 로그로 찍는다 — 거기서 뽑는다(폴링 스레드 안 씀).
    lg = TUNNEL.get("log")
    if not lg or not Path(lg).exists():
        return ""
    m = _TUNNEL_URL.search(Path(lg).read_text(errors="replace"))
    return m.group(0) if m else ""


def start_tunnel(port):
    #  ★토큰 없이는 시작하지 않는다★ — 무방비 원격 코드 실행을 막는 fail-safe.
    if not WEB_TOKEN:
        return ("TB_WEB_TOKEN 이 없습니다 — 인증 없이 인터넷에 열 수 없습니다. "
                "토큰을 정해 TB_WEB_TOKEN 으로 주고 서버를 다시 띄우세요.")
    if shutil.which("cloudflared") is None:
        return ("cloudflared 가 설치돼 있지 않습니다. "
                "https://developers.cloudflare.com/cloudflare-one/connections/"
                "connect-apps/install-and-setup/installation/ 에서 설치하세요.")
    with TUNNEL_LOCK:
        if tunnel_running():
            return "이미 터널이 열려 있습니다"
        logf = ROOT / "runs" / "_tunnel.log"
        logf.parent.mkdir(exist_ok=True)
        fh = open(logf, "w")
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{int(port)}"],
            stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        TUNNEL.update({"proc": proc, "log": str(logf), "started": time.time()})
    return ""


def stop_tunnel():
    p = TUNNEL.get("proc")
    if not tunnel_running():
        return "열린 터널이 없습니다"
    try:
        os.killpg(os.getpgid(p.pid), 15)             # SIGTERM
    except (ProcessLookupError, PermissionError) as e:
        return str(e)
    return ""


def tunnel_status():
    return {"running": tunnel_running(), "url": tunnel_url(),
            "have_cloudflared": shutil.which("cloudflared") is not None,
            "have_token": bool(WEB_TOKEN),
            "elapsed_s": round(time.time() - TUNNEL["started"], 1)
            if TUNNEL.get("started") else 0}


def start_job(kind, argv):
    """명령을 background 로 띄운다. 인자는 명세로 거른다."""
    try:
        cmd = build_argv(kind, argv)
    except ValueError as e:
        return None, str(e)
    with JOB_LOCK:
        if job_running():
            return None, "이미 실행 중인 작업이 있습니다"
        logf = ROOT / "runs" / f"_job_{kind}.log"
        logf.parent.mkdir(exist_ok=True)
        fh = open(logf, "w")
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
            env=dict(os.environ), start_new_session=True)
        spec = COMMANDS[kind]
        user_args = cmd[2 + len(spec["module"]):]      # 모듈 이름은 빼고 보여 준다
        # 대상 런이 있는 명령(render·harvest·reanalyze…)은 그걸 기억해 둔다.
        # 안 그러면 「결과 보기」가 ★엉뚱한 런★을 가리킨다 — 디렉터리 mtime 은
        # 안의 파일을 고쳐도 안 바뀌므로 "가장 최근 폴더"가 답이 아니다.
        target = None
        if (spec.get("pos") or [{}])[0].get("type") == "run" and user_args:
            target = user_args[0] if not user_args[0].startswith("--") else None
        JOB.update({"proc": proc, "kind": kind, "args": user_args, "cmd": " ".join(cmd),
                    "started": time.time(), "log": str(logf), "run_dir": target})
        return proc, None


def run_quick(kind, argv, timeout=300):
    """짧은 점검은 background 로 보내지 않고 그 자리에서 결과를 돌려준다."""
    if not (COMMANDS.get(kind) or {}).get("quick"):
        raise ValueError(f"그 자리에서 돌릴 수 있는 작업이 아닙니다: {kind}")
    cmd = build_argv(kind, argv)
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=timeout, env=dict(os.environ))
    except subprocess.TimeoutExpired:
        raise TimeoutError("제한 시간 안에 끝나지 않았습니다") from None
    return {"name": kind, "cmd": " ".join(cmd), "rc": r.returncode,
            "out": (r.stdout or "") + (r.stderr or "")}


def job_status():
    p = JOB.get("proc")
    st = {"kind": JOB.get("kind"), "args": JOB.get("args"),
          "running": job_running(),
          "elapsed_s": round(time.time() - JOB["started"], 1) if JOB.get("started") else 0}
    if p is not None and p.poll() is not None:
        st["returncode"] = p.returncode
    st["cmd"] = JOB.get("cmd")
    # 진행률·라이브 화면은 ★런을 새로 만드는 명령★에만 있다. 다른 명령에서
    # 남의 런의 progress.json 을 붙이면 끝난 지 오래인 숫자가 실시간처럼 보인다.
    d = None
    if JOB.get("run_dir"):
        d = _safe_run(JOB["run_dir"])
        if d is not None:
            st["run"] = d.name
    elif JOB.get("kind") in ("run", "inject"):
        ds = _run_dirs()
        if ds:
            d = max(ds, key=lambda x: x.stat().st_mtime)
            st["run"] = d.name
    if d is not None and JOB.get("kind") == "run":
        pr = _read_json(d / "progress.json")
        if pr:
            st["progress"] = pr
        st["has_live"] = (d / "latest.jpg").exists()
    if JOB.get("log") and Path(JOB["log"]).exists():
        tail = Path(JOB["log"]).read_text(errors="replace")[-3000:]
        st["log_tail"] = tail
    return st


class Handler(BaseHTTPRequestHandler):
    server_version = "testbed-web"

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

    def _text(self, s, ctype="text/plain; charset=utf-8", code=200):
        self._send(code, s.encode(), ctype)

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    # ── 정적 파일 (Range 지원 — 영상 스크럽에 필요) ─────────────────
    def _file(self, path: Path, ctype=None):
        if not path.is_file():
            return self._err(404, f"파일이 없습니다: {path.name}")
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                a, _, b = rng[6:].partition("-")
                start = int(a) if a else 0
                end = int(b) if b else size - 1
                end = min(end, size - 1)
                if start > end:
                    raise ValueError
            except ValueError:
                return self._err(416, "잘못된 Range 요청입니다")
            with open(path, "rb") as f:
                f.seek(start)
                chunk = f.read(end - start + 1)
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(chunk)
            return None
        self._send(200, path.read_bytes(), ctype, {"Accept-Ranges": "bytes"})
        return None

    def _video(self, path: Path):
        """<video> 로 내보낸다. mp4v 로 찍힌 예전 영상은 한 번만 변환해 캐시.

        OpenCV 기본 코덱(mp4v = MPEG-4 Part 2)은 브라우저가 재생하지 못한다.
        파일은 멀쩡하므로 오류도 안 나고 그냥 검은 화면이 된다 — 그래서
        여기서 코덱을 확인하고 필요하면 H.264 로 굽는다(첫 요청만 ~1초).
        """
        import sys
        sys.path.insert(0, str(ROOT))
        from tb import encode                         # noqa: PLC0415
        p = Path(path)
        try:
            play = encode.web_path(p)
        except Exception:                            # noqa: BLE001
            play = p
        if not encode.is_web_playable(play):
            # 변환 수단이 없다. 그래도 파일은 준다 — 받는 쪽이 안내를 띄운다.
            self.send_response(415)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            body = json.dumps({
                "error": "브라우저가 재생할 수 없는 코덱(mp4v)인데 변환할 "
                         "ffmpeg 이 없습니다. sudo apt install ffmpeg 를 실행한 뒤 "
                         "다시 열면 재생됩니다.",
                "file": p.name, "codec": encode.fourcc_of(p),
            }, ensure_ascii=False).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return None
        ctype = "video/webm" if play.suffix == ".webm" else "video/mp4"
        return self._file(play, ctype)

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
        p = u.path
        q = parse_qs(u.query)
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
        except Exception as e:                # noqa: BLE001
            return self._err(500, f"{type(e).__name__}: {e}")

    def _api(self, route, q):
        if route == "runs":
            return self._json({"runs": list_runs()})
        if route == "baselines":
            return self._json({"baselines": list_baselines()})
        if route == "trash":
            return self._json({"trash": list_trash()})
        if route == "code":
            return self._json({"workspaces": code_timeline()})
        if route == "status":
            return self._json(job_status())
        if route == "tunnel":
            return self._json(tunnel_status())
        if route == "calib":
            try:
                return self._json(calib_state(_scen_arg(q.get("scenario", [""])[0])))
            except SystemExit as e:
                return self._err(404, str(e) or "계약에 calibration 항목이 없습니다")
            except ValueError as e:
                return self._err(400, str(e))
        if route == "commands":
            return self._json(command_specs())
        if route.startswith("quick/"):
            # 짧은 점검 — 쿼리스트링의 인자도 명세로 검사한다.
            #   ?a=--scenario&a=scenarios/x.yaml  처럼 순서대로 넘긴다.
            name = route.split("/", 1)[1]
            argv = list(q.get("a", []))
            if not argv and q.get("scenario"):        # 예전 화면이 쓰던 모양
                argv = ["--scenario", q["scenario"][0]]
            try:
                return self._json(run_quick(name, argv))
            except ValueError as e:
                return self._err(400, str(e))
            except TimeoutError as e:
                return self._err(504, str(e))
        if route == "config":
            return self._json(_cfg().snapshot())
        if route == "scenario":
            nm = q.get("name", [""])[0]
            if not _SAFE.match(nm):
                return self._err(400, "이름이 잘못됐습니다")
            return self._json(_cfg().resolve_scenario(nm))
        if route == "meta":
            return self._json({
                "root": str(ROOT),
                "contracts": [f.name for f in sorted((ROOT / "contracts").glob("*.yaml"))],
                "scenarios": [f.name for f in sorted((ROOT / "scenarios").glob("*.yaml"))],
            })

        parts = route.split("/")
        if parts[0] == "runs" and len(parts) >= 2:
            d = _safe_run(parts[1])
            if d is None:
                return self._err(404, "그런 실행이 없습니다")
            sub = parts[2] if len(parts) > 2 else ""
            if not sub:
                sj = _read_json(d / "summary.json")
                if not sj:
                    return self._err(404, "summary.json 이 없습니다")
                sj["id"] = d.name
                #  ★이 런의 계약이 정한 화면 설정★ (프리셋·열·플래그 이름·전이 표)
                sj["ui"] = contract_ui(_contract_for(d))
                v = find_video(d)
                if v:
                    sj["video"] = {"name": v.name, "size": v.stat().st_size}
                    dm = _read_json(d / "debug_meta.json", {}) or {}
                    sj["video"]["align"] = dm.get(v.name)
                return self._json(sj)
            if sub == "signals":
                return self._json({"rows": _read_csv(d / "signals.csv",
                                                     int(q.get("limit", [0])[0]))})
            if sub == "feedback":
                f = d / "feedback.md"
                return (self._text(f.read_text()) if f.exists()
                        else self._err(404, "아직 만들지 않았습니다"))
            # compare.md = 런 직후 ★기준★ 과의 자동 비교, compare_manual.md = 사람이
            # 「결과 비교」 탭에서 짝을 골라 돌린 것. 섞이면 배지가 거짓말을 한다.
            if sub in ("report", "compare", "compare_manual"):
                f = d / f"{sub}.md"
                return (self._text(f.read_text()) if f.exists()
                        else self._err(404, f"{sub}.md 가 없습니다"))
            if sub == "inject":
                j = _read_json(d / "inject.json")
                return self._json({"cases": j}) if j else self._err(404, "inject.json 이 없습니다")
            if sub == "frames":
                where = q.get("where", [""])[0]
                limit = int(q.get("limit", ["60"])[0])
                try:
                    return self._json(pick_frames(d, where, limit))
                except SyntaxError as e:
                    return self._err(400, f"조건식이 잘못됐습니다: {e}")
            if sub == "overlay":
                try:
                    n = int(q.get("n", ["0"])[0])
                    w = int(q.get("w", ["900"])[0])
                except ValueError:
                    return self._err(400, "n 또는 w 값이 잘못됐습니다")
                try:
                    b = overlay_jpeg(d, n, w)
                except Exception as e:          # noqa: BLE001
                    return self._err(500, f"경로를 그리지 못했습니다: {e}")
                return (self._send(200, b, "image/jpeg") if b
                        else self._err(404, "그 프레임은 그릴 수 없습니다"))
            if sub == "frame":
                try:
                    n = int(q.get("n", ["0"])[0])
                    w = int(q.get("w", ["360"])[0])
                except ValueError:
                    return self._err(400, "n 또는 w 값이 잘못됐습니다")
                try:
                    b = frame_jpeg(d, n, w)
                except Exception as e:          # noqa: BLE001
                    return self._err(500, f"프레임을 뽑지 못했습니다: {e}")
                return (self._send(200, b, "image/jpeg") if b
                        else self._err(404, "그 프레임을 읽지 못했습니다"))
            if sub == "live":
                f = d / "latest.jpg"
                return (self._file(f, "image/jpeg") if f.exists()
                        else self._err(404, "라이브 화면이 아직 없습니다"))
            if sub == "progress":
                pr = _read_json(d / "progress.json")
                return self._json(pr) if pr else self._err(404, "진행률 정보가 없습니다")
            if sub == "pathvideo":
                f = find_path_video(d)
                return (self._video(f) if f
                        else self._err(404, "경로 영상이 아직 없습니다"))
            if sub == "pathmeta":
                pr = _read_json(d / "path_overlay_progress.json")
                pv = find_path_video(d)
                out = {"exists": pv is not None,
                       "file": (pv.name if pv else "")}
                if pr:
                    out.update(pr)
                return self._json(out)
            if sub == "video":
                v = find_video(d)
                return self._video(v) if v else self._err(404, "영상이 없습니다")
            if sub == "code":
                cj = _read_json(d / "code.json", {}) or {}
                prev = prev_code_run(d.name)
                #  ?file= 이면 그 파일의 diff 원문 (직전 런 대비)
                rel = q.get("file", [""])[0]
                if rel:
                    if not prev:
                        return self._err(404, "비교할 앞 실행이 없습니다")
                    df = code_diff(prev, d.name, rel)
                    return (self._text(df) if df is not None
                            else self._err(404, "그 파일의 사본이 없습니다"))
                return self._json({
                    "sha": cj.get("sha"), "n_files": cj.get("n_files"),
                    "git": cj.get("git"), "workspace": cj.get("workspace"),
                    "snapshot": bool(cj), "prev": prev,
                    "changed": code_changes(prev, d.name) if prev else []})
            if sub == "log":
                nm = q.get("name", ["perception"])[0]
                if not _SAFE.match(nm):
                    return self._err(400, "이름이 잘못됐습니다")
                f = d / f"{nm}.log"
                return (self._text(f.read_text(errors="replace")) if f.exists()
                        else self._err(404, "로그가 없습니다"))
        return self._err(404, "없는 API 주소입니다")


def _do_post(self):
    if not self._authed():
        return self._need_auth()
    u = urlparse(self.path)
    if not u.path.startswith("/api/"):
        return self._err(404, "없는 주소입니다")
    route = u.path[5:]
    try:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
    except (ValueError, json.JSONDecodeError):
        return self._err(400, "요청 본문이 잘못됐습니다")

    # ── 캘리브레이션 — 값이 많아 POST 로 받는다 (쿼리스트링에 넣을 양이 아니다)
    if route.startswith("calib/"):
        try:
            return _calib_post(self, route[6:], body)
        except SystemExit as e:
            return self._err(404, str(e) or "계약에 calibration 항목이 없습니다")
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:                     # noqa: BLE001
            return self._err(500, f"{type(e).__name__}: {e}")

    if route == "jobs":
        kind = body.get("kind", "")
        argv = body.get("args") or []
        if not isinstance(argv, list):
            return self._err(400, "args 는 배열이어야 합니다")
        proc, err = start_job(kind, argv)
        if err:
            return self._err(409, err)
        return self._json({"ok": True, "kind": kind, "pid": proc.pid})
    # ── 등록 — local.yaml / contracts / scenarios 를 쓴다 ───────────
    #   주석을 보존하는 것은 엔진(tb.config)이 한다. 여기서는 인자만 넘긴다.
    if route.startswith("config/"):
        if route == "config/scenario/preview":
            #  ★파일을 쓰지 않는다★ — 다른 계약의 판정이 이 계약에서 성립하는지
            #  tb.lint 로 물어보기만 한다. 그래서 실행 중 잠금 위에 둔다.
            try:
                return self._json({"ok": True, **_cfg().preview_checks(
                    body.get("srcs") or [], body.get("contract", ""))})
            except ValueError as e:
                return self._err(400, str(e))
            except Exception as e:                 # noqa: BLE001
                return self._err(500, f"{type(e).__name__}: {e}")
        if job_running():
            return self._err(409, "실행 중에는 설정을 바꿀 수 없습니다")
        what = route[7:]
        cfg = _cfg()
        try:
            if what == "video":
                if body.get("delete"):
                    cfg.del_video(body.get("name", ""))
                    return self._json({"ok": True, "deleted": body.get("name")})
                info = cfg.set_video(body.get("name", ""), body.get("path", ""))
                return self._json({"ok": True, "video": info})
            if what == "contract":
                return self._json({"ok": True, **cfg.new_contract(
                    body.get("name", ""), body.get("workspace", ""),
                    bool(body.get("attach")))})
            if what == "contract/workspace":
                return self._json({"ok": True, **cfg.set_contract_workspace(
                    body.get("file", ""), body.get("workspace", ""))})
            if what == "scenario/clone":
                #  ★있는 시나리오를 본으로 떠서★ — 판정과 그 근거 주석을 물려받는다
                return self._json({"ok": True, **cfg.clone_scenario(
                    body.get("src", ""), body.get("name", ""),
                    body.get("video", ""),
                    body.get("start"), body.get("limit"),
                    body.get("mode") or None, body.get("note", ""))})
            if what == "scenario/compose":
                #  ★여러 시나리오의 판정을 합쳐★ — 목적이 다른 checks: 를 한 영상에
                #  같이 걸고 싶을 때(예: 인지 판정 + 개입 판정). 계약이 다르면 거부된다.
                srcs = body.get("srcs") or []
                if not isinstance(srcs, list):
                    return self._err(400, "srcs 는 배열이어야 합니다")
                return self._json({"ok": True, **cfg.compose_scenario(
                    srcs, body.get("name", ""), body.get("video", ""),
                    body.get("start"), body.get("limit"),
                    body.get("mode") or None, body.get("note", ""))})
            if what == "scenario/graft":
                #  ★다른 계약의 판정을 옮겨 심는다★ — clone/compose 는 본의
                #  contract: 를 물려받으므로 계약을 넘을 수가 없다. 여기서는
                #  대상 계약을 받아 빈 틀 위에 고른 판정만 얹는다.
                srcs = body.get("srcs") or []
                keep = body.get("keep")
                if not isinstance(srcs, list) or (keep is not None
                                                  and not isinstance(keep, list)):
                    return self._err(400, "srcs·keep 은 배열이어야 합니다")
                return self._json({"ok": True, **cfg.graft_scenario(
                    srcs, body.get("name", ""), body.get("contract", ""),
                    body.get("video", ""), keep,
                    body.get("mode") or "lockstep",
                    body.get("start", 0), body.get("limit", 0),
                    body.get("note", ""))})
            if what == "params":
                #  ★파라미터를 고쳐 가며 다시 돌리는 고리★ 를 웹에서 닫는다.
                #  기준 자동 비교에는 ⚠ 가 붙는다(params 는 provenance 다) —
                #  파라미터를 바꾼 것끼리는 «결과 비교» 로 런 대 런으로 본다.
                return self._json({"ok": True, "path": cfg.set_params(
                    cfg.clean_params(body.get("params")),
                    body.get("target") or "local")})
            if what == "scenario":
                return self._json({"ok": True, **cfg.new_scenario(
                    body.get("name", ""), body.get("contract", ""),
                    body.get("video", ""), body.get("mode", "lockstep"),
                    body.get("start", 0), body.get("limit", 0))})
        except ValueError as e:
            return self._err(400, str(e))
        except OSError as e:
            return self._err(500, f"파일을 저장하지 못했습니다: {e}")
        return self._err(404, f"없는 설정 항목입니다: {what}")

    # ── 정리 — 사람이 붙이는 정보. 실행 결과 파일은 건드리지 않는다 ─
    if route == "runs/meta":
        rid = body.get("id", "")
        if _safe_run(rid) is None:
            return self._err(404, "그런 실행이 없습니다")
        ix = _index()
        cur = ix.get(rid) or {}
        if "pin" in body:
            cur["pin"] = bool(body["pin"])
        if "memo" in body:
            cur["memo"] = str(body["memo"])[:500]
        if "tags" in body:
            tags = [str(t).strip()[:24] for t in (body["tags"] or []) if str(t).strip()]
            cur["tags"] = tags[:8]
        ix[rid] = {k: v for k, v in cur.items() if v not in ("", [], False)}
        if not ix[rid]:
            ix.pop(rid)
        try:
            _write_index(ix)
        except OSError as e:
            return self._err(500, f"인덱스를 저장하지 못했습니다: {e}")
        return self._json({"ok": True, "id": rid, "meta": ix.get(rid, {})})

    if route in ("runs/trash", "runs/restore"):
        ids = body.get("ids") or []
        if not isinstance(ids, list):
            return self._err(400, "ids 는 배열이어야 합니다")
        to_trash = route.endswith("trash")
        TRASH.mkdir(parents=True, exist_ok=True)
        moved, skipped = [], []
        for rid in ids:
            rid = str(rid)
            if not _SAFE.match(rid) or rid.startswith("_"):
                skipped.append(rid); continue
            src = (RUNS if to_trash else TRASH) / rid
            dst = (TRASH if to_trash else RUNS) / rid
            if not src.is_dir() or dst.exists():
                skipped.append(rid); continue
            try:
                src.rename(dst)
            except OSError:
                skipped.append(rid); continue
            moved.append(rid)
        return self._json({"ok": True, "moved": moved, "skipped": skipped})

    if route == "trash/empty":
        n = 0
        for d in _run_dirs(TRASH):
            try:
                shutil.rmtree(d)
                n += 1
            except OSError:
                pass
        return self._json({"ok": True, "removed": n})

    # ── 피드백 문서 — 생성은 엔진(tb.feedback)이 한다 ───────────────
    if route == "feedback":
        d = _safe_run(body.get("run", ""))
        if d is None:
            return self._err(404, "그런 실행이 없습니다")
        prev = _safe_run(body.get("vs", "")) if body.get("vs") else None
        if body.get("vs") and prev is None:
            return self._err(404, "비교할 실행이 없습니다")
        import sys
        sys.path.insert(0, str(ROOT))
        try:
            from tb import feedback                    # noqa: PLC0415
            md = feedback.render(d, prev, str(body.get("note", ""))[:4000])
            (d / "feedback.md").write_text(md)
        except SystemExit as e:
            return self._err(400, str(e))
        except Exception as e:                         # noqa: BLE001
            return self._err(500, f"{type(e).__name__}: {e}")
        return self._json({"ok": True, "id": d.name,
                           "path": str(d / "feedback.md"), "md": md})

    if route == "tunnel":
        action = body.get("action")
        if action == "start":
            err = start_tunnel(body.get("port") or 8770)
        elif action == "stop":
            err = stop_tunnel()
        else:
            return self._err(400, "action 은 start 또는 stop 이어야 합니다")
        return self._err(409, err) if err else self._json(tunnel_status())

    if route == "jobs/stop":
        p = JOB.get("proc")
        if not job_running():
            return self._err(409, "실행 중인 작업이 없습니다")
        try:
            os.killpg(os.getpgid(p.pid), 2)      # SIGINT
        except (ProcessLookupError, PermissionError) as e:
            return self._err(500, str(e))
        return self._json({"ok": True})
    return self._err(404, "없는 API 주소입니다")


Handler.do_POST = _do_post


def serve(host="127.0.0.1", port=8770, open_browser=False):
    #  로컬이 아닌 host 에 토큰 없이 바인딩하는 것을 막는다(무방비 노출).
    #  터널(cloudflared)은 localhost 로 붙으므로 이 검사에 안 걸리지만,
    #  --host 0.0.0.0 같은 직접 노출은 여기서 잡힌다.
    if host not in ("127.0.0.1", "localhost", "::1") and not WEB_TOKEN:
        print("거부: 로컬이 아닌 host 인데 TB_WEB_TOKEN 이 없습니다 — "
              "무방비 노출을 막습니다. 토큰을 주고 다시 띄우세요.")
        return 2
    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"테스트베드 웹 뷰어 → {url}")
    print(f"  실행 {len(list_runs())}개 · 베이스라인 {len(list_baselines())}개")
    if WEB_TOKEN:
        print("  인증: TB_WEB_TOKEN 설정됨 — 모든 요청에 비밀번호로 필요")
    print("  Ctrl-C 로 종료")
    if open_browser:
        threading.Timer(0.6, lambda: os.system(f"xdg-open {url} >/dev/null 2>&1 &")).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    serve()
