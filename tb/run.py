"""오케스트레이터 — 노드를 띄우고, 영상을 밀고, 기록하고, 계측한다.

노드는 subprocess (`ros2 run …`) 로만 다룬다. import 하지 않는다.
그래서 대상 패키지 안이 어떻게 바뀌든 이 파일은 바뀌지 않는다.

★판정하지 않는다★ [2026-09-04 개편]
예전에는 시나리오 YAML 의 `checks:` 가 임계값으로 합격/불합격을 찍었다. 그 숫자는
결국 「첫 런의 실측」에서 나온 것이라 근거가 없었고, 근거 없는 기준이 이후 모든
회귀 비교의 잣대가 되어 있었다. 지금은 ★재기만 한다★ — 리포트·CSV·디버그 영상을
내놓고, 그것이 좋은지 나쁜지는 그 결과를 읽는 사람(과 클로드)이 말한다.

입력은 ★계약 + 영상 경로 + 구간★ 셋뿐이다. 시나리오 저작 단계는 없다.

  python3 -m tb.run doctor --contract contracts/x.yaml
  python3 -m tb.run run --contract contracts/x.yaml --video /abs/a.mp4 \
                        --start 300 --limit 900 --out ~/x_ws
  python3 -m tb.run run --preset presets/vote_night_a.yaml      # 자주 쓰는 조합
  python3 -m tb.run replay <런>          # 옛 런의 디버그 영상을 다시 잡는다
  python3 -m tb.run reanalyze <런>       # 계약을 고친 뒤 raw.jsonl 만 다시 읽는다
  python3 -m tb.run diff <런A> <런B>     # 판정 없는 차이표
  python3 -m tb.run export <런> --out <워크스페이스>
  python3 -m tb.run list
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from . import analyze
from . import encode
from .contract import load as load_contract

ROOT = Path(__file__).resolve().parent.parent      # testbed/ (테스트베드 자신)


def runs_dir(sub=""):
    """`runs/` 는 ★git 에 없다★ — 새로 클론한 기계에는 이 폴더가 아예 없다.

    실측한 실패: 갓 클론한 저장소에서 `tb.run list` 가 FileNotFoundError 로 죽었다.
    쓰는 쪽마다 mkdir 을 흩어 놓으면 하나씩 빠지므로 여기 한 곳을 지나게 한다.
    """
    d = ROOT / "runs" / sub if sub else ROOT / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


#  ★프리셋이 물려주는 키★ — 전부 「어느 영상의 어느 구간을 어떻게 재생하는가」다.
#  판정 어휘는 하나도 없다(그래서 프리셋은 시나리오가 아니다). 명령줄 인자가 이긴다.
SPEC_KEYS = ("mode", "rate", "start", "limit", "stride", "warmup_s", "sync_timeout",
             "sync_timeout_first", "sync_retries", "sync_settle_ms", "prime",
             "discard_first", "record_seconds", "sim_time", "view_fps", "view_scale",
             "params", "aux_schedule", "overlay", "perturb")


def _child_preexec():
    """자식 프로세스 시작 훅 — ★새 세션 + 부모 죽으면 같이 죽기★. [2026-08-25]

    · os.setsid()          — 새 프로세스 그룹. Proc.stop 의 killpg 가 그룹째 정리한다.
    · PR_SET_PDEATHSIG      — ★tb.run 이 timeout·kill 로 정상 정리 없이 죽어도★ 커널이
                              이 자식에게 SIGTERM 을 보낸다. 안 그러면 setsid 로 분리된
                              player·viewer·노드·probe 가 ★고아로 남아★ cv2 창을 문 채
                              CPU 를 태운다(실제로 겪었다). 정상 종료 경로(finally 의
                              stop())는 그대로 있고, 이건 그 경로가 ★안 도는 경우★ 의
                              마지막 방어선이다. Linux 전용 — 다른 OS 는 setsid 만 한다.
    """
    os.setsid()
    try:
        import ctypes                                 # noqa: PLC0415
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM, 0, 0, 0)  # 1=PR_SET_PDEATHSIG
    except Exception:      # noqa: BLE001
        pass

# ★대상 워크스페이스는 계약이 정한다★ — 테스트베드는 워크스페이스 밖에 있어도 되고
#   여러 워크스페이스를 계약 파일만 바꿔 가며 붙일 수 있다. contract.workspace 참고.


# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
def _deep_merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_yaml(p):
    with open(p) as f:
        return yaml.safe_load(f) or {}


def local_overrides():
    """머신마다 다른 것(가중치 경로 등). git 에 안 올린다."""
    p = ROOT / "local.yaml"
    return load_yaml(p) if p.exists() else {}


def _resolve_contract(spec):
    """계약 파일 경로를 정한다.

    우선순위: 인자로 준 것 → 프리셋의 contract: → local.yaml 의 default_contract
             → contracts/ 안에 .yaml 이 딱 하나면 그것.
    여러 개인데 지정이 없으면 고르라고 알려 준다(조용히 엉뚱한 걸 쓰지 않게).
    """
    if spec:
        p = Path(spec).expanduser()
        return p if p.is_absolute() else (ROOT / p)
    d = local_overrides().get("default_contract")
    if d:
        p = Path(d).expanduser()
        return p if p.is_absolute() else (ROOT / p)
    cands = sorted((ROOT / "contracts").glob("*.yaml"))
    if len(cands) == 1:
        return cands[0]
    if not cands:
        return None
    raise SystemExit(
        "[tb] 계약이 여러 개다 — --contract 로 고를 것:\n  "
        + "\n  ".join(str(c.relative_to(ROOT)) for c in cands))


def resolve_video(spec, loc):
    """영상 경로를 정한다 — ★그냥 경로다★.

    예전에는 시나리오가 논리 이름을 적고 local.yaml 의 `videos:` 가 실제 경로로
    풀었다. 시나리오 파일을 머신 독립으로 두려던 것인데, 실제로는 영상 하나
    돌리려고 등록·시나리오 저작 두 단계를 먼저 밟아야 했다. 지금은 명령줄이
    절대경로를 직접 받는다. local.yaml 의 최상위 `video:` 는 여전히 전부를
    덮어쓰는 비상 스위치다.
    """
    v = loc.get("video") or spec.get("video")
    return str(Path(str(v)).expanduser()) if v else None


def _param_arg(k, v):
    if isinstance(v, bool):
        return f"{k}:={'true' if v else 'false'}"
    if isinstance(v, float):
        return f"{k}:={v!r}"
    if isinstance(v, (list, tuple)):
        return f"{k}:=[{','.join(str(x) for x in v)}]"
    return f"{k}:={v}"


def ws_prefix(contract=None):
    """대상 워크스페이스 오버레이를 source 하는 bash 접두어.

    계약이 가리키는 워크스페이스(+추가 오버레이)를 순서대로 source 한다.
    계약이 없으면(=doctor 초기 점검) 빈 문자열 — 현재 셸 환경을 그대로 쓴다.
    """
    if contract is None:
        return ""
    return "".join(f"source '{f}' >/dev/null 2>&1; " for f in contract.setup_files())


def _parse_param(s):
    """`--param perception.enable_undistort=false` → ('perception', 'enable…', False).

    값의 형은 YAML 로 읽는다 — `false`·`3`·`0.5`·`[1,2]` 가 전부 제 형으로 온다.
    (문자열로 넘기면 노드가 파라미터 타입 불일치로 죽는다. 경로는 따옴표 없이도
    문자열로 남는다.)
    """
    if "=" not in s or "." not in s.split("=", 1)[0]:
        raise SystemExit(f"[run] --param 은 `노드.이름=값` 꼴이다: {s!r}")
    key, raw = s.split("=", 1)
    node, name = key.split(".", 1)
    try:
        val = yaml.safe_load(raw)
    except yaml.YAMLError:
        val = raw
    return node.strip(), name.strip(), val


def build_spec(args):
    """프리셋(있으면) 위에 명령줄 인자를 덮어 ★이번 런의 조건★ 하나를 만든다.

    ★인자가 언제나 이긴다★ — 다만 '주지 않은 것'과 '기본값을 준 것'을 구별해야
    한다(argparse 기본값을 None 으로 두는 이유다). 안 그러면 프리셋의 start 를
    쓰겠다고 골라 놓고 매번 0 으로 덮인다.
    """
    spec = {}
    pre = Path(args.preset).expanduser() if getattr(args, "preset", "") else None
    if pre:
        if not pre.is_absolute():
            pre = ROOT / pre
        if not pre.is_file():
            raise SystemExit(f"[run] 그런 프리셋이 없다: {pre}")
        raw = load_yaml(pre)
        spec = {k: raw[k] for k in SPEC_KEYS if k in raw}
        spec["video"] = raw.get("video", "")
        spec["contract"] = raw.get("contract", "")
        spec["name"] = raw.get("name", pre.stem)
        spec["preset_file"] = str(pre.resolve())
    for k in SPEC_KEYS:
        v = getattr(args, k, None)
        if v is not None and k != "params":
            spec[k] = v
    if getattr(args, "video", ""):
        spec["video"] = args.video
    #  --param 은 프리셋의 params 를 ★덮어쓰는 것이지 지우는 것이 아니다★
    over = {}
    for s in (getattr(args, "param", None) or []):
        node, name, val = _parse_param(s)
        over.setdefault(node, {})[name] = val
    if over:
        spec["params"] = _deep_merge(spec.get("params") or {}, over)
    spec.setdefault("mode", "lockstep")
    spec.setdefault("perturb", "none")
    #  ★--name 은 폴더 이름이 되지 않는다★ 표시용이라 공백·한글을 그대로 받는데,
    #  그게 런 디렉터리 이름으로 새면 경로에 공백이 들어간다. 폴더 이름은
    #  --tag 나 프리셋 이름에서만 나온다(meta.label 이 사람이 읽는 이름을 따로 든다).
    spec.setdefault("name", "run")
    return spec


class _Recorded(Exception):
    """자극 없이 기록만 끝났음을 알리는 내부 신호."""

    def __init__(self, wall):
        super().__init__("recorded")
        self.wall = wall


class Proc:
    def __init__(self, name, cmd, log_path, env):
        self.name = name
        self.log = open(log_path, "w")
        self.p = subprocess.Popen(
            ["bash", "-c", cmd], stdout=self.log, stderr=subprocess.STDOUT,
            env=env, preexec_fn=_child_preexec)

    def alive(self):
        return self.p.poll() is None

    def stop(self, sig=signal.SIGINT, wait=6.0):
        if self.p.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.p.pid), sig)
        except ProcessLookupError:
            return
        t0 = time.time()
        while self.p.poll() is None and time.time() - t0 < wait:
            time.sleep(0.05)
        if self.p.poll() is None:
            try:
                os.killpg(os.getpgid(self.p.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self.log.close()
        except Exception:   # noqa: BLE001
            pass


# ══════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════
def cmd_doctor(args):
    """돌리기 ★전★ 에 막히는 것을 전부 본다. 아무것도 바꾸지 않는다."""
    spec = build_spec(args)
    loc = local_overrides()
    contract_path = _resolve_contract(args.contract or spec.get("contract"))
    ok = True

    def chk(label, good, detail=""):
        nonlocal ok
        print(f"  {'✅' if good else '❌'} {label}" + (f"  {detail}" if detail else ""))
        ok = ok and good

    print("── 테스트베드 ──")
    chk("ROS_DISTRO", bool(os.environ.get("ROS_DISTRO")), os.environ.get("ROS_DISTRO", ""))
    chk("ros2 실행 가능", shutil.which("ros2") is not None)
    try:
        import cv2        # noqa: F401
        import rclpy      # noqa: F401
        chk("cv2 / rclpy import", True)
    except Exception as e:   # noqa: BLE001
        chk("cv2 / rclpy import", False, str(e))
    prof = ROOT / "fastdds_profile.xml"
    chk("Fast DDS 프로파일", prof.exists(),
        "1080p 무손실 전송용 — 없으면 프레임 ~5% 유실")
    # 디버그 영상은 브라우저·플레이어가 H.264/VP9 만 재생한다. OpenCV 기본
    # 코덱(mp4v)으로 찍히면 오류도 없이 검은 화면이 된다 → 미리 알려 준다.
    cap = encode.capability()
    chk("영상 코덱", not cap.startswith("cv2/mp4v"),
        f"{cap} — mp4v 뿐이면 브라우저에서 재생 불가. `sudo apt install ffmpeg`")
    #  ★없어도 죽지 않는다 — 그래서 더 위험하다★ 한글 폰트가 없으면 디버그 영상과
    #  BEV 의 라벨이 조용히 ???? 가 된다(cv2 폴백). 영상을 열어 봐야 알게 된다.
    from .geometry import find_font                        # noqa: PLC0415
    font = find_font()
    print(f"  {'✅' if font else '⚠️ '} 한글 폰트  "
          + (font if font else "못 찾았다 — 디버그 영상의 라벨이 ???? 로 나온다. "
                               "`sudo apt install fonts-nanum` (또는 TB_FONT 로 지정)"))

    print("── 계약 ──")
    chk("계약 파일", contract_path is not None and Path(contract_path).exists(),
        str(contract_path))
    if not (contract_path and Path(contract_path).exists()):
        print("\n판정: 문제 있음")
        return 1
    c = load_contract(contract_path)
    print(f"     `{c.name}` v{c.version} · 노드 {len(c.nodes)} · "
          f"관찰토픽 {len(c.topics())} · 신호 {len(c.signals)}")
    todo = [k for k in ("TODO", "FIXME") if k in Path(contract_path).read_text()]
    chk("계약에 TODO 가 남아 있지 않다", not todo,
        "초안 계약이다 — 이름을 다 붙이기 전에는 잰 값을 믿을 수 없다" if todo else "")

    print("── 대상 워크스페이스 ──")
    if c.attach:
        print("     attach 모드 — 노드를 띄우지 않고 돌고 있는 시스템에 붙는다")
    else:
        chk("워크스페이스", c.workspace is not None and Path(c.workspace).is_dir(),
            str(c.workspace) if c.workspace else
            "계약에 workspace: 가 없고 자동 탐지도 실패했다 — 테스트베드가 "
            "워크스페이스 밖에 있으면 절대 경로로 반드시 적어야 한다")
        setups = c.setup_files()
        chk("setup.bash", bool(setups), " · ".join(setups) or "없음(현재 셸 환경 사용)")
        out = subprocess.run(
            ["bash", "-c", ws_prefix(c) + "ros2 pkg executables 2>/dev/null"],
            capture_output=True, text=True).stdout
        for n in c.nodes:
            line = f"{n['package']} {n['executable']}"
            chk(f"실행파일 {line}", line in out)

    print("── 이번 실행 ──")
    vid = resolve_video(spec, loc)
    if vid or c.image_topic:
        chk("영상", bool(vid) and Path(vid).exists(), str(vid))
    else:
        print("     영상 자극 없음 (계약에 image_topic 이 없다 — 기록만 하는 계약)")
    params = _deep_merge(spec.get("params") or {}, loc.get("params") or {})
    ids = {n["id"] for n in c.nodes}
    for nid, kv in params.items():
        if nid not in ids:
            # ★local.yaml 은 계약 여럿이 함께 쓴다★ 다른 계약의 노드 이름이 섞여
            # 있는 것은 정상이다(가중치 경로는 계약이 아니라 기계에 묶인다).
            # 이번에 사람이 직접 준 이름의 오타만 실패로 본다.
            if nid in (spec.get("params") or {}):
                chk(f"params.{nid} 가 계약에 없는 노드", False,
                    "이름이 계약의 nodes[].id 와 달라 조용히 무시된다")
            else:
                print(f"  ·  local.yaml 의 params.{nid} 는 이 계약과 무관 — 무시된다")
            continue
        for k, v in kv.items():
            if isinstance(v, str) and v.endswith((".pt", ".engine")):
                chk(f"{nid}.{k}", Path(v).exists(), v)
    bad = _required_params(c, params)
    chk("계약이 요구하는 파라미터", not bad, "; ".join(bad))
    print()
    print("판정:", "OK" if ok else "문제 있음")
    return 0 if ok else 1


def _required_params(contract, params):
    from .contract import check_required                  # noqa: PLC0415
    try:
        return check_required(contract, params)
    except Exception as e:                                # noqa: BLE001
        return [f"확인 실패: {e}"]


# ══════════════════════════════════════════════════════════════════════
def _one_run(spec, contract_path, run_dir, domain_id, args):
    run_dir.mkdir(parents=True, exist_ok=True)
    loc = local_overrides()
    contract = load_contract(contract_path)

    env = dict(os.environ)
    env["ROS_DOMAIN_ID"] = str(domain_id)
    env["RCUTILS_LOGGING_BUFFERED_STREAM"] = "0"
    env.setdefault("QT_QPA_PLATFORM", "offscreen")     # 언제나 헤드리스다
    # 1080p 이미지(6.2MB)를 무손실로 보내기 위한 Fast DDS 프로파일.
    # 없으면 SHM 세그먼트(기본 512KB)에 안 들어가 UDP 로 폴백 → 실측 5.5% 프레임 유실.
    # 자세한 이유는 fastdds_profile.xml 주석 참고. local.yaml 의 env 로 덮어쓸 수 있다.
    prof = ROOT / "fastdds_profile.xml"
    if prof.exists() and "FASTRTPS_DEFAULT_PROFILES_FILE" not in env:
        env["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(prof)
    for k, v in (loc.get("env") or {}).items():
        env[str(k)] = str(v)

    # 프리셋/인자 < local.yaml 순으로 덮인다.
    #   local 이 뒤인 것은 가중치 경로처럼 ★기계에 묶인 값★ 이라서다.
    params = _deep_merge(spec.get("params") or {}, loc.get("params") or {})
    # ★replay 는 그때 값이 맨 뒤다★ 그 사이 캘리브가 바뀌어 있으면(ipm_src_pts·
    # px2m 이 실제로 바뀐 적이 있다) 지금 local.yaml 로 돌린 그림은 그때 그림이
    # 아니다. 되살리려는 것은 「그때 노드가 본 것」이므로 런에 적힌 값이 이긴다.
    params = _deep_merge(params, getattr(args, "params_override", None) or {})
    # ★계약이 요구한 파라미터를 실행 ★전★ 에 막는다★
    #   백엔드(.pt/.engine)가 어긋난 채 돌면 리포트는 멀쩡한데 잰 대상이 실차와
    #   다르다 — 로그를 뒤져야 알 수 있는 종류의 거짓말이라 여기서 끊는다.
    from .contract import check_required
    bad = check_required(contract, params)
    if bad:
        raise SystemExit("[run] 계약이 요구하는 파라미터가 안 맞는다:\n  · "
                         + "\n  · ".join(bad))

    pref = ws_prefix(contract)
    procs = []

    # ── 1) 대상 노드 기동 ────────────────────────────────────────────
    #   attach 모드면 띄우지 않는다 — 이미 돌고 있는 시스템(실차 포함)에 붙어서
    #   관찰만 한다. 영상 투입도 image_topic 에 이미 발행자가 있으면 충돌하므로
    #   보통 attach 는 realtime 관찰/기록 용도로 쓴다.
    for n in ([] if contract.attach else contract.nodes):
        merged = _deep_merge(n.get("params") or {}, params.get(n["id"], {}))
        pargs = " ".join(f"-p {_param_arg(k, v)}" for k, v in merged.items())
        rename = f"-r __node:={n['node_name']}" if n.get("node_name") else ""
        if spec.get("sim_time"):
            pargs += " -p use_sim_time:=true"
        cmd = (f"{pref}exec ros2 run {n['package']} {n['executable']} "
               f"--ros-args {rename} {pargs}")
        (run_dir / f"cmd_{n['id']}.txt").write_text(cmd + "\n")
        procs.append(Proc(n["id"], cmd, run_dir / f"{n['id']}.log", env))
        print(f"  기동: {n['id']}")

    probe = None
    try:
        # ── 2) 프로브 ────────────────────────────────────────────────
        topics = ",".join(contract.topics())
        probe_cmd = (f"{pref}exec python3 -m tb.probe --topics '{topics}' "
                     f"--anchor '{contract.sync_topic or ''}' "
                     f"--out '{run_dir / 'raw.jsonl'}'")
        probe = Proc("probe", probe_cmd, run_dir / "probe.log",
                     {**env, "PYTHONPATH": f"{ROOT}:{env.get('PYTHONPATH', '')}"})
        print("  기동: probe")

        # ── 2.5) 디버그 영상 녹화 ────────────────────────────────────
        #   ★사후에 만들 수 없는 유일한 기록★ 이라 기본으로 켜 둔다. 대상 노드가
        #   그리는 그림이므로 노드가 도는 동안에만 잡을 수 있다(raw.jsonl 에는
        #   숫자만 남는다). 런당 2~7MB — 끌 이유가 거의 없다.
        if getattr(args, "record_debug", True) and contract.debug_topics:
            # 원본과 같은 속도로 저장한다(안 그러면 느리게 보인다).
            vfps = spec.get("view_fps") or _video_fps(resolve_video(spec, loc)) or 15.0
            vcmd = (f"{pref}exec python3 -m tb.viewer "
                    f"--contract '{contract_path}' "
                    f"--save-dir '{run_dir}' --scale {spec.get('view_scale', 0.6)} "
                    f"--fps {vfps:.3f} "
                    f"--jpeg '{run_dir / 'latest.jpg'}'")
            procs.append(Proc("viewer", vcmd, run_dir / "viewer.log",
                              {**env, "PYTHONPATH": f"{ROOT}:{env.get('PYTHONPATH', '')}"}))
            print("  기동: viewer  (디버그 영상 저장)")

        # ── 3) 자극 투입 ─────────────────────────────────────────────
        #   image_topic 이 없으면 영상 없이 그냥 duration 만큼 기록만 한다.
        #   attach 모드(돌고 있는 시스템 관찰)에서 쓴다.
        if not contract.image_topic:
            dur = float(spec.get("record_seconds", 10.0))
            print(f"  기록만 {dur:.0f}s (영상 자극 없음)")
            t0 = time.time()
            while time.time() - t0 < dur:
                time.sleep(0.2)
            raise _Recorded(time.time() - t0)

        video = resolve_video(spec, loc)
        if not video or not Path(video).exists():
            raise SystemExit(f"[run] 영상이 없다: {video!r}  (--video 로 절대경로를 줄 것)")
        #  ★타임라인★ 계약의 aux 는 토픽·타입을 정의하고, '어느 프레임에서 무엇이
        #  되는가' 는 시험마다 달라 프리셋/인자가 준다(계약 불변).
        sched = spec.get("aux_schedule") or {}
        aux_specs = [dict(a, schedule=sched[a["topic"]]) if a["topic"] in sched
                     else a for a in contract.aux]
        aux = json.dumps(aux_specs)
        #  ★합성 자극★ 상대경로는 테스트베드 루트 기준(영상처럼 머신마다 다른 것이
        #  아니라 ★시험 재료★ 라서 저장소에 함께 둔다).
        ovl = dict(spec.get("overlay") or {})
        if ovl.get("image") and not str(ovl["image"]).startswith("/"):
            ovl["image"] = str(ROOT / str(ovl["image"]))
        play = (f"{pref}exec python3 -m tb.player "
                f"--video '{video}' --image-topic '{contract.image_topic}' "
                f"--sync-topic '{contract.sync_topic or ''}' "
                f"--mode {spec.get('mode', 'lockstep')} "
                f"--rate {spec.get('rate', 1.0)} --start {spec.get('start', 0)} "
                f"--limit {spec.get('limit', 0)} --stride {spec.get('stride', 1)} "
                f"--warmup-s {spec.get('warmup_s', 6.0)} "
                f"--sync-timeout {spec.get('sync_timeout', 15.0)} "
                f"--sync-retries {spec.get('sync_retries', 2)} "
                # ★동기 신호 뒤 여유★ 받는 쪽이 이 프레임의 결과를 다 낼 때까지
                #   조금 더 돈다. 동기 토픽보다 ★뒤에★ 나오는 값(두 번째 추론의
                #   결과나 타이머로 나가는 진단값)이 있으면 이게 짧을 때 그 값이
                #   ★다음 프레임 행★ 에 붙는다. 기본 20ms.
                f"--sync-settle-ms {spec.get('sync_settle_ms', 20)} "
                f"--prime {int(spec.get('prime', 0))} "
                f"--sync-timeout-first {spec.get('sync_timeout_first', 90.0)} "
                f"--perturb '{spec.get('perturb', 'none')}' "
                f"{'--sim-time' if spec.get('sim_time') else ''} "
                f"--aux-json '{aux}' --overlay-json '{json.dumps(ovl)}' "
                f"--stats-out '{run_dir / 'player.json'}' "
                f"--progress-out '{run_dir / 'progress.json'}'")
        (run_dir / "cmd_player.txt").write_text(play + "\n")
        t0 = time.time()
        with open(run_dir / "player.log", "w") as lf:
            #  preexec 로 ★부모가 죽으면 player 도 죽는다★ (_child_preexec).
            #  player 는 foreground(subprocess.call)라 stop() 대상이 아니므로 이게 없으면
            #  tb.run 이 timeout 으로 죽었을 때 혼자 남아 프레임을 계속 민다.
            rc = subprocess.call(["bash", "-c", play], stdout=lf,
                                 stderr=subprocess.STDOUT, preexec_fn=_child_preexec,
                                 env={**env, "PYTHONPATH": f"{ROOT}:{env.get('PYTHONPATH', '')}"})
        wall = time.time() - t0
        for p in procs:
            if not p.alive():
                print(f"  ⚠️  {p.name} 가 도중에 죽었다 → {run_dir / (p.name + '.log')}")
        if rc != 0:
            print(f"  ⚠️  player 종료코드 {rc}")
    except _Recorded as e:
        wall = e.wall
    finally:
        time.sleep(1.0)
        # ★이 런이 실제로 어떤 값으로 돌았나★ — 노드가 아직 살아 있는 동안 묻는다.
        #   테스트베드가 준 것만 적으면 노드 기본값(카메라 기하 대부분)이 안 보인다.
        if not contract.attach:
            try:
                _dump_running_params(contract, run_dir, env, pref)
            except Exception as e:                      # noqa: BLE001
                print(f"  ·  파라미터 기록 실패(무해): {e}")
        if probe:
            probe.stop(signal.SIGTERM)
        for p in procs:
            p.stop()

    # ── 3.9) 디버그 영상이 정말 남았나 ───────────────────────────────
    #   ★사후에 만들 수 없는 유일한 기록★ 이라 조용히 빠지면 안 된다.
    #   뷰어는 종료할 때 mp4 를 쓴다 — 노드가 먼저 죽거나 코덱이 없으면
    #   런은 멀쩡히 끝나는데 영상만 없다. 그걸 여기서 말한다.
    #   파일 이름은 ★토픽에서 나온다★(/lane/debug → lane_debug.mp4) — 계약마다
    #   다르므로 이름을 여기 박지 않는다. 뷰어가 남기는 debug_meta.json 이
    #   어느 파일을 썼는지 알려 주는 계약 무관한 표식이다.
    if contract.debug_topics and getattr(args, "record_debug", True):
        if not (run_dir / "debug_meta.json").exists():
            print("  ⚠️  디버그 영상이 안 남았다 — "
                  f"{run_dir / 'viewer.log'} 를 보라 (다시 만들 수 없는 기록이다)")

    # ── 4) 계측 ─────────────────────────────────────────────────────
    pstats = {}
    pj = run_dir / "player.json"
    if pj.exists():
        pstats = json.loads(pj.read_text())

    rows, nlines = analyze.build_table(run_dir / "raw.jsonl", contract,
                                       int(spec.get("discard_first", 0)))
    analyze.write_csv(rows, contract, run_dir / "signals.csv")
    meta = {
        "run_id": run_dir.name,
        # ★사람이 붙인 이름★ 폴더 이름과 달리 ★표시용★ 이라 공백·한글을 그대로
        #   쓴다. `reanalyze` 는 meta 를 물려받아 유지한다.
        "label": (getattr(args, "name", "") or spec.get("name") or "").strip(),
        # ★무엇을 보려고 돌렸나★ 판정이 사라진 자리에 이게 들어간다. 결과를
        #   나중에 읽는 사람이 의도를 모르면 숫자만 남는다.
        "note": (getattr(args, "note", "") or "").strip(),
        "preset": spec.get("name", ""),
        "preset_file": spec.get("preset_file", ""),
        "contract": contract.name, "contract_version": contract.version,
        # ★계약 파일 경로★ 이름만 남기면 나중에 이 런을 재분석할 때 어느 계약으로
        #   봐야 하는지 알 수 없다(기본 계약으로 잘못 열면 신호가 전부 결측인
        #   리포트가 조용히 나온다 — 실제로 겪었다).
        "contract_file": str(Path(contract_path).resolve()),
        "video": pstats.get("video") or resolve_video(spec, loc),
        "perturb": spec.get("perturb", "none"),
        "overlay": spec.get("overlay") or None,
        "start": spec.get("start", 0), "limit": spec.get("limit", 0),
        "stride": spec.get("stride", 1),
        "mode": spec.get("mode", "lockstep"), "wall_s": round(wall, 1),
        # ★영상 fps★ — 초 단위 계측(구간 길이·전이 간격)의 환산 기준이다.
        #   lockstep 은 벽시계가 기계 속도에 좌우되므로 프레임을 장면 시간으로
        #   되돌려 재야 실차에서 일어난 그대로가 된다(analyze.scene_fps).
        "video_fps": _video_fps(pstats.get("video") or ""),
        # 배속. realtime 재생에서 rate=0.5 면 ★노드가 겪는 시간★ 이 두 배로
        # 늘어난다(느린 접근을 흉내낸다) → 초 단위 계측이 그만큼 달라진다.
        "rate": float(spec.get("rate", 1.0)),
        "frames_pushed": pstats.get("frames_pushed", 0),
        "sync_timeouts": pstats.get("sync_timeouts", 0),
        "raw_records": nlines, "domain_id": domain_id,
        "discard_first": int(spec.get("discard_first", 0)),
        # ★이 런이 실제로 쓴 파라미터만★ 남긴다. local.yaml 은 계약 여럿이 함께
        #   쓰므로 다른 계약의 노드가 섞여 있는데, 그것까지 적으면 두 런을 비교할
        #   때 "조건이 다르다"고 잘못 경고한다.
        "params": {k: v for k, v in params.items()
                   if k in {n["id"] for n in contract.nodes}},
        # ★기하의 실효값★ 위 params 는 '요청한 것' 이라 노드 기본값의 변화를
        #   못 잡는다. 사다리꼴이 바뀌면 픽셀의 뜻이 바뀐다(_calib_snapshot).
        "calib": _calib_snapshot(contract, run_dir),
        "when": datetime.now().isoformat(timespec="seconds"),
        "code_fingerprint": _code_snapshot(contract.workspace, run_dir),
        "workspace": str(contract.workspace or ""),
    }
    summary = analyze.summarize(rows, contract, meta)
    # 노드 로그는 토픽에 없는 근거를 갖고 있다(기동 배너·개입 사유).
    summary["log_events"] = analyze.log_events(run_dir, contract)
    drift = contract.drift_report()
    (run_dir / "summary.json").write_text(
        json.dumps({"summary": summary, "drift": drift},
                   indent=2, ensure_ascii=False, default=str))
    (run_dir / "report.md").write_text(analyze.report_run(summary, drift, contract))
    return summary, run_dir


def _dump_running_params(contract, run_dir, env, pref):
    """돌고 있는 노드들의 파라미터를 런 디렉터리에 남긴다(params_actual.yaml)."""
    got = {}
    for n in contract.nodes:
        node = n.get("node_name") or n["executable"]
        r = subprocess.run(["bash", "-c", f"{pref}ros2 param dump /{node} --print"],
                           capture_output=True, text=True, env=env, timeout=30)
        if r.returncode != 0 or not r.stdout.strip():
            continue
        try:
            y = yaml.safe_load(r.stdout) or {}
        except yaml.YAMLError:
            continue
        for _k, v in y.items():
            kv = (v or {}).get("ros__parameters") or {}
            kv.pop("use_sim_time", None)
            if kv:
                got[n["id"]] = kv
    if got:
        (run_dir / "params_actual.yaml").write_text(
            "# ★이 런이 실제로 돌았을 때 노드가 들고 있던 값★ (ros2 param dump)\n"
            "# 테스트베드가 준 것 + 노드 기본값이 합쳐진 실효값이다.\n"
            + yaml.safe_dump({"params": got}, allow_unicode=True, sort_keys=False,
                             default_flow_style=None))


def _video_fps(path):
    """원본 영상의 fps. 저장할 디버그·오버레이 영상 속도를 여기에 맞춘다.

    안 맞추면 30fps 영상이 10~15fps 로 저장돼 ★느린 배속처럼★ 보인다.
    """
    if not path:
        return None
    try:
        import cv2                                  # noqa: PLC0415
        cap = cv2.VideoCapture(str(path))
        v = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        return v if 1.0 <= v <= 120.0 else None
    except Exception:                               # noqa: BLE001
        return None


def _code_snapshot(ws, run_dir):
    """대상 소스의 지문 ★과 사본★ — 어떤 코드로 뽑은 결과인지 남긴다(git 없이도).

    ★내용 해시다★ 예전엔 `이름+크기+mtime` 이었는데, 그러면 touch 만 해도
    지문이 바뀌고 해시에서 내용을 되돌릴 수도 없어서 "런 사이에 뭐가 바뀌었나"에
    답을 못 했다(과거 런 94개가 코드 상태 29개로 갈렸는데 그 내용은 영영 모른다).

    그래서 두 가지를 남긴다:
        code.json      파일별 해시 — 두 런을 비교하면 바뀐 파일이 바로 나온다
        code_src.tar.gz  그 파일들의 사본 — 실제 diff 를 볼 수 있다 (0.2~0.6MB)

    git 은 보조다. 대상이 git 이 아닐 수도 있고(gold_ws), git 이어도 개발이
    커밋 안 된 작업 트리에서 진행되기도 한다(white_vote_ws 는 수정 8개였다).
    """
    import tarfile

    if not ws or not (Path(ws) / "src").is_dir():
        return {"n_files": 0, "sha": "no-src"}
    root = Path(ws)
    files, sha = _code_hashes(root)

    try:
        with tarfile.open(run_dir / "code_src.tar.gz", "w:gz") as t:
            for rel in files:
                t.add(root / rel, arcname=rel)
        (run_dir / "code.json").write_text(json.dumps(
            {"workspace": str(root), "sha": sha, "n_files": len(files),
             "files": files, "git": _git_head(root)},
            indent=1, ensure_ascii=False))
    except OSError as e:
        print(f"  ⚠️  코드 사본을 남기지 못했다: {e}")
    return {"n_files": len(files), "sha": sha}


def _code_hashes(root):
    """소스의 파일별 내용 해시와 전체 지문. 아무것도 쓰지 않는다."""
    import hashlib
    files, digest = {}, hashlib.sha256()
    for f in sorted((Path(root) / "src").rglob("*.py")):
        try:
            fh = hashlib.sha1(f.read_bytes()).hexdigest()[:12]
        except OSError:
            continue
        rel = str(f.relative_to(root))
        files[rel] = fh
        digest.update(rel.encode())
        digest.update(fh.encode())
    return files, digest.hexdigest()[:12]


def _fingerprint_legacy(ws):
    """★예전 방식★ 지문 — 이름+크기+mtime.

    2026-09-02 이전 런은 전부 이 방식으로 찍혀 있다. 내용 해시와는 값이 달라서
    바로 견줄 수 없으므로, 옛 런과 대조할 때만 같은 방식으로 다시 계산한다.
    파일을 건드리지 않았으면 mtime 이 그대로라 여전히 맞는다.
    """
    import hashlib
    h = hashlib.sha256()
    if not ws or not (Path(ws) / "src").is_dir():
        return "no-src"
    for f in sorted((Path(ws) / "src").rglob("*.py")):
        try:
            h.update(f.name.encode())
            h.update(str(f.stat().st_size).encode())
            h.update(str(int(f.stat().st_mtime)).encode())
        except OSError:
            pass
    return h.hexdigest()[:12]


def _same_code(run_dir, meta, ws):
    """그 런을 돌린 코드가 ★지금 워크스페이스와 같은가★.

    새 런은 사본(code.json)이 있으니 내용 해시로, 옛 런은 옛 방식으로 견준다.
    되돌려 볼 수 없는 질문이라 모르면 모른다고 답한다(None).
    """
    cj = run_dir / "code.json"
    if cj.exists():
        try:
            want = json.loads(cj.read_text()).get("sha")
        except (OSError, ValueError):
            return None
        return (_code_hashes(Path(ws))[1] == want) if want else None
    then = (meta.get("code_fingerprint") or {}).get("sha")
    if not then or then == "no-src":
        return None
    return _fingerprint_legacy(ws) == then


def _git_head(ws):
    """워크스페이스가 git 이면 HEAD 와 더러운 파일 수 — 아니면 None."""
    try:
        r = subprocess.run(["git", "-C", str(ws), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return None
        d = subprocess.run(["git", "-C", str(ws), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=10)
        return {"head": r.stdout.strip(),
                "dirty": len([ln for ln in d.stdout.splitlines() if ln.strip()])}
    except (OSError, subprocess.SubprocessError):
        return None


def _sigterm_to_interrupt(signum, frame):
    """SIGTERM 을 KeyboardInterrupt 로 바꿔 ★정상 정리 경로를 타게 한다★.

    기본 SIGTERM 은 즉사라 _one_run 의 finally(자식 killpg)가 안 돈다 → 노드·probe
    가 고아로 남는다. timeout·kill 이 보내는 SIGTERM 을 예외로 바꾸면 그 finally 가
    돌아 프로세스 그룹째 정리된다. PDEATHSIG(_child_preexec)는 이게 못 미치는
    경우(SIGKILL)의 백업이다.
    """
    raise KeyboardInterrupt


def cmd_run(args):
    signal.signal(signal.SIGTERM, _sigterm_to_interrupt)
    spec = build_spec(args)
    contract_path = _resolve_contract(args.contract or spec.get("contract"))
    domain = args.domain or random.randint(30, 99)
    stamp = datetime.now().strftime("%m%d_%H%M%S")
    tag = _slug(args.tag or spec.get("name") or "run")

    rd = runs_dir() / f"{stamp}_{tag}"
    print(f"\n▶ 실행 {rd.name}  (ROS_DOMAIN_ID={domain})")
    summary, rd = _one_run(spec, contract_path, rd, domain, args)

    #  ★행 N 을 맨 앞에★ 행이 0 이면 그 뒤의 어떤 숫자도 뜻이 없다.
    print(f"  행 {summary['rows']} · 유효 {summary.get('valid_rate', 0):.2f} · "
          f"드롭 {summary.get('drop_rate', 0):.3f}")
    if not summary["rows"]:
        print("  ⚠️  잰 것이 하나도 없다 — 노드 로그와 「계약 정합」을 먼저 볼 것")
    print(f"  → {rd / 'report.md'}")
    if args.out:
        _export_and_print(rd, args.out)
    return 0


def _slug(s):
    """런 폴더 이름에 쓸 수 있는 꼴로. (표시용 이름은 meta.label 이 따로 갖는다)"""
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in str(s).strip()]
    return "".join(keep).strip("_")[:40] or "run"


def _export_and_print(rd, out):
    from . import export as EX                            # noqa: PLC0415
    r = EX.export(rd, out)
    print(f"\n[export] {r['dest']}")
    if not r["video"]:
        print("  ⚠️ 디버그 영상이 없다 — `tb.run replay <런>` 으로 다시 잡을 수 있다")
    if r["gitignored"]:
        print(f"  .gitignore 에 {EX.OUT_DIRNAME}/ 를 등록했다")


def cmd_replay(args):
    """과거 런을 ★그때 조건 그대로★ 다시 돌린다 — 옛 런의 디버그 영상을 얻는 길.

    디버그 영상은 대상 노드가 그리는 그림이라 ★실행 중에만★ 잡을 수 있다.
    raw.jsonl 에는 계약의 observe 토픽(숫자)만 남아서 사후 생성이 안 된다.
    그래서 "그때 무엇을 보고 그런 값이 나왔나"를 다시 보려면 한 번 더 돌린다.

    ★코드가 그때와 다르면 그건 복원이 아니다★ — 지문을 먼저 대조하고, 다르면
    멈춘다(--force 로 강행하면 '지금 코드로 같은 영상을 돌린 결과'가 된다).
    파라미터도 local.yaml 이 아니라 ★런에 적힌 값★ 을 쓴다.
    """
    signal.signal(signal.SIGTERM, _sigterm_to_interrupt)
    rd0 = _resolve_run(args.run)
    sj = rd0 / "summary.json"
    if not sj.exists():
        raise SystemExit(f"[replay] 계측 결과가 없는 런이다: {args.run}")
    meta = json.loads(sj.read_text())["summary"]["meta"]

    #  ★런 하나가 자기 조건을 통째로 갖고 있다★ 시나리오 파일을 되찾을 필요가 없다
    #  (예전에는 그 파일이 사라지면 재현이 불가능했다).
    spec = {k: meta[k] for k in SPEC_KEYS if meta.get(k) is not None}
    spec["video"] = meta.get("video") or ""
    spec["name"] = meta.get("preset") or meta.get("label") or "replay"

    cf = Path(meta.get("contract_file") or "")
    if not cf.is_file() and meta.get("contract"):
        cf = ROOT / "contracts" / f"{meta['contract']}.yaml"
    contract_path = cf if cf.is_file() else _resolve_contract("")
    contract = load_contract(contract_path)

    print(f"▶ 재현 {rd0.name}  ({meta.get('label') or meta.get('preset') or '?'})")
    if not contract.debug_topics:
        raise SystemExit(
            f"[replay] 계약 '{meta.get('contract')}' 은 debug_topics 가 비어 있다 — "
            "이 노드는 디버그 이미지를 내지 않으므로 다시 돌려도 영상이 없다.")
    if spec["video"] and not Path(spec["video"]).is_file():
        raise SystemExit(f"[replay] 그때 영상이 없다: {spec['video']}")

    same = _same_code(rd0, meta, contract.workspace)
    if same is False and not args.force:
        raise SystemExit(
            f"[replay] ★코드가 그때와 다르다★ — {contract.workspace}\n"
            f"         그때 지문 {(meta.get('code_fingerprint') or {}).get('sha')} / "
            f"지금 {_fingerprint_legacy(contract.workspace)}\n"
            "         지금 돌리면 그때 그림이 아니라 지금 코드의 그림이 나온다.\n"
            "         그래도 돌리려면 --force.")
    print("  코드 대조: " + {True: "그때와 같다 ✅",
                             False: "다르다 (--force) ⚠️",
                             None: "알 수 없다 (지문 없음) ⚠️"}[same])

    args.params_override = meta.get("params") or {}
    args.record_debug = True               # 이걸 하려고 돌리는 것이다
    args.name = getattr(args, "name", "") or f"{rd0.name} 재현"
    args.note = f"{rd0.name} 의 디버그 영상을 다시 잡으려고 돌린 재현 런"
    stamp = datetime.now().strftime("%m%d_%H%M%S")
    rd = runs_dir() / f"{stamp}_replay_{_slug(spec['name'])}"
    summary, rd = _one_run(spec, contract_path, rd,
                           args.domain or random.randint(30, 99), args)
    (rd / "replay_of.txt").write_text(rd0.name + "\n")
    print(f"  행 {summary['rows']}")
    dm = rd / "debug_meta.json"          # 파일 이름은 계약의 토픽에서 나온다
    names = list(json.loads(dm.read_text())) if dm.exists() else []
    print(f"  → 디버그 영상 {rd / names[0]}" if names
          else f"  ⚠️  디버그 영상이 안 생겼다 — {rd / 'viewer.log'} 를 보라")
    if args.out:
        _export_and_print(rd, args.out)
    return 0


def cmd_reanalyze(args):
    """raw.jsonl 만 다시 읽어 신호·리포트를 재생성한다.

    ★계약을 고쳐도 파이프라인을 다시 돌릴 필요가 없다★ — 대상의 메시지 배치가
    바뀌어 계약의 path 를 고쳤을 때, 과거 런들을 새 계약으로 다시 해석할 수 있다.
    이 성질을 지키려고 raw.jsonl 에 원본 메시지를 그대로 남긴다.
    """
    rd = _resolve_run(args.run)
    sj = rd / "summary.json"
    old = json.loads(sj.read_text())["summary"]["meta"] if sj.exists() else {}
    cpath = _resolve_contract(args.contract or contract_of_run(rd))
    contract = load_contract(cpath)
    discard = int(old.get("discard_first", 0))
    rows, nlines = analyze.build_table(rd / "raw.jsonl", contract, discard)
    analyze.write_csv(rows, contract, rd / "signals.csv")
    meta = dict(old)
    meta.update({"run_id": rd.name, "contract": contract.name,
                 "contract_file": str(Path(cpath).resolve()),
                 "contract_version": contract.version, "raw_records": nlines,
                 "discard_first": discard,
                 "reanalyzed": datetime.now().isoformat(timespec="seconds")})
    summary = analyze.summarize(rows, contract, meta)
    summary["log_events"] = analyze.log_events(rd, contract)
    drift = contract.drift_report()
    (rd / "summary.json").write_text(json.dumps(
        {"summary": summary, "drift": drift},
        indent=2, ensure_ascii=False, default=str))
    (rd / "report.md").write_text(analyze.report_run(summary, drift, contract))
    bad = [d for d in drift if d["status"] == "drift" and not d["optional"]]
    print(f"재분석 완료: {rd.name}  행 {len(rows)}  "
          f"계약불일치 {len(bad)}개  → {rd / 'report.md'}")
    if bad:
        print("  ⚠️  계약이 가리키는 자리에 값이 없다 — 숫자를 논하기 전에 계약을 고칠 것")
    return 0


def cmd_diff(args):
    """두 런의 신호를 나란히 놓는다 — ★판정하지 않는다★.

    예전의 `compare` 는 시나리오의 `compare_tol` 로 SAME/DIFF 를 찍었다. 그 허용
    오차 역시 근거가 약한 숫자였고, 「PASS」 한 줄이 나머지를 안 읽게 만들었다.
    지금은 차이만 낸다 — 그게 회귀인지 개선인지는 읽는 사람이 말한다.

    ★맥락은 결과 자신에게서 물려받는다★ 계약이 기본값으로 열리면 없는 컬럼을
    그냥 건너뛰어 ★본 신호가 0개인데 조용히 끝난다★.
    """
    a, b = _resolve_run(args.a), _resolve_run(args.b)
    ameta, bmeta = _result_meta(a / "signals.csv"), _result_meta(b / "signals.csv")
    contract = load_contract(_resolve_contract(
        args.contract or _contract_of_meta(bmeta) or _contract_of_meta(ameta)))
    rows_a = analyze.read_csv(a / "signals.csv")
    rows_b = analyze.read_csv(b / "signals.csv")
    names = contract.compare_signals or sorted(contract.signals)
    st = analyze.diff_stats(rows_a, rows_b, names, contract)

    md = [f"# 차이 — `{a.name}` → `{b.name}`", ""]
    #  ★조건이 다르면 숫자는 뜻이 없다★ 조용히 넘기는 것이 제일 나쁘다.
    pd = _provenance_diff(ameta, bmeta)
    if pd:
        md += ["> ⚠️ **두 런의 실행 조건이 다르다 — 숫자를 그대로 비교할 수 없다.**", ">"]
        md += [f"> - `{k}`: `{va}` → `{vb}`" for k, va, vb in pd] + [""]
    md += [f"- 공통 프레임 {st['n_common']} (A {len(rows_a)} · B {len(rows_b)})",
           f"- 둘 다 정상 검출 {st['n_both_valid']} · "
           f"검출이 엇갈린 비율 {_f(st['mismatch_rate'], 3)}", ""]
    #  ★둘 다 정상인 프레임에서만 값을 비교한다★ 한쪽만 미검출인 프레임을 섞으면
    #  |Δ| 가 "위치 차이"가 아니라 "0 과 실제값의 차이"가 되어, 무엇을 바꾸든
    #  비슷하게 큰 값이 나온다(= 아무것도 구분 못 하는 지표).
    md += ["| 신호 | n | p95\\|Δ\\| | max\\|Δ\\| | rms |", "|---|---:|---:|---:|---:|"]
    rows = []
    for name in names:
        s = st.get(name)
        if not isinstance(s, dict):
            continue
        rows.append((s.get("p95") or 0.0, name, s))
    for _k, name, s in sorted(rows, reverse=True):
        md.append(f"| `{name}` | {s['n']} | {_f(s.get('p95'))} | "
                  f"{_f(s.get('max'))} | {_f(s.get('rms'))} |")
    if not rows:
        md.append("| — | | | | |")
        md += ["", "⚠️ 비교한 신호가 하나도 없다 — 계약이 이 결과의 것이 맞는지 "
               "(`--contract`) 확인할 것."]
    md.append("")
    out = "\n".join(md)
    print(out)
    (b / "diff.md").write_text(out)
    print(f"→ {b / 'diff.md'}")
    return 0


def _f(v, n=4):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{n}g}"
    return str(v)


def cmd_export(args):
    """런 하나를 ★대상 워크스페이스(또는 아무 폴더) 안★ 으로 내보낸다."""
    from . import export as EX                            # noqa: PLC0415
    rd = _resolve_run(args.run)
    r = EX.export(rd, args.out or None)
    print(f"[export] {r['dest']}")
    print(f"  행 {r['verdict']['rows']}  유효 {r['verdict']['valid_rate']}")
    if r["verdict"]["empty"]:
        print("  ⚠️ 행이 0 이다 — 잰 것이 없다")
    if not r["video"]:
        print("  ⚠️ 디버그 영상이 없다 — `tb.run replay <런>` 으로 다시 잡을 수 있다")
    if r["gitignored"]:
        print(f"  .gitignore 에 {EX.OUT_DIRNAME}/ 를 등록했다")
    return 0


# ══════════════════════════════════════════════════════════════════════
#  워크스페이스의 카메라 설정 — ★노드에게 직접 물어본다★
# ══════════════════════════════════════════════════════════════════════
#  캘리브 값의 진짜 주인은 워크스페이스다. 그런데 노드가 그것을 파일이 아니라
#  ★소스의 declare_parameter 기본값★ 으로 갖고 있으면(white1/camera_model.py 가
#  그렇다) 계약에 옮겨 적을 수밖에 없고, 옮겨 적은 값은 반드시 갈라진다.
#
#  그래서 소스를 파싱하거나 import 하지 않고 ★ros2 param dump★ 으로 묻는다.
#  노드를 한 번 띄우고 자기가 선언한 값을 그대로 받아 오는 것이므로
#    · 계약의 결합 규칙을 깨지 않는다(노드 이름만 계약에서 온다)
#    · camera_model.py 의 기본값이 바뀌면 ★다음 dump 에서 저절로 따라온다★
#
#  ⚠️ ★시나리오 params 는 주지 않는다★ 그건 시험용 지그 값이라 '워크스페이스
#     기본값' 이 아니다. 가중치·device 처럼 ★기계에 묶인 것만★ local.yaml 에서 준다
#     (안 주면 .engine 을 찾다 실패해 기동이 느려진다).
def params_cache_path(contract):
    return ROOT / "runs" / "_params" / f"{contract.name}.yaml"    # 쓰기 전에 runs_dir()


def load_ws_params(contract):
    """`tb.run params` 가 받아 둔 ★워크스페이스 기본값★ 캐시. 없으면 {}.

    캘리브 화면과 도구가 '노드가 실제로 쓰는 값' 에서 출발할 수 있게 하는 것이
    목적이다. 값의 우선순위는 ★시나리오/local params → 이 캐시 → 계약의 default★ 다.
    """
    f = params_cache_path(contract)
    if not f.exists():
        return {}
    try:
        return (load_yaml(f).get("params") or {})
    except (yaml.YAMLError, OSError):
        return {}


def dump_node_params(contract_path, timeout=90.0, local_only=True):
    """대상 노드들의 파라미터를 그대로 받아 온다. {노드id: {이름: 값}}"""
    contract = load_contract(contract_path)
    if contract.attach:
        raise SystemExit("[params] attach 계약은 돌고 있는 시스템에서 직접 dump 할 것")
    loc = local_overrides()
    lp = (loc.get("params") or {}) if local_only else {}
    env = dict(os.environ)
    env["ROS_DOMAIN_ID"] = str(random.randint(30, 200))
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    for k, v in (loc.get("env") or {}).items():
        env[str(k)] = str(v)
    pref = ws_prefix(contract)
    out, procs = {}, []
    tmp = runs_dir("_params")
    try:
        for n in contract.nodes:
            merged = _deep_merge(n.get("params") or {}, lp.get(n["id"], {}))
            pargs = " ".join(f"-p {_param_arg(k, v)}" for k, v in merged.items())
            rename = f"-r __node:={n['node_name']}" if n.get("node_name") else ""
            cmd = (f"{pref}exec ros2 run {n['package']} {n['executable']} "
                   f"--ros-args {rename} {pargs}")
            procs.append((n, Proc(f"params_{n['id']}", cmd,
                                  tmp / f"{n['id']}.log", env)))
        # 노드가 파라미터를 선언하고 그래프에 올라올 때까지 기다린다
        for n, _pr in procs:
            node = n.get("node_name") or n["executable"]
            t0 = time.time()
            while time.time() - t0 < timeout:
                r = subprocess.run(["bash", "-c",
                                    f"{pref}ros2 param dump /{node} --print"],
                                   capture_output=True, text=True, env=env)
                if r.returncode == 0 and r.stdout.strip():
                    try:
                        y = yaml.safe_load(r.stdout) or {}
                    except yaml.YAMLError:
                        y = {}
                    # {/node: {ros__parameters: {...}}}
                    for _k, v in y.items():
                        kv = (v or {}).get("ros__parameters") or {}
                        kv.pop("use_sim_time", None)
                        if kv:
                            out[n["id"]] = kv
                    if out.get(n["id"]):
                        break
                time.sleep(1.0)
    finally:
        for _n, pr in procs:
            pr.stop()
    return out


def cmd_params(args):
    """대상 노드의 파라미터를 받아 적어 둔다 — 캘리브의 ★출발점★ 이 된다."""
    cpath = _resolve_contract(args.contract or
                              (load_yaml(args.scenario).get("contract")
                               if args.scenario else None))
    contract = load_contract(cpath)
    print(f"노드를 띄워 파라미터를 묻는다 — {contract.name} "
          f"(노드 {len(contract.nodes)}개, 최대 {args.timeout:.0f}초)")
    got = dump_node_params(cpath, timeout=args.timeout)
    if not got:
        print("⛔ 받아 오지 못했다 — runs/_params/*.log 를 볼 것 "
              "(가중치 경로·GPU 문제일 수 있다)")
        return 1
    body = yaml.safe_dump({"params": got}, allow_unicode=True, sort_keys=False,
                          default_flow_style=None)
    out = Path(args.out) if args.out else params_cache_path(contract)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# {contract.name} — ★노드가 스스로 선언한 파라미터★ (ros2 param dump)\n"
        f"# {datetime.now().isoformat(timespec='seconds')} · 워크스페이스 "
        f"{contract.workspace}\n"
        "# 이 파일은 캐시다. 시나리오·local.yaml 에 자동으로 반영되지 않는다 —\n"
        "# 캘리브 화면의 «워크스페이스 기본값 불러오기» 가 여기서 읽어 간다.\n"
        + body)
    for nid, kv in got.items():
        print(f"  {nid}: 파라미터 {len(kv)}개")
    print(f"→ {out}")
    return 0


def _calib_snapshot(contract, run_dir):
    """이 런의 ★기하★ — 캘리브 대상 파라미터의 실효값. [2026-08-25]

    ★meta 의 params 로는 모자란다★ 거기엔 테스트베드가 ★요청한★ 것만 있다.
    사다리꼴·범퍼행·문턱을 아무도 요청하지 않으면 노드가 자기 기본값으로 도는데,
    그 기본값은 워크스페이스를 재캘리브하면 ★말없이 바뀐다★. 그러면 sl_px 의 뜻
    자체가 달라지는데 조건은 '같다' 고 나와서, 회귀 비교가 ★기하가 바뀐 것을 노드
    회귀로 읽는다★ — 있지도 않은 회귀를 쫓게 된다. 그래서 조건에 실효값을 넣는다.

    ★캘리브 대상만★ 담는다. params_actual 을 통째로 넣으면 노드가 파라미터 하나
    늘릴 때마다 기준이 전부 무효가 된다(가중치 경로로 이미 겪은 실수다).
    이름은 전부 계약이 준다 — 이 함수에도 워크스페이스 고유명이 들어오지 않는다.
    """
    cal = contract.raw.get("calibration") or {}
    pa = Path(run_dir) / "params_actual.yaml"
    if not cal or not pa.exists():
        return None
    try:
        actual = load_yaml(pa).get("params") or {}
    except (yaml.YAMLError, OSError):
        return None
    names = []
    u = cal.get("undistort") or {}
    if u.get("param"):
        names.append(u["param"])       # 보정이 꺼졌으면 기하가 통째로 다르다
    for t in (cal.get("targets") or {}).values():
        names += t.get("params") or ([t["param"]] if t.get("param") else [])
    out = {}
    for n in contract.nodes:
        kv = actual.get(n["id"]) or {}
        got = {k: kv[k] for k in names if k in kv}
        if got:
            out[n["id"]] = got
    return out or None


def _provenance(meta):
    """이 결과가 '어떤 조건에서 나온 것인가' — 비교 가능 여부를 가르는 것들만."""
    return {
        "video": meta.get("video"),
        "video_key": meta.get("video_key"),
        "mode": meta.get("mode"),
        "perturb": meta.get("perturb"),
        "overlay": meta.get("overlay"),
        "start": meta.get("start"), "limit": meta.get("limit"),
        "stride": meta.get("stride"),
        "contract": meta.get("contract"),
        "params": meta.get("params"),
        # ★기하★ 요청값이 같아도 이게 다르면 픽셀의 뜻이 다르다 — 비교 불가다.
        #   옛 런에는 없다(None) → None 끼리는 같으므로 종전 기준은 그대로 산다.
        "calib": meta.get("calib"),
    }


def _provenance_diff(a, b):
    """두 결과의 조건 차이. 비어 있으면 같은 조건에서 나온 것.

    `params`·`calib` 처럼 dict 인 것은 ★바뀐 키만★ 펴서 내놓는다 — 통째로 찍으면
    파라미터 20개짜리 dict 두 벌이 한 줄로 나와서, 정작 무엇이 달라졌는지
    (가중치가 .engine → .pt 로 바뀌었다 같은 것) 아무도 못 읽는다.
    """
    pa, pb = _provenance(a), _provenance(b)
    out = []
    for k in pa:
        va, vb = pa.get(k), pb.get(k)
        if va == vb:
            continue
        if isinstance(va, dict) and isinstance(vb, dict):
            out.extend(_flat_diff(f"{k}.", va, vb))
        else:
            out.append((k, va, vb))
    return out


def _flat_diff(prefix, a, b):
    """중첩 dict 두 벌의 차이를 `노드.파라미터` 한 줄씩으로."""
    out = []
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        if va == vb:
            continue
        if isinstance(va, dict) and isinstance(vb, dict):
            out.extend(_flat_diff(f"{prefix}{k}.", va, vb))
        else:
            out.append((prefix + k, va, vb))
    return out


def _result_meta(csv_path):
    """그 결과가 ★어떤 조건에서 나왔는가★ — 런이든 기준이든 같은 모양으로 돌려준다.

    런은 `summary.json` 의 meta, 기준은 `baselines/<이름>.json`(등록할 때 그 meta 를
    통째로 복사해 둔 것). 아무것도 모르면 빈 dict — 이때는 조건 비교를 하지 않는다
    (모르는 것을 「다르다」로 세면 경고가 늘 켜져 아무도 안 보게 된다).
    """
    p = Path(csv_path)
    if p.parent == ROOT / "baselines":
        j = p.with_suffix(".json")
        return json.loads(j.read_text()) if j.exists() else {}
    sj = p.parent / "summary.json"
    if not sj.exists():
        return {}
    try:
        return json.loads(sj.read_text())["summary"]["meta"]
    except (KeyError, ValueError):
        return {}


def _contract_of_meta(meta):
    """meta 가 가리키는 계약 파일. 경로가 먼저, 옛 결과(경로가 없다)면 이름으로 찾는다."""
    f = (meta or {}).get("contract_file")
    if f and Path(f).exists():
        return f
    want = (meta or {}).get("contract")
    if not want:
        return None
    for cand in sorted((ROOT / "contracts").glob("*.yaml")):
        try:
            if load_contract(cand).name == want:
                return str(cand)
        except Exception:                                  # noqa: BLE001
            continue
    return None


def contract_of_run(run_dir):
    """그 런이 ★실제로 쓴★ 계약 파일. 없으면 None.

    메타의 contract_file 을 먼저 보고, 옛 런(그 항목이 없다)이면 이름으로 찾는다.
    이것이 없으면 `--contract` 를 생략한 render·harvest·reanalyze·compare 가 기본
    계약으로 열려서, 신호가 전부 결측인 그림·리포트를 조용히 만들어 낸다.
    (compare 는 더 나쁘다 — 볼 신호가 하나도 안 남아도 「PASS」로 끝난다.)
    """
    return _contract_of_meta(_result_meta(Path(run_dir) / "signals.csv"))


def _resolve_run(x):
    p = Path(x)
    if p.is_dir():
        return p
    p = ROOT / "runs" / x
    if p.is_dir():
        return p
    raise SystemExit(f"[run] 런을 찾을 수 없다: {x}")


def cmd_build(args):
    """대상 워크스페이스를 빌드한다 — ★고치고 다시 돌리는 고리의 첫 칸★.

    ★언제 필요한가★ ★파이썬 코드만 고쳤으면 필요 없다★ — `--symlink-install` 이라
    `build/<pkg>/<pkg>` 가 `src/<pkg>/<pkg>` 를 가리키는 심볼릭 링크고, `ros2 run` 이
    읽는 파일이 곧 편집하는 파일이다. 빌드가 필요한 때는 넷뿐이다:
      · `setup.py` 의 entry_points 를 바꿨다 (console_scripts 를 다시 만들어야 한다)
      · `package.xml` 의 의존성을 바꿨다
      · C++ 패키지다 / 새 파일을 launch·resource 처럼 ★실제 디렉터리★ 쪽에 넣었다
      · 처음 한 번 (install/setup.bash 가 아직 없다)
    ⚠️ `stale_sources()` 의 경고는 mtime 만 비교하므로 ★파이썬만 고쳐도 뜬다★ —
    그건 무시해도 되는 경고다. 그래도 이 명령을 두는 것은 위 넷일 때 터미널로
    나가지 않게 하려는 것이다.

    ★워크스페이스 이름이 여기 안 들어온다★ 경로도 패키지도 전부 계약에서 나온다.
    """
    contract_path = _resolve_contract(
        args.contract or (load_yaml(args.scenario).get("contract")
                          if args.scenario else None))
    if not (contract_path and Path(contract_path).exists()):
        print("계약을 찾을 수 없다 — --contract 나 --scenario 로 지정할 것")
        return 1
    c = load_contract(contract_path)
    if c.attach:
        print("attach 계약이다 — 테스트베드가 노드를 띄우지 않으므로 빌드할 것도 없다")
        return 0
    ws = Path(c.workspace) if c.workspace else None
    if not (ws and ws.is_dir()):
        print(f"워크스페이스가 없다: {c.workspace}")
        return 1

    cmd = ["colcon", "build", "--symlink-install"]
    pkgs = sorted({str(n["package"]) for n in c.nodes if n.get("package")})
    if args.all or not pkgs:
        print(f"빌드: {ws} (전체)")
    else:
        # ★--packages-select 가 아니라 -up-to★ 인 이유: 대상 패키지가 다른 패키지를
        #   exec_depend 로 걸고 있으면 select 로는 그 의존이 안 서서 실행이 실패한다.
        cmd += ["--packages-up-to"] + pkgs
        print(f"빌드: {ws} ({', '.join(pkgs)} + 의존)")
    print("  $ " + " ".join(cmd))
    # ★버퍼를 비우고 넘긴다★ 안 그러면 colcon 출력이 먼저 나오고 우리 머리말이
    #   뒤에 붙는다 — 웹의 로그 창에서 무엇을 빌드하는지 끝나야 알게 된다.
    sys.stdout.flush()
    # shell=False. 환경은 그대로 물려준다 — 웹 서버도 CLI 도 ROS 언더레이만
    # source 된 상태이고, 대상의 install 은 안 물려 있다(빌드에 맞는 환경이다).
    rc = subprocess.run(cmd, cwd=str(ws)).returncode
    if rc != 0:
        print(f"\n빌드 실패 (종료코드 {rc}) — 위 오류를 먼저 고칠 것")
        return rc
    # ★빌드가 정말 최신이 됐는가★ 를 되재서 확인한다. colcon 이 0 을 냈어도
    #   패키지를 잘못 골랐으면 고친 파일이 그대로 남는다.
    from .config import stale_sources
    left = stale_sources(ws)
    if left:
        print("\n⚠️  아직 빌드보다 새로운 소스가 있다 — 다른 패키지의 파일이다:")
        for f in left:
            print(f"      {f}")
        print("      `--all` 로 전체를 빌드하거나, 그 패키지를 계약의 nodes 에 넣을 것")
    else:
        print("\n빌드 완료 — 소스와 빌드가 같다")
    return 0


def cmd_verify(args):
    """스튜디오가 그리는 BEV 와 ★노드가 실제로 만든 BEV★ 가 같은지 대조한다.

    ★스튜디오가 정적 페이지로 옮겨 간 뒤로 이 대조는 여기에만 있다★ [2026-09-06]
    브라우저는 노드를 볼 수 없다. 화면의 기하가 cv2 와 같은지는 자체 검사
    (t_geom_js)가 증명하지만, 「그 cv2 파이프라인이 ★이 노드★ 와 같은가」는
    노드가 실제로 뱉은 그림과 맞춰 봐야만 안다 — 그것이 이 명령이다.

        python3 -m tb.run verify --run runs/0902_190358_lane_night_a_base

    쓰는 값은 런이 남긴 `params_actual.yaml` 이다 — 노드가 그때 정말로 쓴 값이라
    「내가 준 값」이 아니라 「노드가 쓴 값」으로 잰다.
    """
    from .calibrate import Calib, verify        # noqa: PLC0415

    run = Path(args.run).expanduser()
    if not run.is_dir():
        run = runs_dir() / args.run
    if not run.is_dir():
        print(f"⛔ 런 폴더가 없다: {args.run}")
        return 2

    contract = load_contract(_resolve_contract(args.contract))
    pa = run / "params_actual.yaml"
    params = (load_yaml(pa).get("params") or {}) if pa.is_file() else local_overrides()
    if not pa.is_file():
        print("알림: params_actual.yaml 이 없어 local.yaml 값으로 잰다 "
              "(노드가 쓴 값과 다를 수 있다)")
    cal = Calib(contract, params)

    cfg = (contract.raw.get("calibration") or {}).get("verify") or {}
    dbg = run / cfg.get("video", "lane_debug.mp4")
    video = args.video
    pj = run / "player.json"
    if not video and pj.is_file():
        video = (json.loads(pj.read_text()) or {}).get("video", "")
    if not video:
        print("⛔ 원본 영상을 모른다 — --video 로 준다")
        return 2

    out_png = run / "calib_verify.png"
    try:
        r = verify(cal, contract, video, dbg, args.start, out_png=str(out_png))
    except ValueError as e:
        print(f"⛔ {e}")
        return 2
    for ln in r["log"]:
        print("  " + ln)
    print(f"\n에지 일치율 중앙값 {r['median']:.3f} "
          f"(프레임 {r['n']}장 · 오프셋 {r['offset']})")
    print(f"  대조 그림: {out_png}")
    #  ★판정하지 않는다★ 0.75 는 「같은 변환이면 대개 이 위」라는 실측 관찰이지
    #  합격선이 아니다 — 숫자를 주고 읽는 것은 사람이 한다.
    print("  참고: 같은 변환이면 대개 0.75 이상이 나온다 "
          "(노드가 BEV 위에 그리는 곡선·HUD 때문에 1.0 은 안 된다)")
    return 0


def cmd_list(_args):
    print("── 프리셋 ──")
    for p in sorted((ROOT / "presets").glob("*.yaml")):
        print(f"  {p.name}")
    print("── 최근 런 ──")
    dirs = [d for d in runs_dir().iterdir()
            if d.is_dir() and not d.name.startswith("_")]
    for d in sorted(dirs, key=lambda x: x.name, reverse=True)[:20]:
        sj = d / "summary.json"
        if sj.is_file():
            try:
                s = json.loads(sj.read_text())["summary"]
                lbl = (s.get("meta") or {}).get("label") or ""
                print(f"  {d.name:<40} 행 {s.get('rows', 0):<6} "
                      f"유효 {s.get('valid_rate', 0):.2f}  {lbl}")
                continue
            except Exception:   # noqa: BLE001
                pass
        print(f"  {d.name:<40} (계측 없음)")
    return 0


def _add_spec_args(p, run=True):
    """재생 조건 인자 — doctor 와 run 이 같은 것을 받는다(같은 해석이라야 한다)."""
    p.add_argument("--contract", default="", help="계약 YAML (비우면 프리셋/기본값)")
    p.add_argument("--preset", default="", help="자주 쓰는 조합 (presets/*.yaml)")
    p.add_argument("--video", default="", help="영상 절대경로")
    if not run:
        return
    p.add_argument("--start", type=int, default=None, help="시작 프레임")
    p.add_argument("--limit", type=int, default=None, help="몇 프레임까지 (0=끝까지)")
    p.add_argument("--stride", type=int, default=None, help="몇 프레임에 하나씩")
    p.add_argument("--mode", default=None,
                   choices=["lockstep", "realtime", "asfast"],
                   help="lockstep=기계 속도와 무관하게 같은 결과 / "
                        "realtime=실차 타이밍 재현")
    p.add_argument("--rate", type=float, default=None, help="realtime 배속")
    p.add_argument("--perturb", default=None,
                   help="영상 섭동 (none/blur/dark/noise… — 강건성 대조용)")
    p.add_argument("--discard-first", type=int, default=None, dest="discard_first",
                   help="앞 N 프레임 버리기 (워밍업 잔재)")
    p.add_argument("--param", action="append", default=[], metavar="노드.이름=값",
                   help="노드 파라미터 덮어쓰기 (여러 번)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tb.run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="환경·계약·영상 점검 (아무것도 바꾸지 않는다)")
    _add_spec_args(d)
    d.set_defaults(fn=cmd_doctor, param=[], start=None, limit=None, stride=None,
                   mode=None, rate=None, perturb=None, discard_first=None)

    r = sub.add_parser("run", help="영상을 밀어 넣고 계측한다")
    _add_spec_args(r)
    r.add_argument("--out", default="",
                   help="결과를 내보낼 폴더 (보통 대상 워크스페이스)")
    r.add_argument("--name", default="", help="이 런에 붙일 이름 (공백·한글 가능)")
    r.add_argument("--note", default="", help="무엇을 보려고 돌렸는가 — 결과에 남는다")
    r.add_argument("--tag", default="", help="런 폴더 이름에 쓸 꼬리표")
    r.add_argument("--domain", type=int, default=0, help="ROS_DOMAIN_ID (0=임의)")
    r.add_argument("--no-record-debug", dest="record_debug", action="store_false",
                   help="디버그 영상을 남기지 않는다 (기본은 남긴다 — 런당 2~7MB)")
    r.set_defaults(fn=cmd_run, record_debug=True)

    rp = sub.add_parser("replay", help="옛 런을 그때 조건으로 다시 돌린다 (디버그 영상)")
    rp.add_argument("run")
    rp.add_argument("--out", default="", help="결과를 내보낼 폴더")
    rp.add_argument("--name", default="")
    rp.add_argument("--force", action="store_true",
                    help="코드가 그때와 달라도 강행 (그때 그림이 아니게 된다)")
    rp.add_argument("--domain", type=int, default=0)
    rp.set_defaults(fn=cmd_replay)

    ra = sub.add_parser("reanalyze", help="계약을 고친 뒤 raw.jsonl 만 다시 읽는다")
    ra.add_argument("run")
    ra.add_argument("--contract", default="")
    ra.set_defaults(fn=cmd_reanalyze)

    df = sub.add_parser("diff", help="두 런의 신호 차이 (판정하지 않는다)")
    df.add_argument("a")
    df.add_argument("b")
    df.add_argument("--contract", default="")
    df.set_defaults(fn=cmd_diff)

    ex = sub.add_parser("export", help="런을 워크스페이스(또는 폴더)로 내보낸다")
    ex.add_argument("run")
    ex.add_argument("--out", default="",
                    help="받을 폴더 (비우면 런이 기록한 워크스페이스)")
    ex.set_defaults(fn=cmd_export)

    pr = sub.add_parser("params", help="대상 노드가 스스로 선언한 파라미터를 받아 적는다")
    pr.add_argument("--contract", default="")
    pr.add_argument("--out", default="")
    pr.add_argument("--timeout", type=float, default=90.0)
    pr.set_defaults(fn=cmd_params)

    bd = sub.add_parser("build", help="대상 워크스페이스를 colcon build")
    bd.add_argument("--contract", default="")
    bd.add_argument("--all", action="store_true", help="워크스페이스 전체를 빌드")
    bd.set_defaults(fn=cmd_build)

    vf = sub.add_parser("verify", help="런의 디버그 영상과 BEV 기하를 대조한다")
    vf.add_argument("--run", required=True, help="런 폴더 (runs/ 안의 이름도 된다)")
    vf.add_argument("--contract")
    vf.add_argument("--video", default="", help="원본 영상 (없으면 런의 player.json)")
    vf.add_argument("--start", type=int, default=0)
    vf.set_defaults(fn=cmd_verify)

    ls = sub.add_parser("list", help="프리셋과 최근 런")
    ls.set_defaults(fn=cmd_list)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        print("\n중단했다.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
