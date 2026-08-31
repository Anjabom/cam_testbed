"""설정 읽기·쓰기 — 웹앱의 "등록" 화면이 쓰는 엔진.

★왜 엔진에 두는가★ 시나리오 하나가 실제로 무엇을 쓰는지(어느 계약 → 어느
워크스페이스 → 어느 영상 파일)를 푸는 규칙은 이미 `tb.run` 에 있다. 웹앱이
그 규칙을 JS 로 한 벌 더 쓰면 반드시 어긋난다. 그래서 여기서 한 번만 풀고
화면은 결과를 표시만 한다.

쓰기는 ★주석을 보존한다★. local.yaml·계약 파일에는 "왜 이렇게 뒀는지"가
주석으로 남아 있는데 yaml.safe_dump 로 왕복시키면 전부 날아간다. 그래서
YAML 로 파싱해 검증하고, 실제 수정은 해당 줄만 텍스트로 갈아 끼운다.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def _run():
    from . import run as r          # 순환 import 를 피해 늦게 부른다
    return r


# ══════════════════════════════════════════════════════════════════
#  읽기
# ══════════════════════════════════════════════════════════════════
def _video_info(path):
    """영상이 실제로 열리는가 · 몇 프레임인가. 등록 전에 확인시켜 준다."""
    p = Path(str(path)).expanduser()
    out = {"path": str(p), "exists": p.is_file()}
    if not out["exists"]:
        return out
    out["size_mb"] = round(p.stat().st_size / 1e6, 1)
    try:
        import cv2
        cap = cv2.VideoCapture(str(p))
        if cap.isOpened():
            out["frames"] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            out["w"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            out["h"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            out["fps"] = round(cap.get(cv2.CAP_PROP_FPS), 2)
            out["openable"] = out["frames"] > 0
        else:
            out["openable"] = False
        cap.release()
    except Exception:                            # noqa: BLE001
        out["openable"] = None
    return out


def video_info(path):
    """영상 한 개의 상태 — 화면이 「이 파일이 열리긴 하는가」를 물을 때."""
    return _video_info(path)


_DECOR_RE = re.compile(r"^#\s*[═─━=\-]{5,}\s*$")


def _scenario_summary(text):
    """머리말 첫 설명 줄 — 테스트 준비의 «본 떠서» 목록에 파일명 대신 보여준다.

    새 필드를 만들지 않는다 — 시나리오 파일은 이미 첫머리에 사람이 쓴 한 줄
    설명을 관행으로 달고 있다(★제목★ 박스로 감싼 것도 있다). 그걸 그대로 쓴다.
    """
    for line in text.splitlines()[:6]:
        if not line.startswith("#"):
            break
        if _DECOR_RE.match(line):
            continue
        s = line.lstrip("#").strip()
        if s:
            return s[:120]
    return ""


def snapshot():
    """등록 화면이 통째로 그리는 데 필요한 현재 상태."""
    r = _run()
    loc = r.local_overrides()
    from .contract import load as load_contract

    contracts = []
    for f in sorted((ROOT / "contracts").glob("*.yaml")):
        ent = {"file": f.name, "name": f.stem, "ok": False}
        try:
            c = load_contract(f)
            ws = Path(c.workspace) if c.workspace else None
            ent.update({
                "name": c.name, "attach": c.attach,
                "workspace": str(c.workspace or ""),
                "ws_exists": bool(ws and ws.is_dir()),
                "setup_ok": bool(ws and (ws / "install" / "setup.bash").exists()),
                "nodes": [f"{n.get('package')}/{n.get('executable')}" for n in c.nodes],
                "image_topic": c.image_topic, "signals": len(c.signals),
                #  ★이름까지★ 준다 — 새 워크스페이스는 물려받을 판정이 없어서 사람이
                #  checks: 를 손으로 쓴다. 그때 필요한 유일한 정보가 이 목록이다.
                "signal_names": sorted(c.signals), "ok": True,
                #  discover 초안 그대로면 `ros2 run TODO TODO` 다 — resolve_scenario 가
                #  실행 직전에 block 하지만, 시나리오를 ★만드는 자리★에서 미리 보여 준다.
                "draft": bool(str(c.sync_topic) == "TODO" or any(
                    "TODO" in (str(n.get("package")), str(n.get("executable")))
                    for n in c.nodes)),
            })
        except Exception as e:                   # noqa: BLE001
            ent["error"] = str(e)
        contracts.append(ent)

    videos = {}
    for k, v in (loc.get("videos") or {}).items():
        videos[str(k)] = _video_info(v)

    scenarios = []
    for f in sorted((ROOT / "scenarios").glob("*.yaml")):
        text = f.read_text()
        try:
            sc = yaml.safe_load(text) or {}
        except Exception as e:                   # noqa: BLE001
            scenarios.append({"file": f.name, "error": str(e)})
            continue
        scenarios.append({
            "file": f.name, "name": sc.get("name", f.stem),
            "contract": sc.get("contract", ""), "video": sc.get("video", ""),
            "mode": sc.get("mode", "lockstep"),
            "start": sc.get("start", 0), "limit": sc.get("limit", 0),
            "variants": [v.get("name") for v in (sc.get("variants") or [])],
            "summary": _scenario_summary(text),
        })

    # 화면이 처음 고를 것 — 알파벳순 첫 파일(demo_foreign)이 뽑히면 곤란하다.
    #   1) 기준(baseline)이 등록된 시나리오 = 평소 돌리는 회귀 루프
    #   2) local.yaml 의 default_contract 를 쓰는 시나리오
    #   3) 그냥 첫 번째
    have_bl = {f.stem for f in (ROOT / "baselines").glob("*.json")}
    dc = Path(str(loc.get("default_contract") or "")).name
    suggest = next((x["file"] for x in scenarios if x.get("name") in have_bl), None)
    if suggest is None and dc:
        suggest = next((x["file"] for x in scenarios
                        if Path(str(x.get("contract") or "")).name == dc), None)
    if suggest is None:
        suggest = next((x["file"] for x in scenarios if not x.get("error")), "")

    return {"root": str(ROOT), "contracts": contracts, "videos": videos,
            "scenarios": scenarios, "suggest": suggest,
            "default_contract": loc.get("default_contract", ""),
            "video_override": loc.get("video", ""),
            "has_local": (ROOT / "local.yaml").exists()}


#  빌드보다 새로운 소스 — 고쳐 놓고 빌드를 잊으면 ★옛 바이너리★가 돈다.
#  colcon 은 빌드할 때마다 install/setup.bash 를 다시 쓴다 → 그것을 빌드 시각으로 본다.
#  (install/ 전체를 훑지 않는 이유: 패키지 디렉터리 mtime 은 안쪽 파일을 고쳐도 안 바뀐다)
SRC_EXT = {".py", ".cpp", ".cc", ".c", ".hpp", ".h", ".xml", ".yaml", ".launch", ".cfg"}


#  ★빌드가 정말 필요한 것과 아닌 것★ `--symlink-install` 이면 패키지의 파이썬 모듈은
#  build/<pkg>/<pkg> 가 src 를 가리키는 ★심볼릭 링크★ 라 고치면 바로 반영된다. 그래서
#  "소스가 빌드보다 새롭다"만으로 빌드하라고 하면 ★대부분 거짓 경보★ 다(코드를 고칠
#  때마다 뜬다). 다시 빌드해야 하는 것은 링크로 해결되지 않는 것들뿐이다:
#    · setup.py    entry_points 가 바뀌면 console_scripts 를 다시 만들어야 한다
#    · package.xml / CMakeLists.txt  의존성·빌드 규칙
#    · C/C++ 소스  컴파일 산출물이 install 에 들어간다
REBUILD_NAMES = {"setup.py", "package.xml", "setup.cfg", "CMakeLists.txt"}
REBUILD_EXT = {".cpp", ".cc", ".c", ".hpp", ".h"}


def needs_rebuild(paths):
    """이 중 ★정말 빌드가 필요한★ 것만. 나머지는 심볼릭 링크로 그대로 반영된다."""
    out = []
    for s in paths:
        f = Path(s)
        if f.name in REBUILD_NAMES or f.suffix in REBUILD_EXT:
            out.append(s)
    return out


def stale_sources(ws, limit=5):
    """install/setup.bash 보다 새로운 소스 파일들 (상대 경로, limit 개까지)."""
    setup = Path(ws) / "install" / "setup.bash"
    src = Path(ws) / "src"
    if not setup.exists() or not src.is_dir():
        return []
    t = setup.stat().st_mtime
    out = []
    for f in src.rglob("*"):
        if f.suffix not in SRC_EXT or not f.is_file():
            continue
        try:
            if f.stat().st_mtime > t:
                out.append(str(f.relative_to(src)))
        except OSError:
            pass
    out.sort()
    return out[:limit] if limit else out


def resolve_scenario(fname):
    """★이 시나리오를 지금 돌리면 실제로 무엇이 쓰이는가★.

    화면이 "영상 등록을 안 했다"는 것을 실행 전에 알려 주기 위한 것이다.
    """
    r = _run()
    from .contract import load as load_contract
    p = ROOT / "scenarios" / fname
    if not p.is_file():
        return {"error": f"시나리오가 없다: {fname}"}
    sc = yaml.safe_load(p.read_text()) or {}
    loc = r.local_overrides()
    out = {"file": fname, "name": sc.get("name", p.stem),
           "mode": sc.get("mode", "lockstep"),
           "start": sc.get("start", 0), "limit": sc.get("limit", 0),
           "sim_time": bool(sc.get("sim_time")),
           "variants": [v.get("name", "base") for v in (sc.get("variants") or [])] or ["base"],
           "warn": [], "block": []}

    try:
        cpath = r._resolve_contract(sc.get("contract"))
    except SystemExit as e:
        out["block"].append(str(e))
        return out
    if cpath is None or not Path(cpath).exists():
        out["block"].append(f"계약 파일이 없다: {cpath}")
        return out
    out["contract_file"] = str(Path(cpath).name)
    try:
        c = load_contract(cpath)
    except Exception as e:                       # noqa: BLE001
        out["block"].append(f"계약을 읽을 수 없다: {e}")
        return out

    out["contract"] = c.name
    out["attach"] = c.attach
    out["workspace"] = str(c.workspace or "")
    out["nodes"] = [
        {"id": n.get("id"),
         "cmd": f"ros2 run {n.get('package')} {n.get('executable')}"}
        for n in c.nodes]
    # discover 로 뽑은 초안을 그대로 돌리면 `ros2 run TODO TODO` 가 된다.
    # 실행하기 전에 여기서 막는다 — 로그를 뒤지게 만들 이유가 없다.
    todo = [n["id"] for n in c.nodes
            if "TODO" in (str(n.get("package")), str(n.get("executable")))]
    if todo or str(c.sync_topic) == "TODO":
        out["block"].append(
            f"계약 `{Path(cpath).name}` 이 아직 초안이다 — TODO 를 채워야 한다"
            + (f" (노드: {', '.join(todo)})" if todo else "")
            + ("; sync_topic 도 비어 있다" if str(c.sync_topic) == "TODO" else ""))

    if c.attach:
        out["warn"].append("attach 모드 — 노드를 띄우지 않고 돌고 있는 시스템에 붙는다")
    elif not c.workspace:
        out["block"].append("계약에 workspace: 가 없다 — 절대 경로로 적어야 한다")
    elif not Path(c.workspace).is_dir():
        out["block"].append(f"워크스페이스가 없다: {c.workspace}")
    elif not (Path(c.workspace) / "install" / "setup.bash").exists():
        out["block"].append(f"빌드가 안 돼 있다 — {c.workspace}/install/setup.bash 가 없다. "
                            "대상 워크스페이스에서 `colcon build` 를 먼저 한다")
    else:
        stale = stale_sources(c.workspace)
        hard = needs_rebuild(stale)
        if hard:
            out["warn"].append(
                "★빌드가 필요한 파일이 바뀌었다★ — 지금 돌리면 고치기 ★전★ 것이 돈다. "
                f"«워크스페이스 빌드» 를 먼저 누를 것 ({', '.join(hard)}"
                + (" …" if len(hard) >= 5 else "") + ")")
        elif stale:
            #  파이썬 모듈은 심볼릭 링크라 그대로 반영된다 — 빌드하라고 하지 않는다.
            #  그래도 알려는 주는 것은 "내가 고친 게 맞나"를 확인시켜 주기 때문이다.
            out["warn"].append(
                "소스가 빌드보다 새롭다 — 파이썬 모듈은 심볼릭 링크라 ★그대로 반영된다★ "
                f"(빌드 불필요): {', '.join(stale)}"
                + (" …" if len(stale) >= 5 else ""))

    key = loc.get("video") or sc.get("video") or ""
    out["video_key"] = key
    out["video_from_local"] = bool(loc.get("video"))
    if not key:
        if not c.attach:
            out["block"].append("시나리오에 video: 가 없다")
    else:
        vpath = r.resolve_video(sc, loc)
        out["video"] = _video_info(vpath) if vpath else {}
        known = key in (loc.get("videos") or {})
        out["video_registered"] = known
        if not known:
            out["warn"].append(
                f"`{key}` 가 local.yaml 의 videos: 에 없어 경로로 그대로 해석했다 — "
                "논리 이름으로 등록해 두면 머신이 바뀌어도 시나리오를 안 고친다")
        if not out["video"].get("exists"):
            out["block"].append(f"영상 파일이 없다: {vpath}")
        elif out["video"].get("openable") is False:
            out["block"].append(f"영상을 열 수 없다(코덱): {vpath}")
        elif out["limit"] and out["start"] + out["limit"] > out["video"].get("frames", 0):
            out["warn"].append(
                f"start+limit({out['start'] + out['limit']}) 가 영상 길이"
                f"({out['video'].get('frames')})를 넘는다 — 짧게 끝난다")

    #  ★이름 오타는 실행 뒤에 ⚠️ 로만 드러난다★ — checks 의 신호 이름을 하나
    #  잘못 적으면 그 판정이 조용히 사라진 채 리포트가 초록으로 나온다. 런까지
    #  가기 전에 여기서 묻는다(판정을 막지는 않으므로 block 이 아니라 warn 이다).
    from .lint import lint
    out["warn"] += [f"이름이 안 맞는다 — {m}" for m in lint(c, sc, r.load_ws_params(c))]

    params = r._deep_merge(sc.get("params", {}), loc.get("params", {}))
    out["params"] = params
    #  ★어느 파일에서 온 값인가★ — 화면이 「고치면 어디가 바뀌는지」를 말할 수 있게.
    #  둘 다에 있으면 local 이 이긴다(우선순위가 시나리오 < local 이다).
    out["params_local"] = loc.get("params", {}) or {}
    out["cmd"] = f"python3 -m tb.run run --scenario scenarios/{fname}"
    return out


# ══════════════════════════════════════════════════════════════════
#  쓰기 — 주석을 보존한다
# ══════════════════════════════════════════════════════════════════
def _keep_comment(old_line, new_body):
    """줄을 갈아 끼우되 뒤에 달린 주석과 그 정렬 간격을 그대로 살린다."""
    i = old_line.find("#")
    if i < 0:
        return new_body
    body, cmt = old_line[:i], old_line[i:]
    pad = len(body) - len(body.rstrip())
    return new_body + " " * max(2, pad) + cmt


def _local_text():
    p = ROOT / "local.yaml"
    if p.exists():
        return p.read_text()
    ex = ROOT / "local.yaml.example"
    return ex.read_text() if ex.exists() else "# 머신마다 다른 것들\n"


def _write_local(text):
    yaml.safe_load(text)                          # 깨진 YAML 을 저장하지 않는다
    (ROOT / "local.yaml").write_text(text)


def set_video(name, path):
    """local.yaml 의 videos: 에 논리 이름 하나를 등록·수정한다."""
    name = str(name).strip()
    if not NAME_RE.match(name):
        raise ValueError("이름은 영문·숫자·_·-·. 만 쓸 수 있다")
    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise ValueError(f"그런 파일이 없다: {p}")
    info = _video_info(p)
    if info.get("openable") is False:
        raise ValueError(f"영상을 열 수 없다(코덱/손상): {p}")

    lines = _local_text().splitlines()
    entry = f"  {name}: {p}"
    vi = next((i for i, ln in enumerate(lines)
               if re.match(r"^videos:\s*(#.*)?$", ln)), None)
    if vi is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines += ["videos:", entry]
    else:
        end = vi + 1
        while end < len(lines) and (not lines[end].strip()
                                    or lines[end].startswith((" ", "\t"))):
            end += 1
        hit = next((i for i in range(vi + 1, end)
                    if re.match(rf"^\s+{re.escape(name)}\s*:", lines[i])), None)
        if hit is not None:
            lines[hit] = _keep_comment(lines[hit], entry)
        else:
            while end > vi + 1 and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, entry)
    _write_local("\n".join(lines) + "\n")
    return info


def del_video(name):
    lines = _local_text().splitlines()
    keep = [ln for ln in lines if not re.match(rf"^\s+{re.escape(name)}\s*:", ln)]
    if len(keep) == len(lines):
        raise ValueError(f"등록돼 있지 않다: {name}")
    _write_local("\n".join(keep) + "\n")


CONTRACT_TMPL = """\
# ══════════════════════════════════════════════════════════════════
#  계약 — 테스트베드가 이 워크스페이스를 아는 ★유일한★ 통로
# ══════════════════════════════════════════════════════════════════
#  ★아직 절반만 채워져 있다.★ 아래 TODO 를 채워야 실행된다.
#  대상 시스템을 평소처럼 띄워 놓고 점검 탭의 `discover` 를 돌리면
#  토픽·타입·필드 배치를 읽어 signals: 초안을 만들어 준다.
version: 1
name: {name}

