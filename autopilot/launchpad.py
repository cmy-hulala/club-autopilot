"""First-time club setup and the service-module catalog.

Progress and the club config are written only outside the git repository.
Connection checks never send email, push LINE, publish Instagram, or edit Canva.
When credentials are missing, the check is an explicit simulated pass.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import yaml

from .launchpad_i18n import T

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_NAME = "sample-club-lecture.yaml"
SAMPLE_SLUG = "sample-club-lecture"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
HTTPS_RE = re.compile(r"^https://\S+$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9.:_-]{1,80}$")
NOTION_RE = re.compile(r"^[A-Za-z0-9-]{1,80}$")
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
LINE_ID_RE = re.compile(r"^\S{1,80}$")

STEPS: list[tuple[str, str]] = [
    ("welcome", "step_welcome"),
    ("profile", "step_profile"),
    ("google", "step_google"),
    ("line", "step_line"),
    ("linktree", "step_linktree"),
    ("instagram", "step_instagram"),
    ("canva", "step_canva"),
    ("sample", "step_sample"),
    ("dryrun", "step_dryrun"),
    ("done", "step_done"),
]
STEP_IDS = [sid for sid, _ in STEPS]
SERVICE_STEPS = ("google", "line", "linktree", "instagram", "canva")
LEADS = {
    "welcome": "lead_welcome",
    "profile": "lead_profile",
    "google": "lead_google",
    "line": "lead_line",
    "linktree": "lead_linktree",
    "instagram": "lead_instagram",
    "canva": "lead_canva",
    "sample": "lead_sample",
    "dryrun": "lead_dryrun",
    "done": "lead_done",
}
CHECK_OK = {"ok", "stub"}
KNOWN_MESSAGES = set(T.values())

MODULE_DEFS: list[dict] = [
    {"id": "notion", "name": T["mod_notion_name"], "summary": T["mod_notion_summary"],
     "fields": [{"key": "notion_page_id", "label": T["notion_page_id"], "kind": "notion_id", "invalid": T["err_notion_id"]}]},
    {"id": "google_forms", "name": T["mod_forms_name"], "summary": T["mod_forms_summary"],
     "fields": [{"key": "forms_account", "label": T["personal_email"], "kind": "email", "invalid": T["err_personal_email"]}]},
    {"id": "gmail", "name": T["mod_gmail_name"], "summary": T["mod_gmail_summary"],
     "fields": [{"key": "club_email", "label": T["club_inbox"], "kind": "email", "invalid": T["err_club_inbox"]}]},
    {"id": "line", "name": T["mod_line_name"], "summary": T["mod_line_summary"],
     "fields": [
         {"key": "group_name", "label": T["group_name"], "kind": "short", "invalid": T["err_group_len"]},
         {"key": "group_id", "label": T["group_id"], "kind": "line_id", "invalid": T["err_group_id"]},
     ]},
    {"id": "linktree", "name": T["mod_linktree_name"], "summary": T["mod_linktree_summary"],
     "fields": [{"key": "url", "label": T["linktree_url"], "kind": "https", "invalid": T["err_linktree_url"]}]},
    {"id": "instagram", "name": T["mod_ig_name"], "summary": T["mod_ig_summary"],
     "fields": [{"key": "adb_serial", "label": T["phone_serial"], "kind": "serial", "invalid": T["err_serial"]}]},
    {"id": "canva", "name": T["mod_canva_name"], "summary": T["mod_canva_summary"],
     "fields": [{"key": "note", "label": T["template_note"], "kind": "note", "invalid": T["err_note"]}]},
    {"id": "banner", "name": T["mod_banner_name"], "summary": T["mod_banner_summary"], "fields": []},
    {"id": "certificate", "name": T["mod_cert_name"], "summary": T["mod_cert_summary"], "fields": []},
    {"id": "qr", "name": T["mod_qr_name"], "summary": T["mod_qr_summary"], "fields": []},
]
MODULE_IDS = [m["id"] for m in MODULE_DEFS]
_MODULE_BY_ID = {m["id"]: m for m in MODULE_DEFS}
_SETTING_KEYS = {f["key"] for m in MODULE_DEFS for f in m["fields"]}
NOTICES = {"saved": "saved", "on": "turned_on", "off": "turned_off", "tested": "tested"}


class UnsafeConfigPath(ValueError):
    """Raised when a write would land inside the git repository."""


def is_inside_repo(path: Path, root: Path | None = None) -> bool:
    root = (root or ROOT).resolve()
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return True
    return resolved == root or root in resolved.parents


def assert_outside_repo(path: Path, root: Path | None = None) -> Path:
    resolved = Path(path).expanduser().resolve()
    if is_inside_repo(resolved, root):
        raise UnsafeConfigPath("refusing to write inside the git repository")
    return resolved


def output_allowed(path: Path, root: Path | None = None) -> bool:
    """A sample copy may live outside the repo, or under the gitignored out/ folder."""
    root = (root or ROOT).resolve()
    resolved = Path(path).expanduser().resolve()
    if not is_inside_repo(resolved, root):
        return True
    out = (root / "out").resolve()
    return resolved == out or out in resolved.parents


def private_dir() -> Path:
    env = os.environ.get("AUTOPILOT_PRIVATE_DIR")
    if env:
        return assert_outside_repo(env)
    return assert_outside_repo(Path.home() / "創創社-私密")


def config_destination() -> Path:
    """Where onboarding writes club settings. Never a path inside the git repo.

    AUTOPILOT_LOCAL_CONFIG is used only when it already points outside the repo.
    Tests point that variable at a fixture inside the repo; that file is left untouched
    and the private folder is used instead.
    """
    explicit = os.environ.get("AUTOPILOT_ONBOARDING_CONFIG")
    if explicit:
        return assert_outside_repo(explicit)
    local = os.environ.get("AUTOPILOT_LOCAL_CONFIG")
    if local:
        try:
            return assert_outside_repo(local)
        except UnsafeConfigPath:
            pass
    return assert_outside_repo(private_dir() / "autopilot_local.yaml")


def sample_source(root: Path | None = None) -> Path:
    """Fictional lecture used by onboarding. The real club fixture stays in tests/ only."""
    root = Path(root or ROOT)
    path = root / "samples" / "demo-club" / "lecture.yaml"
    if path.is_file():
        return path
    return root / "samples" / "demo-club" / "lecture.yaml"


def sample_preview(root: Path | None = None) -> dict:
    """Key fields shown on the import step. Empty strings if the file cannot be read."""
    blank = {"club": "", "title": "", "speaker": "", "date": "", "place": ""}
    path = sample_source(root)
    if not path.is_file():
        return blank
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return blank
    if not isinstance(raw, dict):
        return blank
    club = raw.get("club") if isinstance(raw.get("club"), dict) else {}
    speaker = raw.get("speaker") if isinstance(raw.get("speaker"), dict) else {}
    place = f"{raw.get('campus') or ''} {raw.get('room') or ''}".strip()
    return {
        "club": str(club.get("name_zh") or club.get("name_en") or ""),
        "title": str(raw.get("title") or ""),
        "speaker": str(speaker.get("name") or ""),
        "date": str(raw.get("date") or ""),
        "place": place,
    }


def needs_onboarding() -> bool:
    """True when the wizard has not written a finished progress file."""
    try:
        path = private_dir() / "onboarding_progress.json"
    except Exception:
        return False
    if not path.is_file():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    return not bool(isinstance(data, dict) and data.get("finished"))


def fresh_progress() -> dict:
    return {
        "version": 1,
        "current": "welcome",
        "completed": [],
        "profile": {
            "club_name": "",
            "contact_name": "",
            "contact_email": "",
            "brand_color": "#c98d17",
            "tagline": "",
        },
        "services": {
            "google": {"forms_account": "", "club_email": "", "last_check": None, "has_account": "", "guide_done": ""},
            "line": {
                "group_name": "", "group_id": "", "last_check": None, "has_account": "", "guide_done": "",
                "token_saved": "", "bot_name": "", "bot_picture": "", "token_mask": "",
            },
            "linktree": {"url": "", "last_check": None, "has_account": "", "guide_done": ""},
            "instagram": {"adb_serial": "", "last_check": None, "has_account": "", "guide_done": ""},
            "canva": {
                "note": "", "last_check": None, "has_account": "", "guide_done": "",
                "ig_template": "", "cert_template": "", "use_sample": "",
            },
        },
        "sample": {"imported": False, "slug": "", "path_name": ""},
        "dry_run": {"ran": False, "ok": False, "sent": False, "message": "", "detail": "", "steps": 0, "slug": ""},
        "finished": False,
        "config_written": False,
    }


def fresh_modules() -> dict:
    return {m["id"]: {"enabled": True, "settings": {}, "last_test": None} for m in MODULE_DEFS}


def _clip(value: str, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def _known_check(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    message = raw.get("message")
    if status not in ("ok", "stub", "error") or message not in KNOWN_MESSAGES:
        return None
    return {"status": status, "message": message}


def _atomic_text(path: Path, text: str) -> None:
    path = assert_outside_repo(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class Store:
    def __init__(self, out_root: Path, root: Path | None = None):
        self.root = Path(root or ROOT)
        self.out_root = Path(out_root)
        self.private = private_dir()
        self.progress_path = assert_outside_repo(self.private / "onboarding_progress.json")
        self.modules_path = assert_outside_repo(self.private / "modules_state.yaml")
        self.config_path = config_destination()

    def load_progress(self) -> dict:
        raw: dict = {}
        if self.progress_path.is_file():
            try:
                loaded = json.loads(self.progress_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception:
                raw = {}
        progress = _merge_progress(raw)
        sample_path = self.private / "events" / SAMPLE_NAME
        if progress["sample"]["imported"] and not sample_path.is_file():
            progress["sample"] = {"imported": False, "slug": "", "path_name": ""}
        return progress

    def save_progress(self, progress: dict) -> None:
        safe = _merge_progress(progress)
        safe["dry_run"]["sent"] = False
        _atomic_text(self.progress_path, json.dumps(safe, ensure_ascii=False, indent=2))

    def load_modules(self) -> dict:
        raw: dict = {}
        if self.modules_path.is_file():
            try:
                loaded = yaml.safe_load(self.modules_path.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict) and isinstance(loaded.get("modules"), dict):
                    raw = loaded["modules"]
            except Exception:
                raw = {}
        return _merge_modules(raw)

    def save_modules(self, state: dict) -> None:
        safe = _merge_modules(state)
        body = yaml.safe_dump({"modules": modules_public(safe)}, allow_unicode=True, sort_keys=False)
        _atomic_text(self.modules_path, body)

    def import_sample(self) -> dict:
        src = sample_source(self.root)
        if not src.is_file():
            raise FileNotFoundError(SAMPLE_NAME)
        dest = assert_outside_repo(self.private / "events" / SAMPLE_NAME)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        web = self.out_root / "_web" / SAMPLE_NAME
        if output_allowed(web, self.root):
            web.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, web)
        return {"imported": True, "slug": SAMPLE_SLUG, "path_name": SAMPLE_NAME}

    def dry_run(self) -> dict:
        event = self.private / "events" / SAMPLE_NAME
        failed = {"ran": True, "ok": False, "sent": False, "message": T["dry_failed"], "detail": "", "steps": 0, "slug": ""}
        if not event.is_file():
            failed["detail"] = "sample file missing"
            return failed
        try:
            from .pipeline import run_pipeline
            run = run_pipeline(event, "preview", self.out_root)
        except Exception as exc:
            failed["detail"] = str(exc)[:500]
            return failed
        linktree = (run.manifest.get("linktree") or {})
        drafts = run.manifest.get("drafts") or []
        live = bool(linktree.get("executed")) or any(isinstance(d, dict) and d.get("draft_id") for d in drafts)
        if run.mode != "preview" or live:
            failed["detail"] = "preview produced a live send"
            failed["steps"] = len(run.steps)
            return failed
        return {
            "ran": True,
            "ok": True,
            "sent": False,
            "message": T["dry_ok"],
            "detail": "",
            "steps": len(run.steps),
            "slug": run.slug,
        }

    def write_config(self, progress: dict, modules: dict) -> Path:
        dest = assert_outside_repo(self.config_path)
        existing: dict = {}
        if dest.is_file():
            try:
                loaded = yaml.safe_load(dest.read_text(encoding="utf-8")) or {}
                if isinstance(loaded, dict):
                    existing = loaded
            except Exception:
                existing = {}
        fresh = build_config(progress, modules)
        merged = dict(existing)
        for key, value in fresh.items():
            if key in ("modules", "dry_run_sent", "onboarding_completed"):
                merged[key] = value
                continue
            if value in ("", None, [], {}):
                continue
            merged[key] = value
        merged["dry_run_sent"] = False
        merged["onboarding_completed"] = True
        text = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False)
        _atomic_text(dest, text)
        return dest


def _merge_progress(saved: dict) -> dict:
    base = fresh_progress()
    if not isinstance(saved, dict):
        return base
    current = saved.get("current")
    base["current"] = current if current in STEP_IDS else "welcome"
    base["completed"] = [s for s in (saved.get("completed") or []) if s in STEP_IDS]
    base["finished"] = bool(saved.get("finished"))
    base["config_written"] = bool(saved.get("config_written"))
    profile = saved.get("profile") if isinstance(saved.get("profile"), dict) else {}
    for key in base["profile"]:
        if isinstance(profile.get(key), str):
            base["profile"][key] = _clip(profile[key])
    services = saved.get("services") if isinstance(saved.get("services"), dict) else {}
    for sid, bucket in base["services"].items():
        raw = services.get(sid) if isinstance(services.get(sid), dict) else {}
        for key in list(bucket):
            if key == "last_check":
                bucket["last_check"] = _known_check(raw.get("last_check"))
            elif isinstance(raw.get(key), str):
                limit = 800 if key in ("bot_picture", "ig_template", "cert_template") else 300
                bucket[key] = _clip(raw[key], limit)
    sample = saved.get("sample") if isinstance(saved.get("sample"), dict) else {}
    if sample.get("path_name") == SAMPLE_NAME and sample.get("slug") == SAMPLE_SLUG and sample.get("imported"):
        base["sample"] = {"imported": True, "slug": SAMPLE_SLUG, "path_name": SAMPLE_NAME}
    dry = saved.get("dry_run") if isinstance(saved.get("dry_run"), dict) else {}
    message = dry.get("message") if dry.get("message") in (T["dry_ok"], T["dry_failed"], "") else ""
    steps = dry.get("steps")
    base["dry_run"] = {
        "ran": bool(dry.get("ran")),
        "ok": bool(dry.get("ok")) and message == T["dry_ok"],
        "sent": False,
        "message": message,
        "detail": _clip(dry.get("detail") if isinstance(dry.get("detail"), str) else "", 500),
        "steps": int(steps) if isinstance(steps, int) and steps >= 0 else 0,
        "slug": dry.get("slug") if isinstance(dry.get("slug"), str) and SLUG_RE.fullmatch(dry.get("slug") or "") else "",
    }
    if not base["dry_run"]["ok"]:
        base["dry_run"]["ok"] = False
    return base


def _merge_modules(saved: dict) -> dict:
    base = fresh_modules()
    if not isinstance(saved, dict):
        return base
    for mid, mod in base.items():
        raw = saved.get(mid) if isinstance(saved.get(mid), dict) else {}
        if "enabled" in raw:
            mod["enabled"] = bool(raw.get("enabled"))
        settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        clean = {}
        for field in _MODULE_BY_ID[mid]["fields"]:
            value = settings.get(field["key"])
            if isinstance(value, str):
                clean[field["key"]] = _clip(value)
        mod["settings"] = clean
        mod["last_test"] = _known_check(raw.get("last_test"))
    return base


def modules_public(state: dict) -> dict:
    """Settings snapshot safe to store. Tokens are not a field, so they cannot be copied."""
    out = {}
    state = _merge_modules(state)
    for mid, mod in state.items():
        check = _known_check(mod.get("last_test"))
        out[mid] = {
            "enabled": bool(mod["enabled"]),
            "settings": {k: v for k, v in mod["settings"].items() if k in _SETTING_KEYS},
            "last_test": check,
        }
    return out


def build_config(progress: dict, modules: dict) -> dict:
    progress = _merge_progress(progress)
    profile = progress["profile"]
    google = progress["services"]["google"]
    line = progress["services"]["line"]
    cfg = {
        "club_name": profile["club_name"].strip(),
        "contact_name": profile["contact_name"].strip(),
        "contact_email": profile["contact_email"].strip(),
        "brand_color": profile["brand_color"].strip() or "#c98d17",
        "tagline": profile["tagline"].strip(),
        "forms_account": google["forms_account"].strip(),
        "club_email": google["club_email"].strip(),
        "linktree_url": progress["services"]["linktree"]["url"].strip(),
        "ig_adb_serial": progress["services"]["instagram"]["adb_serial"].strip(),
        "canva_note": progress["services"]["canva"]["note"].strip(),
        "canva_ig_template_design_id": _saved_ig_template(progress),
        "canva_cert_template_design_id": _saved_cert_template(progress),
        "onboarding_completed": True,
        "dry_run_sent": False,
        "sample_event": progress["sample"]["slug"],
        "modules": modules_public(modules),
    }
    if line["group_id"].strip():
        cfg["line_groups"] = [{"name": line["group_name"].strip(), "id": line["group_id"].strip()}]
    return cfg


def blank_template_yaml() -> str:
    return """# Club Autopilot local config template.
