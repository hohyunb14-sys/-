@echo off
rem Windows: 처음 한 번 설치하고 브라우저 검토 화면을 연다.
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q -e ".[ai]"
voicecut serve %*
pause
