"""설정 읽기·쓰기 — 보정 스튜디오와 CLI 가 함께 쓰는 엔진.

★시나리오 저작은 여기 없다★ [2026-09-04] 예전에는 이 파일의 절반이 시나리오를
만들고·본뜨고·접붙이는 코드였다(clone/compose/graft/preview_checks). 시나리오
층 자체가 사라졌으므로 전부 지웠다. 남은 것은 셋이다:

  · 계약과 영상의 현재 상태를 읽어 화면에 넘기는 `snapshot`
  · 대상 워크스페이스가 빌드보다 낡았는지 보는 `stale_sources`
  · ★주석을 보존하며★ local.yaml·계약을 고치는 쓰기 함수들

쓰기가 주석을 보존하는 이유: local.yaml·계약 파일에는 "왜 이 값인지"가 주석으로
남아 있는데 yaml.safe_dump 로 왕복시키면 전부 날아간다. 그래서 YAML 로 파싱해
검증하고, 실제 수정은 해당 줄만 텍스트로 갈아 끼운다.
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
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def _video_info(path):
    """이 파일이 열리는가 · 몇 프레임인가. 고르기 전에 확인시켜 준다.

    ★사진 한 장도 소스다★ 보정에 필요한 것은 프레임 하나뿐이라, 이미지면
    「1프레임짜리 영상」으로 취급한다(화면이 프레임 바를 숨기는 근거가 이 값이다).
    """
    p = Path(str(path)).expanduser()
    out = {"path": str(p), "exists": p.is_file()}
    if not out["exists"]:
        return out
    out["size_mb"] = round(p.stat().st_size / 1e6, 1)
    if p.suffix.lower() in IMAGE_EXT:
        try:
            import cv2
            img = cv2.imread(str(p))
            if img is None:
                return {**out, "openable": False}
            h, w = img.shape[:2]
            return {**out, "frames": 1, "w": int(w), "h": int(h), "fps": 0.0,
                    "openable": True, "still": True}
        except Exception:                        # noqa: BLE001
            return {**out, "openable": None}
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


def snapshot():
    """화면이 통째로 그리는 데 필요한 현재 상태 — 계약·보정 프로필·최근 영상."""
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
                "signal_names": sorted(c.signals), "ok": True,
                #  ★보정 프로필을 가진 계약인가★ 스튜디오의 목록이 이걸로 갈린다.
                "calibration": bool(c.raw.get("calibration")),
                #  초안 그대로면 `ros2 run TODO TODO` 다 — 돌리기 전에 보여 준다.
                "draft": bool(str(c.sync_topic) == "TODO" or any(
                    "TODO" in (str(n.get("package")), str(n.get("executable")))
                    for n in c.nodes)),
            })
        except Exception as e:                   # noqa: BLE001
            ent["error"] = str(e)
        contracts.append(ent)

    return {"root": str(ROOT), "contracts": contracts,
            "recent": recent_videos(),
            "presets": [f.name for f in sorted((ROOT / "presets").glob("*.yaml"))],
            "default_contract": loc.get("default_contract", ""),
            "has_local": (ROOT / "local.yaml").exists()}


#  ★영상은 등록하지 않는다 — 최근 것만 기억한다★ [2026-09-04]
#  예전에는 영상을 쓰려면 local.yaml 의 `videos:` 에 ★논리 이름★ 을 붙여 등록하고,
#  그 이름을 시나리오에 적어야 했다. 시나리오가 머신 독립이어야 했기 때문인데,
#  그 대가로 "영상 하나 열어 보기"에 등록·저작 두 단계가 앞섰다. 지금은 경로를
#  그대로 쓰고, 방금 연 것만 목록으로 남긴다(고르는 수고만 덜어 주는 것이지
#  이 목록이 없다고 못 여는 것은 아니다).
RECENT_MAX = 12
RECENT_NOTE = "# 최근 연 영상 — 보정 스튜디오가 자동으로 관리한다(손으로 안 고쳐도 된다)"


def strip_block(lines, key, note=""):
    """`key:` 블록과 ★그 표식 주석까지★ 걷어낸다 — 기계가 관리하는 절을 갈아 끼울 때.

    ★표식 주석을 같이 안 지우면 저장할 때마다 한 줄씩 쌓인다★ — 실제로 그랬다.
    블록은 지워지고 그 위의 안내 주석만 남아, 다음 저장이 또 하나를 붙인다.
    사람이 쓴 주석은 표식과 글자가 다르므로 그대로 살아남는다.
    """
    out, skip = [], False
    for ln in lines:
        if note and ln.strip() == note.strip():
            continue
        if re.match(rf"^{re.escape(key)}\s*:", ln):
            skip = True
            continue
        if skip and (ln.startswith((" ", "\t")) or not ln.strip()):
            continue
        skip = False
        out.append(ln)
    while out and not out[-1].strip():
        out.pop()
    return out


def _local():
    """local.yaml 을 ★쓰는 것과 같은 경로로★ 읽는다.

    `tb.run.local_overrides()` 도 같은 파일을 읽지만 그쪽은 `run.ROOT` 를 본다.
    읽기와 쓰기가 서로 다른 뿌리를 보면 자체 검사가 임시 폴더에서 도는 순간
    ★쓴 것과 다른 것을 읽는다★ — 실제로 그렇게 잡혔다.
    """
    import yaml as _y                                       # noqa: PLC0415
    try:
        return _y.safe_load(_local_text()) or {}
    except (yaml.YAMLError, OSError):
        return {}


def browse_roots():
    """보정 스튜디오가 훑어도 되는 뿌리 폴더들 (`local.yaml` 의 `browse_roots:`).

    안 적으면 홈 하나 — 지금까지와 같다. 넣을 수 있게 열어 둔 이유는
    ★화면이 원격이 되었기 때문★ 이다. 외장 디스크나 다른 사용자의 폴더에
    영상이 있으면 여기 한 줄을 더한다. 없는 폴더는 조용히 버린다 —
    USB 를 뽑아 두면 목록에서 사라질 뿐 스튜디오가 안 뜨면 곤란하다.
    """
    raw = _local().get("browse_roots")
    if not isinstance(raw, list):
        raw = [raw] if raw else []
    roots = []
    for r in raw:
        try:
            q = Path(str(r)).expanduser().resolve()
        except OSError:
            continue
        if q.is_dir() and q not in roots:
            roots.append(q)
    return roots or [Path.home().resolve()]


def recent_videos():
    """최근 연 영상·이미지 — 각각 해상도·프레임 수까지 붙여서."""
    loc = _local()
    out = []
    for pth in (loc.get("recent") or [])[:RECENT_MAX]:
        info = _video_info(pth)
        info["path"] = str(pth)
        out.append(info)
    return out


def push_recent(path):
    """방금 연 것을 목록 맨 앞으로. 같은 경로는 위로 올라올 뿐 늘지 않는다."""
    path = str(Path(str(path)).expanduser())
    if not Path(path).is_file():
        raise ValueError(f"그런 파일이 없습니다: {path}")
    cur = [p for p in (_local().get("recent") or []) if p != path]
    cur.insert(0, path)
    cur = cur[:RECENT_MAX]
    keep = strip_block(_local_text().splitlines(), "recent", RECENT_NOTE)
    keep += ["", RECENT_NOTE, "recent:"] + [f"  - {p}" for p in cur]
    _write_local("\n".join(keep) + "\n")
    return cur


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


CONTRACT_TMPL = """\
# ══════════════════════════════════════════════════════════════════
#  계약 — 테스트베드가 이 워크스페이스를 아는 ★유일한★ 통로
# ══════════════════════════════════════════════════════════════════
#  ★아직 절반만 채워져 있다.★ 아래 TODO 를 채워야 실행된다
#  (`tb.run doctor` 가 TODO 가 남아 있으면 막는다).
#
#  채우는 법: 대상 시스템을 평소처럼 띄워 놓고 `ros2 topic list` · `ros2 topic
#  info -v <토픽>` · `ros2 topic echo --once <토픽>` 으로 토픽·타입·필드 배치를
#  실제 메시지에서 읽는다. 숫자 배열은 이름이 없으므로 몇 번째가 무엇인지는
#  발행 코드를 같이 봐야 안다 — 한 칸만 밀려도 조용히 엉뚱한 값을 잰다.
#  절차 전체는 skills/cam-test/attach.md 에 있다.
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

    쓸 곳은 local.yaml 이다 — 캘리브레이션 값은 카메라·차량마다 다른, 곧
    ★기계에 묶인 값★ 이라 저장소에 올라가는 파일에 굳히면 안 된다. 워크스페이스에
    반영하는 것은 스튜디오의 «내보내기» 가 따로 한다(그쪽은 대상 저장소의 파일이다).

    ★쓰고 나서 다시 읽어 확인한다★ — 줄 단위로 갈아 끼우는 방식이라
    파일 모양이 예상과 다르면 조용히 엉뚱한 곳에 붙을 수 있다. 그 상태로
    저장하면 다음 실행이 틀린 값으로 돌아간다.
    """
    if not isinstance(node_params, dict) or not node_params:
        raise ValueError("저장할 파라미터가 없다")
    if target not in ("local", "local.yaml"):
        raise ValueError(f"저장할 곳은 local.yaml 뿐이다: {target}")
    path, text = ROOT / "local.yaml", _local_text()

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