# 이 워크스페이스의 install/setup.bash 를 source 해서 노드를 띄운다.
workspace: {workspace}

# 띄울 노드 — import 하지 않고 `ros2 run` 으로 띄운다.
nodes:
  - id: TODO
    package: TODO
    executable: TODO
    node_name: TODO
    params: {{}}

# 영상을 밀어 넣을 토픽. 기록만 할 거면 "" 로 두고 attach: true 를 켠다.
stimulus:
  image_topic: /image_raw

# "한 프레임 처리가 끝났다"를 알리는 토픽 (lockstep 의 기준)
sync_topic: TODO

observe: []
signals: {{}}
compare_signals: []
compare_categorical: []
compare_sequence: []
hold_signals: []
mirror_odd_signals: []
"""


def new_contract(name, workspace, attach=False):
    if not NAME_RE.match(str(name)):
        raise ValueError("이름은 영문·숫자·_·-·. 만 쓸 수 있다")
    ws = Path(str(workspace)).expanduser()
    f = ROOT / "contracts" / f"{name}.yaml"
    if f.exists():
        raise ValueError(f"이미 있다: contracts/{f.name}")
    warn = []
    if not attach:
        if not ws.is_dir():
            raise ValueError(f"그런 디렉터리가 없다: {ws}")
        if not (ws / "install" / "setup.bash").exists():
            warn.append(f"{ws}/install/setup.bash 가 없다 — 대상에서 `colcon build` 필요")
    text = CONTRACT_TMPL.format(name=name, workspace=ws)
    if attach:
        text = text.replace("workspace: ", "attach: true\nworkspace: ", 1)
    yaml.safe_load(text)
    f.write_text(text)
    return {"file": f.name, "warn": warn}


def set_contract_workspace(fname, workspace):
    f = ROOT / "contracts" / fname
    if not f.is_file():
        raise ValueError(f"그런 계약이 없다: {fname}")
    ws = Path(str(workspace)).expanduser()
    if not ws.is_dir():
        raise ValueError(f"그런 디렉터리가 없다: {ws}")
    lines = f.read_text().splitlines()
    hit = next((i for i, ln in enumerate(lines) if re.match(r"^workspace\s*:", ln)), None)
    if hit is None:
        lines.insert(0, f"workspace: {ws}")
    else:
        lines[hit] = _keep_comment(lines[hit], f"workspace: {ws}")
    text = "\n".join(lines) + "\n"
    yaml.safe_load(text)
    f.write_text(text)
    return {"workspace": str(ws),
            "setup_ok": (ws / "install" / "setup.bash").exists()}


SCEN_TMPL = """\
# 시나리오 — ★무엇을 어떻게 돌릴지★. 계약(대상)과 분리돼 있다.
name: {name}
contract: contracts/{contract}

