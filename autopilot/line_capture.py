"""Capture a LINE group ID from a local webhook. Tokens stay in the private folder.

Nothing in this module prints the channel access token or the channel secret.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

from .launchpad_i18n import T

BOT_INFO_URL = "https://api.line.me/v2/bot/info"
WEBHOOK_URL = "https://api.line.me/v2/bot/channel/webhook/endpoint"
GROUP_SUMMARY = "https://api.line.me/v2/bot/group/{group_id}/summary"
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

_LOCK = threading.Lock()
_CAPTURE: dict = {
    "group_id": "",
    "group_name": "",
    "previous_endpoint": "",
    "tunnel_url": "",
    "mode": "",
    "error": "",
}
_TUNNEL = None


def mask_secret(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def sign(body: bytes, secret: str) -> str:
    mac = hmac.new((secret or "").encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


def signature_ok(body: bytes, secret: str, header: str) -> bool:
    if not secret or not header:
        return False
    return hmac.compare_digest(sign(body, secret), header.strip())


def group_ids_from_payload(data: dict) -> list[str]:
    found: list[str] = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        source = event.get("source") or {}
        if not isinstance(source, dict):
            continue
        if source.get("type") != "group":
            continue
        group_id = str(source.get("groupId") or "").strip()
        if group_id and group_id not in found:
            found.append(group_id)
    return found


def parse_tunnel_url(text: str) -> str:
    match = TUNNEL_RE.search(text or "")
    return match.group(0) if match else ""


def secrets_path() -> Path:
    from .launchpad import assert_outside_repo, is_inside_repo, private_dir

    env = os.environ.get("LINE_BOT_CONFIG")
    if env and not is_inside_repo(env):
        return assert_outside_repo(env)
    return assert_outside_repo(private_dir() / "line" / "line_bot.json")


def load_secrets() -> dict:
    path = secrets_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_secrets(token: str, secret: str, group_id: str = "", group_name: str = "") -> None:
    path = secrets_path()
    current = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                current = loaded
        except Exception:
            current = {}
    if token:
        current["channel_access_token"] = token.strip()
    if secret:
        current["channel_secret"] = secret.strip()
    if group_id:
        groups = [g for g in (current.get("group_ids") or []) if str(g).strip() and str(g).strip() != group_id]
        groups.insert(0, group_id.strip())
        current["group_ids"] = groups
        names = current.get("group_names") if isinstance(current.get("group_names"), dict) else {}
        if group_name:
            names[group_id.strip()] = group_name.strip()
        current["group_names"] = names
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _call(method: str, url: str, token: str, opener, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token.strip(),
        "Content-Type": "application/json",
    })
    try:
        with opener(req, timeout=20) as resp:
            status = int(getattr(resp, "status", 200))
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            raw = exc.read()
        except Exception:
            raw = b""
    except Exception:
        return 0, {}
    try:
        parsed = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def bot_info(token: str, opener=urllib.request.urlopen) -> dict:
    token = (token or "").strip()
    if not token:
        return {"ok": False, "message": T["line_token_bad"]}
    status, data = _call("GET", BOT_INFO_URL, token, opener)
    if status == 0:
        return {"ok": False, "message": T["line_token_network"]}
    if status == 401:
        return {"ok": False, "message": T["line_token_rejected"]}
    if status != 200 or not data.get("displayName"):
        return {"ok": False, "message": T["line_token_bad"]}
    picture = str(data.get("pictureUrl") or "")
    if not picture.startswith("https://"):
        picture = ""
    return {
        "ok": True,
        "message": T["line_token_ok"],
        "display_name": str(data.get("displayName") or ""),
        "picture_url": picture,
        "basic_id": str(data.get("basicId") or ""),
        "masked": mask_secret(token),
    }


def group_summary(token: str, group_id: str, opener=urllib.request.urlopen) -> dict:
    status, data = _call("GET", GROUP_SUMMARY.format(group_id=group_id), token, opener)
    if status != 200:
        return {"ok": False, "name": ""}
    return {"ok": True, "name": str(data.get("groupName") or ""), "picture_url": str(data.get("pictureUrl") or "")}


def get_webhook(token: str, opener=urllib.request.urlopen) -> str:
    status, data = _call("GET", WEBHOOK_URL, token, opener)
    if status != 200:
        return ""
    return str(data.get("endpoint") or "")


def set_webhook(token: str, endpoint: str, opener=urllib.request.urlopen) -> bool:
    status, _data = _call("PUT", WEBHOOK_URL, token, opener, {"endpoint": endpoint})
    return 200 <= status < 300


def remember_group(group_id: str) -> None:
    with _LOCK:
        _CAPTURE["group_id"] = group_id
        _CAPTURE["error"] = ""


def snapshot() -> dict:
    with _LOCK:
        return dict(_CAPTURE)


def reset_state() -> None:
    global _TUNNEL
    with _LOCK:
        _CAPTURE.update({
            "group_id": "",
            "group_name": "",
            "previous_endpoint": "",
            "tunnel_url": "",
            "mode": "",
            "error": "",
        })
    proc = _TUNNEL
    _TUNNEL = None
    if proc is not None:
        try:
            proc.kill()
        except Exception:
            pass


def begin_capture(port: int, opener=urllib.request.urlopen, starter=None) -> dict:
    """Point LINE at a temporary public URL, or report that the tunnel is unavailable."""
    secrets = load_secrets()
    token = str(secrets.get("channel_access_token") or "")
    secret = str(secrets.get("channel_secret") or "")
    if not token or not secret:
        return {"ok": False, "mode": "manual", "message": T["line_need_secret"]}
    start = starter or start_quick_tunnel
    tunnel = start(port)
    if not tunnel.get("url"):
        with _LOCK:
            _CAPTURE["mode"] = "manual"
            _CAPTURE["error"] = T["line_capture_manual"]
        return {"ok": True, "mode": "manual", "message": T["line_capture_manual"]}
    previous = get_webhook(token, opener)
    public = tunnel["url"].rstrip("/") + "/api/line/webhook"
    if not set_webhook(token, public, opener):
        with _LOCK:
            _CAPTURE["mode"] = "manual"
            _CAPTURE["error"] = T["line_capture_manual"]
        return {"ok": True, "mode": "manual", "message": T["line_capture_manual"]}
    with _LOCK:
        _CAPTURE["previous_endpoint"] = previous
        _CAPTURE["tunnel_url"] = tunnel["url"]
        _CAPTURE["mode"] = "tunnel"
        _CAPTURE["error"] = ""
    return {"ok": True, "mode": "tunnel", "message": T["line_capture_wait"]}


def finish_capture(opener=urllib.request.urlopen) -> dict:
    """Save the group name and put the previous webhook back when there was one."""
    secrets = load_secrets()
    token = str(secrets.get("channel_access_token") or "")
    with _LOCK:
        group_id = _CAPTURE.get("group_id") or ""
        previous = _CAPTURE.get("previous_endpoint") or ""
    name = ""
    if token and group_id:
        name = group_summary(token, group_id, opener).get("name") or ""
    if token and previous:
        set_webhook(token, previous, opener)
    if group_id:
        save_secrets(token, str(secrets.get("channel_secret") or ""), group_id, name)
    reset_state()
    with _LOCK:
        _CAPTURE["group_id"] = group_id
        _CAPTURE["group_name"] = name
    return {"ok": bool(group_id), "group_id": group_id, "group_name": name, "message": T["line_group_saved"] if group_id else T["line_capture_wait"]}


def start_quick_tunnel(port: int) -> dict:
    """Launch cloudflared if it exists. Tests replace this function."""
    import shutil
    import subprocess

    binary = shutil.which("cloudflared")
    if not binary:
        return {"ok": False, "url": ""}
    proc = subprocess.Popen(
        [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    global _TUNNEL
    _TUNNEL = proc
    url = ""
    try:
        for _ in range(40):
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                break
            url = parse_tunnel_url(line)
            if url:
                break
    except Exception:
        url = ""
    if not url:
        try:
            proc.kill()
        except Exception:
            pass
        return {"ok": False, "url": ""}
    return {"ok": True, "url": url}
