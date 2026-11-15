"""List upcoming club lectures from Notion, with a local YAML fallback.

The 11/25 demo event file was filled from the club's Notion「📣 Marketing」page
(page id: env NOTION_PAGE_ID or notion_page_id in the local settings file). This module talks
to the official Notion API when a token is available in the environment or in a
private file *outside* the repo. If the token is missing or the API fails, it
loads events/*.yaml so the dashboard still works offline.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
NOTION_VERSION = "2022-06-28"
# The club page id lives outside the repo (env NOTION_PAGE_ID or local settings), see local_settings.py.
DEFAULT_PAGE_ID = ""
TOKEN_FILENAMES = (
    "notion-token.txt",
    "notion_token.txt",
    "notion-api-token.txt",
    Path("notion") / "token.txt",
    Path("notion") / "token",
)

def club_defaults() -> dict:
    """Neutral fields for a Notion import. A saved club profile replaces the placeholders."""
    name, email, link = "社團", "club@example.com", ""
    try:
        from .local_settings import load
        saved = load() or {}
        if isinstance(saved, dict):
            name = str(saved.get("club_name") or name).strip() or name
            email = str(saved.get("club_email") or saved.get("contact_email") or email).strip() or email
            link = str(saved.get("linktree_url") or "").strip()
    except Exception:
        pass
    return {
        "club": {
            "name_zh": name,
            "name_en": name,
            "short": name,
            "email": email,
            "instagram": "",
            "ig_handle": "",
            "logo_url": "",
            "linktree_url": link,
        },
        "campus": "",
    }


def private_dir() -> Path:
    env = os.environ.get("CLUB_PRIVATE_DIR")
    if env:
        return Path(env)
    return Path.home() / "創創社-私密"


def load_token() -> str | None:
    for key in ("NOTION_TOKEN", "NOTION_API_KEY", "NOTION_SECRET"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    root = private_dir()
    for name in TOKEN_FILENAMES:
        p = root / name
        if p.is_file():
            text = p.read_text(encoding="utf-8").strip()
            # first non-empty, non-comment line
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    return None


def _dash_id(raw: str) -> str:
    hex_id = re.sub(r"[^0-9A-Fa-f]", "", raw)
    if len(hex_id) != 32:
        return raw
    return f"{hex_id[0:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:32]}"


def _plain(rich) -> str:
    if rich is None:
        return ""
    if isinstance(rich, str):
        return rich.strip()
    if isinstance(rich, dict):
        if "title" in rich or "rich_text" in rich:
            return _plain(rich.get("title") or rich.get("rich_text"))
        return (rich.get("plain_text") or rich.get("name") or "").strip()
    if isinstance(rich, list):
        return "".join(_plain(x) for x in rich).strip()
    return str(rich).strip()


def _prop(props: dict, *names: str) -> Any:
    lower = {k.lower(): v for k, v in props.items()}
    for name in names:
        if name in props:
            return props[name]
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def _prop_text(props: dict, *names: str) -> str:
    val = _prop(props, *names)
    if val is None:
        return ""
    if isinstance(val, dict):
        typ = val.get("type")
        if typ == "select":
            return ((val.get("select") or {}).get("name") or "").strip()
        if typ == "multi_select":
            return "、".join(x.get("name", "") for x in val.get("multi_select") or [] if x.get("name"))
        if typ == "people":
            return "、".join(_plain(p.get("name")) for p in val.get("people") or [])
        if typ == "url":
            return (val.get("url") or "").strip()
        if typ == "email":
            return (val.get("email") or "").strip()
        if typ == "number" and val.get("number") is not None:
            return str(val["number"])
        if typ == "status":
            return ((val.get("status") or {}).get("name") or "").strip()
        return _plain(val.get(typ) or val.get("title") or val.get("rich_text") or val)
    return _plain(val)


def _prop_date(props: dict, *names: str) -> str:
    val = _prop(props, *names)
    if val is None:
        return ""
    if isinstance(val, dict):
        start = (val.get("date") or {}).get("start") or val.get("start")
        if start:
            return str(start)[:10]
    return str(val)[:10] if val else ""


def _page_title(page: dict) -> str:
    props = page.get("properties") or {}
    for v in props.values():
        if isinstance(v, dict) and v.get("type") == "title":
            t = _plain(v.get("title"))
            if t:
                return t
    return _plain((page.get("properties") or {}).get("title")) or page.get("id", "")


def _notion_request(token: str, method: str, path: str, body: dict | None = None) -> dict:
    url = "https://api.notion.com/v1" + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Notion-Version", NOTION_VERSION)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError(f"Notion API {e.code} {path}: {detail}") from e


def _iter_children(token: str, block_id: str):
    cursor = None
    while True:
        q = f"/blocks/{_dash_id(block_id)}/children?page_size=100"
        if cursor:
            q += f"&start_cursor={cursor}"
        data = _notion_request(token, "GET", q)
        yield from data.get("results") or []
        if not data.get("has_more"):
            return
        cursor = data.get("next_cursor")


def _event_from_page(page: dict, assumed: list[str] | None = None) -> dict:
    props = page.get("properties") or {}
    title = _prop_text(props, "社課全名", "主題", "標題", "Title", "Name", "name") or _page_title(page)
    short = _prop_text(props, "短標題", "short_title", "IG標題")
    date = _prop_date(props, "日期", "Date", "date", "活動日期")
    speaker_name = _prop_text(props, "講者", "講者姓名", "Speaker", "speaker")
    speaker_title = _prop_text(props, "講者職稱", "職稱", "title_role", "Speaker title")
    room = _prop_text(props, "教室", "地點", "Room", "room")
    start = _prop_text(props, "開始", "Start", "start") or "19:00"
    end = _prop_text(props, "結束", "End", "end") or "21:30"
    deadline = _prop_text(props, "報名截止", "截止", "deadline")
    hook = _prop_text(props, "鉤子", "hook")
    session = _prop_text(props, "堂次", "session_no", "Session")
    semester = _prop_text(props, "學期", "semester") or "115-1"
    try:
        session_no = int(re.search(r"\d+", session).group()) if session else 0
    except Exception:
        session_no = 0
    if not deadline and date:
        d = dt.date.fromisoformat(date)
        deadline = (d - dt.timedelta(days=1)).isoformat() + " 23:59"
    raw = {
        **club_defaults(),
        "semester": semester,
        "session_no": session_no or 1,
        "title": title,
        "short_title": short or (title.split("：", 1)[0] if "：" in title else title),
        "speaker": {
            "name": speaker_name or "（講者）",
            "name_zh": speaker_name.split()[0] if speaker_name else "",
            "title": speaker_title,
        },
        "date": date,
        "start": start if re.match(r"\d{1,2}:\d{2}", start) else "19:00",
        "end": end if re.match(r"\d{1,2}:\d{2}", end) else "21:30",
        "room": room or "（教室待確認）",
        "deadline": deadline or "",
        "hook": hook,
        "highlights": [],
        "form_url": "",
        "form_short_url": "",
        "feedback_prefill_url": "",
        "notion_source": page.get("url") or page.get("id"),
        "assumed": assumed or [],
    }
    if not date:
        raw["assumed"] = list(dict.fromkeys([*(raw.get("assumed") or []), "date"]))
    return raw


def _lecture_card(raw: dict, slug: str, source: str, path: str | None = None) -> dict:
    date = str(raw.get("date") or "")
    try:
        d = dt.date.fromisoformat(str(date)[:10])
        upcoming = d >= dt.date.today()
        date_label = f"{d.month}/{d.day}"
    except Exception:
        upcoming = True
        date_label = date or "（日期未定）"
    speaker = raw.get("speaker") or {}
    return {
        "slug": slug,
        "title": raw.get("title") or slug,
        "short_title": raw.get("short_title") or raw.get("title") or slug,
        "session_no": raw.get("session_no"),
        "date": str(date)[:10],
        "date_label": date_label,
        "speaker": speaker.get("name") or "",
        "room": raw.get("room") or "",
        "start": raw.get("start") or "",
        "end": raw.get("end") or "",
        "source": source,
        "path": path,
        "upcoming": upcoming,
        "notion_source": raw.get("notion_source") or "",
        "raw": raw,
    }


def notion_cache_dir() -> Path:
    """Offline export of the Notion「📣 Marketing」lecture pages (one YAML per lecture, outside the repo).
    Used as the Notion source when no API token is set, so 「從 Notion 匯入」 still starts from Notion data."""
    env = os.environ.get("CLUB_NOTION_CACHE")
    return Path(env) if env else private_dir() / "notion_cache"


def list_from_cache(cache_dir: Path | None = None) -> list[dict]:
    d = Path(cache_dir or notion_cache_dir())
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if raw.get("title"):
            out.append(_lecture_card(raw, p.stem, "notion_cache", None))
    return out


def list_from_yaml(events_dir: Path | None = None) -> list[dict]:
    events_dir = Path(events_dir or ROOT / "events")
    out = []
    for p in sorted(events_dir.glob("*.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        out.append(_lecture_card(raw, p.stem, "yaml", str(p)))
    return out


def _list_from_database(token: str, database_id: str) -> list[dict]:
    lectures = []
    cursor = None
    while True:
        body: dict[str, Any] = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = _notion_request(token, "POST", f"/databases/{_dash_id(database_id)}/query", body)
        for page in data.get("results") or []:
            raw = _event_from_page(page)
            if not raw.get("title"):
                continue
            slug = _slug_for(raw)
            lectures.append(_lecture_card(raw, slug, "notion", None))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return lectures


def _slug_for(raw: dict) -> str:
    date = str(raw.get("date") or "undated")
    speaker = ((raw.get("speaker") or {}).get("name") or "event")
    token = re.sub(r"[^A-Za-z0-9]+", "-", speaker).strip("-").lower() or "event"
    # keep first ascii token; if the name is CJK-only, use 'event'
    first = token.split("-")[0]
    if first.isascii() and first:
        token = first
    else:
        token = "event"
    return f"{date}-{token}"


def list_from_notion(token: str) -> list[dict]:
    db = (os.environ.get("NOTION_DATABASE_ID") or "").strip()
    from .local_settings import notion_page_id
    page_id = (notion_page_id() or DEFAULT_PAGE_ID).strip()
    if db:
        return _list_from_database(token, db)
    if not page_id:
        raise RuntimeError("Notion page id not set (NOTION_PAGE_ID or local settings)")
    # Probe the Marketing page: it may itself be a database, or contain one / child pages.
    try:
        page = _notion_request(token, "GET", f"/pages/{_dash_id(page_id)}")
    except RuntimeError:
        # Maybe it's a database id already.
        try:
            _notion_request(token, "GET", f"/databases/{_dash_id(page_id)}")
            return _list_from_database(token, page_id)
        except RuntimeError:
            raise
    parent = page.get("parent") or {}
    if parent.get("type") == "database_id" and parent.get("database_id"):
        # A single row — list the whole database (upcoming lectures live together).
        return _list_from_database(token, parent["database_id"])
    # Walk children looking for child databases or child pages that look like lectures.
    lectures: list[dict] = []
    for block in _iter_children(token, page_id):
        typ = block.get("type")
        bid = block.get("id")
        if typ == "child_database":
            lectures.extend(_list_from_database(token, bid))
        elif typ == "child_page":
            try:
                child = _notion_request(token, "GET", f"/pages/{_dash_id(bid)}")
            except RuntimeError:
                continue
            raw = _event_from_page(child)
            title = raw.get("title") or ""
            if re.search(r"\d{1,2}\s*/\s*\d{1,2}|社課|講者", title) or raw.get("date"):
                lectures.append(_lecture_card(raw, _slug_for(raw), "notion", None))
    if lectures:
        return lectures
    # Last resort: treat the page itself as one lecture.
    raw = _event_from_page(page)
    return [_lecture_card(raw, _slug_for(raw), "notion", None)]


def list_lectures(events_dir: Path | None = None) -> dict:
    """Return {source, error, lectures} for the dashboard picker.

    Notion results come first when the API works; local YAML events are always
    included (matched by date + title so we don't list the same lecture twice).
    """
    yaml_lectures = list_from_yaml(events_dir)
    error = None
    source = "yaml_offline"
    notion_lectures: list[dict] = []
    token = load_token()
    if token:
        try:
            notion_lectures = list_from_notion(token)
            source = "notion"
        except Exception as e:
            error = f"{e.__class__.__name__}: {e}"
            source = "yaml_fallback"
    else:
        source = "yaml_offline"
    if not notion_lectures:
        notion_lectures = list_from_cache()
        if notion_lectures:
            source = "notion_cache"

    seen = set()
    merged: list[dict] = []

    def _key(card: dict) -> str:
        return (card.get("date") or "") + "|" + (card.get("title") or card.get("slug") or "")

    for card in notion_lectures + yaml_lectures:
        k = _key(card)
        if k in seen:
            # Prefer a yaml path so "import" can just open the existing file.
            if card.get("path") and not any(c.get("slug") == card["slug"] and c.get("path") for c in merged):
                for c in merged:
                    if _key(c) == k and not c.get("path"):
                        c["path"] = card["path"]
                        c["slug"] = card["slug"]
            continue
        seen.add(k)
        merged.append(card)

    merged.sort(key=lambda c: (c.get("date") or "9999-99-99", c.get("slug") or ""))
    return {
        "source": source,
        "error": error,
        "token_present": bool(token),
        "lectures": merged,
    }


def write_event_yaml(raw: dict, dest: Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dest
