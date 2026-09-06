#!/usr/bin/env bash
#  상주를 걷어낸다. ★토큰과 올린 파일은 남긴다★ — 지우면 되돌릴 수 없고,
#  「지우기」를 눌렀을 때 사라져야 하는 것은 서비스지 사람의 자료가 아니다.
set -euo pipefail

UNIT="$HOME/.config/systemd/user/cam-studio.service"
ENVFILE="$HOME/.config/cam-testbed/env"

systemctl --user disable --now cam-studio.service 2>/dev/null || true
rm -f "$UNIT"
systemctl --user daemon-reload
echo "서비스를 내리고 유닛을 지웠습니다."

[ -f "$ENVFILE" ] && echo "토큰은 남겨 둡니다: $ENVFILE  (지우려면 rm)"
UP="${TB_UPLOAD_DIR:-$HOME/cam_testbed_uploads}"
[ -d "$UP" ] && echo "올린 파일도 남겨 둡니다: $UP  ($(du -sh "$UP" 2>/dev/null | cut -f1))"

if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = "yes" ]; then
  echo "linger 를 켰었다면 되돌리기:  sudo loginctl disable-linger $USER"
fi
