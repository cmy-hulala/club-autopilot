@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM 只需要做一次：用一般的 Chrome 視窗打開 Linktree 登入頁（程式專用 profile）。
REM 請按 Continue with Google，用社團帳號 nycu.iec@gmail.com 登入，看到後台後關掉視窗即可。
cd /d "%~dp0"
.venv\Scripts\python -m autopilot.linktree login
