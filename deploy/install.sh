#!/usr/bin/env bash
#  카메라 보정 스튜디오를 ★이 기계에 상주★ 시킨다.
#
#  왜 이 스크립트가 있나 — 스튜디오를 쓰는 사람은 다른 기기 앞에 있다.
#  터미널을 열어 명령을 쳐야 화면이 뜬다면 원격으로 옮긴 뜻이 없다.
#  그래서 부팅하면 이미 떠 있게 만들고, 사람은 주소만 안다.
#
#      bash deploy/install.sh [포트]        # 기본 8770
#
#  되돌리기는 deploy/uninstall.sh 다.
set -euo pipefail

TESTBED="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-8770}"
ENVDIR="$HOME/.config/cam-testbed"
ENVFILE="$ENVDIR/env"
UNITDIR="$HOME/.config/systemd/user"
UNIT="$UNITDIR/cam-studio.service"

case "$PORT" in
  ''|*[!0-9]*) echo "포트는 숫자여야 합니다: $PORT" >&2; exit 2 ;;
esac

#  ROS 는 «워크스페이스 값 불러오기» 버튼에만 필요하다 — 없어도 설치는 된다.
ROS=""
for d in /opt/ros/*/setup.bash; do [ -f "$d" ] && ROS="$d"; done
[ -n "$ROS" ] || echo "알림: /opt/ros/*/setup.bash 가 없습니다 — «워크스페이스 값 불러오기»만 못 씁니다."

# ── 토큰 ───────────────────────────────────────────────────────────
#  ★있으면 다시 만들지 않는다★ 토큰이 바뀌면 다른 기기에 저장된 로그인이
#  전부 끊긴다. 재설치는 흔한 일이고 그때마다 전원이 다시 로그인해야 한다면
#  사람은 결국 토큰을 없애 버린다.
mkdir -p "$ENVDIR"; chmod 700 "$ENVDIR"
if [ -f "$ENVFILE" ] && grep -q '^TB_WEB_TOKEN=.' "$ENVFILE"; then
  TOKEN="$(sed -n 's/^TB_WEB_TOKEN=//p' "$ENVFILE" | head -1)"
  echo "토큰: 이미 있는 것을 씁니다 ($ENVFILE)"
else
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  echo "토큰: 새로 만들었습니다"
fi

umask 077
cat > "$ENVFILE" <<EOF
#  스튜디오가 읽는 값들. ★이 파일만 0600 이다★ — 토큰이 여기 있다.
#  고친 뒤에는:  systemctl --user restart cam-studio
TB_WEB_TOKEN=$TOKEN
TB_WEB_PORT=$PORT
#  올린 파일이 가는 곳 / 한 개 최대 크기(바이트). 주석을 풀면 바뀐다.
#TB_UPLOAD_DIR=$HOME/cam_testbed_uploads
#TB_UPLOAD_MAX=8589934592
EOF
chmod 600 "$ENVFILE"

# ── 유닛 ───────────────────────────────────────────────────────────
mkdir -p "$UNITDIR"
sed -e "s#@TESTBED@#$TESTBED#g" -e "s#@ENVFILE@#$ENVFILE#g" -e "s#@ROS@#$ROS#g" \
    "$TESTBED/deploy/cam-studio.service" > "$UNIT"

systemctl --user daemon-reload
systemctl --user enable --now cam-studio.service

#  ★로그아웃해도 살아 있게★ 실차 노트북은 뚜껑만 열려 있고 아무도 로그인해
#  있지 않은 일이 흔하다. linger 가 없으면 그때 서비스가 통째로 내려간다.
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
  echo
  echo "로그아웃해도 계속 떠 있게 하려면 (한 번만):"
  echo "    sudo loginctl enable-linger $USER"
fi

#  방화벽이 켜져 있으면 다른 기기에서 못 들어온다 — 조용히 실패하는 자리다.
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q '^Status: active'; then
  echo
  echo "ufw 가 켜져 있습니다 — 같은 공유기에서 들어오게 하려면:"
  echo "    sudo ufw allow from 192.168.0.0/16 to any port $PORT proto tcp"
  echo "  (공유기 대역이 다르면 그 대역으로 바꾸세요. 전체 개방은 하지 마세요.)"
fi

sleep 1
echo
systemctl --user --no-pager --lines=0 status cam-studio.service | head -4 || true
echo
echo "──────────────────────────────────────────────────────────"
echo " 다른 기기의 브라우저에서 아래 주소로 들어갑니다"
python3 - "$PORT" <<'PY'
import socket, sys
port = sys.argv[1]
host = socket.gethostname().split(".")[0]
print(f"   http://{host}.local:{port}")
try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(0.2); s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
    if not ip.startswith("127."):
        print(f"   http://{ip}:{port}      (이름이 안 잡히는 망에서)")
except OSError:
    pass
PY
echo
echo " 로그인 창이 뜨면 — 아이디는 아무거나, 비밀번호가 이것입니다:"
echo "   $TOKEN"
echo "──────────────────────────────────────────────────────────"
echo
echo " 상태 보기   systemctl --user status cam-studio"
echo " 로그 보기   journalctl --user -u cam-studio -f"
echo " 다시 시작   systemctl --user restart cam-studio"
echo " 지우기      bash deploy/uninstall.sh"
