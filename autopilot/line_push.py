"""LINE 「直接發送」: push the LINE announcement text to the club groups via the club's official-account bot.

Only ever called from the dashboard button after the officer confirms the exact text. It is never
part of 全部執行 / run_pipeline. Credentials come from env vars or a private file outside the repo:

  token:  env LINE_CHANNEL_ACCESS_TOKEN, or %USERPROFILE%\\創創社-私密\\line\\line_bot.json
          {"channel_access_token": "...", "group_ids": ["C..", "C.."]}  (older "group_id" also accepted;
          path overridable with LINE_BOT_CONFIG)
  groups: env LINE_GROUP_IDS (comma), else the private file, else line_groups in the local settings file
          (autopilot/local_settings.py), else config/line_groups.yaml (empty in the public repo)

Nothing secret is logged or returned; status shows only which piece is missing.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_TEXT = 5000


def config_path() -> Path:
    env = os.environ.get("LINE_BOT_CONFIG")
    if env:
        return Path(env)
    return Path.home() / "創創社-私密" / "line" / "line_bot.json"


GROUPS_FILE = Path(__file__).resolve().parent.parent / "config" / "line_groups.yaml"


def default_groups() -> list[dict]:
    """Named groups from the local settings file, else config/line_groups.yaml (no IDs in the public repo)."""
    from .local_settings import line_groups
    local = line_groups()
    if local:
        return local
    try:
        import yaml
        data = yaml.safe_load(GROUPS_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    return [{"name": str(g.get("name") or g.get("id")), "id": str(g["id"])}
            for g in data.get("groups") or [] if isinstance(g, dict) and g.get("id")]


def load_config() -> dict:
    """Token from env or the private file; groups from env / private file / repo default.

    Private file accepts {"channel_access_token": "...", "group_ids": ["C..", "C.."]} or the older
    {"group_id": "C.."}. Returns {"token", "groups": [{"name","id"}], "missing": [...]}.
    """
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or ""
    p = config_path()
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    token = token or data.get("channel_access_token") or data.get("token") or ""
    names = {g["id"]: g["name"] for g in default_groups()}
    ids: list[str] = []
    env_ids = os.environ.get("LINE_GROUP_IDS") or os.environ.get("LINE_GROUP_ID") or ""
    if env_ids:
        ids = [x.strip() for x in env_ids.split(",") if x.strip()]
    elif data.get("group_ids") or data.get("group_id"):
        raw = data.get("group_ids") or [data.get("group_id")]
        ids = [str(x).strip() for x in (raw if isinstance(raw, list) else [raw]) if str(x).strip()]
    else:
        ids = list(names)
    groups = [{"id": i, "name": names.get(i, i[:6] + "…")} for i in ids]
    missing = []
    if not token:
        missing.append("LINE_CHANNEL_ACCESS_TOKEN")
    if not groups:
        missing.append("群組 ID")
    return {"token": token, "groups": groups, "missing": missing}


def public_status() -> dict:
    cfg = load_config()
    return {"configured": not cfg["missing"], "missing": cfg["missing"], "config_file": str(config_path()),
            "groups": [g["name"] for g in cfg["groups"]]}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _push_one(token: str, to: str, text: str, opener) -> dict:
    retry_key = str(uuid.uuid4())
    body = json.dumps({"to": to, "messages": [{"type": "text", "text": text}]}).encode("utf-8")
    req = urllib.request.Request(PUSH_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "X-Line-Retry-Key": retry_key,
    })
    try:
        with opener(req, timeout=20) as resp:
            status = getattr(resp, "status", 200)
            request_id = resp.headers.get("x-line-request-id", "") if getattr(resp, "headers", None) else ""
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8")).get("message", "")
        except Exception:
            pass
        return {"ok": False, "error": f"LINE API {e.code}{('：' + detail) if detail else ''}"}
    except Exception as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
    return {"ok": 200 <= int(status) < 300, "status": int(status), "request_id": request_id}


def push_text(text: str, *, opener=None) -> dict:
    """Send one text message to every configured group. Only called after the officer confirmed."""
    text = (text or "").strip()
    if not text:
        raise ValueError("LINE 文案是空的。")
    if len(text) > MAX_TEXT:
        raise ValueError(f"LINE 文案超過 {MAX_TEXT} 字。")
    cfg = load_config()
    if cfg["missing"]:
        raise RuntimeError("LINE 未設定：缺少 " + "、".join(cfg["missing"]))
    open_ = opener or urllib.request.urlopen
    results = []
    for g in cfg["groups"]:
        r = _push_one(cfg["token"], g["id"], text, open_)
        results.append({"group": g["name"], **r})
    return {"ok": all(r["ok"] for r in results), "results": results,
            "request_id": ",".join(r.get("request_id", "") for r in results if r.get("request_id"))}
