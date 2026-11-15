@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
start "" http://127.0.0.1:5050/
.venv\Scripts\python -m autopilot web --port 5050
