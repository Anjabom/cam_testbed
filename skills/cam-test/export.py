#!/usr/bin/env python3
"""런 하나를 ★대상 워크스페이스 안★으로 내보낸다 — 결과 + 디버그 영상 + 실행 조건.

★왜 tb/ 가 아니라 여기 있는가★
`tb.run` 에 서브커맨드를 늘리면 경계 규칙 ⑤ 때문에 `web/server.py` 의 `COMMANDS`
도 같이 늘려야 한다. 이건 스킬만 쓰는 기능이라 그 값을 치를 이유가 없다.
대신 tb/ 를 import 하지 않는다 — 런 폴더의 파일만 읽는다.

★왜 복사인가(링크가 아니라)★
워크스페이스는 남에게 넘어간다. 링크는 그때 깨지고, 깨진 링크는 "결과가 없다"가
아니라 "결과를 못 읽는다"로 나타나서 더 헷갈린다.

    python3 export.py <런 폴더> [--ws <워크스페이스>]
    python3 export.py --selftest          # 자체 검사 (ROS·영상 불필요)
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

#  판정에 쓴 값·리포트·영상. 없는 것은 조용히 건너뛴다(변형·모드에 따라 안 나온다).
COPY = ["report.md", "compare.md", "feedback.md", "summary.json", "signals.csv",
        "params_actual.yaml", "code.json", "debug_meta.json",
        "cmd_player.txt", "progress.json"]

OUT_DIRNAME = "testbed_results"

#  colcon 이 mp4 수백 MB 를 매 빌드마다 훑지 않게. 대상 저장소를 건드리는
#  유일한 파일 둘 중 하나다(나머지 하나는 .gitignore 한 줄).
IGNORE_NOTE = "# 테스트베드 결과 보관함 — colcon 빌드 대상이 아니다\n"


def _git(cwd, *args):
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:                            # noqa: BLE001
        return ""


def _gpu():
    """드라이버가 죽어 있으면 빈 문자열 — 그것 자체가 기록할 값이다."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version",
                            "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 else ""
    except Exception:                            # noqa: BLE001
        return ""


def run_env(run_dir, meta, code):
    """★이 숫자가 어느 조건에서 나왔는가★ — 베이스라인은 머신을 넘지 못한다.

    워크스페이스에 결과를 넣으면 남의 머신 숫자와 한자리에 섞인다. 나중에
    "왜 값이 다르지"를 물을 때 답할 수 있는 최소한만 박아 둔다.
    """
    params = meta.get("params") or {}
    weights = {f"{node}.{k}": v
               for node, d in params.items() if isinstance(d, dict)
               for k, v in d.items()
               if isinstance(v, str) and v.endswith((".pt", ".engine"))}
    return {
        "run_id": meta.get("run_id", run_dir.name),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "gpu": _gpu(),
        "workspace": code.get("workspace", ""),
        "workspace_git": _git(code.get("workspace") or ".", "rev-parse", "--short", "HEAD"),
        "workspace_code_sha": code.get("sha", ""),   # tb 가 잰 소스 내용 해시
        "testbed_git": _git(Path(__file__).resolve().parents[2],
                            "rev-parse", "--short", "HEAD"),
        "contract": meta.get("contract", ""),
        "scenario": meta.get("scenario", ""),
        "variant": meta.get("variant", ""),
        "video": meta.get("video", ""),
        "mode": meta.get("mode", ""),
        "weights": weights,
    }


def verdict(s):
    """★행 N 을 체크 통과율보다 먼저★ — 행 0 인데 「전부 통과」로 찍히는 함정."""
    summ = s.get("summary") or {}
    checks = s.get("checks") or []
    rows = summ.get("rows", 0)
    ok = sum(1 for c in checks if c.get("ok"))
    return {"rows": rows, "valid_rate": summ.get("valid_rate"),
            "checks_ok": ok, "checks_total": len(checks),
            "empty": not rows or not checks}


