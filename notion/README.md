# notion/ — 개발 기록 Notion 동기화

마크다운 문서를 Notion 페이지로 올리고, 이후에도 **같은 페이지를 갱신**한다.
외부 패키지 불필요 — 시스템 `python3` 로 바로 돈다(저장소 규칙: 외부 의존성 0).

## 문서 셋

| 파일 | 무엇 | 누가 쓰나 |
|---|---|---|
| `STATUS.md` | **현재 현황 · 미검증 항목** | **사람이 손으로 고치는 유일한 파일** |
| `PROJECT_LOG.md` | `STATUS.md` + `git log` 전문 | `gen_log.sh` 가 생성 — **직접 편집 금지** |
| `DEV_HISTORY.md` | **서사** — 무엇을 요청했고 왜 그렇게 결정했나 | 세션 기록을 재료로 사람+Claude |

`git log` 는 **무엇을 바꿨나**만 알려 준다. **무엇을 요청했고 왜 그렇게 결정했나**는
세션 기록에 있다. 둘을 합쳐야 서사가 된다 — 그게 `DEV_HISTORY.md` 다.

## 최초 1회 세팅

1. <https://www.notion.so/my-integrations> → **New integration**
   - Type: **Internal** · Capabilities: Read / Insert / Update content
   - → **Internal Integration Secret**(`ntn_...`) 복사
2. Notion 에서 문서를 넣을 **상위 페이지**를 하나 만든다 (예: `cam_testbed`)
   - 그 페이지 우측 상단 `···` → **Connections(연결)** → 1번의 integration 선택
   - ‼ 이 단계를 빼먹으면 API 가 페이지를 못 본다 (`Could not find page`)
3. 저장:
   ```bash
   python3 notion/notion_sync.py setup --token ntn_xxxxx --parent "<상위 페이지 URL>"
   ```
   토큰은 `~/.config/cam_testbed_notion/config.json`(0600, **저장소 밖**)에 저장된다.
   ★`skku_ws` 와 설정 디렉터리를 나눠 두었다★ — 상위 페이지와 문서↔페이지 매핑이 섞이면
   한쪽을 올릴 때 다른 쪽 페이지를 덮어쓴다.

## 매번 쓰는 명령

```bash
bash notion/gen_log.sh                                  # PROJECT_LOG.md 다시 생성
python3 notion/notion_sync.py push                      # PROJECT_LOG.md 올리기/갱신
python3 notion/notion_sync.py push --file notion/DEV_HISTORY.md
python3 notion/notion_sync.py status                    # 설정/동기화 상태
```

- 첫 `push` 는 새 페이지를 만들고, 두 번째부터는 **같은 페이지의 내용을 교체**한다
  → **URL 이 안 바뀌므로 공유 링크가 유지된다**
- 일부러 새 페이지: `push --new`
- 문서↔페이지 매핑은 `notion/.notion_pages.json` (비밀정보 아님, git 포함)

## 진행 상황을 계속 올리는 방법

1. `notion/STATUS.md` 의 현재 현황·미검증 항목을 고친다 (손으로 쓰는 곳은 여기 하나)
2. 이번 회차의 서사를 `notion/DEV_HISTORY.md` 에 절로 추가한다
3. `bash notion/gen_log.sh` → `push`

‼ **`push` 는 사용자가 요청할 때만 돈다** (작업마다 자동 푸시하면 토큰이 샌다).
Claude Code 는 로컬 문서만 갱신해 두고, 업로드는 명시적으로 요청할 때 실행한다:
> "오늘 작업 내용을 `notion/DEV_HISTORY.md` 에 추가하고 노션에 올려줘"

## 개발 일지 재료 뽑기

```bash
python3 notion/claude_prompts.py --since 2026-08-01 --max-len 220
```
Claude Code 세션 기록에서 **사람이 실제로 친 요청**만 날짜순으로 뽑는다.
기록 위치는 `~/.claude/projects/<cwd 의 / 와 _ 를 - 로 바꾼 것>/*.jsonl` 이다.
‼ 이 기록은 **그 컴퓨터에만** 있다 — 다른 PC 로 옮기려면 그 디렉터리를 통째로 복사한다.

## 지원하는 마크다운

헤딩(`#`~`###`) · 문단 · 목록 · 번호목록 · 인용(`>`) · 코드펜스(언어 인식) · 표 · 구분선
인라인: `**굵게**`, `` `코드` ``, `[링크](url)`

## 출처

`~/skku_ws/notion/` 에서 그대로 가져왔다(`notion_sync.py` · `claude_prompts.py`).
바꾼 것은 `CONFIG_DIR` 한 줄과 `gen_log.sh` 의 제목·머리말뿐이다.
