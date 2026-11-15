@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM 只做本機設定裡社團信箱的 Google 授權，存到私密資料夾的 google-oauth\token.json。
REM 不會建立表單、不會建立或寄出任何信。瀏覽器授權頁最多等 10 分鐘。
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo 找不到 .venv，請先執行 run_web.cmd 建好環境。
  pause
  exit /b 1
)
echo 瀏覽器會打開 Google 授權頁：請選本機設定裡的社團信箱（club_email），按「繼續／允許」。
echo 完成前請不要關掉這個視窗（最多等 10 分鐘）。
echo.
.venv\Scripts\python -m autopilot auth --force --timeout 600
if errorlevel 1 (
  echo.
  echo 授權沒有完成，請再雙擊一次。
) else (
  echo.
  echo 完成！可以回到儀表板，把「Gmail 草稿」按重跑。
)
pause