video: {video}          # ★논리 이름★ — 실제 경로는 local.yaml 의 videos: 에 있다
mode: {mode}            # lockstep(결정적, 회귀용) / realtime(실시간, 타이밍용)
start: {start}          # 시작 프레임
limit: {limit}          # 0 = 끝까지

# 노드 파라미터를 여기서 덮어쓴다 (런치 파일은 건드리지 않는다)
params: {{}}

# ── 판정 ───────────────────────────────────────────────────────────────
#   ★빈 틀로 두면 판정이 0 개라 리포트가 늘 초록이다★ — 그래서 어느 계약에서나
#   성립하는 두 줄만 미리 깔아 둔다. 이 둘은 신호 이름을 안 쓰므로(요약 지표)
#   워크스페이스가 달라도 그대로 유효하다.
#   ⚠️ 여기부터는 사람이 채운다. 신호를 쓰는 판정을 붙일 때는 계약의 signals:
#      에 있는 이름만 쓴다 — 오타는 `tb.run doctor` 가 실행 전에 잡아 준다.
checks:
  - {{stat: drop_rate, max: 0.02,
     why: "lockstep 인데 프레임이 새면 동기 실패 — 판정 이전의 문제다"}}
  - {{stat: latency_p95_ms, max: 1000,
     why: "프레임당 처리 지연. ★이 값은 이 대상에 맞게 실측으로 고칠 것★"}}
