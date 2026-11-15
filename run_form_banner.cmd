@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM 把社團黑底 Banner 補上課前／課後表單（只使用個人帳號：AUTOPILOT_FORMS_ACCOUNT 或本機設定，絕不用社團帳號）。
REM 會打開可見的 Chrome，設定檔在 %USERPROFILE%\創創社-私密\chrome-form-banner（不是你平常的瀏覽器）。
REM 表單 ID 從 out\<活動>\state.json 讀；確認訊息來自活動檔（與 outputs\1_報名表單.md、5_課後表單.md 同一句）。
REM 不會改題目，也不會寄信。若跳出登入頁，請用個人帳號登入，程式最多等 10 分鐘。
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  .venv\Scripts\python -m pip install -q -r requirements.txt
)
set EVENT=events\2026-11-25-candice.yaml
echo.
echo 會用個人帳號（本機設定）把 Banner 補到 %EVENT% 已建立的課前／課後表單。
echo 不會寄信，也不會改題目。
.venv\Scripts\python -m autopilot set-banner --event %EVENT%
echo.
pause
