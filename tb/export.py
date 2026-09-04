"""런 하나를 ★대상 워크스페이스(또는 아무 폴더) 안★ 으로 내보낸다.

결과 데이터 + 디버그 영상 + 실행 조건. 이것이 시험의 산출물 전부다 —
판정도 피드백 문서도 없다(그 해석은 결과를 읽는 사람이 한다).

★왜 복사인가(링크가 아니라)★
워크스페이스는 남에게 넘어간다. 링크는 그때 깨지고, 깨진 링크는 "결과가 없다"가
아니라 "결과를 못 읽는다"로 나타나서 더 헷갈린다.

    python3 -m tb.run export <런> --out <워크스페이스>
    python3 -m tb.run run ... --out <워크스페이스>      # 돌리고 바로
"""
from __future__ import annotations

import json
import platform
import shutil
import socket
import subprocess
from datetime import datetime
from pathlib import Path

#  판정에 쓴 값·리포트·영상. 없는 것은 조용히 건너뛴다(변형·모드에 따라 안 나온다).
#  잰 값·리포트·영상·조건. 없는 것은 조용히 건너뛴다(계약·모드에 따라 안 나온다).
COPY = ["report.md", "diff.md", "summary.json", "signals.csv",
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
        "testbed_git": _git(Path(__file__).resolve().parents[1],
                            "rev-parse", "--short", "HEAD"),
        "contract": meta.get("contract", ""),
        "label": meta.get("label", ""),
        "note": meta.get("note", ""),
        "preset": meta.get("preset", ""),
        "start": meta.get("start", 0), "limit": meta.get("limit", 0),
        "video": meta.get("video", ""),
        "mode": meta.get("mode", ""),
        "weights": weights,
    }


def verdict(s):
    """★행 N 을 맨 앞에★ — 잰 것이 없으면 그 아래 숫자는 전부 뜻이 없다."""
    summ = s.get("summary") or {}
    rows = summ.get("rows", 0)
    return {"rows": rows, "valid_rate": summ.get("valid_rate"), "empty": not rows}


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


def _readme(run_id, meta, v, copied):
    """★결과 폴더가 스스로를 설명하게 한다★.

    이 폴더는 몇 달 뒤 다른 사람이 연다. 그때 report.md 옆의 signals.csv 가
    무엇인지, raw.jsonl 이 왜 없는지(원본은 테스트베드에 남는다) 알 방법이
    폴더 안에 있어야 한다 — 없으면 파일만 있고 아무도 안 읽는다.
    """
    what = {
        "report.md": "사람이 읽는 계측 요약 — 신호 통계·게이트 통과율·전이·계약 정합",
        "summary.json": "같은 내용의 기계 읽기용",
        "signals.csv": "프레임별 신호 원표 (그래프·재계산은 여기서)",
        "diff.md": "다른 런과의 차이표",
        "params_actual.yaml": "노드가 실제로 들고 돈 파라미터 전부",
        "run_env.json": "호스트·GPU·가중치·코드 해시 — 값이 왜 다른지 물을 때의 근거",
        "code.json": "대상 소스의 내용 해시 (replay 가 이걸로 대조한다)",
        "cmd_player.txt": "영상을 어떻게 밀어 넣었는지 그 명령줄 그대로",
        "progress.json": "프레임 진행 기록",
        "debug_meta.json": "디버그 영상 파일 이름 (토픽에서 나온다)",
    }
    L = [f"# {run_id}", ""]
    if meta.get("note"):
        L += [f"**보려던 것** — {meta['note']}", ""]
    L += [f"- 영상 `{meta.get('video', '?')}`  구간 start={meta.get('start', 0)} "
          f"limit={meta.get('limit', 0)} stride={meta.get('stride', 1)}",
          f"- 계약 `{meta.get('contract', '?')}`  모드 `{meta.get('mode', '?')}`",
          f"- 행 {v['rows']}  유효 {v['valid_rate']}", ""]
    if v["empty"]:
        L += ["> ⚠️ **행이 0 이다 — 잰 것이 없다.** 아래 숫자는 전부 빈 입력에서 나왔다.", ""]
    L += ["## 이 폴더의 파일", "", "| 파일 | 무엇 |", "|---|---|"]
    for n in copied + ["run_env.json"]:
        if n.endswith(".mp4"):
            L.append(f"| `{n}` | ★디버그 영상★ — 대상 노드가 그린 그림. "
                     "다시 만들 수 없는 유일한 기록이다 |")
        elif n in what:
            L.append(f"| `{n}` | {what[n]} |")
    L += ["", "원본(raw.jsonl 포함)은 테스트베드의 `runs/" + run_id + "/` 에 남아 있다 — ",
          "계약을 고친 뒤 `tb.run reanalyze` 로 다시 읽거나, `tb.run replay` 로 "
          "디버그 영상을 다시 잡을 수 있다.", ""]
    return "\n".join(L)


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
    (dest / "README.md").write_text(_readme(run_dir.name, meta, v, copied))

    #  INDEX.md — 워크스페이스만 열어도 시험 이력이 보이게. 한 런 = 한 줄.
    idx = root / "INDEX.md"
    if not idx.is_file():
        idx.write_text("# 카메라 테스트베드 시험 이력\n\n"
                       "| 실행 | 보려던 것 | 영상 | 행 | 유효 | 호스트 | 코드 |\n"
                       "|---|---|---|---|---|---|---|\n")
    vr = "—" if v["valid_rate"] is None else f"{v['valid_rate']:.3f}"
    head = f"| [{run_dir.name}]({run_dir.name}/report.md) |"
    row = (f"{head} {env['note'] or env['label'] or env['preset'] or '—'} | "
           f"{Path(env['video']).name if env['video'] else '—'} | {v['rows']} | {vr} | "
           f"{env['host']} | {env['workspace_code_sha'][:8]} |")
    #  ★같은 런을 다시 내보내는 것이 정상 흐름이다★ — 계약을 고쳐 재분석하고,
    #  다른 런과 비교하고, replay 로 영상을 잡은 뒤에 다시 내보낸다. 그때마다
    #  줄이 쌓이면 이력이 아니라 중복이 된다 → 그 런의 줄을 갈아 끼운다.
    lines = [ln for ln in idx.read_text().splitlines() if not ln.startswith(head)]
    idx.write_text("\n".join(lines + [row]) + "\n")

    return {"dest": dest, "copied": copied, "verdict": v, "env": env,
            "gitignored": ensure_gitignore(ws),
            "video": any(n.endswith(".mp4") for n in copied)}
