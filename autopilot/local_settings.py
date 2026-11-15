"""Machine-local private settings, kept OUT of the (public) repo.

Values come from, in order:
  1. environment variables (AUTOPILOT_FORMS_ACCOUNT, NOTION_PAGE_ID, LINE_GROUP_IDS, ...)
  2. a private YAML file: env AUTOPILOT_LOCAL_CONFIG, else
     %USERPROFILE%\\創創社-私密\\autopilot_local.yaml, else config/local.yaml (git-ignored)
  3. a harmless placeholder

Example autopilot_local.yaml:
  forms_account: officer@gmail.com          # personal account that owns the Google Forms
  notion_page_id: 0123456789abcdef...       # club Notion「📣 Marketing」page
  line_groups:                              # LINE groups for the 「直接發送」 button
    - {name: Club group, id: C0123...}
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_ACCOUNT = "your-personal-account@gmail.com"


def config_path() -> Path | None:
    env = os.environ.get("AUTOPILOT_LOCAL_CONFIG")
    if env:
        return Path(env)
    for p in (Path.home() / "創創社-私密" / "autopilot_local.yaml", ROOT / "config" / "local.yaml"):
        if p.is_file():
            return p
    return None


def load() -> dict:
    p = config_path()
    if not p or not p.is_file():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    except Exception:
        return {}


def get(key: str, env: str | None = None, default=None):
    if env and os.environ.get(env):
        return os.environ[env].strip()
    v = load().get(key)
    return default if v in (None, "") else v


def forms_account() -> str:
    """Personal Google account that owns the club's Forms (never the club account)."""
    return str(get("forms_account", "AUTOPILOT_FORMS_ACCOUNT", PLACEHOLDER_ACCOUNT)).strip()


def forms_account_local() -> str:
    return forms_account().split("@", 1)[0]


def notion_page_id() -> str:
    return str(get("notion_page_id", "NOTION_PAGE_ID", "") or "").strip()


def line_groups() -> list[dict]:
    raw = load().get("line_groups") or []
    return [{"name": str(g.get("name") or g.get("id")), "id": str(g["id"])}
            for g in raw if isinstance(g, dict) and g.get("id")]
