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

import csv
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
        for verdict in ("PASS", "DIFF", "NO_OVERLAP"):
            if f"판정: {verdict}" in head:
                b["compare"] = verdict
                break
    b["has_video"] = any((d / n).exists()
                         for n in ("lane_debug.mp4", "debug.mp4"))
    b["has_path_video"] = (d / "path_overlay.mp4").exists()
    b["has_inject"] = (d / "inject.json").exists()
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
    from tb.harvest import read_signals, source_video   # noqa: PLC0415
    from tb.render import Renderer                  # noqa: PLC0415

    key = str(run_dir)
    ent = _RENDER_CACHE.get(key)
    if ent is None:
        c = _contract_for(run_dir)
        if c is None:
            return None
        meta = ((_read_json(Path(run_dir) / "summary.json", {}) or {})
                .get("summary", {}).get("meta", {}))
        ent = {"r": Renderer(c, meta.get("params")),
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


_CALIB = {}


def calib_state():
    """캘리브레이션 화면이 쓸 현재 값 — 계약 + local.yaml + 시나리오."""
    import sys
    sys.path.insert(0, str(ROOT))
    import yaml                                     # noqa: PLC0415
    from tb.contract import load as _load           # noqa: PLC0415
    cands = sorted((ROOT / "contracts").glob("*.yaml"))
    if not cands:
        return None
    c = _load(cands[0])
    for f in cands:
        cc = _load(f)
        if (cc.raw.get("calibration")):
            c = cc
            break
    cal = c.raw.get("calibration") or {}
    r = c.raw.get("render") or {}
    quad = r.get("ipm_src_pts")
    for f in sorted((ROOT / "scenarios").glob("*.yaml")):
        try:
            sc = yaml.safe_load(f.read_text()) or {}
        except Exception:      # noqa: BLE001
            continue
        for nid, kv in (sc.get("params") or {}).items():
            if "ipm_src_pts" in kv:
                quad = kv["ipm_src_pts"]
    return {"contract": c.name, "quad": quad,
            "bev": cal.get("bev", {"w": 640, "h": 480}),
            "size": (cal.get("undistort") or {}).get("size", [1920, 1080]),
            "targets": {k: {"kind": v.get("kind"), "hint": v.get("hint", ""),
                            "param": v.get("param"), "params": v.get("params"),
                            "nodes": v.get("nodes", [])}
                        for k, v in (cal.get("targets") or {}).items()},
            "videos": _videos()}


def _videos():
    import yaml                                     # noqa: PLC0415
    out = {}
    lp = ROOT / "local.yaml"
    if lp.exists():
        try:
            d = yaml.safe_load(lp.read_text()) or {}
            out = dict(d.get("videos") or {})
            if d.get("video"):
                out.setdefault("(local.video)", d["video"])
        except Exception:      # noqa: BLE001
            pass
    return out


def calib_view(video, frame, quad, px2m, undist=True, width=1400, meas=None):
    """4점을 받아 ★원본(사각형 표시) + BEV + 수직도★ 를 돌려준다."""
    import sys
    sys.path.insert(0, str(ROOT))
    import cv2                                      # noqa: PLC0415
    import numpy as np                              # noqa: PLC0415
    from tb.contract import load as _load           # noqa: PLC0415
    from tb.geometry import (Undistorter, draw_grid, put_text,   # noqa: PLC0415
                             quad_is_sane, verticality, warp_bev)
    st = calib_state()
    if st is None:
        return None, {}
    cands = [f for f in sorted((ROOT / "contracts").glob("*.yaml"))]
    c = None
    for f in cands:
        cc = _load(f)
        if cc.raw.get("calibration"):
            c = cc
            break
    cal = c.raw.get("calibration") or {}
    u = cal.get("undistort") or {}

    key = "calib:" + str(video)
    ent = _CALIB.get(key)
    if ent is None:
        ent = {"video": video, "cap": None, "pos": -1,
               "und": Undistorter(u["size"], u["K"], u["D"], u.get("alpha", 0.0))
               if u else None}
        _CALIB[key] = ent
    img = _grab(ent, frame)
    if img is None:
        return None, {}
    if undist and ent["und"] is not None:
        img = ent["und"](img)
    else:
        img = cv2.resize(img, tuple(st["size"]))

    bw, bh = int(st["bev"]["w"]), int(st["bev"]["h"])
    q = np.asarray(quad, np.float32).reshape(4, 2)
    bev = warp_bev(img, q, bw, bh)
    dev, nline = verticality(bev)
    bev = draw_grid(bev, px2m)

    src = img.copy()
    cv2.polylines(src, [q.astype(np.int32).reshape(-1, 1, 2)], True,
                  (70, 160, 255), 3, cv2.LINE_AA)
    for i, lab in enumerate(("TL", "TR", "BR", "BL")):
        pt = (int(q[i][0]), int(q[i][1]))
        cv2.circle(src, pt, 12, (0, 255, 255), -1, cv2.LINE_AA)
        put_text(src, lab, (pt[0] + 16, pt[1] - 8), 22, (0, 255, 255))

    # 측정 점 — BEV 위에 표시
    for i, pt in enumerate(meas or []):
        cv2.circle(bev, (int(pt[0]), int(pt[1])), 5, (0, 255, 255), -1, cv2.LINE_AA)
    if meas and len(meas) >= 2:
        cv2.line(bev, (int(meas[0][0]), int(meas[0][1])),
                 (int(meas[1][0]), int(meas[1][1])), (0, 255, 255), 2, cv2.LINE_AA)

    ok, why = quad_is_sane(q, st["size"][0], st["size"][1])
    sc = bh / float(src.shape[0])
    src_small = cv2.resize(src, (int(src.shape[1] * sc), bh))
    both = np.hstack([src_small, bev])
    if width and both.shape[1] > width:
        s2 = width / float(both.shape[1])
        both = cv2.resize(both, (width, int(round(both.shape[0] * s2))))
    okj, buf = cv2.imencode(".jpg", both, [cv2.IMWRITE_JPEG_QUALITY, 84])
    meta = {"verticality_deg": None if dev != dev else round(dev, 2),
            "lines": nline, "sane": ok, "why": why,
            "src_w": src.shape[1], "src_h": src.shape[0],
            "panel_w": src_small.shape[1], "bev_w": bw, "bev_h": bh,
            "bev_width_m": round(bw * px2m, 3)}
    return (buf.tobytes() if okj else None), meta


def pick_frames(run_dir, where, limit):
    """조건에 맞는 프레임 목록 + 플래그별 집계."""
    import sys
    sys.path.insert(0, str(ROOT))
    from tb.contract import load as _load          # noqa: PLC0415
    from tb.harvest import read_signals, select, summarize   # noqa: PLC0415
    rows = read_signals(run_dir)
    picked = select(rows, where)
    total = len(picked)
    if limit and total > limit:
        step = total / float(limit)
        picked = [picked[int(i * step)] for i in range(limit)]
    # 이 런이 쓴 계약을 이름으로 찾는다 (그냥 첫 파일을 쓰면 엉뚱한 flag_bits 가 붙는다)
    want = ((_read_json(Path(run_dir) / "summary.json", {}) or {})
            .get("summary", {}).get("meta", {}).get("contract"))
    contract = None
    for f in sorted((ROOT / "contracts").glob("*.yaml")):
        try:
            c = _load(f)
        except Exception:      # noqa: BLE001
            continue
        if contract is None:
            contract = c
        if want and c.name == want:
            contract = c
            break
    return {
        "total_rows": len(rows), "matched": total, "shown": len(picked),
        "counts": summarize(picked, contract),
        "frames": [{"frame": int(r["frame"]),
                    "flags": r.get("flags"), "conf_raw": r.get("conf_raw"),
                    "theta_deg": r.get("theta_deg"), "cte_rear_m": r.get("cte_rear_m"),
                    "lane_width_m": r.get("lane_width_m")}
                   for r in picked if isinstance(r.get("frame"), (int, float))],
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
       "log": None, "run_dir": None}
JOB_LOCK = threading.Lock()

# 실행할 수 있는 작업과 그 인자 — ★화이트리스트★. 여기 없으면 거부한다.
ALLOWED_JOBS = {
    "run":       ["--scenario", "--variant", "--tag", "--record-debug", "--watch"],
    "inject":    ["--scenario"],
    "harvest":   ["--where", "--limit", "--out", "--width"],
    "render":    ["--frames", "--where", "--limit", "--width", "--out", "--scenario",
                  "--mp4", "--fps"],
    "reanalyze": ["--scenario", "--contract"],
    "baseline":  ["--name", "--force"],
    "compare":   ["--scenario", "--contract"],
    "doctor":    ["--scenario", "--contract"],
}
# 인자 없이 도는 짧은 점검 — 결과를 그 자리에서 돌려준다
QUICK = {
    "doctor":   ["python3", "-m", "tb.run", "doctor"],
    "selftest": ["python3", "-m", "tb.selftest"],
    # 돌고 있는 ROS 그래프에서 계약 초안을 뽑는다 (대상 시스템이 떠 있어야 한다)
    "discover": ["python3", "-m", "tb.discover", "--seconds", "6"],
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


def start_job(kind, argv):
    """`python3 -m tb.run <kind> …` 를 띄운다. 인자는 화이트리스트로 거른다."""
    if kind not in ALLOWED_JOBS:
        return None, f"허용되지 않은 작업입니다: {kind}"
    with JOB_LOCK:
        if job_running():
            return None, "이미 실행 중인 작업이 있습니다"
        clean, i = [], 0
        allowed = ALLOWED_JOBS[kind]
        while i < len(argv):
            a = str(argv[i])
            if a.startswith("--"):
                if a not in allowed:
                    return None, f"허용되지 않은 인자입니다: {a}"
                clean.append(a)
                if i + 1 < len(argv) and not str(argv[i + 1]).startswith("--"):
                    v = str(argv[i + 1])
                    if any(c in v for c in ";|&`$\n"):
                        return None, "인자에 셸 특수문자가 들어 있습니다"
                    clean.append(v)
                    i += 1
            elif not clean:
                if any(c in a for c in ";|&`$\n"):
                    return None, "인자에 셸 특수문자가 들어 있습니다"
                clean.append(a)          # 위치 인자 (harvest 의 런 이름)
            i += 1

        logf = ROOT / "runs" / f"_job_{kind}.log"
        logf.parent.mkdir(exist_ok=True)
        fh = open(logf, "w")
        proc = subprocess.Popen(
            ["python3", "-m", "tb.run", kind] + clean,
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
            env=dict(os.environ), start_new_session=True)
        JOB.update({"proc": proc, "kind": kind, "args": clean,
                    "started": time.time(), "log": str(logf), "run_dir": None})
        return proc, None


def job_status():
    p = JOB.get("proc")
    st = {"kind": JOB.get("kind"), "args": JOB.get("args"),
          "running": job_running(),
          "elapsed_s": round(time.time() - JOB["started"], 1) if JOB.get("started") else 0}
    if p is not None and p.poll() is not None:
        st["returncode"] = p.returncode
    # 진행 중인 런 디렉터리를 찾아 진행률·라이브 이미지를 붙인다
    newest = None
    ds = _run_dirs()
    if ds:
        newest = max(ds, key=lambda d: d.stat().st_mtime)
    if newest is not None:
        st["run"] = newest.name
        pr = _read_json(newest / "progress.json")
        if pr:
            st["progress"] = pr
        st["has_live"] = (newest / "latest.jpg").exists()
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

    # ── 라우팅 ─────────────────────────────────────────────────────
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
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
        if route == "status":
            return self._json(job_status())
        if route == "calib":
            st = calib_state()
            return self._json(st) if st else self._err(404, "계약에 calibration 항목이 없습니다")
        if route == "calib/view":
            try:
                quad = [float(x) for x in q.get("quad", [""])[0].split(",")]
                frame = int(q.get("frame", ["0"])[0])
                px2m = float(q.get("px2m", ["0.006"])[0])
                video = unquote(q.get("video", [""])[0])
                undist = q.get("undistort", ["1"])[0] != "0"
                width = int(q.get("w", ["1400"])[0])
                meas = []
                for part in (q.get("meas", [""])[0] or "").split(";"):
                    if not part:
                        continue
                    a, b2 = part.split(",")
                    meas.append((float(a), float(b2)))
            except (ValueError, IndexError):
                return self._err(400, "값이 잘못됐습니다")
            if len(quad) != 8 or not video or not Path(video).exists():
                return self._err(400, "quad 값 8개와 실제로 있는 영상이 필요합니다")
            try:
                b, meta = calib_view(video, frame, quad, px2m, undist, width, meas)
            except Exception as e:              # noqa: BLE001
                return self._err(500, f"{type(e).__name__}: {e}")
            if not b:
                return self._err(404, "그 프레임을 읽지 못했습니다")
            import base64
            return self._json({"img": base64.b64encode(b).decode(), "meta": meta})
        if route.startswith("quick/"):
            name = route.split("/", 1)[1]
            cmd = QUICK.get(name)
            if not cmd:
                return self._err(404, f"그런 점검이 없습니다: {name}")
            extra = []
            if name == "doctor" and q.get("scenario"):
                sc = q["scenario"][0]
                if not any(c in sc for c in ";|&`$\n"):
                    extra = ["--scenario", sc]
            try:
                r = subprocess.run(cmd + extra, cwd=str(ROOT), capture_output=True,
                                   text=True, timeout=180, env=dict(os.environ))
            except subprocess.TimeoutExpired:
                return self._err(504, "점검이 제한 시간 안에 끝나지 않았습니다")
            return self._json({"name": name, "rc": r.returncode,
                               "out": (r.stdout or "") + (r.stderr or "")})
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
            if sub in ("report", "compare"):
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
            if sub == "log":
                nm = q.get("name", ["perception"])[0]
                if not _SAFE.match(nm):
                    return self._err(400, "이름이 잘못됐습니다")
                f = d / f"{nm}.log"
                return (self._text(f.read_text(errors="replace")) if f.exists()
                        else self._err(404, "로그가 없습니다"))
        return self._err(404, "없는 API 주소입니다")


def _do_post(self):
    u = urlparse(self.path)
    if not u.path.startswith("/api/"):
        return self._err(404, "없는 주소입니다")
    route = u.path[5:]
    try:
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
    except (ValueError, json.JSONDecodeError):
        return self._err(400, "요청 본문이 잘못됐습니다")

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
    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"테스트베드 웹 뷰어 → {url}")
    print(f"  실행 {len(list_runs())}개 · 베이스라인 {len(list_baselines())}개")
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
