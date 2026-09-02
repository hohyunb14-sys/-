#!/usr/bin/env bash
# macOS / Linux: 처음 한 번 설치하고 브라우저 검토 화면을 연다.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
pip install -q -e ".[ai]"
exec voicecut serve "$@"
