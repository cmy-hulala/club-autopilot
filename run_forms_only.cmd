@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM 只用個人 Google 帳號建課前／課後表單，不建 Gmail 草稿。不會寄出任何信。
REM token 只從本機私密資料夾讀，不會複製進這個資料夾，也不會印出內容。
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
set EVENT=events\2026-11-25-candice.yaml
REM 帳號來自環境變數 AUTOPILOT_FORMS_ACCOUNT 或 %USERPROFILE%\創創社-私密\autopilot_local.yaml（不放 repo）。
for /f "usebackq delims=" %%t in (`.venv\Scripts\python -c "from autopilot.web import default_forms_token; print(default_forms_token())"`) do set TOKEN=%%t
if not exist "%TOKEN%" (
  echo 找不到個人帳號 token：%TOKEN%
  exit /b 1
)
echo.
echo 會用個人帳號（本機設定）建立課前表單與課後表單（先核對帳號，不符就停止）。
echo 不會建立或寄出 Gmail 草稿。
choice /M "確定要用個人帳號建立表單嗎"
if errorlevel 2 goto :eof
.venv\Scripts\python -m autopilot run %EVENT% --forms-only --forms-token "%TOKEN%"
echo.
echo 完成後：用輸出的編輯／填寫網址檢查表單；Banner 與確認訊息照 outputs\1_報名表單.md 手動補。
pause
