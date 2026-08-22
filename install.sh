#!/usr/bin/env bash
# install.sh — ~/agency 를 만들고 시작 파일을 넣습니다.
#
#   이미 있는 파일은 절대 덮어쓰지 않습니다. 아무것도 지우지 않습니다.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${AGENCY_DIR:-$HOME/agency}"

echo "설치 위치: $DEST"
if [ -d "$DEST" ]; then
  echo "이미 있는 폴더입니다. 없는 파일만 채웁니다 (기존 파일은 그대로)."
fi

mkdir -p "$DEST"
# -n : 이미 있는 파일은 건너뜀
cp -Rn "$SRC/agency/." "$DEST/" 2>/dev/null || true

echo ""
echo "완료. 들어 있는 것:"
find "$DEST" -type f | sed "s|$DEST|  ~/agency|" | sort

cat <<'MSG'

다음 단계
  1) 폴더를 화면에 반영          node scan.mjs
  2) 화면 열기                   open office-map.html
  3) Claude Code 로 이 방들 쓰기  cd ~/agency && claude

scan.mjs 는 읽기만 합니다. 지우거나 고치지 않습니다.
MSG
