#!/usr/bin/env python3
"""claude_prompts.py — Claude Code 세션 기록에서 '사람이 실제로 친 요청'만 날짜순으로 뽑는다.

개발 일지(`DEV_HISTORY.md`)를 쓸 때의 재료다. `git log` 는 **무엇을 바꿨는지**만 알려주지만,
세션 기록에는 **무엇을 요청했고 왜 그렇게 결정했는지**가 남아 있다. 둘을 합쳐야 서사가 된다.

    python3 notion/claude_prompts.py                       # 현재 워크스페이스 전체
    python3 notion/claude_prompts.py --since 2026-08-01
    python3 notion/claude_prompts.py --cwd ~/other_ws --max-len 300 > /tmp/prompts.md

기록 위치는 `~/.claude/projects/<cwd 경로의 / 와 _ 를 - 로 바꾼 것>/*.jsonl` 이다.
예) `/home/me/my_ws` → `~/.claude/projects/-home-me-my-ws/`
‼ 이 기록은 **그 컴퓨터에만** 있다. 다른 PC 로 옮기려면 저 디렉터리를 통째로 복사한다.

표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re

# 사람이 친 게 아닌 것들: IDE 컨텍스트 주입, 슬래시명령 메타, 스킬 본문, 첨부 안내
SKIP = re.compile(
    r"^\s*(<ide_|<command-|<local-command-|<bash-|<system-reminder>|"
    r"\[Image:|\[Request interrupted|Caveat: The messages below|"
    r"Base directory for this skill:)")


def project_dir(cwd: str) -> str:
    slug = os.path.abspath(os.path.expanduser(cwd)).replace("/", "-").replace("_", "-")
    return os.path.expanduser(f"~/.claude/projects/{slug}")


def texts(msg) -> list[str]:
    c = msg.get("content")
    if isinstance(c, str):
        return [c]
    if isinstance(c, list):
        return [b.get("text", "") for b in c
                if isinstance(b, dict) and b.get("type") == "text"]
    return []


def main() -> None:
    p = argparse.ArgumentParser(description="Claude Code 세션 기록 → 날짜별 요청 목록")
    p.add_argument("--cwd", default=os.getcwd(), help="대상 워크스페이스 (기본: 현재 디렉터리)")
    p.add_argument("--since", default="", help="YYYY-MM-DD 이후만")
    p.add_argument("--until", default="", help="YYYY-MM-DD 까지만")
    p.add_argument("--max-len", type=int, default=0, help="요청 1건 최대 길이 (0=자르지 않음)")
    a = p.parse_args()

    d = project_dir(a.cwd)
    files = sorted(glob.glob(os.path.join(d, "*.jsonl")))
    if not files:
        raise SystemExit(f"세션 기록이 없습니다: {d}")

    rows: list[tuple[str, str]] = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                # promptId 가 있는 user 이벤트만 = 사람이 제출한 프롬프트
                # (도구 결과도 type=user 로 들어오고, 서브에이전트는 isSidechain 이다)
                if e.get("type") != "user" or e.get("isSidechain") or not e.get("promptId"):
                    continue
                for t in texts(e.get("message", {})):
                    t = t.strip()
                    if t and not SKIP.match(t):
                        rows.append(((e.get("timestamp") or "")[:19], t))

    rows.sort()
    seen: set[str] = set()
    day = None
    n = 0
    for ts, t in rows:
        if ts[:10] < a.since or (a.until and ts[:10] > a.until):
            continue
        if t in seen:          # 재시도/재개로 같은 프롬프트가 여러 세션에 중복 기록된다
            continue
        seen.add(t)
        if ts[:10] != day:
            day = ts[:10]
            print(f"\n## {day}\n")
        body = " ".join(t.split())
        if a.max_len and len(body) > a.max_len:
            body = body[:a.max_len] + " …"
        print(f"- `{ts[11:16]}` {body}")
        n += 1

    print(f"\n<!-- 세션 {len(files)}개에서 요청 {n}건 -->")


if __name__ == "__main__":
    main()
