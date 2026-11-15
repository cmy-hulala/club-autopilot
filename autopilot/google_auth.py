"""Google OAuth (refactored from the pre-hackathon _tools/google_oauth.py).

Tokens never live in this repo. Pass --oauth-dir (default: <private folder>\\google-oauth).
The default --google path refuses to run unless the authorised account is the club inbox
saved in local settings (club_email) or CLUB_GOOGLE_ACCOUNT. There is no built-in inbox.
--forms-only uses a separate token file and refuses unless it is the requested personal account.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/forms",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
def club_account() -> str:
    """Club Gmail from the environment or the saved local profile. Empty for a new install."""
    from .local_settings import club_email
    return club_email()


def require_club_account() -> str:
    email = club_account()
    if "@" not in email:
        sys.exit(
            "尚未設定社團信箱。請在本機私密設定的 club_email 填上社團 Gmail，"
            "或設定環境變數 CLUB_GOOGLE_ACCOUNT。"
        )
    return email


def default_oauth_dir() -> Path:
    env = (os.environ.get("CLUB_OAUTH_DIR") or "").strip()
    if env:
        return Path(env)
    from .local_settings import private_dir
    return private_dir() / "google-oauth"


def get_credentials(oauth_dir: Path, interactive: bool = True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    oauth_dir = Path(oauth_dir)
    repo = Path(__file__).resolve().parent.parent
    if repo in oauth_dir.resolve().parents or oauth_dir.resolve() == repo:
        sys.exit("OAuth 資料夾不能放在 repo 裡。")
    token, secret = oauth_dir / "token.json", oauth_dir / "credentials.json"
    creds = None
    if token.is_file():
        creds = Credentials.from_authorized_user_file(str(token))  # use the scopes the token already has
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token.write_text(creds.to_json(), encoding="utf-8")
            except Exception:
                creds = None
    if not creds or not creds.valid:
        if not interactive:
            # Dashboard / background runs: never pop a browser login window.
            sys.exit("社團 Gmail 授權已過期：請執行 run_real_google.cmd，用本機設定裡的社團信箱重新授權一次，再按重跑。")
        if not secret.is_file():
            sys.exit(f"找不到 {secret}。請先照私密資料夾的 README 建好 OAuth 用戶端。")
        account = require_club_account()
        print(f"瀏覽器會打開 Google 授權，請選 {account}。")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(port=0, prompt="select_account consent", login_hint=account)
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def authorize(oauth_dir: Path, timeout_s: int = 600, force: bool = False) -> str:
    """Only the consent step for the saved club inbox: open Google sign-in, wait up to `timeout_s` for the
    redirect, check the account, then save token.json. Creates nothing else."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    oauth_dir = Path(oauth_dir)
    repo = Path(__file__).resolve().parent.parent
    if repo in oauth_dir.resolve().parents or oauth_dir.resolve() == repo:
        sys.exit("OAuth 資料夾不能放在 repo 裡。")
    expected = require_club_account()
    token, secret = oauth_dir / "token.json", oauth_dir / "credentials.json"
    if not force and token.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(token))
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            if creds.valid and set(SCOPES) <= set(creds.scopes or []):
                email = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(userId="me").execute().get("emailAddress", "")
                if email.lower() == expected.lower():
                    token.write_text(creds.to_json(), encoding="utf-8")
                    return email
        except Exception:
            pass
    if not secret.is_file():
        sys.exit(f"找不到 {secret}。請先照私密資料夾的 README 建好 OAuth 用戶端。")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    print(f"瀏覽器會打開 Google 授權頁，請用 {expected} 登入並按「允許」（最多等 {timeout_s // 60} 分鐘）。")
    try:
        creds = flow.run_local_server(port=0, prompt="select_account consent", login_hint=expected,
                                      timeout_seconds=timeout_s,
                                      success_message="授權完成，可以關掉這個分頁回到終端機。")
    except Exception as e:  # timeout leaves no redirect -> the library raises
        sys.exit(f"沒有在 {timeout_s // 60} 分鐘內完成授權（{e.__class__.__name__}），token 沒有變更。請再執行一次。")
    if not creds or not creds.valid:
        sys.exit("沒有在時間內完成授權，token 沒有變更。請再執行一次。")
    email = build("gmail", "v1", credentials=creds, cache_discovery=False).users().getProfile(userId="me").execute().get("emailAddress", "")
    if email.lower() != expected.lower():
        sys.exit(f"授權的是 {email}，不是 {expected}，token 沒有儲存。請重跑並選 {expected}。")
    oauth_dir.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json(), encoding="utf-8")
    return email


def services(oauth_dir: Path, interactive: bool = True):
    from googleapiclient.discovery import build

    creds = get_credentials(oauth_dir, interactive=interactive)
    gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
    email = gmail.users().getProfile(userId="me").execute().get("emailAddress", "")
    expected = require_club_account()
    if email.lower() != expected.lower():
        sys.exit(f"目前授權的是 {email}，不是 {expected}。為了讓草稿進社團草稿匣，已停止。")
    forms = build("forms", "v1", credentials=creds, cache_discovery=False)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return {"gmail": gmail, "forms": forms, "drive": drive, "account": email}


def get_credentials_from_token(token_path: Path):
    """Load an existing token file. Never prints the token. Refreshes in place if expired; does not start a browser login."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_path = Path(token_path)
    repo = Path(__file__).resolve().parent.parent
    if repo in token_path.resolve().parents or token_path.resolve() == repo:
        sys.exit("OAuth token 不能放在 repo 裡。")
    if not token_path.is_file():
        sys.exit(f"找不到 token 檔：{token_path}")
    creds = Credentials.from_authorized_user_file(str(token_path))
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        except Exception as e:
            sys.exit(f"token 已過期且無法重新整理（不會在這裡開瀏覽器重新授權）：{e}")
    if not creds or not creds.valid:
        sys.exit("token 無效或已過期，請先在本機重新授權。")
    return creds


def account_email(creds, drive=None) -> str:
    """Read the signed-in address without printing token contents."""
    from googleapiclient.discovery import build
    try:
        oauth2 = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        email = (oauth2.userinfo().get().execute() or {}).get("email", "")
        if email:
            return email
    except Exception:
        pass
    if drive is None:
        drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    about = drive.about().get(fields="user").execute()
    return (about.get("user") or {}).get("emailAddress", "")


def forms_services(token_path: Path, expected_account: str):
    """Forms + Drive for --forms-only. Stops if the token is not the expected account. No Gmail drafts."""
    from googleapiclient.discovery import build

    creds = get_credentials_from_token(token_path)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    email = account_email(creds, drive)
    if email.lower() != expected_account.lower():
        sys.exit(f"目前授權的是 {email}，不是 {expected_account}。為了用指定的個人帳號建表，已停止。")
    forms = build("forms", "v1", credentials=creds, cache_discovery=False)
    return {"gmail": None, "forms": forms, "drive": drive, "account": email}


def club_services_optional(oauth_dir: Path):
    """Club Drive/Forms for inspection (same account check as services())."""
    return services(oauth_dir)
