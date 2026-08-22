#!/usr/bin/env bash
# install.sh — ~/agency 를 만들고 시작 파일을 넣습니다.
#
#   이미 있는 파일은 절대 덮어쓰지 않습니다. 아무것도 지우지 않습니다.
set -uo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${AGENCY_DIR:-$HOME/agency}"

echo ""
echo "  방호현 에이전시 설치"
echo "  ─────────────────────────────────────────────"
echo "  자료 폴더 : $DEST"
echo "  화면 파일 : $SRC/office-map.html"
echo ""

if [ -d "$DEST" ]; then
  echo "  이미 있는 폴더입니다. 없는 파일만 채웁니다 (기존 파일은 그대로)."
else
  echo "  새로 만듭니다."
fi

mkdir -p "$DEST"
cp -Rn "$SRC/agency/." "$DEST/" 2>/dev/null || true

echo ""
echo "  들어 있는 것:"
find "$DEST" -type f -not -name ".DS_Store" | sed "s|$DEST|    ~/agency|" | sort
echo ""

# ── 폴더를 화면에 반영 ──────────────────────────────
if command -v node >/dev/null 2>&1; then
  echo "  폴더를 읽는 중..."
  echo ""
  if AGENCY_DIR="$DEST" node "$SRC/scan.mjs"; then
    echo ""
    echo "  ─────────────────────────────────────────────"
    echo "  다 됐습니다. 이 줄을 복사해서 실행하세요:"
    echo ""
    echo "      open \"$SRC/office-map.html\""
    echo ""
    echo "  앞으로 폴더를 고친 뒤에는:"
    echo ""
    echo "      node \"$SRC/scan.mjs\""
    echo ""
    echo "  실제 작업은 Claude Code 로:"
    echo ""
    echo "      cd ~/agency && claude"
    echo ""
  fi
else
  echo "  ─────────────────────────────────────────────"
  echo "  폴더는 만들어졌지만 Node.js 가 없어 화면에 반영하지 못했습니다."
  echo ""
  echo "  Node.js 설치 (둘 중 하나):"
  echo "    · https://nodejs.org 에서 LTS 버튼 → 받은 .pkg 실행"
  echo "    · 또는 터미널에서:  brew install node"
  echo ""
  echo "  설치한 뒤 터미널을 새로 열고 이 파일을 다시 실행하세요:"
  echo ""
  echo "      bash \"$SRC/install.sh\""
  echo ""
  echo "  Node 없이 지금 화면만 보시려면 (데모 모드로 열립니다):"
  echo ""
  echo "      open \"$SRC/office-map.html\""
  echo ""
fi
