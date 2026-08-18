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
                "image_topic": c.image_topic, "signals": len(c.signals), "ok": True,
            })
        except Exception as e:                   # noqa: BLE001
            ent["error"] = str(e)
        contracts.append(ent)

    videos = {}
    for k, v in (loc.get("videos") or {}).items():
        videos[str(k)] = _video_info(v)

    scenarios = []
    for f in sorted((ROOT / "scenarios").glob("*.yaml")):
        try:
            sc = yaml.safe_load(f.read_text()) or {}
        except Exception as e:                   # noqa: BLE001
            scenarios.append({"file": f.name, "error": str(e)})
            continue
        scenarios.append({
            "file": f.name, "name": sc.get("name", f.stem),
            "contract": sc.get("contract", ""), "video": sc.get("video", ""),
            "mode": sc.get("mode", "lockstep"),
            "start": sc.get("start", 0), "limit": sc.get("limit", 0),
            "variants": [v.get("name") for v in (sc.get("variants") or [])],
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

    params = r._deep_merge(sc.get("params", {}), loc.get("params", {}))
    out["params"] = params
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
    return {"file": f.name}