"""


def new_scenario(name, contract, video, mode="lockstep", start=0, limit=0):
    if not NAME_RE.match(str(name)):
        raise ValueError("이름은 영문·숫자·_·-·. 만 쓸 수 있다")
    if mode not in ("lockstep", "realtime", "asfast"):
        raise ValueError(f"모드가 잘못됐다: {mode}")
    if not (ROOT / "contracts" / str(contract)).is_file():
        raise ValueError(f"그런 계약이 없다: {contract}")
    f = ROOT / "scenarios" / f"{name}.yaml"
    if f.exists():
        raise ValueError(f"이미 있다: scenarios/{f.name}")
    text = SCEN_TMPL.format(name=name, contract=contract, video=video,
                            mode=mode, start=int(start), limit=int(limit))
    yaml.safe_load(text)
    f.write_text(text)
    #  ★막지는 않는다★ — 새 워크스페이스는 discover 로 계약을 채우기 ★전에★
    #  시나리오부터 만들어 두는 순서가 실제로 있다. 다만 조용히 두면 3·4단계까지
    #  가서야 `ros2 run TODO TODO` 를 만난다.
    warn = []
    try:
        from .contract import load as load_contract
        c = load_contract(ROOT / "contracts" / str(contract))
        if str(c.sync_topic) == "TODO" or any(
                "TODO" in (str(n.get("package")), str(n.get("executable")))
                for n in c.nodes):
            warn.append(f"계약 contracts/{contract} 이 아직 초안이다 — "
                        "«환경 점검» 탭의 discover 로 TODO 를 채워야 돌아간다")
    except Exception as e:                       # noqa: BLE001
        warn.append(f"계약을 읽지 못했다: {e}")
    return {"file": f.name, "warn": warn}


def _substitute_fields(lines, subs, drop_comment=()):
    """`key: value` 줄을 찾아 subs 의 값으로 갈아 끼운다(주석은 보존). 없으면 에러.

    clone_scenario·compose_scenario 가 같이 쓴다 — name/video/mode/start/limit 처럼
    한 줄짜리 스칼라 필드를 손대는 공통 로직이다.

    ★drop_comment★ 는 그 줄의 꼬리 주석까지 지운다. `mode:` 가 그렇다 — 본의 주석은
    "머신 속도와 무관하게 같은 결과"처럼 ★그 모드를 설명하는 말★ 이라, 값만 갈아
    끼우면 realtime 옆에 lockstep 설명이 붙어 거짓말이 된다.
    """
    done = set()
    for i, ln in enumerate(lines):
        m = re.match(r"^([a-z_]+):(\s*)([^#]*?)(\s*#.*)?$", ln)
        if not m:
            continue
        key = m.group(1)
        if key not in subs or key in done:
            continue
        done.add(key)
        tail = '' if key in drop_comment else (m.group(4) or '')
        lines[i] = f"{key}: {subs[key]}{tail}"
    missing = [k for k in subs if k not in done]
    if missing:
        raise ValueError(f"본에 그 항목이 없어 못 바꿨다: {', '.join(missing)}")
    return lines


def clone_scenario(src, name, video, start=None, limit=None, mode=None, note=""):
    """★있는 시나리오를 본으로 떠서★ 새 시나리오를 만든다.

    `new_scenario` 는 빈 틀을 만든다 — 새 워크스페이스를 붙일 때는 그게 맞지만,
    이미 잘 정리된 시험(정지선처럼 판정 20여 개와 그 근거 주석이 들어 있는 것)을
    ★새 영상에 옮길★ 때는 그 판정을 그대로 물려받아야 한다. 그래서 텍스트를 복사하고
    바뀌는 줄(name·video·구간·모드)만 갈아 끼운다 — ★주석이 전부 남는다★.
    """
    src = str(src)
    if not src.endswith(".yaml") or "/" in src or ".." in src:
        raise ValueError(f"본이 될 시나리오 이름이 잘못됐다: {src}")
    sp = ROOT / "scenarios" / src
    if not sp.is_file():
        raise ValueError(f"그런 시나리오가 없다: {src}")
    if not NAME_RE.match(str(name)):
        raise ValueError("이름은 영문·숫자·_·-·. 만 쓸 수 있다")
    if not NAME_RE.match(str(video)):
        raise ValueError("영상 이름은 영문·숫자·_·-·. 만 쓸 수 있다 (논리 이름)")
    out = ROOT / "scenarios" / f"{name}.yaml"
    if out.exists():
        raise ValueError(f"이미 있다: scenarios/{out.name}")

    lines = sp.read_text().splitlines()
    subs = {"name": str(name), "video": str(video)}
    if mode:
        if mode not in ("lockstep", "realtime", "asfast"):
            raise ValueError(f"모드가 잘못됐다: {mode}")
        subs["mode"] = mode
    if start is not None:
        subs["start"] = str(int(start))
    if limit is not None:
        subs["limit"] = str(int(limit))
    lines = _substitute_fields(lines, subs, drop_comment=("mode",) if mode else ())

    head = [f"# ★{name}★ — scenarios/{src} 를 본으로 떠서 만들었다 "
            f"({datetime.now().strftime('%Y-%m-%d %H:%M')})"]
    if note:
        head += ["#   " + ln2 for ln2 in str(note).splitlines() if ln2.strip()]
    head.append("#   ⚠️ 본의 판정 기준을 그대로 물려받았다 — 이 영상에 맞는지 확인할 것.")
    text = "\n".join(head + lines) + "\n"
    yaml.safe_load(text)                       # 깨진 YAML 을 쓰지 않는다
    out.write_text(text)
    return {"file": out.name, "from": src}


def _replace_block(lines, key, new_block):
    """top-level `key:` 블록을 통째로 들어내고 new_block(줄 목록)으로 갈아 끼운다.

    없으면 파일 끝에 붙인다. 합친 결과로 ★통째로 교체★한다(compose_scenario 전용).
    """
    idx = next((i for i, ln in enumerate(lines) if re.match(rf"^{key}\s*:", ln)), None)
    if idx is None:
        return lines + [""] + new_block
    end = _block_end(lines, idx, 0)
    return lines[:idx] + new_block + lines[end:]


def compose_scenario(srcs, name, video, start=None, limit=None, mode=None, note=""):
    """★여러 시나리오의 판정을 합쳐★ 새 시나리오를 만든다.

    "인지 판정 + 개입 판정을 한 영상에 같이" 처럼 목적이 다른 checks: 를 섞고
    싶을 때 쓴다. clone_scenario 는 본 하나의 텍스트(주석까지)를 통째로 베끼지만,
    여기서는 checks:/compare_tol: 을 ★구조로 파싱해 합친다★ — 여러 본을 그대로
    이어 붙이면 겹치는 항목을 어떻게 할지 정할 수 없어서다. 완전히 같은 판정
    (dict 이 동일)은 한 번만 남긴다 — 부팅 안전 판정처럼 여러 본이 그대로 겹치는
    경우가 실제로 있다(이 계약의 stopline_* 4개가 전부 boot_* 로그 판정을 갖는다).

    ★대가★ checks: 항목 자체의 why: 는 그대로 남지만, 그 위에 달린 절 구분
    주석(「── 단계 0 ──」 같은)은 없어진다 — 파싱 후 재조립이라 원문 줄과
    항목의 대응을 알 수 없다. 본 하나만 옮길 때는 clone_scenario 를 쓸 것.
    """
    srcs = [str(s) for s in srcs]
    if len(srcs) < 2:
        raise ValueError("2개 이상을 골라야 «합쳐서» 다 — 하나뿐이면 clone_scenario 를 쓸 것")
    for s in srcs:
        if not s.endswith(".yaml") or "/" in s or ".." in s:
            raise ValueError(f"본이 될 시나리오 이름이 잘못됐다: {s}")
    docs = []
    for s in srcs:
        sp = ROOT / "scenarios" / s
        if not sp.is_file():
            raise ValueError(f"그런 시나리오가 없다: {s}")
        docs.append((s, yaml.safe_load(sp.read_text()) or {}))
    contracts = {d.get("contract") for _, d in docs}
    if len(contracts) > 1:
        raise ValueError(f"본들의 계약이 다르다 — 신호 이름이 안 맞는다: {sorted(contracts)}")
    if not NAME_RE.match(str(name)):
        raise ValueError("이름은 영문·숫자·_·-·. 만 쓸 수 있다")
    if not NAME_RE.match(str(video)):
        raise ValueError("영상 이름은 영문·숫자·_·-·. 만 쓸 수 있다 (논리 이름)")
    out = ROOT / "scenarios" / f"{name}.yaml"
    if out.exists():
        raise ValueError(f"이미 있다: scenarios/{out.name}")

    merged_checks, seen = [], []
    for _, d in docs:
        for c in (d.get("checks") or []):
            if c in seen:
                continue
            seen.append(c)
            merged_checks.append(c)
    if not merged_checks:
        raise ValueError("합칠 판정이 하나도 없다 — 본들의 checks: 가 비어 있다")

    merged_tol, tol_warnings = {}, []
    for s, d in docs:
        for k, v in (d.get("compare_tol") or {}).items():
            if k in merged_tol and not _same(merged_tol[k], v):
                tol_warnings.append(f"compare_tol.{k}: {merged_tol[k]} 를 유지 "
                                    f"({s} 의 {v} 는 버렸다 — 다르면 손으로 확인할 것)")
                continue
            merged_tol[k] = v

    base_src = srcs[0]
    lines = (ROOT / "scenarios" / base_src).read_text().splitlines()
    subs = {"name": str(name), "video": str(video)}
    if mode:
        if mode not in ("lockstep", "realtime", "asfast"):
            raise ValueError(f"모드가 잘못됐다: {mode}")
        subs["mode"] = mode
    if start is not None:
        subs["start"] = str(int(start))
    if limit is not None:
        subs["limit"] = str(int(limit))
    lines = _substitute_fields(lines, subs, drop_comment=("mode",) if mode else ())

    checks_block = ["checks:"] + ["  - " + _yv(_order_check(c)) for c in merged_checks]
    lines = _replace_block(lines, "checks", checks_block)
    if merged_tol:
        tol_block = ["compare_tol:"] + [f"  {k}: {_yv(v)}" for k, v in merged_tol.items()]
        lines = _replace_block(lines, "compare_tol", tol_block)

    head = [f"# ★{name}★ — {', '.join(srcs)} 를 합쳐서 만들었다 "
            f"({datetime.now().strftime('%Y-%m-%d %H:%M')})"]
    if note:
        head += ["#   " + ln2 for ln2 in str(note).splitlines() if ln2.strip()]
    head.append(f"#   ⚠️ 판정 {len(merged_checks)}개를 물려받았다(절 구분 주석은 요약됐다,")
    head.append("#      각 판정의 why: 는 남아 있다) — 이 영상·이 계약에 맞는지 확인할 것.")
    if tol_warnings:
        head.append("#   ⚠️ compare_tol 충돌 (본들끼리 값이 달랐다):")
        head += [f"#     - {w}" for w in tol_warnings]
    text = "\n".join(head + lines) + "\n"
    parsed = yaml.safe_load(text)                  # 깨진 YAML 을 쓰지 않는다
    if len(parsed.get("checks") or []) != len(merged_checks):
        raise ValueError(f"{name} 의 checks: 를 예상대로 못 합쳤다.")
    out.write_text(text)
    return {"file": out.name, "from": srcs, "warnings": tol_warnings}


# ══════════════════════════════════════════════════════════════════════
#  판정을 ★다른 계약으로★ 옮기기 — 이름이 안 맞는 것을 먼저 보여 준다
# ══════════════════════════════════════════════════════════════════════
#  ★왜 clone/compose 로는 안 되는가★ 둘은 본의 `contract:` 줄을 그대로 물려받는다
#  (compose 는 본들의 계약이 다르면 아예 거부한다). 즉 "A 계약의 판정을 B 계약에
#  붙인다"는 표현할 수가 없었다.
#
#  ★왜 그냥 복사하면 안 되는가★ checks: 의 신호 이름은 계약이 정의한다. B 에 없는
#  이름을 쓰면 `_stat_value` 가 None 을 내고 리포트에 ⚠️ 「값 없음」 으로 남는다 —
#  ★실패가 아니다★. 20개를 물려받아 3개만 살아 있는데 전부 초록으로 보인다.
#  그래서 여기서는 옮기기 ★전에★ tb.lint 를 돌려 한 줄씩 성립 여부를 보여 주고,
#  사람이 고른 것만 쓴다.
#
#  ★그래도 남는 것★ 이름이 맞아도 ★문턱(숫자)은 그 차량·그 영상 것★이다.
#  머리말에 그 경고를 박는다 — 느슨하게 고쳐 통과시키는 것은 금지다(CLAUDE.md).

def _load_srcs(srcs):
    """본 목록을 [(파일명, dict)] 로 읽는다 — 이름 검사도 여기서."""
    out = []
    for s in [str(x) for x in (srcs or [])]:
        if not s.endswith(".yaml") or "/" in s or ".." in s:
            raise ValueError(f"본이 될 시나리오 이름이 잘못됐다: {s}")
        sp = ROOT / "scenarios" / s
        if not sp.is_file():
            raise ValueError(f"그런 시나리오가 없다: {s}")
        out.append((s, yaml.safe_load(sp.read_text()) or {}))
    return out


def _graft_items(srcs, contract):
    """본들의 판정을 ★순서대로★ 편다 — 완전히 같은 판정은 한 번만.

    preview_checks 와 graft_scenario 가 ★같은 순서★ 를 봐야 한다. 화면이 돌려주는
    keep 은 이 목록의 인덱스이기 때문이다 — 두 곳에서 따로 세면 엉뚱한 판정이 남는다.
    """
    from . import lint as _lint
    from .contract import load as load_contract

    cp = ROOT / "contracts" / str(contract)
    if not str(contract).endswith(".yaml") or "/" in str(contract) or ".." in str(contract):
        raise ValueError(f"계약 이름이 잘못됐다: {contract}")
    if not cp.is_file():
        raise ValueError(f"그런 계약이 없다: {contract}")
    c = load_contract(cp)

    items, seen = [], []
    for s, d in _load_srcs(srcs):
        for chk in (d.get("checks") or []):
            if chk in seen:
                continue
            seen.append(chk)
            #  lint_scenario 는 `checks[0]: …` 로 태그를 단다 — 한 개씩 물어서
            #  그 태그를 떼면 그대로 사람이 읽을 문장이 된다.
            probs = [p.split(": ", 1)[-1]
                     for p in _lint.lint_scenario(c, {"checks": [chk]})]
            items.append({"i": len(items), "src": s, "check": chk,
                          "yaml": _yv(_order_check(chk)), "problems": probs})
    return c, items


def preview_checks(srcs, contract):
    """옮기기 전에 묻는다 — 이 판정들이 저 계약에서 성립하는가.

    돌려주는 것: items[] (i · src · yaml 한 줄 · problems[]) 와 계약의 신호 이름.
    problems 가 비어 있으면 그 판정은 새 계약에서 그대로 판정된다.
    """
    c, items = _graft_items(srcs, contract)
    return {"contract": str(contract), "signals": sorted(c.signals),
            "items": [{k: v for k, v in it.items() if k != "check"} for it in items],
            "ok_count": sum(1 for it in items if not it["problems"]),
            "total": len(items)}


def graft_scenario(srcs, name, contract, video, keep=None, mode="lockstep",
                   start=0, limit=0, note=""):
    """고른 판정만 ★새 계약의★ 시나리오로 옮겨 심는다.

    keep 은 preview_checks 가 준 `i` 의 목록이다. 안 주면 ★깨끗한 것만★ 남긴다.
    본의 텍스트를 베끼지 않고 빈 틀(SCEN_TMPL)에서 시작한다 — 본의 주석은 본의
    계약을 설명하는 말이라 새 계약 위에서는 거짓말이 되기 때문이다. 대신 판정마다
    ★어느 본에서 왔는지★ 를 꼬리 주석으로 남긴다.
    """
    if mode not in ("lockstep", "realtime", "asfast"):
        raise ValueError(f"모드가 잘못됐다: {mode}")
    if not NAME_RE.match(str(name)):
        raise ValueError("이름은 영문·숫자·_·-·. 만 쓸 수 있다")
    if not NAME_RE.match(str(video)):
        raise ValueError("영상 이름은 영문·숫자·_·-·. 만 쓸 수 있다 (논리 이름)")
    out = ROOT / "scenarios" / f"{name}.yaml"
    if out.exists():
        raise ValueError(f"이미 있다: scenarios/{out.name}")

    c, items = _graft_items(srcs, contract)
    if keep is None:
        picked = [it for it in items if not it["problems"]]
    else:
        want = {int(k) for k in keep}
        bad = want - {it["i"] for it in items}
        if bad:
            #  화면이 본 목록과 지금 파일이 어긋났다는 뜻이다 — 조용히 빼면
            #  ★사람이 고른 판정이 없는 채로★ 시나리오가 만들어진다.
            raise ValueError(f"고른 판정 번호가 목록에 없다: {sorted(bad)} — 화면을 새로 고칠 것")
        picked = [it for it in items if it["i"] in want]
    if not picked:
        raise ValueError("옮길 판정이 하나도 없다 — 하나 이상 골라야 한다")

    #  ★compare_tol 은 이름이 확인된 것만★ 가져온다. 오타·없는 이름이면 허용오차가
    #  조용히 _default 로 떨어져 ★회귀 비교가 통과하는 쪽으로★ 틀린다(lint.py 참고).
    tol, tol_dropped = {}, []
    for s, d in _load_srcs(srcs):
        for k, v in (d.get("compare_tol") or {}).items():
            if k != "_default" and k not in c.signals:
                tol_dropped.append(f"{k} ({s})")
            elif k not in tol:
                tol[k] = v

    lines = SCEN_TMPL.format(name=name, contract=str(contract), video=video,
                             mode=mode, start=int(start), limit=int(limit)).splitlines()
    checks_block = ["checks:"] + [
        f"  - {it['yaml']}    # ← {it['src']}" for it in picked]
    #  ★빈 틀의 안내 주석을 갈아 끼운다★ — SCEN_TMPL 의 그 문단은 "여기부터 사람이
    #  채운다"는 말이라, 판정이 17개 들어찬 파일 위에 남으면 거짓말이 된다.
    #  _replace_block 은 `checks:` 줄부터만 손대므로 그 위 주석은 여기서 지운다.
    ci = next(i for i, ln in enumerate(lines) if ln.startswith("checks:"))
    top = ci
    while top > 0 and (lines[top - 1].startswith("#") or not lines[top - 1].strip()):
        top -= 1
    lines = lines[:top] + [
        "",
        "# ── 판정 ───────────────────────────────────────────────────────────────",
        "#   ★다른 계약에서 옮겨 심은 것이다★ — 꼬리의 `# ←` 가 출처다.",
        "#   이 계약의 signals: 에 있는 이름만 남겼다(나머지는 옮기지 않았다).",
    ] + lines[ci:]
    lines = _replace_block(lines, "checks", checks_block)
    if tol:
        lines = _replace_block(lines, "compare_tol",
                               ["compare_tol:"] + [f"  {k}: {_yv(v)}" for k, v in tol.items()])

    head = [f"# ★{name}★ — {', '.join(str(s) for s in srcs)} 의 판정을 "
            f"contracts/{contract} 로 옮겨 심었다 "
            f"({datetime.now().strftime('%Y-%m-%d %H:%M')})"]
    if note:
        head += ["#   " + ln2 for ln2 in str(note).splitlines() if ln2.strip()]
    head.append(f"#   판정 {len(picked)}/{len(items)} 개를 골랐다 "
                f"(나머지는 이 계약에 없는 이름을 쓴다).")
    head.append("#   ⚠️ 이름은 맞췄지만 ★문턱(숫자)은 본의 차량·영상 것★ 이다 — "
                "이 대상에서 실측해 고칠 것.")
    head.append("#   ⚠️ 통과시키려고 문턱을 느슨하게 하지 않는다. 근거를 먼저 적을 것.")
    if tol_dropped:
        head.append("#   compare_tol 에서 뺀 이름(이 계약에 없다): " + ", ".join(tol_dropped))

    text = "\n".join(head + lines) + "\n"
    parsed = yaml.safe_load(text)                  # 깨진 YAML 을 쓰지 않는다
    if len(parsed.get("checks") or []) != len(picked):
        raise ValueError(f"{name} 의 checks: 를 예상대로 못 옮겼다.")
    out.write_text(text)
    return {"file": out.name, "from": [str(s) for s in srcs],
            "kept": len(picked), "total": len(items),
            "warn": ([f"compare_tol 에서 {len(tol_dropped)}개를 뺐다 — 계약에 없는 이름"]
                     if tol_dropped else [])}


# ══════════════════════════════════════════════════════════════════════
#  params: 쓰기 — 캘리브레이션 화면이 맞춘 값을 되돌려 놓는 곳
# ══════════════════════════════════════════════════════════════════════
def _yv(v):
    """YAML 스칼라 한 개 표기. 리스트는 흐름식으로 한 줄에 둔다."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_yv(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k}: {_yv(x)}" for k, x in v.items()) + "}"
    if isinstance(v, float):
        s = f"{v:.6f}".rstrip("0")
        return s + "0" if s.endswith(".") else s
    if isinstance(v, int) or v is None:
        return "null" if v is None else str(v)
    s = str(v)
    #  ★allow_unicode 필수★ 없으면 한글이 \uXXXX 로 이스케이프된다 — why: 에 한글이
    #  들어가고 나서야 드러난 잠재 버그였다(그전 호출자는 전부 영문·숫자만 줬다).
    #  ★default_style='"' 도 필수★ yaml.safe_dump 는 이 문자열이 ★독립 문서★ 로
    #  쓰일 걸로 보고 콤마가 있어도 안 따옴표를 친다 — 그런데 여기 결과는 손으로
    #  조립한 흐름식 매핑({k: v, k2: v2}) ★안에 끼워 넣는다★. 콤마 있는 문자열이
    #  안 따옴표로 나오면 그 콤마가 다음 키의 구분자로 읽혀 매핑이 깨진다(why: 에
    #  "실측 80, 여유 30%" 처럼 콤마 든 문장을 넣고서야 드러났다). 그래서 이
    #  분기에 오는 문자열은 ★무조건★ 큰따옴표를 강제한다.
    if NAME_RE.match(s) or s.startswith("/"):
        return s
    return yaml.safe_dump(s, default_flow_style=True, allow_unicode=True,
                          default_style='"').strip().rstrip("\n...").strip()