def ensure_gitignore(ws):
    """대상 저장소에 mp4 가 커밋되지 않게 한 줄 등록. git 이 아니면 아무것도 안 한다."""
    if not (Path(ws) / ".git").exists():
        return False
    gi = Path(ws) / ".gitignore"
    line = f"{OUT_DIRNAME}/"
    old = gi.read_text() if gi.is_file() else ""
    if line in old.split():
        return False
    sep = "" if (not old or old.endswith("\n")) else "\n"
    gi.write_text(f"{old}{sep}\n# 카메라 테스트베드 결과(영상 포함) — 커밋하지 않는다\n{line}\n")
    return True


def export(run_dir, ws=None):
    run_dir = Path(run_dir).resolve()
    sj = run_dir / "summary.json"
    if not sj.is_file():
        raise SystemExit(f"[export] summary.json 이 없다 — 분석이 끝난 런이 아니다: {run_dir}")
    s = json.loads(sj.read_text())
    meta = (s.get("summary") or {}).get("meta") or {}
    cj = run_dir / "code.json"
    code = json.loads(cj.read_text()) if cj.is_file() else {}

    ws = Path(ws or code.get("workspace") or "").expanduser()
    if not ws.is_dir():
        raise SystemExit("[export] 대상 워크스페이스를 못 찾았다 — --ws 로 지정할 것"
                         f" (런이 기록한 값: {code.get('workspace') or '없음'})")

    root = ws / OUT_DIRNAME
    dest = root / run_dir.name
    dest.mkdir(parents=True, exist_ok=True)
    (root / "COLCON_IGNORE").write_text(IGNORE_NOTE)

    copied = []
    for n in COPY + [p.name for p in sorted(run_dir.glob("*.mp4"))]:
        src = run_dir / n
        if src.is_file():
            shutil.copy2(src, dest / n)
            copied.append(n)

    env = run_env(run_dir, meta, code)
    (dest / "run_env.json").write_text(
        json.dumps(env, ensure_ascii=False, indent=1) + "\n")
    v = verdict(s)

    #  INDEX.md — 워크스페이스만 열어도 시험 이력이 보이게. 한 런 = 한 줄.
    idx = root / "INDEX.md"
    if not idx.is_file():
        idx.write_text("# 카메라 테스트베드 시험 이력\n\n"
                       "| 실행 | 시나리오 | 변형 | 행 | 유효 | 체크 | 호스트 | 코드 |\n"
                       "|---|---|---|---|---|---|---|---|\n")
    vr = "—" if v["valid_rate"] is None else f"{v['valid_rate']:.3f}"
    head = f"| [{run_dir.name}]({run_dir.name}/report.md) |"
    row = (f"{head} {env['scenario']} | {env['variant']} | {v['rows']} | {vr} | "
           f"{v['checks_ok']}/{v['checks_total']} | {env['host']} | "
           f"{env['workspace_code_sha'][:8]} |")
    #  ★같은 런을 다시 내보내는 것이 정상 흐름이다★ — feedback.md 를 뽑고,
    #  기준과 비교하고, replay 로 영상을 잡은 뒤에 다시 내보낸다. 그때마다
    #  줄이 쌓이면 이력이 아니라 중복이 된다 → 그 런의 줄을 갈아 끼운다.
    lines = [ln for ln in idx.read_text().splitlines() if not ln.startswith(head)]
    idx.write_text("\n".join(lines + [row]) + "\n")

    return {"dest": dest, "copied": copied, "verdict": v, "env": env,
            "gitignored": ensure_gitignore(ws),
            "video": any(n.endswith(".mp4") for n in copied)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="export.py")
    ap.add_argument("run", nargs="?", default="")
    ap.add_argument("--ws", default="", help="대상 워크스페이스 (비우면 런의 code.json)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.run:
        ap.error("런 폴더를 지정할 것")
    r = export(a.run, a.ws or None)
    v = r["verdict"]
    print(f"[export] {r['dest']}")
    print(f"  행 {v['rows']}  유효 {v['valid_rate']}  "
          f"체크 {v['checks_ok']}/{v['checks_total']}")
    if v["empty"]:
        print("  ⚠️ 행이나 체크가 0 이다 — 「전부 통과」로 보여도 잰 것이 없다")
    if not r["video"]:
        print("  ⚠️ 디버그 영상이 없다 — `tb.run replay <런>` 으로 다시 잡을 수 있다")
    if r["gitignored"]:
        print(f"  .gitignore 에 {OUT_DIRNAME}/ 를 등록했다")
    return 0


# ══════════════════════════════════════════════════════════════════════
#  자체 검사 — 가짜 런 폴더로 내보내기 전체를 한 번 돌린다
# ══════════════════════════════════════════════════════════════════════
def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        run = td / "runs" / "0101_000000_x_base"
        run.mkdir(parents=True)
        ws = td / "ws"
        (ws / ".git").mkdir(parents=True)
        (ws / ".gitignore").write_text("build/\n")
        (run / "summary.json").write_text(json.dumps({
            "summary": {"rows": 12, "valid_rate": 0.5,
                        "meta": {"run_id": run.name, "scenario": "s", "variant": "base",
                                 "mode": "lockstep", "video": "/v.mp4",
                                 "params": {"perception": {"w": "/a/best.engine",
                                                           "device": "cuda"}}}},
            "checks": [{"ok": True}, {"ok": False}]}))
        (run / "code.json").write_text(json.dumps({"workspace": str(ws), "sha": "abc123def456"}))
        (run / "report.md").write_text("# r\n")
        (run / "lane_debug.mp4").write_bytes(b"\x00")

        r = export(run)
        d = r["dest"]
        assert d == ws / OUT_DIRNAME / run.name, d
        assert (d / "report.md").is_file() and (d / "lane_debug.mp4").is_file()
        assert r["video"] and r["gitignored"]
        assert (ws / OUT_DIRNAME / "COLCON_IGNORE").is_file(), "colcon 이 결과를 훑는다"
        env = json.loads((d / "run_env.json").read_text())
        assert env["weights"] == {"perception.w": "/a/best.engine"}, env["weights"]
        assert env["workspace_code_sha"] == "abc123def456"
        assert r["verdict"] == {"rows": 12, "valid_rate": 0.5, "checks_ok": 1,
                                "checks_total": 2, "empty": False}, r["verdict"]
        idx = (ws / OUT_DIRNAME / "INDEX.md").read_text()
        assert idx.count("\n|") >= 2 and run.name in idx

        #  두 번 내보내도 .gitignore 도 이력도 늘지 않는다 (같은 런은 갈아 끼운다)
        r2 = export(run)
        assert not r2["gitignored"], ".gitignore 가 중복으로 늘어난다"
        rows = [ln for ln in (ws / OUT_DIRNAME / "INDEX.md").read_text().splitlines()
                if ln.startswith("| [")]
        assert len(rows) == 1, rows

        #  다른 런은 따로 쌓인다
        run2 = run.parent / "0101_000001_y_base"
        shutil.copytree(run, run2)
        export(run2)
        rows = [ln for ln in (ws / OUT_DIRNAME / "INDEX.md").read_text().splitlines()
                if ln.startswith("| [")]
        assert len(rows) == 2, rows

        #  행 0 인데 체크가 전부 통과 — 「빈 런」을 잡아내야 한다
        (run / "summary.json").write_text(json.dumps({
            "summary": {"rows": 0, "meta": {}}, "checks": []}))
        assert export(run, str(ws))["verdict"]["empty"], "빈 런을 못 잡았다"

        #  분석이 안 끝난 런은 내보내지 않는다
        (run / "summary.json").unlink()
        try:
            export(run, str(ws))
            raise AssertionError("summary.json 없는 런을 내보냈다")
        except SystemExit:
            pass
    print("통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
