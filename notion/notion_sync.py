#!/usr/bin/env python3
"""
notion_sync.py — 마크다운 문서를 Notion 페이지로 올리고, 이후에도 같은 페이지를 갱신한다.

이 워크스페이스의 진행상황을 반복적으로 Notion에 동기화하기 위한 도구.
표준 라이브러리만 쓴다(외부 패키지 불필요). 시스템 python3로 그냥 실행하면 된다.

┌ 최초 1회 세팅 ────────────────────────────────────────────────────────────
│ 1) https://www.notion.so/my-integrations 에서 "New integration" 생성
│      Type = Internal, Capabilities = Read/Insert/Update content
│    → Internal Integration Secret(`ntn_...`) 복사
│ 2) Notion에서 이 문서를 넣을 **상위 페이지**를 하나 만든다 (예: "cam_testbed")
│    그 페이지 우측 상단 ··· → Connections(연결) → 위에서 만든 integration 선택
│ 3) 토큰과 상위 페이지 URL을 저장:
│      python3 notion/notion_sync.py setup --token ntn_xxx --parent <상위페이지 URL>
│    (토큰은 ~/.config/cam_testbed_notion/config.json 에 0600 권한으로 저장 — 저장소 밖)
└───────────────────────────────────────────────────────────────────────────

사용:
    python3 notion/notion_sync.py push                    # PROJECT_LOG.md 를 올리거나 갱신
    python3 notion/notion_sync.py push --file 다른.md      # 다른 문서
    python3 notion/notion_sync.py push --new              # 갱신 대신 새 페이지 생성
    python3 notion/notion_sync.py status                  # 현재 설정/매핑 확인

동작:
    문서별로 생성된 Notion 페이지 ID를 notion/.notion_pages.json 에 기억한다.
    두 번째 실행부터는 그 페이지의 기존 블록을 지우고 새 내용으로 교체한다
    (= URL이 유지되므로 공유 링크가 안 깨진다).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

CONFIG_DIR = os.path.expanduser("~/.config/cam_testbed_notion")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, ".notion_pages.json")
DEFAULT_DOC = os.path.join(HERE, "PROJECT_LOG.md")

MAX_TEXT = 1900          # Notion rich_text 조각 상한(2000)에 여유를 둔 값
MAX_CHILDREN = 90        # 한 요청당 블록 수 상한(100)에 여유를 둔 값


# ──────────────────────────────────────────────────────────── 설정/상태 저장

def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def extract_id(value: str) -> str:
    """Notion URL 또는 raw ID에서 32자리 ID를 뽑아 대시 형식으로 정규화한다."""
    value = (value or "").strip()
    hexes = re.findall(r"[0-9a-fA-F]{32}", value.replace("-", ""))
    if not hexes:
        raise SystemExit(f"Notion 페이지 ID를 찾을 수 없습니다: {value!r}\n"
                         "페이지 URL 전체(https://www.notion.so/...-32자리해시)를 넣어주세요.")
    h = hexes[-1].lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


# ──────────────────────────────────────────────────────────────── API 호출

def api(method: str, path: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")

    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            # 429(rate limit) / 5xx 는 재시도
            if e.code == 429 or 500 <= e.code < 600:
                wait = float(e.headers.get("Retry-After", 1 + attempt))
                time.sleep(wait)
                continue
            try:
                msg = json.loads(raw).get("message", raw)
            except Exception:
                msg = raw
            raise SystemExit(f"Notion API {e.code} ({method} {path})\n  {msg}")
        except urllib.error.URLError as e:
            if attempt == 4:
                raise SystemExit(f"Notion 연결 실패: {e}")
            time.sleep(1 + attempt)
    raise SystemExit("Notion API 재시도 초과")


# ────────────────────────────────────────────────── 마크다운 → Notion 블록

INLINE_RE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<link>\[[^\]]+\]\([^)]+\))"
    r"|(?P<bold>\*\*[^*]+\*\*)"
)


def rich_text(text: str) -> list[dict]:
    """인라인 마크다운(**굵게**, `코드`, [링크](url))을 Notion rich_text 배열로."""
    out: list[dict] = []

    def emit(content: str, *, bold=False, code=False, link=None):
        while content:
            chunk, content = content[:MAX_TEXT], content[MAX_TEXT:]
            item = {"type": "text", "text": {"content": chunk}}
            if link:
                item["text"]["link"] = {"url": link}
            ann = {}
            if bold:
                ann["bold"] = True
            if code:
                ann["code"] = True
            if ann:
                item["annotations"] = ann
            out.append(item)

    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            emit(text[pos:m.start()])
        if m.group("code"):
            emit(m.group("code")[1:-1], code=True)
        elif m.group("bold"):
            emit(m.group("bold")[2:-2], bold=True)
        else:  # link
            lm = re.match(r"\[([^\]]+)\]\(([^)]+)\)", m.group("link"))
            # ‼ Notion 은 http(s)/mailto 가 아닌 URL 을 400 으로 거절한다("Invalid URL for
            #   link") — 그런데 이 문서는 git 커밋 메시지 생성물이라 링크가 아닌 것이
            #   문법에만 걸린다(예: `/parking/vision[7](axis_slope)`, 저장소 상대경로).
            #   실패하면 페이지를 비운 뒤 채우기 전에 죽으므로, 링크로 못 쓸 것은 원문
            #   그대로 평문으로 흘린다.
            if re.match(r"^(https?://|mailto:)", lm.group(2)):
                emit(lm.group(1), link=lm.group(2))
            else:
                emit(m.group("link"))
        pos = m.end()
    if pos < len(text):
        emit(text[pos:])

    return out or [{"type": "text", "text": {"content": ""}}]


def block(kind: str, text: str, **extra) -> dict:
    payload = {"rich_text": rich_text(text)}
    payload.update(extra)
    return {"object": "block", "type": kind, kind: payload}


LANG_MAP = {
    "bash": "bash", "sh": "bash", "shell": "bash",
    "python": "python", "py": "python",
    "json": "json", "yaml": "yaml", "yml": "yaml",
    "c": "c", "cpp": "c++", "ini": "ini", "diff": "diff",
    "": "plain text", "text": "plain text",
}


def split_table_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def md_to_blocks(md: str) -> list[dict]:
    lines = md.split("\n")
    blocks: list[dict] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 코드 펜스
        if stripped.startswith("```"):
            lang = LANG_MAP.get(stripped[3:].strip().lower(), "plain text")
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            code = "\n".join(buf)
            # 코드블록도 2000자 제한 → 넘치면 잘라 여러 블록으로
            while True:
                head, code = code[:MAX_TEXT], code[MAX_TEXT:]
                blocks.append({
                    "object": "block", "type": "code",
                    "code": {"rich_text": [{"type": "text", "text": {"content": head}}],
                             "language": lang},
                })
                if not code:
                    break
            continue

        # 표
        if stripped.startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = split_table_row(stripped)
            width = len(header)
            i += 2
            rows = [header]
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = split_table_row(lines[i])
                cells = (cells + [""] * width)[:width]
                rows.append(cells)
                i += 1
            blocks.append({
                "object": "block", "type": "table",
                "table": {
                    "table_width": width,
                    "has_column_header": True,
                    "has_row_header": False,
                    "children": [
                        {"object": "block", "type": "table_row",
                         "table_row": {"cells": [rich_text(c) for c in r]}}
                        for r in rows
                    ],
                },
            })
            continue

        # 구분선
        if re.match(r"^-{3,}$", stripped) or re.match(r"^\*{3,}$", stripped):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            i += 1
            continue

        # 헤딩
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 3)
            blocks.append(block(f"heading_{level}", m.group(2)))
            i += 1
            continue

        # 인용
        if stripped.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            blocks.append(block("quote", "\n".join(buf).strip()))
            continue

        # 목록 (중첩은 평탄화 — Notion에서도 충분히 읽힌다)
        m = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
        if m:
            blocks.append(block("bulleted_list_item", m.group(2).strip()))
            i += 1
            continue
        m = re.match(r"^(\s*)\d+[.)]\s+(.*)$", line)
        if m:
            blocks.append(block("numbered_list_item", m.group(2).strip()))
            i += 1
            continue

        # 빈 줄
        if not stripped:
            i += 1
            continue

        # 문단 (연속된 줄을 하나로 합침)
        buf = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", ">", "|", "```"))
                    or re.match(r"^[-*+]\s", nxt) or re.match(r"^\d+[.)]\s", nxt)
                    or re.match(r"^-{3,}$", nxt)):
                break
            buf.append(nxt)
            i += 1
        blocks.append(block("paragraph", " ".join(buf)))

    return blocks


# ─────────────────────────────────────────────────────────────── 페이지 조작

def page_title(md: str, fallback: str) -> str:
    for line in md.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def clear_page(page_id: str, token: str) -> int:
    """페이지의 기존 최상위 블록을 모두 삭제한다."""
    removed = 0
    while True:
        res = api("GET", f"/blocks/{page_id}/children?page_size=100", token)
        ids = [b["id"] for b in res.get("results", [])]
        if not ids:
            return removed
        for bid in ids:
            api("DELETE", f"/blocks/{bid}", token)
            removed += 1
        if not res.get("has_more"):
            return removed


def append_blocks(page_id: str, blocks: list[dict], token: str) -> None:
    for n in range(0, len(blocks), MAX_CHILDREN):
        api("PATCH", f"/blocks/{page_id}/children", token,
            {"children": blocks[n:n + MAX_CHILDREN]})


# ─────────────────────────────────────────────────────────────── 서브커맨드

def cmd_setup(args) -> None:
    cfg = load_config()
    if args.token:
        cfg["token"] = args.token.strip()
    if args.parent:
        cfg["parent_page_id"] = extract_id(args.parent)
    if not cfg.get("token") or not cfg.get("parent_page_id"):
        raise SystemExit("--token 과 --parent 를 모두 지정해야 합니다.")

    # 상위 페이지에 실제로 접근 가능한지 즉시 확인
    page = api("GET", f"/pages/{cfg['parent_page_id']}", cfg["token"])
    save_config(cfg)
    title = "".join(
        t.get("plain_text", "")
        for prop in page.get("properties", {}).values()
        if prop.get("type") == "title"
        for t in prop.get("title", [])
    ) or "(제목 없음)"
    print(f"✓ 설정 저장: {CONFIG_PATH}")
    print(f"✓ 상위 페이지 연결 확인: {title}")
    print(f"  {page.get('url', '')}")
    print("\n이제 다음을 실행하세요:  python3 notion/notion_sync.py push")


def cmd_status(args) -> None:
    cfg = load_config()
    print(f"설정 파일 : {CONFIG_PATH} {'(있음)' if cfg else '(없음 — setup 필요)'}")
    if cfg:
        tok = cfg.get("token", "")
        print(f"토큰      : {tok[:7]}…{tok[-4:]}" if tok else "토큰      : (없음)")
        print(f"상위 페이지: {cfg.get('parent_page_id', '(없음)')}")
    state = load_state()
    if state:
        print("\n동기화된 문서:")
        for name, info in state.items():
            print(f"  {name}")
            print(f"    {info.get('url', info.get('page_id'))}")
            print(f"    마지막 동기화: {info.get('synced_at', '?')}")
    else:
        print("\n아직 동기화된 문서가 없습니다.")


def cmd_push(args) -> None:
    cfg = load_config()
    token = os.environ.get("NOTION_TOKEN") or cfg.get("token")
    if not token:
        raise SystemExit("토큰이 없습니다. 먼저 setup 을 실행하세요 "
                         "(python3 notion/notion_sync.py setup --token ... --parent ...)")

    path = os.path.abspath(args.file)
    if not os.path.exists(path):
        raise SystemExit(f"파일이 없습니다: {path}")
    with open(path, encoding="utf-8") as f:
        md = f.read()

    key = os.path.basename(path)
    title = args.title or page_title(md, key)
    blocks = md_to_blocks(md)
    # 첫 H1은 Notion 페이지 제목으로 이미 쓰이므로 본문에서 중복 제거
    if blocks and blocks[0]["type"] == "heading_1":
        first = "".join(t["text"]["content"] for t in blocks[0]["heading_1"]["rich_text"])
        if first.strip() == title.strip():
            blocks = blocks[1:]
    print(f"· 문서   : {path}")
    print(f"· 제목   : {title}")
    print(f"· 블록 수: {len(blocks)}")

    state = load_state()
    existing = None if args.new else state.get(key, {}).get("page_id")

    if existing:
        print(f"· 대상   : 기존 페이지 갱신 ({existing})")
        n = clear_page(existing, token)
        print(f"  기존 블록 {n}개 삭제")
        append_blocks(existing, blocks, token)
        page = api("GET", f"/pages/{existing}", token)
        page_id, url = existing, page.get("url", "")
    else:
        parent = cfg.get("parent_page_id")
        if not parent:
            raise SystemExit("상위 페이지가 설정되지 않았습니다. setup --parent <URL> 을 실행하세요.")
        print(f"· 대상   : 새 페이지 생성 (상위 {parent})")
        page = api("POST", "/pages", token, {
            "parent": {"type": "page_id", "page_id": parent},
            "properties": {"title": {"title": [{"type": "text",
                                                "text": {"content": title[:2000]}}]}},
            "children": blocks[:MAX_CHILDREN],
        })
        page_id, url = page["id"], page.get("url", "")
        if len(blocks) > MAX_CHILDREN:
            append_blocks(page_id, blocks[MAX_CHILDREN:], token)

    state[key] = {
        "page_id": page_id,
        "url": url,
        "title": title,
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_state(state)
    print(f"\n✓ 완료 → {url}")


def main() -> None:
    p = argparse.ArgumentParser(description="cam_testbed 문서 → Notion 동기화")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="토큰/상위 페이지 저장 및 연결 확인")
    s.add_argument("--token", help="Notion Internal Integration Secret (ntn_...)")
    s.add_argument("--parent", help="문서를 넣을 상위 페이지 URL 또는 ID")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("push", help="마크다운을 Notion에 올리거나 갱신")
    s.add_argument("--file", default=DEFAULT_DOC, help=f"기본값: {DEFAULT_DOC}")
    s.add_argument("--title", help="페이지 제목 (기본: 문서의 첫 # 제목)")
    s.add_argument("--new", action="store_true", help="갱신 대신 새 페이지를 만든다")
    s.set_defaults(func=cmd_push)

    s = sub.add_parser("status", help="현재 설정과 동기화 상태 확인")
    s.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
