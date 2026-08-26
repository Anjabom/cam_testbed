"""오케스트레이터 — 노드를 띄우고, 영상을 밀고, 기록하고, 분석한다.

노드는 subprocess (`ros2 run …`) 로만 다룬다. import 하지 않는다.
그래서 white 패키지 안이 어떻게 바뀌든 이 파일은 바뀌지 않는다.

사용:
  python3 -m tb.run doctor
  python3 -m tb.run run  --scenario scenarios/regression.yaml
  python3 -m tb.run compare <baseline이름|런디렉토리> <런디렉토리>
  python3 -m tb.run baseline <런디렉토리> --name track_record
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

    우선순위: 인자로 준 것 → 시나리오의 contract: → local.yaml 의 default_contract
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
        "[tb] 계약이 여러 개다 — 시나리오의 `contract:` 나 --contract 로 고를 것:\n  "
        + "\n  ".join(str(c.relative_to(ROOT)) for c in cands))


def resolve_video(sc, loc):
    """시나리오의 video: 를 실제 파일 경로로 푼다.

    시나리오에는 ★논리 이름★을 적고 실제 경로는 local.yaml 의 videos: 에 둔다.
    영상은 머신마다·대회마다 다르므로 시나리오 파일에 절대경로를 박으면
    다른 사람/다른 머신에서 그대로 못 쓴다.

        # scenarios/regression.yaml
        video: track_a
        # local.yaml
        videos:
          track_a: /home/me/2026_대회/run1.mp4

    논리 이름이 videos: 에 없으면 그대로 경로로 취급한다(빠른 일회성 실행용).
    local.yaml 의 최상위 video: 는 모든 시나리오를 덮어쓰는 비상 스위치.
    """
    v = loc.get("video") or sc.get("video")
    if not v:
        return None
    return str(Path(str((loc.get("videos") or {}).get(v, v))).expanduser())


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
def cmd_doctor(args):
    sc = load_yaml(args.scenario) if args.scenario else {}
    loc = local_overrides()
    contract_path = _resolve_contract(args.contract or sc.get("contract"))
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
    # 웹앱의 <video> 는 H.264/VP9 만 재생한다. OpenCV 기본 코덱(mp4v)으로
    # 찍히면 오류도 없이 검은 화면이 된다 → 여기서 미리 알려 준다.
    cap = encode.capability()
    chk("영상 코덱", not cap.startswith("cv2/mp4v"),
        f"{cap} — mp4v 뿐이면 웹앱에서 재생 불가. `sudo apt install ffmpeg`")

    print("── 계약 ──")
    chk("계약 파일", contract_path is not None and Path(contract_path).exists(),
        str(contract_path))
    if not (contract_path and Path(contract_path).exists()):
        print("\n판정: 문제 있음")
        return 1
    c = load_contract(contract_path)
    print(f"     `{c.name}` v{c.version} · 노드 {len(c.nodes)} · "
          f"관찰토픽 {len(c.topics())} · 신호 {len(c.signals)}")

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

    if sc:
        print("── 시나리오 ──")
        params = _deep_merge(sc.get("params", {}), loc.get("params", {}))
        vid = resolve_video(sc, loc)
        chk("영상", bool(vid) and Path(vid).exists(),
            f"{sc.get('video')!r} → {vid}")
        ids = {n["id"] for n in c.nodes}
        for nid, kv in params.items():
            if nid not in ids:
                # ★local.yaml 은 계약 여럿이 함께 쓴다★ 다른 계약의 노드 이름이
                # 섞여 있는 것은 정상이다(가중치 경로는 계약이 아니라 기계에 묶인다).
                # 시나리오에 적힌 오타만 실패로 본다 — 그건 조용히 무시되면 곤란하다.
                if nid in (sc.get("params") or {}):
                    chk(f"params.{nid} 가 계약에 없는 노드", False,
                        "시나리오의 이름이 계약의 nodes[].id 와 달라 무시된다")
                else:
                    print(f"  ·  local.yaml 의 params.{nid} 는 이 계약과 무관 "
                          f"(다른 계약용) — 무시된다")
                    continue
            for k, v in kv.items():
                if not (isinstance(v, str) and "/" in v):
                    continue
                if v.endswith(".pt") or v.endswith(".engine"):
                    chk(f"{nid}.{k}", Path(v).exists(), v)

    # ── 이름 정합 ────────────────────────────────────────────────────
    #   체크가 없는 신호를 가리켜도 런은 죽지 않는다 — ok:None(⚠️ 값 없음)으로
    #   조용히 빠지고 리포트는 초록으로 나온다. 그걸 ★런을 돌리기 전에★ 묻는다.
    print("── 이름 정합 ──")
    from .lint import lint
    #  캐시를 같이 넘겨 ★계약의 default 가 노드와 어긋났는지★ 도 본다.
    #  (계약의 default 는 노드 기본값을 옮겨 적은 문서라 조용히 낡는다)
    problems = lint(c, sc, load_ws_params(c))
    chk("계약·시나리오의 이름 참조", not problems,
        "" if not problems else f"{len(problems)}건")
    for m in problems:
        print(f"     · {m}")
    print()
    print("판정:", "OK" if ok else "문제 있음")
    return 0 if ok else 1


# ══════════════════════════════════════════════════════════════════════
def _one_run(sc, contract_path, variant, run_dir, domain_id, args):
    run_dir.mkdir(parents=True, exist_ok=True)
    loc = local_overrides()
    contract = load_contract(contract_path)

    env = dict(os.environ)
    env["ROS_DOMAIN_ID"] = str(domain_id)
    env["RCUTILS_LOGGING_BUFFERED_STREAM"] = "0"
    watching = bool(getattr(args, "watch", False))
    if not watching:
        # 헤드리스 기본값. --watch 일 때는 진짜 창을 띄워야 하므로 건드리지 않는다.
        env.setdefault("QT_QPA_PLATFORM", "offscreen")
    # 1080p 이미지(6.2MB)를 무손실로 보내기 위한 Fast DDS 프로파일.
    # 없으면 SHM 세그먼트(기본 512KB)에 안 들어가 UDP 로 폴백 → 실측 5.5% 프레임 유실.
    # 자세한 이유는 fastdds_profile.xml 주석 참고. local.yaml 의 env 로 덮어쓸 수 있다.
    prof = ROOT / "fastdds_profile.xml"
    if prof.exists() and "FASTRTPS_DEFAULT_PROFILES_FILE" not in env:
        env["FASTRTPS_DEFAULT_PROFILES_FILE"] = str(prof)
    for k, v in (loc.get("env") or {}).items():
        env[str(k)] = str(v)

    # 시나리오 < 변형 < local.yaml 순으로 덮인다.
    #   ★변형이 파라미터를 바꿀 수 있다★ — '기능을 끄고 같은 영상을 돌린다' 같은
    #   대조군은 섭동이 아니라 파라미터로 만들어야 하고(예: 그 기능만 false),
    #   그래야 base 와 나란히 비교표에 오른다.
    #   local 이 맨 뒤인 것은 가중치 경로처럼 ★기계에 묶인 값★ 이라서다.
    params = _deep_merge(_deep_merge(sc.get("params", {}),
                                     variant.get("params", {}) or {}),
                         loc.get("params", {}))
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
        if sc.get("sim_time"):
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

        # ── 2.5) 뷰어 ────────────────────────────────────────────────
        #   --watch  창을 띄워 보면서 space/n 으로 세워 가며 디버깅
        #   --record-debug  창 없이 디버그 영상을 mp4 로만 남긴다(헤드리스 리뷰용)
        #  ★저작 키가 있으면 디버그 영상이 없어도 띄운다★ 볼 그림은 없지만 누를 것이
        #  있다(계약의 stimulus.aux[].keys → 타임라인 저작). 녹화는 그림이 있을 때만.
        authoring = any((a.get("keys") or {}) for a in (contract.aux or []))
        if ((watching and (contract.debug_topics or authoring))
                or (getattr(args, "record_debug", False) and contract.debug_topics)):
            # 디버그 영상도 원본과 같은 속도로 저장한다(안 그러면 느리게 보인다).
            vfps = sc.get("view_fps") or _video_fps(resolve_video(sc, loc)) or 15.0
            vcmd = (f"{pref}exec python3 -m tb.viewer "
                    f"--contract '{contract_path}' "
                    f"--save-dir '{run_dir}' --scale {sc.get('view_scale', 0.6)} "
                    f"--fps {vfps:.3f} "
                    f"--jpeg '{run_dir / 'latest.jpg'}'"
                    + ("" if watching else " --headless"))
            viewer = Proc("viewer", vcmd, run_dir / "viewer.log",
                          {**env, "PYTHONPATH": f"{ROOT}:{env.get('PYTHONPATH', '')}"})
            procs.append(viewer)
            print("  기동: viewer" + ("  (space=정지 n=한프레임 s=스냅샷 q=닫기)"
                                      if watching else "  (mp4 저장만)"))
            if watching and authoring:
                print("     타임라인 저작 키: "
                      + "  ".join(f"[{k}]{v.get('label', k)}"
                                  for a in contract.aux
                                  for k, v in (a.get("keys") or {}).items())
                      + f"  → 끝나면 {run_dir / 'schedule.yaml'}")
        elif watching and not contract.debug_topics:
            print("  ⚠️  계약에 debug_topics 도 저작 키도 없어 볼 것이 없다")

        # ── 3) 자극 투입 ─────────────────────────────────────────────
        #   image_topic 이 없으면 영상 없이 그냥 duration 만큼 기록만 한다.
        #   attach 모드(돌고 있는 시스템 관찰)에서 쓴다.
        if not contract.image_topic:
            dur = float(sc.get("record_seconds", 10.0))
            print(f"  기록만 {dur:.0f}s (영상 자극 없음)")
            t0 = time.time()
            while time.time() - t0 < dur:
                time.sleep(0.2)
            wall = time.time() - t0
            rc = 0
            raise _Recorded(wall)

        video = resolve_video(sc, loc)
        if not video or not Path(video).exists():
            raise SystemExit(
                f"[run] 영상이 없다: {video!r}\n"
                f"      시나리오의 video: 는 논리 이름이고 실제 경로는 "
                f"local.yaml 의 videos: 에 둔다.")
        #  ★타임라인은 시나리오가 소유한다★ 계약의 aux 는 토픽·타입·저작키를 정의하고,
        #  '어느 프레임에서 무엇이 되는가' 는 시험마다 달라 시나리오/변형이 준다.
        #  시나리오의 aux_schedule 를 토픽으로 맞춰 계약 aux 항목에 얹는다(계약 불변).
        sched = _deep_merge(sc.get("aux_schedule") or {},
                            variant.get("aux_schedule") or {})
        aux_specs = [dict(a, schedule=sched[a["topic"]]) if a["topic"] in sched
                     else a for a in contract.aux]
        aux = json.dumps(aux_specs)
        #  ★합성 자극★ 시나리오 < 변형 순으로 덮는다. 상대경로는 테스트베드 루트 기준
        #  (영상처럼 머신마다 다른 것이 아니라 ★시험 재료★ 라서 저장소에 함께 둔다).
        ovl = _deep_merge(sc.get("overlay") or {}, variant.get("overlay") or {})
        if ovl.get("image") and not str(ovl["image"]).startswith("/"):
            ovl = dict(ovl, image=str(ROOT / str(ovl["image"])))
        play = (f"{pref}exec python3 -m tb.player "
                f"--video '{video}' --image-topic '{contract.image_topic}' "
                f"--sync-topic '{contract.sync_topic or ''}' "
                f"--mode {sc.get('mode', 'lockstep')} "
                f"--rate {sc.get('rate', 1.0)} --start {sc.get('start', 0)} "
                f"--limit {sc.get('limit', 0)} --stride {sc.get('stride', 1)} "
                f"--warmup-s {sc.get('warmup_s', 6.0)} "
                f"--sync-timeout {sc.get('sync_timeout', 15.0)} "
                f"--sync-retries {sc.get('sync_retries', 2)} "
                # ★동기 신호 뒤 여유★ 받는 쪽이 이 프레임의 결과를 다 낼 때까지
                #   조금 더 돈다. 동기 토픽보다 ★뒤에★ 나오는 값(두 번째 추론의
                #   결과나 타이머로 나가는 진단값)이 있으면 이게 짧을 때 그 값이
                #   ★다음 프레임 행★ 에 붙는다. 기본 20ms.
                f"--sync-settle-ms {sc.get('sync_settle_ms', 20)} "
                f"--prime {int(sc.get('prime', 0))} "
                f"--sync-timeout-first {sc.get('sync_timeout_first', 90.0)} "
                f"--perturb '{variant.get('perturb', 'none')}' "
                f"{'--sim-time' if sc.get('sim_time') else ''} "
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
                print(f"  ⚠️  노드 {p.name} 가 도중에 죽었다 → {run_dir / (p.name + '.log')}")
        if rc != 0 and not args.keep_going:
            print(f"  ⚠️  player 종료코드 {rc}")
    except _Recorded as e:
        wall, rc = e.wall, 0
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

    # ── 4) 분석 ─────────────────────────────────────────────────────
    pstats = {}
    pj = run_dir / "player.json"
    if pj.exists():
        pstats = json.loads(pj.read_text())

    rows, nlines = analyze.build_table(run_dir / "raw.jsonl", contract,
                                      int(sc.get("discard_first", 0)))
    analyze.write_csv(rows, contract, run_dir / "signals.csv")
    meta = {
        "run_id": run_dir.name, "scenario": sc.get("name", "?"),
        "variant": variant.get("name", "base"),
        "contract": contract.name, "contract_version": contract.version,
        # ★계약 파일 경로★ 이름만 남기면 나중에 이 런을 다시 그리거나 재분석할 때
        #   어느 계약으로 봐야 하는지 알 수 없다(기본 계약으로 잘못 열면 신호가
        #   전부 결측인 그림이 조용히 나온다 — 실제로 겪었다).
        "contract_file": str(Path(contract_path).resolve()),
        # ★시나리오 파일 경로★ 같은 이유다 — `reanalyze` 가 이걸 못 찾으면
        #   `checks:` 를 통째로 못 읽어 ★판정 0 개짜리 초록 리포트★ 를 만든다.
        #   이름(`scenario`)만으로는 못 찾는다: 시나리오 name 은 나중에 바뀐다.
        "scenario_file": (str(Path(getattr(args, "scenario", "") or "").resolve())
                          if getattr(args, "scenario", "") else ""),
        "video": pstats.get("video"), "video_key": sc.get("video"),
        "perturb": variant.get("perturb", "none"),
        "overlay": _deep_merge(sc.get("overlay") or {},
                               variant.get("overlay") or {}) or None,
        "start": sc.get("start", 0), "limit": sc.get("limit", 0),
        "stride": sc.get("stride", 1),
        "mode": sc.get("mode", "lockstep"), "wall_s": round(wall, 1),
        # ★영상 fps★ — 초 단위 판정(구간 길이·전이 간격)의 환산 기준이다.
        #   lockstep 은 벽시계가 기계 속도에 좌우되므로 프레임을 장면 시간으로
        #   되돌려 재야 실차에서 일어난 그대로가 된다(analyze.scene_fps).
        "video_fps": _video_fps(pstats.get("video") or ""),
        # 배속. realtime 재생에서 rate=0.5 면 ★노드가 겪는 시간★ 이 두 배로
        # 늘어난다(느린 접근을 흉내낸다) → 초 단위 판정이 그만큼 달라진다.
        "rate": float(sc.get("rate", 1.0)),
        "frames_pushed": pstats.get("frames_pushed", 0),
        "sync_timeouts": pstats.get("sync_timeouts", 0),
        "raw_records": nlines, "domain_id": domain_id,
        "discard_first": int(sc.get("discard_first", 0)),
        # ★이 런이 실제로 쓴 파라미터만★ 남긴다. local.yaml 은 계약 여럿이 함께
        #   쓰므로 다른 계약의 노드가 섞여 있는데, 그것까지 적으면 회귀 비교가
        #   "조건이 다르다"고 잘못 경고한다(가중치 경로 하나 늘렸다고 기준이 무효가
        #   되면 안 된다). 계약이 띄운 노드의 것만 이 런의 조건이다.
        "params": {k: v for k, v in params.items()
                   if k in {n["id"] for n in contract.nodes}},
        # ★기하의 실효값★ 위 params 는 '요청한 것' 이라 노드 기본값의 변화를
        #   못 잡는다. 사다리꼴이 바뀌면 sl_px 의 뜻이 바뀐다(_calib_snapshot).
        "calib": _calib_snapshot(contract, run_dir),
        "when": datetime.now().isoformat(timespec="seconds"),
        "code_fingerprint": _fingerprint(contract.workspace),
        "workspace": str(contract.workspace or ""),
    }
    summary = analyze.summarize(rows, contract, meta)
    # 노드 로그는 토픽에 없는 근거를 갖고 있다(기동 배너·개입 사유).
    # 체크가 `log:<이름>` 으로 참조하므로 판정 ★전에★ 채워 둔다.
    summary["log_events"] = analyze.log_events(run_dir, contract)
    # 변형이 자기 체크를 ★덧붙일★ 수 있다. 대조군은 조건이 달라서 기준도 달라지기
    # 때문이다(기능을 끈 변형에서 "기능이 켜져 있다"를 요구하면 안 된다).
    checks = analyze.run_checks(
        summary, rows, contract,
        (sc.get("checks") or []) + (variant.get("checks") or []))
    drift = contract.drift_report()
    (run_dir / "summary.json").write_text(
        json.dumps({"summary": summary, "checks": checks, "drift": drift},
                   indent=2, ensure_ascii=False, default=str))
    (run_dir / "report.md").write_text(
        analyze.report_run(summary, checks, drift, contract))
    return summary, checks, run_dir


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


def _fingerprint(ws):
    """대상 소스의 지문 — 어떤 코드로 뽑은 결과인지 남긴다(git 없이도)."""
    import hashlib
    h = hashlib.sha256()
    if not ws or not (Path(ws) / "src").is_dir():
        return {"n_files": 0, "sha": "no-src"}
    files = sorted((Path(ws) / "src").rglob("*.py"))
    for f in files:
        try:
            h.update(f.name.encode())
            h.update(str(f.stat().st_size).encode())
            h.update(str(int(f.stat().st_mtime)).encode())
        except OSError:
            pass
    return {"n_files": len(files), "sha": h.hexdigest()[:12]}


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
    sc = load_yaml(args.scenario)
    contract_path = _resolve_contract(args.contract or sc.get("contract"))
    variants = sc.get("variants") or [{"name": "base", "perturb": "none"}]
    if args.variant:
        variants = [v for v in variants if v.get("name") in args.variant]
        if not variants:
            raise SystemExit(f"[run] 그런 variant 가 없다: {args.variant}")
    domain = args.domain or random.randint(30, 99)
    stamp = datetime.now().strftime("%m%d_%H%M%S")
    tag = args.tag or sc.get("name", Path(args.scenario).stem)

    results = []
    for v in variants:
        rd = ROOT / "runs" / f"{stamp}_{tag}_{v.get('name', 'base')}"
        print(f"\n▶ 실행 {rd.name}  (perturb={v.get('perturb', 'none')}, "
              f"ROS_DOMAIN_ID={domain})")
        summary, checks, rd = _one_run(sc, contract_path, v, rd, domain, args)
        failed = [c for c in checks if c["ok"] is False]
        print(f"  행 {summary['rows']} · 유효 {summary.get('valid_rate', 0):.2f} · "
              f"체크 {len(checks) - len(failed)}/{len(checks)} 통과")
        print(f"  → {rd / 'report.md'}")
        results.append((rd, summary, checks))

    # ── ★저작 → 자동 판정★ [2026-08-25] ────────────────────────────────
    #   --watch 로 타임라인을 찍었으면, 그것을 시나리오에 저장하고 ★헤드리스 판정
    #   런을 한 번 더★ 돌린다(사용자 확정 설계). 저작 런의 분석은 라이브 주입이라
    #   꼬리 프레임이 지저분하다 — 이 두 번째 런이 '깨끗한 테스트 시뮬레이션' 이다.
    #   재귀지만 두 번째 호출은 watch=False 라 이 가지를 다시 타지 않는다.
    if getattr(args, "watch", False) and results:
        sf = results[0][0] / "schedule.json"
        marks = json.loads(sf.read_text()) if sf.exists() else {}
        marks = {t: m for t, m in marks.items() if m}     # 빈 토픽은 버린다
        if marks:
            from . import config as _config
            saved = _config.set_aux_schedule(args.scenario, marks)
            print(f"\n✎ 저작한 타임라인을 {Path(saved).name} 에 저장했다.")
            print("▶ 저작한 타임라인으로 ★판정 런★ (헤드리스)")
            args.watch = False
            args.tag = (args.tag + "_" if args.tag else "") + "authored"
            return cmd_run(args)      # 갱신된 시나리오를 다시 읽어 헤드리스로 판정

    # 섭동 대조: base 대비 각 변형이 얼마나 무너졌는가 (GT 없이 재는 강건성)
    if len(results) > 1:
        contract = load_contract(contract_path)
        base = next((r for r in results if r[1]["meta"]["variant"] == "base"), results[0])
        brows = analyze.read_csv(base[0] / "signals.csv")
        names = contract.compare_signals
        entries = []
        for (rd, sm, _), v in zip(results, variants):
            crows = analyze.read_csv(rd / "signals.csv")
            if v.get("mirror"):
                crows = analyze.mirror_rows(crows, contract.mirror_odd)
            entries.append({"name": sm["meta"]["variant"], "summary": sm,
                            "mirror": bool(v.get("mirror")),
                            "diff": analyze.diff_stats(brows, crows, names, contract)})
        out = ROOT / "runs" / f"{stamp}_{tag}_robustness.md"
        out.write_text(analyze.report_robustness(base[0].name, entries, contract))
        print(f"\n섭동 대조 → {out}")

    # 베이스라인이 있으면 자동 회귀 비교
    # 기준 이름은 ★시나리오★를 따른다 — 태그는 런을 구분하려고 붙이는 것이지
    # 다른 기준을 쓰겠다는 뜻이 아니다(태그를 붙였다고 비교가 사라지면 곤란하다).
    bl_name = args.baseline or sc.get("name") or tag
    bl = ROOT / "baselines" / f"{bl_name}.csv"
    if bl.exists() and results:
        contract = load_contract(contract_path)
        rd, sm, _ = results[0]
        warn = ""
        bmeta_p = bl.with_suffix(".json")
        if bmeta_p.exists():
            d = _provenance_diff(json.loads(bmeta_p.read_text()), sm["meta"])
            if d:
                warn = ("\n> ⚠️ **기준과 실행 조건이 다르다 — 이 비교는 신뢰할 수 없다.**\n>\n"
                        + "".join(f"> - `{k}`: `{va}` → `{vb}`\n" for k, va, vb in d)
                        + "> \n> 조건을 되돌리거나, 새 조건으로 베이스라인을 다시 등록할 것.\n")
                print("\n⚠️  기준과 조건이 다르다:")
                for k, va, vb in d:
                    print(f"      {k}: {va!r} → {vb!r}")
        res = analyze.compare(analyze.read_csv(bl),
                              analyze.read_csv(rd / "signals.csv"),
                              contract, sc.get("compare_tol"))
        md = analyze.report_compare(res, bl.stem, rd.name)
        if warn:
            md = md.replace("\n\n- 공통 프레임", warn + "\n- 공통 프레임", 1)
        (rd / "compare.md").write_text(md)
        print(f"\n회귀 비교: {res['verdict']}  → {rd / 'compare.md'}")
    elif results:
        print(f"\n(기준 `{bl_name}` 없음 — `python3 -m tb.run baseline "
              f"{results[0][0].name} --name {bl_name}` 로 등록)")
    return 0


def cmd_render(args):
    """판정에 쓴 값으로 차선·중심선·θ 를 그려 저장한다."""
    import cv2
    from .harvest import effective_params, read_signals, source_video
    from .render import Renderer
    rd = _resolve_run(args.run)
    sc = load_yaml(args.scenario) if args.scenario else {}
    contract = load_contract(_resolve_contract(
        args.contract or sc.get("contract") or contract_of_run(rd)))
    #  ★요청값이 아니라 실효값★ 으로 그린다 — 아무도 안 준 파라미터는 노드
    #  기본값으로 도는데, 그것이 계약의 default 와 어긋나면 선이 엉뚱한 곳에 얹힌다.
    R = Renderer(contract, effective_params(rd))
    if R.quad_guessed:
        print("⚠️  이 런의 사다리꼴을 알 수 없어 계약의 default 로 그린다 —\n"
              "    노드가 실제로 쓴 값이 아닐 수 있다(선 위치를 믿지 말 것).\n"
              "    `params_actual.yaml` 이 없는 옛 런이면 어쩔 수 없다.")

    rows = {int(r["frame"]): r for r in read_signals(rd)
            if isinstance(r.get("frame"), (int, float))}
    if not rows:
        raise SystemExit(f"[render] signals.csv 가 없다: {rd}")
    video = args.video or source_video(rd)
    if not video:
        raise SystemExit("[render] 원본 영상을 찾을 수 없다 (--video)")

    want = sorted(rows)
    if args.frames:
        want = [int(x) for x in args.frames.split(",") if x.strip()]
    elif args.where:
        from .expr import evaluate
        want = [f for f in want if evaluate(args.where, rows[f]) is True]
    if args.limit and len(want) > args.limit:
        step = len(want) / float(args.limit)
        want = [want[int(i * step)] for i in range(args.limit)]

    cap = cv2.VideoCapture(str(video))
    writer, out, prog = None, None, None

    # ── 재생 속도 ──────────────────────────────────────────────────
    #   기본은 ★원본과 같은 속도★다. 예전 기본값 10fps 는 30fps 영상을
    #   1/3 배속으로 만들어 "느린 배속이 걸린 것처럼" 보였다.
    #   프레임을 솎아 냈으면(--limit) 그만큼 올려 벽시계 시간을 맞춘다:
    #       fps = 원본fps × 그린프레임수 ÷ (마지막 - 처음 + 1)
    fps = float(args.fps or 0)
    if fps <= 0:
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        span = (want[-1] - want[0] + 1) if len(want) > 1 else 1
        fps = src_fps * len(want) / float(span)
        fps = max(1.0, min(60.0, fps))
    args.fps = fps

    if args.mp4:
        # ★영상으로 뽑는다★ — 정지 이미지 한 장씩 보는 것보다 훨씬 읽기 쉽다.
        out = Path(args.mp4) if args.mp4 != "auto" else (rd / "path_overlay.mp4")
        if not out.is_absolute():
            out = ROOT / out
        prog = out.with_name("path_overlay_progress.json")
    else:
        out = Path(args.out) if args.out else (rd / "overlay")
        if not out.is_absolute():
            out = ROOT / out
        out.mkdir(parents=True, exist_ok=True)

    n, total = 0, len(want)
    t0 = time.time()
    for fr in want:
        if fr not in rows:
            continue
        # 연속 프레임이면 seek 없이 읽는다 (seek 가 제일 비싸다)
        if cap.get(cv2.CAP_PROP_POS_FRAMES) != fr:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fr)
        ok, img = cap.read()
        if not ok:
            continue
        vis = R.draw(img, rows[fr])
        if args.width and vis.shape[1] > args.width:
            s2 = args.width / float(vis.shape[1])
            vis = cv2.resize(vis, (args.width, int(round(vis.shape[0] * s2))))
        if args.mp4:
            if writer is None:
                # mp4v 로 쓰면 브라우저가 재생하지 못한다 → encode.Writer 가
                # H.264 를 우선 고르고, 없으면 webm 으로 내려간다.
                writer = encode.Writer(out, args.fps, (vis.shape[1], vis.shape[0]))
                out = writer.path          # 확장자가 바뀌었을 수 있다
            writer.write(vis)
        else:
            cv2.imwrite(str(out / f"ov{fr:06d}.png"), vis)
        n += 1
        if prog and (n % 5 == 0 or n == 1):
            el = time.time() - t0
            try:
                prog.write_text(json.dumps({
                    "done": n, "total": total, "elapsed_s": round(el, 1),
                    "eta_s": round(el / max(1, n) * (total - n), 1),
                    "finished": False}))
            except OSError:
                pass
    cap.release()
    if writer is not None:
        writer.release()
    if prog:
        try:
            prog.write_text(json.dumps({
                "done": n, "total": total, "first_frame": (want[0] if want else -1),
                "frames": [int(x) for x in want[:n]], "fps": args.fps,
                "file": (out.name if args.mp4 else ""),
                "codec": (writer.kind if writer is not None else ""),
                "elapsed_s": round(time.time() - t0, 1), "finished": True}))
        except OSError:
            pass
    print(f"오버레이 {n}장 → {out}")
    # 범례는 ★그린 화면 종류★ 에 맞춘다 — 계약이 어느 렌더러를 골랐는지에 달려 있다
    if getattr(R, "bd", None):
        print("  자홍색 가로선 = 노드가 발행한 거리 · 초록/주황/빨강 = 기준선과 문턱")
        print("  ★원본 화면의 자홍색 선이 노면의 그것과 겹치는지 보는 것이 요점★")
    else:
        print("  좌차선=파랑 · 우차선=빨강 · 중심선(경로)=주황 · θ시컨트=노랑 · 접선=회색")
    return 0


def cmd_harvest(args):
    """조건에 맞는 프레임을 원본 영상에서 뽑아낸다 (능동 학습 입구)."""
    from . import harvest as H
    rd = _resolve_run(args.run)
    sc = load_yaml(args.scenario) if args.scenario else {}
    contract = load_contract(_resolve_contract(
        args.contract or sc.get("contract") or contract_of_run(rd)))

    rows = H.read_signals(rd)
    if not rows:
        raise SystemExit(f"[harvest] signals.csv 가 없다: {rd}")
    picked = H.select(rows, args.where)
    if args.limit and len(picked) > args.limit:
        step = len(picked) / float(args.limit)
        picked = [picked[int(i * step)] for i in range(args.limit)]
    frames = [r["frame"] for r in picked if isinstance(r.get("frame"), (int, float))]
    print(f"조건 `{args.where or '(전부)'}` → {len(frames)}장 "
          f"(전체 {len(rows)} 중)")
    print("  플래그별:", H.summarize(picked, contract))
    if not frames:
        return 1

    video = args.video or H.source_video(rd)
    if not video:
        raise SystemExit("[harvest] 원본 영상을 찾을 수 없다 (--video 로 지정)")

    out = Path(args.out) if args.out else (rd / "harvest")
    if not out.is_absolute():
        out = ROOT / out
    if args.dry_run:
        print(f"  (건너뜀) 저장 위치 {out}, 프레임 {frames[:10]}…")
        return 0
    saved = H.grab(video, frames, out, width=args.width)
    (out / "harvest.json").write_text(json.dumps({
        "run": rd.name, "where": args.where, "video": str(video),
        "frames": [int(f) for f in frames], "count": len(saved),
        "when": datetime.now().isoformat(timespec="seconds"),
    }, indent=2, ensure_ascii=False))
    print(f"  {len(saved)}장 저장 → {out}")
    return 0


def cmd_web(args):
    """로컬 웹 뷰어를 띄운다 (표준 라이브러리만 — 외부 의존성 없음)."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "web"))
    import server as web_server          # noqa: E402
    return web_server.serve(args.host, args.port, args.open)


