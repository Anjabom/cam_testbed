#!/usr/bin/env bash
# PROJECT_LOG.md 생성 — 이력의 단일 소스는 git log 다. PROJECT_LOG.md 를 손으로 고치지 말 것.
# 손으로 쓰는 부분은 STATUS.md(현재 현황) 하나뿐이며 여기에 앞쪽으로 끼워 넣는다.
#   bash notion/gen_log.sh                 # 생성
#   python3 notion/notion_sync.py push     # 노션 반영 (사용자가 요청할 때만)
set -euo pipefail
cd "$(dirname "$0")/.."

out=notion/PROJECT_LOG.md
{
    echo "# cam_testbed — 카메라 인지 노드 테스트 플랫폼 개발 기록"
    echo
    echo "> \`notion/gen_log.sh\` 가 \`git log\` 에서 생성한 문서다. **직접 편집 금지** (다음 생성 때 덮어쓴다)."
    echo "> 설계·경계 규칙은 \`CLAUDE.md\` 와 \`README.md\`, 현재 현황은 \`notion/STATUS.md\` 를 고친다."
    echo "> 서사(무엇을 왜 그렇게 결정했나)는 \`notion/DEV_HISTORY.md\`."
    echo "> 생성일 $(date +%F) · 커밋 $(git rev-list --count HEAD)개"
    echo
    if [ -f notion/STATUS.md ]; then
        cat notion/STATUS.md
        echo
        echo "---"
        echo
    fi
    echo "## 날짜별 개발 일지"
    echo
    git log --date=short --format='### %ad — %s%n%n%b'
} > "$out"

echo "생성 완료: $out ($(wc -l < "$out") 줄, $(du -h "$out" | cut -f1))"