def _block_end(lines, i, indent):
    """lines[i] 뒤로 들여쓰기가 indent 보다 깊은 구간의 끝(배타)."""
    j = i + 1
    while j < len(lines):
        ln = lines[j]
        if ln.strip() and (len(ln) - len(ln.lstrip())) <= indent:
            break
        j += 1
    # 끝에 붙은 빈 줄은 블록 밖으로 본다 (그래야 삽입이 문단 사이에 낀다)
    while j > i + 1 and not lines[j - 1].strip():
        j -= 1
    return j


def _child_indent(lines, i, indent, default=2):
    """lines[i] 아래 자식들이 쓰는 들여쓰기 폭. 자식이 없으면 default."""
    for j in range(i + 1, len(lines)):
        ln = lines[j]
        if not ln.strip():
            continue
        w = len(ln) - len(ln.lstrip())
        if w <= indent:
            break
        if not ln.lstrip().startswith("#"):
            return w - indent
    return default


def _ensure_key(lines, key, indent, lo, hi):
    """lo~hi 안에서 `key:` 줄을 찾는다. 없으면 만들고 그 줄 번호를 준다.

    `key: {}` 처럼 빈 흐름식으로 돼 있으면 블록식으로 편다 — 뒤에 달린 주석은
    그대로 살린다(이 파일들의 주석은 「왜 이렇게 뒀는지」라서 지우면 안 된다).
    """
    pat = re.compile(rf"^\s{{{indent}}}{re.escape(key)}\s*:(.*)$")
    for i in range(lo, min(hi, len(lines))):
        m = pat.match(lines[i])
        if not m:
            continue
        rest = m.group(1)
        cut = rest.find("#") if not rest.lstrip().startswith("{") else -1
        val = (rest[:cut] if cut >= 0 else rest).strip()
        if val:                                  # `key: {a: 1, b: 2}` → 블록식
            if not (val.startswith("{") and val.endswith("}")):
                raise ValueError(
                    f"`{key}:` 가 예상 못 한 모양이라 건드리지 않았다: {lines[i].strip()}")
            try:
                kids = yaml.safe_load(val) or {}
            except yaml.YAMLError as e:
                raise ValueError(f"`{key}:` 를 읽지 못했다: {e}") from None
            step = _child_indent(lines, i, indent)
            lines[i] = _keep_comment(lines[i], " " * indent + f"{key}:")
            for j, (k2, v2) in enumerate(kids.items()):
                lines.insert(i + 1 + j, " " * (indent + step) + f"{k2}: {_yv(v2)}")
        return i, True
    lines.insert(hi, " " * indent + f"{key}:")
    return hi, False