def cmd_app(args):
    """같은 화면을 ★별도의 창★으로 띄운다 (주소창·탭 없음)."""
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "web"))
    import shell as web_shell            # noqa: E402
    return web_shell.launch(args.host, args.port, args.page, args.size)


def cmd_publish(args):
    """★서버 없이 결과만 보는 사이트★를 굽는다 (GitHub Pages)."""
    from .publish import publish            # noqa: PLC0415
    return publish(args.out, args.run, args.all)


def cmd_inject(args):
    """합성 신호를 쏘아 변환 수학만 검사한다 (영상·YOLO 없음)."""
    from . import inject as INJ
    sc = load_yaml(args.scenario) if args.scenario else {}
    loc = local_overrides()
    contract = load_contract(_resolve_contract(args.contract or sc.get("contract")))
    cfg = contract.raw.get("injection") or {}
    if not cfg:
        raise SystemExit("[inject] 계약에 injection: 블록이 없다.")
    cases_path = Path(args.cases or cfg.get("cases", ""))
    if not cases_path.is_absolute():
        cases_path = ROOT / cases_path
    if not cases_path.exists():
        raise SystemExit(f"[inject] 케이스 파일이 없다: {cases_path}")

    env = dict(os.environ)
    env["ROS_DOMAIN_ID"] = str(args.domain or random.randint(30, 99))
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    prof = ROOT / "fastdds_profile.xml"
    if prof.exists():
        env.setdefault("FASTRTPS_DEFAULT_PROFILES_FILE", str(prof))
    for k, v in (loc.get("env") or {}).items():
        env[str(k)] = str(v)

    stamp = datetime.now().strftime("%m%d_%H%M%S")
    rd = ROOT / "runs" / f"{stamp}_inject"
    rd.mkdir(parents=True, exist_ok=True)

    params = _deep_merge(sc.get("params", {}), loc.get("params", {}))
    wanted = set(cfg.get("nodes") or [])
    pref = ws_prefix(contract)
    procs = []
    for n in contract.nodes:
        if wanted and n["id"] not in wanted:
            continue
        merged = _deep_merge(n.get("params") or {}, params.get(n["id"], {}))
        pargs = " ".join(f"-p {_param_arg(k, v)}" for k, v in merged.items())
        rename = f"-r __node:={n['node_name']}" if n.get("node_name") else ""
        cmd = (f"{pref}exec ros2 run {n['package']} {n['executable']} "
               f"--ros-args {rename} {pargs}")
        procs.append(Proc(n["id"], cmd, rd / f"{n['id']}.log", env))
        print(f"  기동: {n['id']}")

    try:
        os.environ["ROS_DOMAIN_ID"] = env["ROS_DOMAIN_ID"]
        if "FASTRTPS_DEFAULT_PROFILES_FILE" in env:
            os.environ["FASTRTPS_DEFAULT_PROFILES_FILE"] = \
                env["FASTRTPS_DEFAULT_PROFILES_FILE"]
        results = INJ.run_cases(contract, cases_path)
    finally:
        for p in procs:
            p.stop()

    md = INJ.report(results)
    (rd / "inject.md").write_text(md)
    (rd / "inject.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print()
    print(md)
    n_ok = sum(1 for r in results if r["ok"])
    print(f"→ {rd / 'inject.md'}")
    return 0 if n_ok == len(results) else 1


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
    return ROOT / "runs" / "_params" / f"{contract.name}.yaml"


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
    tmp = ROOT / "runs" / "_params"
    tmp.mkdir(parents=True, exist_ok=True)
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


def cmd_reanalyze(args):
    """raw.jsonl 만 다시 읽어 신호/리포트를 재생성한다.

    ★계약을 고쳐도 파이프라인을 다시 돌릴 필요가 없다★ — 워크스페이스의 메시지
    배치가 바뀌어 계약의 path 를 고쳤을 때, 과거 런들을 새 계약으로 다시 해석해
    베이스라인 비교를 이어갈 수 있다.
    """
    rd = _resolve_run(args.run)
    sj = rd / "summary.json"
    old = json.loads(sj.read_text())["summary"]["meta"] if sj.exists() else {}
    #  ★시나리오를 못 찾으면 판정이 통째로 사라진다★ — `checks:` 는 시나리오에만
    #  있어서, 예전에는 `--scenario` 를 빼면 리포트가 판정 0 개로 다시 쓰였다
    #  (13개 → 0개, 그런데 초록으로 보인다). 그 런이 실제로 쓴 시나리오를 되찾는다.
    spath = args.scenario or scenario_of_run(rd)
    sc = load_yaml(spath) if spath else {}
    cpath = _resolve_contract(args.contract or sc.get("contract")
                              or contract_of_run(rd))
    contract = load_contract(cpath)
    discard = int(sc.get("discard_first", old.get("discard_first", 0)))
    rows, nlines = analyze.build_table(rd / "raw.jsonl", contract, discard)
    analyze.write_csv(rows, contract, rd / "signals.csv")
    meta = dict(old)
    meta.update({"run_id": rd.name, "contract": contract.name,
                 "contract_version": contract.version, "raw_records": nlines,
                 "discard_first": discard,
                 "reanalyzed": datetime.now().isoformat(timespec="seconds")})
    summary = analyze.summarize(rows, contract, meta)
    summary["log_events"] = analyze.log_events(rd, contract)
    #  ★변형의 checks 도 함께★ 런 하나는 변형 하나다 — 그 변형에만 걸린 판정을
    #  빼면 재분석 결과가 원래 런과 개수부터 달라진다.
    vname = old.get("variant") or "base"
    vchecks = next((v.get("checks") or [] for v in (sc.get("variants") or [])
                    if v.get("name") == vname), [])
    checks = analyze.run_checks(summary, rows, contract,
                                (sc.get("checks") or []) + list(vchecks))
    #  그래도 0 개면 조용히 넘기지 않는다 — 옛 런은 meta 에 시나리오가 없다.
    if not checks and (json.loads(sj.read_text()).get("checks") if sj.exists() else None):
        print("⚠️  판정이 하나도 없다 — 시나리오를 못 찾았다. "
              "`--scenario scenarios/<파일>.yaml` 을 붙여 다시 돌릴 것.")
    drift = contract.drift_report()
    (rd / "summary.json").write_text(json.dumps(
        {"summary": summary, "checks": checks, "drift": drift},
        indent=2, ensure_ascii=False, default=str))
    (rd / "report.md").write_text(analyze.report_run(summary, checks, drift, contract))
    bad = [d for d in drift if d["status"] == "drift" and not d["optional"]]
    print(f"재분석 완료: {rd.name}  행 {len(rows)}  "
          f"계약불일치 {len(bad)}개  → {rd / 'report.md'}")
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
    """두 결과의 조건 차이. 비어 있으면 같은 조건에서 나온 것."""
    out = []
    for k in _provenance(a):
        va, vb = _provenance(a).get(k), _provenance(b).get(k)
        if va != vb:
            out.append((k, va, vb))
    return out


def cmd_baseline(args):
    rd = _resolve_run(args.run)
    # 기본 이름도 그 런이 쓴 시나리오를 따른다 (run 쪽 기준 탐색과 짝이 맞게)
    _m = json.loads((rd / "summary.json").read_text())["summary"]["meta"] \
        if (rd / "summary.json").exists() else {}
    name = args.name or _m.get("scenario") or rd.name.split("_", 2)[-1]
    dst = ROOT / "baselines" / f"{name}.csv"
    meta = json.loads((rd / "summary.json").read_text())["summary"]["meta"]
    if dst.exists() and not args.force:
        old = json.loads((ROOT / "baselines" / f"{name}.json").read_text())
        d = _provenance_diff(old, meta)
        print(f"⚠️  `{name}` 을 덮어쓴다.")
        if d:
            print("    조건이 달라졌다 — 이전 기준과는 비교가 불가능해진다:")
            for k, va, vb in d:
                print(f"      {k}: {va!r} → {vb!r}")
        # ★웹앱에서 부를 때는 물어볼 상대가 없다★ — stdin 이 터미널이 아니면
        #   기다리다 멈춰 버린다(작업 하나만 돌 수 있어 그 뒤가 전부 막힌다).
        #   그럴 때는 되묻지 않고 거절한다 — `--force` 를 붙이라고 말해 준다.
        if not sys.stdin.isatty():
            print(f"    ⛔ 되물을 수 없는 자리다 — 덮어쓰려면 `--force` 를 붙일 것 "
                  f"(python3 -m tb.run baseline {rd.name} --name {name} --force)")
            return 1
        if input("    계속할까? [y/N] ").strip().lower() not in ("y", "yes"):
            print("    취소했다.")
            return 1
    shutil.copy(rd / "signals.csv", dst)
    (ROOT / "baselines" / f"{name}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str))
    print(f"베이스라인 등록: {dst}")
    print(f"  영상 {meta.get('video_key') or '?'} = {meta.get('video')}")
    print(f"  구간 start={meta.get('start')} limit={meta.get('limit')} "
          f"stride={meta.get('stride')} mode={meta.get('mode')}")
    return 0


def cmd_compare(args):
    a = _resolve_csv(args.a)
    b = _resolve_csv(args.b)
    sc0 = load_yaml(args.scenario) if args.scenario else {}
    contract = load_contract(_resolve_contract(args.contract or sc0.get("contract")))
    tol = sc0.get("compare_tol")
    res = analyze.compare(analyze.read_csv(a), analyze.read_csv(b), contract, tol)
    md = analyze.report_compare(res, Path(a).stem, Path(b).parent.name)
    print(md)
    if Path(b).parent.is_dir():
        (Path(b).parent / "compare.md").write_text(md)
    return 0 if res["verdict"] == "PASS" else 1


def contract_of_run(run_dir):
    """그 런이 ★실제로 쓴★ 계약 파일. 없으면 None.

    메타의 contract_file 을 먼저 보고, 옛 런(그 항목이 없다)이면 이름으로 찾는다.
    이것이 없으면 `--contract` 를 생략한 render·harvest·reanalyze 가 기본 계약으로
    열려서, 신호가 전부 결측인 그림·리포트를 조용히 만들어 낸다.
    """
    sj = Path(run_dir) / "summary.json"
    if not sj.exists():
        return None
    try:
        meta = json.loads(sj.read_text())["summary"]["meta"]
    except (KeyError, ValueError):
        return None
    f = meta.get("contract_file")
    if f and Path(f).exists():
        return f
    want = meta.get("contract")
    if not want:
        return None
    for cand in sorted((ROOT / "contracts").glob("*.yaml")):
        try:
            if load_contract(cand).name == want:
                return str(cand)
        except Exception:                                  # noqa: BLE001
            continue
    return None


def scenario_of_run(run_dir):
    """그 런이 ★실제로 쓴★ 시나리오 파일. 없으면 None.

    `contract_of_run` 과 같은 이유로 있다. 이게 없으면 `reanalyze` 가 `checks:` 를
    못 읽어 ★판정이 통째로 사라진 초록 리포트★ 가 나온다(실측: 13개 → 0개).
    이름으로 찾는 것은 마지막 수단이다 — 시나리오 name 은 나중에 바뀐다.
    """
    sj = Path(run_dir) / "summary.json"
    if not sj.exists():
        return None
    try:
        meta = json.loads(sj.read_text())["summary"]["meta"]
    except (KeyError, ValueError):
        return None
    f = meta.get("scenario_file")
    if f and Path(f).exists():
        return f
    want = meta.get("scenario")
    if not want:
        return None
    for cand in sorted((ROOT / "scenarios").glob("*.yaml")):
        try:
            if (load_yaml(cand) or {}).get("name") == want:
                return str(cand)
        except Exception:                                  # noqa: BLE001
            continue
    return None


def _resolve_run(x):
    p = Path(x)
    if p.is_dir():
        return p
    p = ROOT / "runs" / x
    if p.is_dir():
        return p
    raise SystemExit(f"[run] 런을 찾을 수 없다: {x}")


def _resolve_csv(x):
    p = Path(x)
    if p.is_file():
        return p
    for cand in (ROOT / "baselines" / f"{x}.csv", ROOT / "runs" / x / "signals.csv",
                 Path(x) / "signals.csv"):
        if cand.is_file():
            return cand
    raise SystemExit(f"[run] CSV 를 찾을 수 없다: {x}")


def cmd_feedback(args):
    """실행 결과를 코드 개선 요청문(feedback.md)으로 옮긴다."""
    from . import feedback                       # noqa: PLC0415
    d = _resolve_run(args.run)
    prev = _resolve_run(args.vs) if args.vs else None
    note = args.note
    if args.note_file:
        note = Path(args.note_file).read_text()
    out = feedback.write(d, prev, note)
    if args.quiet:
        print(out)
    else:
        print(out.read_text())
        print(f"\n[feedback] {out}")
    return 0


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


def cmd_list(_args):
    print("── 베이스라인 ──")
    for p in sorted((ROOT / "baselines").glob("*.csv")):
        print(f"  {p.stem}")
    print("── 최근 런 ──")
    dirs = [d for d in (ROOT / "runs").iterdir() if d.is_dir()]
    for d in sorted(dirs, key=lambda x: x.name, reverse=True)[:20]:
        sj = d / "summary.json"
        if sj.is_file():
            try:
                m = json.loads(sj.read_text())["summary"]
                print(f"  {d.name:<40} 행 {m.get('rows', 0):<5} "
                      f"유효 {m.get('valid_rate', 0):.2f}  "
                      f"체크 {sum(1 for c in json.loads(sj.read_text())['checks'] if c['ok'])}"
                      f"/{len(json.loads(sj.read_text())['checks'])}")
                continue
            except Exception:   # noqa: BLE001
                pass
        print(f"  {d.name:<40} (분석 없음)")
    print("── 리포트 ──")
    for f in sorted((ROOT / "runs").glob("*.md"), reverse=True)[:5]:
        print(f"  {f.name}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tb.run")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="환경/계약 점검")
    d.add_argument("--scenario", default="")
    d.add_argument("--contract", default="")
    d.set_defaults(fn=cmd_doctor)

    r = sub.add_parser("run", help="시나리오 실행")
    r.add_argument("--scenario", required=True)
    r.add_argument("--contract", default="", help="시나리오의 contract: 를 덮어쓴다")
    r.add_argument("--variant", action="append")
    r.add_argument("--tag", default="")
    r.add_argument("--domain", type=int, default=0)
    r.add_argument("--baseline", default="")
    r.add_argument("--watch", action="store_true",
                   help="디버그 영상 창을 띄워 보면서 실행 (space=정지, n=한프레임)")
    r.add_argument("--record-debug", action="store_true",
                   help="창 없이 디버그 영상을 mp4 로 남긴다")
    r.add_argument("--keep-going", action="store_true")
    r.set_defaults(fn=cmd_run)

    rn = sub.add_parser("render",
                        help="판정에 쓴 값으로 차선·중심선·θ 를 그려 저장")
    rn.add_argument("run")
    rn.add_argument("--frames", default="", help="쉼표로 프레임 번호 지정")
    rn.add_argument("--where", default="", help="조건식으로 고르기")
    rn.add_argument("--limit", type=int, default=20)
    rn.add_argument("--width", type=int, default=1600)
    rn.add_argument("--out", default="")
    rn.add_argument("--mp4", default="",
                    help="영상으로 저장. 'auto' 면 <런>/path_overlay.mp4")
    rn.add_argument("--fps", type=float, default=0.0,
                help="영상 재생 속도. 0=원본과 같은 속도(기본)")
    rn.add_argument("--video", default="")
    rn.add_argument("--scenario", default="",
                          help="비우면 런이 기록한 계약·파라미터를 그대로 쓴다")
    rn.add_argument("--contract", default="")
    rn.set_defaults(fn=cmd_render)

    hv = sub.add_parser("harvest",
                        help="조건에 맞는 프레임을 원본에서 추출 (능동 학습)")
    hv.add_argument("run")
    hv.add_argument("--where", default="",
                    help='조건식. 예: "int(flags) % 4 >= 2" (폭 게이트 탈락)')
    hv.add_argument("--out", default="", help="저장 폴더 (기본 <런>/harvest)")
    hv.add_argument("--limit", type=int, default=0, help="균등 샘플링 상한")
    hv.add_argument("--width", type=int, default=0, help="가로 축소 (0=원본)")
    hv.add_argument("--video", default="")
    hv.add_argument("--scenario", default="",
                          help="비우면 런이 기록한 계약·파라미터를 그대로 쓴다")
    hv.add_argument("--contract", default="")
    hv.add_argument("--dry-run", action="store_true")
    hv.set_defaults(fn=cmd_harvest)

    w = sub.add_parser("web", help="로컬 웹 뷰어 (기본 http://127.0.0.1:8770)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8770)
    w.add_argument("--open", action="store_true", help="브라우저를 함께 연다")
    w.set_defaults(fn=cmd_web)

    ap_ = sub.add_parser("app", help="같은 화면을 별도의 창으로 (주소창·탭 없음)")
    ap_.add_argument("--host", default="127.0.0.1")
    ap_.add_argument("--port", type=int, default=8770)
    ap_.add_argument("--page", default="",
                     help="열 화면. 예: exec, runs, calib (기본: 홈)")
    ap_.add_argument("--size", default="1600,1000",
                     help="첫 실행 때의 창 크기 W,H (그 뒤엔 창이 기억한다)")
    ap_.set_defaults(fn=cmd_app)

    ij = sub.add_parser("inject",
                        help="합성 신호로 변환 수학만 검사 (영상·YOLO 없음)")
    #  inject 는 런이 아니라 ★시나리오★ 를 대상으로 도는 명령이라 기본값을 남겨 둔다
    ij.add_argument("--scenario", default="scenarios/regression.yaml")
    ij.add_argument("--contract", default="")
    ij.add_argument("--cases", default="")
    ij.add_argument("--domain", type=int, default=0)
    ij.set_defaults(fn=cmd_inject)

    ra = sub.add_parser("reanalyze",
                        help="raw.jsonl 로 신호/리포트만 재생성 (계약 수정 후)")
    ra.add_argument("run")
    ra.add_argument("--scenario", default="",
                          help="비우면 런이 기록한 계약·파라미터를 그대로 쓴다")
    ra.add_argument("--contract", default="")
    ra.set_defaults(fn=cmd_reanalyze)

    b = sub.add_parser("baseline", help="런을 기준으로 등록")
    b.add_argument("run")
    b.add_argument("--name", default="")
    b.add_argument("--force", action="store_true", help="확인 없이 덮어쓴다")
    b.set_defaults(fn=cmd_baseline)

    c = sub.add_parser("compare", help="두 결과 비교")
    c.add_argument("a"); c.add_argument("b")
    c.add_argument("--contract", default="")
    c.add_argument("--scenario", default="")
    c.set_defaults(fn=cmd_compare)

    fb = sub.add_parser("feedback",
                        help="실행 결과를 코드 개선 요청문으로 (feedback.md)")
    fb.add_argument("run")
    fb.add_argument("--vs", default="", help="이전 런과 개선 전/후를 비교한다")
    fb.add_argument("--note", default="", help="사람이 본 것을 함께 적는다")
    fb.add_argument("--note-file", default="", help="메모를 파일에서 읽는다")
    fb.add_argument("--quiet", action="store_true", help="경로만 출력")
    fb.set_defaults(fn=cmd_feedback)

    pr = sub.add_parser("params",
                        help="대상 노드의 파라미터를 받아 적는다 (캘리브의 출발점)")
    pr.add_argument("--scenario", default="")
    pr.add_argument("--contract", default="")
    pr.add_argument("--out", default="", help="쓸 파일 (비우면 runs/_params/<계약>.yaml)")
    pr.add_argument("--timeout", type=float, default=90.0)
    pr.set_defaults(fn=cmd_params)

    bd = sub.add_parser("build", help="대상 워크스페이스를 colcon build")
    bd.add_argument("--scenario", default="")
    bd.add_argument("--contract", default="")
    bd.add_argument("--all", action="store_true",
                    help="계약의 패키지만이 아니라 워크스페이스 전체를 빌드")
    bd.set_defaults(fn=cmd_build)

    pb = sub.add_parser(
        "publish", help="정적 사이트로 내보내기 — 서버 없이 결과만 보는 읽기 전용")
    pb.add_argument("--run", action="append", default=[],
                    help="공개할 실행 (여러 번 쓸 수 있다). 비우면 ★핀 꽂은 실행만★")
    pb.add_argument("--all", action="store_true", help="핀과 무관하게 전부")
    pb.add_argument("--out", default="docs",
                    help="내보낼 폴더 (기본 docs/ — GitHub Pages 가 여기서 읽는다)")
    pb.set_defaults(fn=cmd_publish)

    ls = sub.add_parser("list"); ls.set_defaults(fn=cmd_list)

    args = ap.parse_args(argv)
    # 사용자가 준 경로는 ★현재 디렉터리 기준★으로 먼저 확정한다.
    # 그 뒤에 테스트베드 루트로 옮겨 가야 relative 인자가 안 깨진다.
    for attr in ("scenario", "contract"):
        v = getattr(args, attr, "")
        if v and Path(v).exists():
            setattr(args, attr, str(Path(v).resolve()))
    os.chdir(ROOT)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        #  Ctrl+C 또는 SIGTERM(→ _sigterm_to_interrupt). 자식 정리는 각 명령의
        #  finally 가 이미 했다. 트레이스백 없이 조용히 나간다.
        print("\n중단됨 — 정리하고 종료합니다.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
