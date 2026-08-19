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


def video_info(path):
    """영상 한 개의 상태 — 화면이 「이 파일이 열리긴 하는가」를 물을 때."""
    return _video_info(path)


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
    return s if NAME_RE.match(s) or s.startswith("/") else yaml.safe_dump(
        s, default_flow_style=True).strip().rstrip("\n...").strip()


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