def set_aux_schedule(scenario, schedule):
    """시나리오의 top-level `aux_schedule:` 를 저작한 것으로 갈아 끼운다. [2026-08-25]

    schedule = {토픽: {프레임: 값}} — 뷰어(--watch)가 저작해 낸 그대로. 블록을 통째로
    교체하고 ★자동생성 표식★ 을 단다(그 앞에 있던 손주석은 값이 바뀌면 거짓이 되므로
    남기지 않는다 — 이건 사람이 쓴 설명이 아니라 기계가 찍은 타임라인이다).

    ★params 처럼 줄 단위로 병합하지 않는 이유★ aux_schedule 은 top-level 한 덩이라
    통째 교체가 가장 안전하다(중간에 끼우면 프레임 키가 흩어진다). 다른 블록의
    주석·순서는 그대로 둔다 — 이 블록만 들어낸다. 쓰고 나서 되읽어 확인한다.
    """
    name = Path(str(scenario)).name
    if not name.endswith(".yaml"):
        raise ValueError(f"시나리오 파일이 아니다: {scenario}")
    path = ROOT / "scenarios" / name
    if not path.is_file():
        raise ValueError(f"그런 시나리오가 없다: scenarios/{name}")
    if not schedule:
        raise ValueError("저작한 타임라인이 비어 있다")

    text = path.read_text()
    lines = text.splitlines()
    # ① 기존 aux_schedule 블록(키 줄 + 들여쓴 자식·빈 줄)을 걷어낸다
    out, i, n = [], 0, len(lines)
    while i < n:
        if re.match(r"^aux_schedule\s*:", lines[i]):
            i += 1
            while i < n and (not lines[i].strip() or lines[i][:1] in " \t"):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    # ② 새 블록 조립 (흐름식 한 줄/토픽)
    block = ["aux_schedule:",
             f"  # ★뷰어(--watch)에서 저작됨 "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M')}★ 판정 런이 이 프레임을 재생한다"]
    for topic, marks in schedule.items():
        pairs = ", ".join(f"{int(f)}: {_yv(v)}"
                          for f, v in sorted(marks.items(), key=lambda x: int(x[0])))
        block.append(f'  "{topic}": {{{pairs}}}')
    # ③ params: 앞에 넣는다(없으면 variants: 앞, 그것도 없으면 끝)
    at = next((j for j, ln in enumerate(out)
               if re.match(r"^(params|variants)\s*:", ln)), len(out))
    out[at:at] = block + [""]

    new = "\n".join(out) + "\n"
    after = yaml.safe_load(new) or {}                        # 깨진 YAML 차단
    got = after.get("aux_schedule") or {}
    for topic, marks in schedule.items():
        for f, v in marks.items():
            g = (got.get(topic) or {}).get(int(f))
            if not _same(g, v):
                raise ValueError(f"{name} 에 aux_schedule 을 제대로 넣지 못했다 "
                                 f"({topic}[{f}]: 넣으려던 {v}, 다시 읽은 {g}).")
    # 그 밖의 것이 사라지지 않았는가 (aux_schedule 만 빼고 비교)
    b, a = yaml.safe_load(text) or {}, dict(after)
    b.pop("aux_schedule", None)
    a.pop("aux_schedule", None)
    if b != a:
        raise ValueError(f"{name} 의 다른 내용이 바뀔 뻔했다 — 저장을 취소한다.")
    path.write_text(new)
    return str(path)


_CHECK_KEY_ORDER = ("signal", "where", "event", "stat", "when_valid", "last",
                    "min", "max", "why")


def _order_check(chk):
    ordered = {k: chk[k] for k in _CHECK_KEY_ORDER if k in chk}
    ordered.update((k, v) for k, v in chk.items() if k not in ordered)
    return ordered


def _same(a, b):
    """값 비교 — 0.006 과 0.0060 은 같다."""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


def _leaves(obj, prefix=()):
    """중첩 dict 를 (경로 튜플, 값) 목록으로 편다. 리스트는 값으로 본다."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaves(v, prefix + (str(k),))
    else:
        yield prefix, obj


def _typed(v):
    """화면은 값을 문자열로 보낸다 — 원래 종류로 되돌린다.

    ★왜 여기서 하는가★ `device: "cuda:0"` 은 문자열이지만 `show_window: "false"`
    를 문자열로 쓰면 노드가 참으로 읽는다(빈 문자열이 아니므로). 종류를 웹의 JS 가
    한 벌 더 판단하면 반드시 어긋나므로 파일에 쓰는 쪽에서 한 번만 한다.
    """
    if not isinstance(v, str):
        return v
    s = v.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def clean_params(raw):
    """화면이 보낸 파라미터를 거른다 — 파일에 쓰기 전의 문지방.

    리스트·사전은 받지 않는다 — IPM 4점 같은 것은 «카메라 보정» 이 맡는다.
    이름 검사와 되읽어 확인은 `set_params` 가 이어서 한다.
    """
    if not isinstance(raw, dict) or not raw:
        raise ValueError("저장할 파라미터가 없습니다")
    out = {}
    for nid, kv in raw.items():
        if not isinstance(kv, dict) or not kv:
            raise ValueError(f"{nid} 아래에 값이 없습니다")
        vals = {}
        for k, v in kv.items():
            if isinstance(v, (list, dict)):
                raise ValueError(f"{nid}.{k} 는 여기서 못 고칩니다 — «카메라 보정» 에서")
            if isinstance(v, str) and ("\n" in v or len(v) > 300):
                raise ValueError(f"{nid}.{k} 값이 한 줄이 아니거나 너무 깁니다")
            vals[str(k)] = _typed(v)
        out[str(nid)] = vals
    return out


def set_params(node_params, target="local"):
    """노드 파라미터를 `params:` 블록에 병합한다 (주석 보존).

    target "local" 이면 local.yaml, 아니면 scenarios/<파일>. 캘리브레이션 값은
    카메라·영상마다 다르므로 보통 local.yaml 이 맞고, 시나리오에 굳혀 두고
    싶을 때만 시나리오를 고른다.

    ★쓰고 나서 다시 읽어 확인한다★ — 줄 단위로 갈아 끼우는 방식이라
    파일 모양이 예상과 다르면 조용히 엉뚱한 곳에 붙을 수 있다. 그 상태로
    저장하면 다음 실행이 틀린 값으로 돌아간다.
    """
    if not isinstance(node_params, dict) or not node_params:
        raise ValueError("저장할 파라미터가 없다")
    if target in ("local", "local.yaml"):
        path, text = ROOT / "local.yaml", _local_text()
    else:
        name = Path(str(target)).name
        if not NAME_RE.match(name.replace(".yaml", "")) or not name.endswith(".yaml"):
            raise ValueError(f"저장할 곳이 잘못됐다: {target}")
        path = ROOT / "scenarios" / name
        if not path.is_file():
            raise ValueError(f"그런 시나리오가 없다: scenarios/{name}")
        text = path.read_text()

    lines = text.splitlines()
    pi, _ = _ensure_key(lines, "params", 0, 0, len(lines))
    for nid, kv in node_params.items():
        if not NAME_RE.match(str(nid)):
            raise ValueError(f"노드 이름이 잘못됐다: {nid}")
        pend = _block_end(lines, pi, 0)
        ni, _ = _ensure_key(lines, str(nid), 2, pi + 1, pend)
        step = _child_indent(lines, ni, 2)
        for k, v in kv.items():
            if not NAME_RE.match(str(k)):
                raise ValueError(f"파라미터 이름이 잘못됐다: {k}")
            nend = _block_end(lines, ni, 2)
            body = " " * (2 + step) + f"{k}: {_yv(v)}"
            pat = re.compile(rf"^\s+{re.escape(str(k))}\s*:")
            hit = next((i for i in range(ni + 1, nend) if pat.match(lines[i])), None)
            if hit is not None:
                lines[hit] = _keep_comment(lines[hit], body)
            else:
                # ★블록 끝에 붙인다★ — 중간에 끼우면 여러 줄짜리 주석이
                #   설명하던 줄에서 떨어져 나간다.
                lines.insert(nend, body)

    out = "\n".join(lines) + "\n"
    before = yaml.safe_load(text) or {}
    after = yaml.safe_load(out) or {}                        # 깨진 YAML 차단

    # ① 넣으려던 값이 실제로 들어갔는가
    got = after.get("params") or {}
    for nid, kv in node_params.items():
        for k, v in kv.items():
            g = (got.get(nid) or {}).get(k)
            if not _same(g, v):
                raise ValueError(
                    f"{path.name} 에 {nid}.{k} 를 제대로 넣지 못했다 "
                    f"(넣으려던 값 {v}, 다시 읽은 값 {g}). 파일을 바꾸지 않았다.")

    # ② ★그 밖의 것이 하나도 사라지지 않았는가★
    #    줄 단위로 갈아 끼우는 방식이라 파일 모양이 예상과 다르면 키가 중복돼
    #    조용히 덮이는 일이 생긴다(YAML 은 중복 키를 에러로 보지 않는다).
    #    실제로 `perception: {show_window: false, …}` 를 그렇게 날려 먹었다.
    touched = {("params", str(n), str(k)) for n, kv in node_params.items() for k in kv}
    for key, val in _leaves(before):
        if key in touched:
            continue
        if key not in dict(_leaves(after)):
            raise ValueError(f"{path.name} 의 {'.'.join(key)} 가 사라졌다 — "
                             "파일을 바꾸지 않았다.")
        if not _same(dict(_leaves(after))[key], val):
            raise ValueError(f"{path.name} 의 {'.'.join(key)} 가 바뀌었다 — "
                             "파일을 바꾸지 않았다.")
    path.write_text(out)
    return str(path.relative_to(ROOT))
