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
            env=env, preexec_fn=os.setsid)

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
        for nid, kv in params.items():
            if nid not in {n["id"] for n in c.nodes}:
                chk(f"params.{nid} 가 계약에 없는 노드", False,
                    "계약의 nodes[].id 와 이름이 달라 무시된다")
            for k, v in kv.items():
                if not (isinstance(v, str) and "/" in v):
                    continue
                if v.endswith(".pt") or v.endswith(".engine"):
                    chk(f"{nid}.{k}", Path(v).exists(), v)
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

    params = _deep_merge(sc.get("params", {}), loc.get("params", {}))
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
        if (watching or getattr(args, "record_debug", False)) and contract.debug_topics:
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
        elif watching and not contract.debug_topics:
            print("  ⚠️  계약에 debug_topics 가 없어 볼 것이 없다")

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
        aux = json.dumps(contract.aux)
        play = (f"{pref}exec python3 -m tb.player "
                f"--video '{video}' --image-topic '{contract.image_topic}' "
                f"--sync-topic '{contract.sync_topic or ''}' "
                f"--mode {sc.get('mode', 'lockstep')} "
                f"--rate {sc.get('rate', 1.0)} --start {sc.get('start', 0)} "
                f"--limit {sc.get('limit', 0)} --stride {sc.get('stride', 1)} "
                f"--warmup-s {sc.get('warmup_s', 6.0)} "
                f"--sync-timeout {sc.get('sync_timeout', 15.0)} "
                f"--sync-retries {sc.get('sync_retries', 2)} "
                f"--sync-timeout-first {sc.get('sync_timeout_first', 90.0)} "
                f"--perturb '{variant.get('perturb', 'none')}' "
                f"{'--sim-time' if sc.get('sim_time') else ''} "
                f"--aux-json '{aux}' --stats-out '{run_dir / 'player.json'}' "
                f"--progress-out '{run_dir / 'progress.json'}'")
        (run_dir / "cmd_player.txt").write_text(play + "\n")
        t0 = time.time()
        with open(run_dir / "player.log", "w") as lf:
            rc = subprocess.call(["bash", "-c", play], stdout=lf,
                                 stderr=subprocess.STDOUT,
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
        "video": pstats.get("video"), "video_key": sc.get("video"),
        "perturb": variant.get("perturb", "none"),
        "start": sc.get("start", 0), "limit": sc.get("limit", 0),
        "stride": sc.get("stride", 1),
        "mode": sc.get("mode", "lockstep"), "wall_s": round(wall, 1),
        "frames_pushed": pstats.get("frames_pushed", 0),
        "sync_timeouts": pstats.get("sync_timeouts", 0),
        "raw_records": nlines, "domain_id": domain_id,
        "discard_first": int(sc.get("discard_first", 0)),
        "params": params, "when": datetime.now().isoformat(timespec="seconds"),
        "code_fingerprint": _fingerprint(contract.workspace),
        "workspace": str(contract.workspace or ""),
    }
    summary = analyze.summarize(rows, contract, meta)
    checks = analyze.run_checks(summary, rows, contract, sc.get("checks"))
    drift = contract.drift_report()
    (run_dir / "summary.json").write_text(
        json.dumps({"summary": summary, "checks": checks, "drift": drift},
                   indent=2, ensure_ascii=False, default=str))
    (run_dir / "report.md").write_text(
        analyze.report_run(summary, checks, drift, contract))
    return summary, checks, run_dir


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


def cmd_run(args):
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
    from .harvest import read_signals, source_video
    from .render import Renderer
    rd = _resolve_run(args.run)
    sc = load_yaml(args.scenario) if args.scenario else {}
    contract = load_contract(_resolve_contract(args.contract or sc.get("contract")))
    meta = json.loads((rd / "summary.json").read_text())["summary"]["meta"] \
        if (rd / "summary.json").exists() else {}
    R = Renderer(contract, meta.get("params"))

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
    print("  좌차선=파랑 · 우차선=빨강 · 중심선(경로)=주황 · θ시컨트=노랑 · 접선=회색")
    return 0


def cmd_harvest(args):
    """조건에 맞는 프레임을 원본 영상에서 뽑아낸다 (능동 학습 입구)."""
    from . import harvest as H
    rd = _resolve_run(args.run)
    sc = load_yaml(args.scenario) if args.scenario else {}
    contract = load_contract(_resolve_contract(args.contract or sc.get("contract")))

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


def cmd_reanalyze(args):
    """raw.jsonl 만 다시 읽어 신호/리포트를 재생성한다.

    ★계약을 고쳐도 파이프라인을 다시 돌릴 필요가 없다★ — 워크스페이스의 메시지
    배치가 바뀌어 계약의 path 를 고쳤을 때, 과거 런들을 새 계약으로 다시 해석해
    베이스라인 비교를 이어갈 수 있다.
    """
    rd = _resolve_run(args.run)
    sj = rd / "summary.json"
    old = json.loads(sj.read_text())["summary"]["meta"] if sj.exists() else {}
    sc = load_yaml(args.scenario) if args.scenario else {}
    cpath = _resolve_contract(args.contract or sc.get("contract"))
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
    checks = analyze.run_checks(summary, rows, contract, sc.get("checks"))
    drift = contract.drift_report()
    (rd / "summary.json").write_text(json.dumps(
        {"summary": summary, "checks": checks, "drift": drift},
        indent=2, ensure_ascii=False, default=str))
    (rd / "report.md").write_text(analyze.report_run(summary, checks, drift, contract))
    bad = [d for d in drift if d["status"] == "drift" and not d["optional"]]
    print(f"재분석 완료: {rd.name}  행 {len(rows)}  "
          f"계약불일치 {len(bad)}개  → {rd / 'report.md'}")
    return 0


def _provenance(meta):
    """이 결과가 '어떤 조건에서 나온 것인가' — 비교 가능 여부를 가르는 것들만."""
    return {
        "video": meta.get("video"),
        "video_key": meta.get("video_key"),
        "mode": meta.get("mode"),
        "perturb": meta.get("perturb"),
        "start": meta.get("start"), "limit": meta.get("limit"),
        "stride": meta.get("stride"),
        "contract": meta.get("contract"),
        "params": meta.get("params"),
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
    rn.add_argument("--scenario", default="scenarios/regression.yaml")
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
    hv.add_argument("--scenario", default="scenarios/regression.yaml")
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
    ij.add_argument("--scenario", default="scenarios/regression.yaml")
    ij.add_argument("--contract", default="")
    ij.add_argument("--cases", default="")
    ij.add_argument("--domain", type=int, default=0)
    ij.set_defaults(fn=cmd_inject)

    ra = sub.add_parser("reanalyze",
                        help="raw.jsonl 로 신호/리포트만 재생성 (계약 수정 후)")
    ra.add_argument("run")
    ra.add_argument("--scenario", default="scenarios/regression.yaml")
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

    ls = sub.add_parser("list"); ls.set_defaults(fn=cmd_list)

    args = ap.parse_args(argv)
    # 사용자가 준 경로는 ★현재 디렉터리 기준★으로 먼저 확정한다.
    # 그 뒤에 테스트베드 루트로 옮겨 가야 relative 인자가 안 깨진다.
    for attr in ("scenario", "contract"):
        v = getattr(args, attr, "")
        if v and Path(v).exists():
            setattr(args, attr, str(Path(v).resolve()))
    os.chdir(ROOT)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
