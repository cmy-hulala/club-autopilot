@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM 同步 Linktree：新增本期課前表單、隱藏（不刪除）上一期。
REM 在背景用無視窗的 Chrome 執行，不會跳出視窗或搶走焦點。
REM 第一次使用請先執行 run_linktree_login.cmd 用「社團」帳號登入；登入狀態存在 %LOCALAPPDATA%\club-autopilot\linktree-profile。
REM 截圖與結果：out\2026-11-25-candice\linktree_shots\
cd /d "%~dp0"
.venv\Scripts\python -m autopilot run events\2026-11-25-candice.yaml --linktree
pause
