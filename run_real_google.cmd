@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM 真的用社團 Google 帳號建「課前表單、課後表單、3 封 Gmail 草稿」。不會寄出任何信。
REM 權杖只從私密資料夾的 google-oauth 讀，不會複製進這個資料夾。
REM 新安裝用 %%USERPROFILE%%\ClubAutopilot-private；已有「創創社-私密」就沿用。AUTOPILOT_PRIVATE_DIR 優先。
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
set EVENT=events\2026-11-25-candice.yaml
if defined AUTOPILOT_PRIVATE_DIR (
  set "PRIV=%AUTOPILOT_PRIVATE_DIR%"
) else if exist "%USERPROFILE%\創創社-私密\" (
  set "PRIV=%USERPROFILE%\創創社-私密"
) else (
  set "PRIV=%USERPROFILE%\ClubAutopilot-private"
)
set "OAUTH=%PRIV%\google-oauth"
REM 社員密件副本清單（私密檔，可省略；省略時預告信草稿沒有密件副本，寄前自己貼）
set "MEMBERS=%PRIV%\密件副本\社員.txt"
echo.
echo === 1/2 先跑一次預覽（不連 Google）===
.venv\Scripts\python -m autopilot run %EVENT%
echo.
echo === 2/2 真的建表單與 Gmail 草稿（本機設定裡的社團信箱，只存草稿）===
choice /M "確定要用社團帳號建立表單和草稿嗎"
if errorlevel 2 goto :eof
if exist "%MEMBERS%" (
  .venv\Scripts\python -m autopilot run %EVENT% --google --oauth-dir "%OAUTH%" --members-bcc-file "%MEMBERS%"
) else (
  .venv\Scripts\python -m autopilot run %EVENT% --google --oauth-dir "%OAUTH%"
)
echo.
echo 完成後：到本機設定裡的社團信箱草稿匣檢查 3 封草稿；表單 Banner 與確認訊息照 outputs\1_報名表單.md 手動補。
pause