# Save this OUTSIDE the git repository. Suggested path:
#   %USERPROFILE%\\創創社-私密\\autopilot_local.yaml
#   (or set AUTOPILOT_ONBOARDING_CONFIG / AUTOPILOT_LOCAL_CONFIG to that file)
# Do not commit this file. Do not put channel tokens, OAuth tokens, or member lists in the repo.
club_name: Your Club Name
contact_name: Club Officer
contact_email: officer@example.com
brand_color: "#c98d17"
tagline: One sentence about the club
forms_account: officer@example.com
club_email: club@example.com
line_groups: []
linktree_url: https://linktr.ee/yourclub
ig_adb_serial: ""
canva_note: ""
onboarding_completed: false
dry_run_sent: false
modules:
  google_forms: {enabled: true, settings: {forms_account: officer@example.com}}
  gmail: {enabled: true, settings: {club_email: club@example.com}}
  line: {enabled: true, settings: {}}
"""


def _field(name: str, label: str, value: str, kind: str = "text") -> dict:
    return {"name": name, "label": label, "value": value, "kind": kind}


def _profile_errors(profile: dict) -> list[str]:
    errors = []
    name = profile["club_name"].strip()
    if not name:
        errors.append(T["err_club_name"])
    elif len(name) > 80:
        errors.append(T["err_club_name_len"])
    if len(profile["contact_name"].strip()) > 80:
        errors.append(T["err_contact_len"])
    email = profile["contact_email"].strip()
    if not email:
        errors.append(T["err_contact_email"])
    elif not EMAIL_RE.fullmatch(email):
        errors.append(T["err_contact_email_fmt"])
    color = profile["brand_color"].strip() or "#c98d17"
    if not COLOR_RE.fullmatch(color):
        errors.append(T["err_color"])
    if len(profile["tagline"].strip()) > 200:
        errors.append(T["err_tagline"])
    return errors


def validate_settings(module_id: str, values: Mapping) -> list[str]:
    spec = _MODULE_BY_ID[module_id]
    errors = []
    cleaned = {field["key"]: _clip(values.get(field["key"]) or "") for field in spec["fields"]}
    for field in spec["fields"]:
        raw = cleaned[field["key"]]
        kind = field["kind"]
        if not raw:
            continue
        if kind == "email" and not EMAIL_RE.fullmatch(raw):
            errors.append(field["invalid"])
        elif kind == "https" and not HTTPS_RE.fullmatch(raw):
            errors.append(field["invalid"])
        elif kind == "serial" and not SERIAL_RE.fullmatch(raw):
            errors.append(field["invalid"])
        elif kind == "notion_id" and not NOTION_RE.fullmatch(raw):
            errors.append(field["invalid"])
        elif kind == "line_id" and (not LINE_ID_RE.fullmatch(raw) or any(ch.isspace() for ch in raw)):
            errors.append(field["invalid"])
        elif kind in ("short", "note") and len(raw) > (80 if kind == "short" else 200):
            errors.append(field["invalid"])
    if "group_id" in cleaned and cleaned["group_id"] and not cleaned.get("group_name"):
        errors.append(T["err_group_name"])
    if cleaned.get("group_name") and len(cleaned["group_name"]) > 80 and T["err_group_len"] not in errors:
        errors.append(T["err_group_len"])
    return errors


def _service_errors(step: str, bucket: dict) -> list[str]:
    if step == "google":
        errors = []
        if bucket["forms_account"] and not EMAIL_RE.fullmatch(bucket["forms_account"]):
            errors.append(T["err_personal_email"])
        if bucket["club_email"] and not EMAIL_RE.fullmatch(bucket["club_email"]):
            errors.append(T["err_club_inbox"])
        return errors
    if step == "line":
        return validate_settings("line", bucket)
    if step == "linktree":
        return validate_settings("linktree", {"url": bucket["url"]})
    if step == "instagram":
        return validate_settings("instagram", {"adb_serial": bucket["adb_serial"]})
    if step == "canva":
        from .canva_templates import design_id_from_text
        errors = validate_settings("canva", {"note": bucket["note"]})
        if bucket.get("use_sample") != "1" and bucket.get("ig_template") and not design_id_from_text(bucket["ig_template"]):
            errors.append(T["err_canva_url"])
        if bucket.get("cert_template") and not design_id_from_text(bucket["cert_template"]):
            errors.append(T["err_canva_url"])
        return errors
    return []


def _assign_service(progress: dict, step: str, form: Mapping) -> None:
    bucket = progress["services"][step]
    changed = False
    for key in list(bucket):
        if key == "last_check" or key not in form:
            continue
        limit = 800 if key in ("bot_picture", "ig_template", "cert_template") else 300
        value = _clip(form.get(key) or "", limit)
        if bucket.get(key) != value:
            bucket[key] = value
            changed = True
    if changed:
        bucket["last_check"] = None


def _save_step(progress: dict, step: str, form: Mapping) -> list[str]:
    if step == "profile":
        profile = progress["profile"]
        for key in profile:
            profile[key] = _clip(form.get(key) or "")
        if not profile["brand_color"]:
            profile["brand_color"] = "#c98d17"
        return _profile_errors(profile)
    if step in SERVICE_STEPS:
        _assign_service(progress, step, form)
        return _service_errors(step, progress["services"][step])
    return []


def _gate(progress: dict, step: str) -> list[str]:
    if step in SERVICE_STEPS:
        check = progress["services"][step].get("last_check") or {}
        if check.get("status") not in CHECK_OK:
            return [T["err_need_test"]]
    if step == "sample" and not progress["sample"]["imported"]:
        return [T["err_need_sample"]]
    if step == "dryrun" and not progress["dry_run"]["ok"]:
        return [T["err_need_dry"]]
    return []


def _advance(progress: dict) -> None:
    current = progress["current"]
    if current not in progress["completed"]:
        progress["completed"].append(current)
    index = STEP_IDS.index(current)
    if index + 1 < len(STEP_IDS):
        progress["current"] = STEP_IDS[index + 1]


def _finish_errors(progress: dict) -> list[str]:
    errors = _profile_errors(progress["profile"])
    for step in SERVICE_STEPS:
        errors.extend(_service_errors(step, progress["services"][step]))
        errors.extend(_gate(progress, step))
    if not progress["sample"]["imported"]:
        errors.append(T["err_need_sample"])
    if not progress["dry_run"]["ok"] or progress["dry_run"]["sent"]:
        errors.append(T["err_need_dry"])
    return errors


def _seed_modules(state: dict, progress: dict) -> dict:
    state = _merge_modules(state)
    copies = (
        ("google_forms", "forms_account", progress["services"]["google"]["forms_account"]),
        ("gmail", "club_email", progress["services"]["google"]["club_email"]),
        ("linktree", "url", progress["services"]["linktree"]["url"]),
        ("instagram", "adb_serial", progress["services"]["instagram"]["adb_serial"]),
        ("canva", "note", progress["services"]["canva"]["note"]),
    )
    for mid, key, value in copies:
        if value and not state[mid]["settings"].get(key):
            state[mid]["settings"][key] = value
    line = progress["services"]["line"]
    if line["group_id"] and not state["line"]["settings"].get("group_id"):
        state["line"]["settings"]["group_name"] = line["group_name"]
        state["line"]["settings"]["group_id"] = line["group_id"]
    share = {"google": "google_forms", "line": "line", "linktree": "linktree", "instagram": "instagram", "canva": "canva"}
    for sid, mid in share.items():
        check = progress["services"][sid].get("last_check")
        if check and not state[mid].get("last_test"):
            state[mid]["last_test"] = dict(check)
    google_check = progress["services"]["google"].get("last_check")
    if google_check and not state["gmail"].get("last_test"):
        state["gmail"]["last_test"] = dict(google_check)
    return state


GUIDE_ITEMS = {
    "google": {"signup"},
    "line": {"create_oa", "messaging_api", "issue_token", "group_settings"},
    "linktree": {"signup"},
    "instagram": {"usb"},
    "canva": {"signup", "no_template"},
}


def _saved_ig_template(progress: dict) -> str:
    from .canva_templates import design_id_from_text, ig_template_id
    bucket = progress["services"]["canva"]
    if bucket.get("use_sample") == "1" or "no_template" in (bucket.get("guide_done") or "").split(","):
        if bucket.get("use_sample") == "1":
            return ig_template_id(use_sample=True)
    return design_id_from_text(bucket.get("ig_template") or "")


def _saved_cert_template(progress: dict) -> str:
    from .canva_templates import design_id_from_text
    return design_id_from_text(progress["services"]["canva"].get("cert_template") or "")


def _guide_action(progress: dict, form: Mapping, action: str, step: str) -> tuple[dict, list[str]]:
    bucket = progress["services"][step]
    if action == "skip":
        bucket["has_account"] = "skip"
        bucket["last_check"] = {"status": "stub", "message": T["check_skipped"]}
        _advance(progress)
        return progress, []
    if action == "account_choice":
        choice = _clip(form.get("choice") or "", 16)
        if choice not in ("yes", "no", "android"):
            return progress, [T["err_unknown"]]
        bucket["has_account"] = choice
        return progress, []
    item = _clip(form.get("item") or "", 40)
    if item not in GUIDE_ITEMS.get(step, ()):
        return progress, [T["err_unknown"]]
    done = [part for part in (bucket.get("guide_done") or "").split(",") if part]
    if item not in done:
        done.append(item)
    bucket["guide_done"] = ",".join(done)
    if step == "canva" and item == "no_template":
        bucket["use_sample"] = "1"
    return progress, []


def handle_action(progress: dict, form: Mapping, store: Store) -> tuple[dict, list[str]]:
    action = _clip(form.get("action") or "", 40)
    posted = _clip(form.get("step") or "", 40)
    if action in {"skip", "account_choice", "guide_done"}:
        if posted != progress["current"] or posted not in SERVICE_STEPS:
            return progress, [T["err_stay"]]
        return _guide_action(progress, form, action, posted)
    if action == "reset":
        return fresh_progress(), []
    if action == "back":
        index = STEP_IDS.index(progress["current"])
        progress["current"] = STEP_IDS[max(0, index - 1)]
        return progress, []
    if action == "goto":
        if posted in STEP_IDS and (posted in progress["completed"] or posted == progress["current"]):
            progress["current"] = posted
            return progress, []
        return progress, [T["err_skip"]]
    if action not in {"next", "test", "import_sample", "dry_run", "finish"}:
        return progress, [T["err_unknown"]]
    if posted != progress["current"]:
        return progress, [T["err_stay"]]
    step = progress["current"]
    if action == "next":
        if step == "done":
            return progress, [T["err_stay"]]
        errors = _save_step(progress, step, form)
        if errors:
            return progress, errors
        gated = _gate(progress, step)
        if gated:
            return progress, gated
        _advance(progress)
        return progress, []
    if action == "test":
        if step not in SERVICE_STEPS:
            return progress, [T["err_stay"]]
        errors = _save_step(progress, step, form)
        if errors:
            return progress, errors
        progress["services"][step]["last_check"] = check_service(step, progress["services"][step])
        return progress, []
    if action == "import_sample":
        if step != "sample":
            return progress, [T["err_stay"]]
        try:
            progress["sample"] = store.import_sample()
        except FileNotFoundError:
            return progress, [T["err_no_sample_file"]]
        return progress, []
    if action == "dry_run":
        if step != "dryrun":
            return progress, [T["err_stay"]]
        if not progress["sample"]["imported"]:
            return progress, [T["err_need_sample"]]
        progress["dry_run"] = store.dry_run()
        if not progress["dry_run"]["ok"]:
            return progress, [T["dry_failed"]]
        return progress, []
    if action == "finish":
        if step != "done":
            return progress, [T["err_stay"]]
        errors = _finish_errors(progress)
        if errors:
            return progress, errors
        modules = _seed_modules(store.load_modules(), progress)
        store.save_modules(modules)
        store.write_config(progress, modules)
        progress["finished"] = True
        progress["config_written"] = True
        progress["dry_run"]["sent"] = False
        return progress, []
    return progress, [T["err_unknown"]]


def handle_module(state: dict, module_id: str, form: Mapping) -> tuple[dict, dict]:
    state = _merge_modules(state)
    mod = state[module_id]
    action = _clip(form.get("action") or "", 40)
    if action == "toggle":
        mod["enabled"] = _clip(form.get("enabled") or "", 2) == "1"
        message = T["turned_on"] if mod["enabled"] else T["turned_off"]
        return state, {"module_id": module_id, "level": "ok", "message": message, "errors": []}
    if action in {"save", "test"}:
        spec = _MODULE_BY_ID[module_id]
        posted = {field["key"]: _clip(form.get(field["key"]) or "") for field in spec["fields"]}
        errors = validate_settings(module_id, posted)
        if errors:
            return state, {"module_id": module_id, "level": "error", "message": "", "errors": errors, "posted": posted}
        mod["settings"] = posted
        if action == "test":
            mod["last_test"] = check_service(module_id, posted)
            return state, {
                "module_id": module_id,
                "level": mod["last_test"]["status"],
                "message": mod["last_test"]["message"],
                "errors": [],
            }
        return state, {"module_id": module_id, "level": "ok", "message": T["saved"], "errors": []}
    return state, {"module_id": module_id, "level": "error", "message": T["err_unknown"], "errors": [T["err_unknown"]]}


def badge(status: str | None) -> tuple[str, str]:
    if status == "ok":
        return "p-ok", T["badge_ok"]
    if status == "stub":
        return "p-warn", T["badge_stub"]
    if status == "error":
        return "p-err", T["badge_error"]
    return "p-idle", T["badge_none"]


def _safe_color(value: str) -> str:
    value = (value or "").strip()
    return value if COLOR_RE.fullmatch(value) else "#c98d17"


def wizard_view(progress: dict, errors: list[str] | None = None) -> dict:
    progress = _merge_progress(progress)
    current = progress["current"]
    index = STEP_IDS.index(current)
    completed = set(progress["completed"])
    steps = []
    for number, (sid, title_key) in enumerate(STEPS, start=1):
        if sid == current:
            state = "on"
        elif sid in completed:
            state = "done"
        else:
            state = "todo"
        steps.append({
            "id": sid,
            "title": T[title_key],
            "state": state,
            "reachable": sid == current or sid in completed,
            "number": number,
        })
    profile = progress["profile"]
    fields: list[dict] = []
    if current == "profile":
        fields = [
            _field("club_name", T["club_name"], profile["club_name"]),
            _field("contact_name", T["contact_name"], profile["contact_name"]),
            _field("contact_email", T["contact_email"], profile["contact_email"], "email"),
            _field("brand_color", T["brand_color"], profile["brand_color"] or "#c98d17"),
            _field("tagline", T["tagline"], profile["tagline"]),
        ]
    elif current == "google":
        bucket = progress["services"]["google"]
        fields = [
            _field("forms_account", T["personal_email"], bucket["forms_account"], "email"),
            _field("club_email", T["club_inbox"], bucket["club_email"], "email"),
        ]
    elif current == "line":
        bucket = progress["services"]["line"]
        fields = [
            _field("group_name", T["group_name"], bucket["group_name"]),
            _field("group_id", T["line_manual_label"], bucket["group_id"]),
        ]
    elif current == "linktree":
        fields = [_field("url", T["linktree_url"], progress["services"]["linktree"]["url"], "url")]
    elif current == "instagram":
        fields = [_field("adb_serial", T["ig_serial_label"], progress["services"]["instagram"]["adb_serial"])]
    elif current == "canva":
        bucket = progress["services"]["canva"]
        fields = [
            _field("ig_template", T["canva_ig_label"], bucket.get("ig_template") or ""),
            _field("cert_template", T["canva_cert_label"], bucket.get("cert_template") or ""),
            _field("note", T["canva_note_optional"], bucket["note"]),
        ]
    notice = None
    if current in SERVICE_STEPS:
        check = progress["services"][current].get("last_check")
        if check:
            notice = {"level": check["status"], "text": check["message"]}
    elif current == "sample" and progress["sample"]["imported"]:
        notice = {"level": "ok", "text": T["sample_ok"]}
    elif current == "dryrun" and progress["dry_run"]["ran"]:
        notice = {"level": "ok" if progress["dry_run"]["ok"] else "error", "text": progress["dry_run"]["message"] or T["dry_failed"]}
    elif current == "done" and progress["config_written"]:
        notice = {"level": "ok", "text": T["config_saved"]}
    review = []
    for sid in SERVICE_STEPS:
        check = progress["services"][sid].get("last_check") or {}
        css, label = badge(check.get("status"))
        review.append({"name": T[dict(STEPS)[sid]], "cls": css, "badge": label})
    slug = progress["sample"]["slug"] if SLUG_RE.fullmatch(progress["sample"]["slug"] or "") else ""
    return {
        "title": T["wizard_title"],
        "steps": steps,
        "step": {
            "id": current,
            "title": T[dict(STEPS)[current]],
            "lead": T[LEADS[current]],
            "number": index + 1,
            "total": len(STEPS),
            "fields": fields,
            "show_back": index > 0,
            "show_next": current not in {"done"},
            "show_test": current in SERVICE_STEPS,
            "show_skip": current in SERVICE_STEPS,
            "show_import": current == "sample",
            "show_dry": current == "dryrun",
            "show_finish": current == "done",
            "swatch": _safe_color(profile["brand_color"]) if current in {"profile", "done"} else "",
            "color_help": current == "profile",
        },
        "errors": list(errors or []),
        "notice": notice,
        "profile": profile,
        "sample": {"imported": progress["sample"]["imported"], "slug": slug},
        "dry": progress["dry_run"],
        "review": review,
        "config_path": "",
        "show_config_path": False,
        "guide": _guide_for(progress, current),
        "sample_card": sample_preview() if current == "sample" else {"club": "", "title": "", "speaker": "", "date": "", "place": ""},
    }


def _guide_for(progress: dict, step: str) -> dict:
    if step not in SERVICE_STEPS:
        return {"choice": "", "done": [], "token_saved": False, "bot_name": "", "bot_picture": "", "token_mask": "", "use_sample": False}
    bucket = progress["services"][step]
    picture = bucket.get("bot_picture") or ""
    if not str(picture).startswith("https://"):
        picture = ""
    return {
        "choice": bucket.get("has_account") or "",
        "done": [part for part in (bucket.get("guide_done") or "").split(",") if part],
        "token_saved": bucket.get("token_saved") == "yes",
        "bot_name": bucket.get("bot_name") or "",
        "bot_picture": picture,
        "token_mask": bucket.get("token_mask") or "",
        "use_sample": bucket.get("use_sample") == "1",
    }


def attach_config_path(view: dict, store: Store, progress: dict) -> dict:
    if progress.get("config_written"):
        view["config_path"] = str(store.config_path)
        view["show_config_path"] = True
    return view


def modules_view(state: dict, focus: str = "", notice: str = "", banner: dict | None = None) -> dict:
    state = _merge_modules(state)
    cards = []
    for spec in MODULE_DEFS:
        mod = state[spec["id"]]
        settings = dict(mod["settings"])
        if banner and banner.get("module_id") == spec["id"] and banner.get("posted"):
            settings.update(banner["posted"])
        css, label = badge((mod.get("last_test") or {}).get("status"))
        cards.append({
            "id": spec["id"],
            "name": spec["name"],
            "summary": spec["summary"],
            "enabled": bool(mod["enabled"]),
            "fields": [
                {"key": field["key"], "label": field["label"], "value": settings.get(field["key"], ""),
                 "kind": "email" if field["kind"] == "email" else "text"}
                for field in spec["fields"]
            ],
            "badge_cls": css,
            "badge": label,
        })
    shown = None
    if banner and banner.get("module_id") in MODULE_IDS:
        shown = {"module_id": banner["module_id"], "level": banner.get("level") or "error",
                 "message": banner.get("message") or "", "errors": list(banner.get("errors") or [])}
    elif notice == "tested" and focus in MODULE_IDS:
        check = state[focus].get("last_test")
        if check:
            shown = {"module_id": focus, "level": check["status"], "message": check["message"], "errors": []}
    elif notice in NOTICES and focus in MODULE_IDS and notice != "tested":
        shown = {"module_id": focus, "level": "ok", "message": T[NOTICES[notice]], "errors": []}
    return {"title": T["modules_title"], "cards": cards, "banner": shown, "focus": focus if focus in MODULE_IDS else ""}


def _token_candidates() -> list[Path]:
    found: list[Path] = []
    env = os.environ.get("CLUB_FORMS_TOKEN")
    if env:
        found.append(Path(env))
    folders = []
    if os.environ.get("CLUB_OAUTH_DIR"):
        folders.append(Path(os.environ["CLUB_OAUTH_DIR"]))
    folders.append(Path.home() / "創創社-私密" / "google-oauth")
    try:
        folders.append(private_dir() / "google-oauth")
    except UnsafeConfigPath:
        pass
    seen: set[Path] = set()
    for folder in folders:
        try:
            resolved = folder.resolve()
        except OSError:
            continue
        if resolved in seen or not folder.is_dir():
            continue
        seen.add(resolved)
        found.extend(sorted(folder.glob("token*.json")))
    return found


def _read_token_file(path: Path) -> str:
    """Return ok, invalid, or repo. File contents are never returned."""
    if not path.is_file():
        return "missing"
    inside = is_inside_repo(path)
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError("large")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            raise ValueError("shape")
    except Exception:
        return "repo" if inside else "invalid"
    return "repo" if inside else "ok"


def _google_status() -> dict:
    saw_invalid = False
    saw_repo = False
    saw_any = False
    for path in _token_candidates():
        kind = _read_token_file(path)
        if kind == "missing":
            continue
        saw_any = True
        if kind == "ok":
            return {"status": "ok", "message": T["check_file"]}
        if kind == "invalid":
            saw_invalid = True
        elif kind == "repo":
            saw_repo = True
    if saw_invalid:
        return {"status": "error", "message": T["check_bad_file"]}
    if saw_repo:
        return {"status": "stub", "message": T["check_repo"]}
    if not saw_any:
        return {"status": "stub", "message": T["check_stub"]}
    return {"status": "stub", "message": T["check_stub"]}


def _adb_table(stdout: str) -> dict[str, str]:
    found = {}
    for line in (stdout or "").splitlines():
        if "\t" not in line:
            continue
        serial, status = line.split("\t", 1)
        serial = serial.strip()
        if serial:
            found[serial] = status.strip()
    return found


def check_service(step: str, settings: Mapping) -> dict:
    if step in {"google", "google_forms", "gmail"}:
        return _google_status()
    if step == "line":
        from . import line_push
        status = line_push.public_status()
        if status.get("configured"):
            return {"status": "ok", "message": T["check_line_ok"]}
        return {"status": "stub", "message": T["check_stub"]}
    if step == "linktree":
        url = _clip(settings.get("url") or "")
        if not url:
            return {"status": "stub", "message": T["check_url_empty"]}
        if HTTPS_RE.fullmatch(url):
            return {"status": "ok", "message": T["check_url_ok"]}
        return {"status": "error", "message": T["err_linktree_url"]}
    if step == "instagram":
        return _check_phone(_clip(settings.get("adb_serial") or ""))
    if step == "canva":
        try:
            from . import canva_job  # noqa: F401
        except Exception:
            return {"status": "error", "message": T["check_module_fail"]}
        return {"status": "stub", "message": T["check_canva"]}
    if step == "notion":
        if (os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY") or "").strip():
            return {"status": "ok", "message": T["check_notion_ok"]}
        return {"status": "stub", "message": T["check_notion_stub"]}
    module = {"banner": "autopilot.form_banner", "certificate": "autopilot.certificate", "qr": "autopilot.canva_step"}.get(step)
    if module:
        try:
            __import__(module)
        except Exception:
            return {"status": "error", "message": T["check_module_fail"]}
        return {"status": "stub", "message": T["check_module_ok"]}
    return {"status": "error", "message": T["check_module_fail"]}


def _check_phone(serial: str) -> dict:
    adb = shutil.which("adb")
    if not adb:
        # No phone tools on this machine: simulated pass, even if a serial was typed.
        return {"status": "stub", "message": T["check_phone_stub"]}
    try:
        proc = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=3)
        table = _adb_table(proc.stdout or "")
    except Exception:
        table = {}
    if serial:
        if table.get(serial) == "device":
            return {"status": "ok", "message": T["check_phone_ok"]}
        return {"status": "error", "message": T["check_phone_miss"]}
    if any(status == "device" for status in table.values()):
        return {"status": "ok", "message": T["check_phone_ok"]}
    return {"status": "stub", "message": T["check_phone_stub"]}
