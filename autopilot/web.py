"""Feature 10: one Flask page — import from Notion, inspect every output, run each step."""
from __future__ import annotations

import importlib
import json
import os
import threading
import traceback
from datetime import date, datetime
from html import escape as esc
from pathlib import Path
from typing import Any

import yaml
from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory, url_for

from . import checker, ig_music, launchpad
from .event import Event, build_event, load_event
from .pipeline import ROOT, run_pipeline

from .local_settings import forms_account as _forms_account, forms_account_local

FORMS_ACCOUNT = _forms_account()
WEB_TEMPLATES = Path(__file__).resolve().parent / "templates_web"
FIELDS = [
    ("session_no", "堂次（數字）"),
    ("title", "社課全名"),
    ("speaker.name", "講者"),
    ("speaker.title", "講者職稱"),
    ("date", "日期 YYYY-MM-DD"),
    ("start", "開始"),
    ("end", "結束"),
    ("room", "教室"),
    ("deadline", "報名截止 YYYY-MM-DD HH:MM"),
    ("hook", "鉤子問句"),
]
# Every pipeline action, in the order 「全部執行」 runs them: (key, label, confirm text for 重跑).
ACTION_META = [
    ("import", "匯入活動資料（Notion／活動檔）", "重新從 Notion（讀不到時用本機活動檔）讀這堂社課。確定？"),
    ("preview", "產生文案＋規則檢查", "重新產生預告信、提醒信、課後信、LINE／IG 文案並跑規則檢查。確定？"),
    ("canva", "Canva 圖卡＋感謝狀（複製模板→填入→匯出）", "會把講者照片上傳到 Canva，複製 IG 模板與感謝狀模板成新的複本、填入本期資料並匯出 PNG／PDF。原模板不會被修改。確定？"),
    ("forms", "建立課前／課後表單（個人帳號）", "會用個人帳號建立課前／課後表單（不會寄信）。已建過就沿用，不會重複建立。確定？"),
    ("banner", "表單黑底 Banner＋確認訊息（背景）", "會在背景（不開視窗）用個人帳號把黑底 Banner 和確認訊息補到兩份表單上。確定？"),
    ("qr", "更新 Canva QR（ig_04）", "會依目前的課前表單網址更新 QR 並更新 ig_04 圖卡。確定？"),
    ("ig", "準備 IG 圖卡（不發文）", "會檢查 ig_01～ig_04 四張圖卡都在。不會發 IG。確定？"),
    ("cert", "講者感謝狀", "已有正式感謝狀就沿用不覆蓋；沒有才產生。確定？"),
    ("drafts", "Gmail 草稿 3 封（社團信箱，不寄出）", "會在社團信箱建立預告信、當天提醒信、課後信草稿，絕對不會寄出。已存在的草稿不會重複建立。確定？"),
    ("linktree", "同步 Linktree（背景）", "會在背景（不開視窗）新增本期課前表單連結（已存在就略過）、隱藏上一期（不刪除）。確定？"),
    ("stats", "更新報名／回饋人數", "會讀課前／課後表單回覆，只更新人數（不列出 Email）。確定？"),
]
ACTION_KEYS = [k for k, *_ in ACTION_META]
# A step only runs when every step it depends on is done; otherwise 全部執行 marks it 已略過.
STEP_DEPS = {
    "import": [],
    "preview": ["import"],
    "forms": ["import"],
    "banner": ["forms"],
    "canva": ["import"],
    "qr": ["forms", "canva"],
    "ig": ["qr"],
    "cert": ["canva"],
    "drafts": ["forms", "preview"],
    "linktree": ["forms"],
    "stats": ["forms"],
}
# Steps that touch live services: once done, 全部執行 leaves them alone (no duplicate forms/drafts/links).
SKIP_IF_DONE = {"canva", "forms", "banner", "qr", "cert", "linktree"}
# drafts is NOT skipped: it updates the same 3 drafts in place (new registrants -> BCC), never duplicates.
LINE_OUTPUT = "3_Line訊息.txt"
# IDs of already-created forms/drafts per event, used only to fill missing keys in state.json.
# Empty: the 11/25 event was reset (2026-11-15) and is re-run from scratch, so nothing old is re-seeded.
KNOWN_STATE: dict = {}
DRAFT_PLAN = (
    ("2_預告信.html", "預告信", "members"),
    ("4_提醒信.html", "當天提醒信", "registrants"),
    ("6_課後信.html", "課後信", "registrants"),
)

_JOBS_LOCK = threading.RLock()  # re-entrant: start_action calls set_action while holding it
_JOBS: dict[tuple[str, str, str], threading.Thread] = {}
_RUNALL: dict[tuple[str, str], threading.Thread] = {}


def _get(d, dotted):
    for k in dotted.split("."):
        d = (d or {}).get(k)
    return d


def _set(d, dotted, v):
    ks = dotted.split(".")
    for k in ks[:-1]:
        d = d.setdefault(k, {})
    d[ks[-1]] = int(v) if ks[-1] == "session_no" and str(v).isdigit() else v


def default_forms_token() -> Path:
    env = os.environ.get("CLUB_FORMS_TOKEN")
    if env:
        return Path(env)
    return Path.home() / "創創社-私密" / "google-oauth" / f"token-{forms_account_local()}.json"


def default_members_bcc_file() -> Path | None:
    env = os.environ.get("CLUB_MEMBERS_BCC_FILE")
    if env:
        p = Path(env)
        return p if p.is_file() else None
    folder = Path.home() / "創創社-私密" / "密件副本"
    if not folder.is_dir():
        return None
    # Prefer the newest 預告信 list (used for the announcement draft).
    cands = sorted(folder.glob("*預告信*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _module_available(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False



LINKTREE_SHOTS = {
    "linktree_01_before.png": "Linktree 同步前截圖",
    "linktree_02_after.png": "Linktree 同步後截圖",
}

BANNER_CHECK_SHOTS = {
    "signup": "form_banner_check_signup.png",
    "feedback": "form_banner_check_feedback.png",
}


def _norm_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


def _file_date(path: Path | None) -> str:
    if not path or not Path(path).is_file():
        return ""
    return datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%Y-%m-%d")


def banner_result_paths(out_root: Path, slug: str) -> list[Path]:
    out_root = Path(out_root)
    return [
        out_root / slug / "form_banner_shots" / "result.json",
        out_root / "form_banner_shots" / "result.json",
    ]


def load_banner_result(out_root: Path, slug: str) -> tuple[dict | None, Path | None]:
    for path in banner_result_paths(out_root, slug):
        data = _read_json(path, None)
        if isinstance(data, dict) and (data.get("forms") or data.get("ok") is not None):
            return data, path
    return None, None


def banner_covers_event(data: dict, state: dict) -> bool:
    forms = [f for f in (data.get("forms") or []) if isinstance(f, dict)]
    if not forms:
        return bool(data.get("ok"))
    wanted = {
        (state.get("signup") or {}).get("formId"),
        (state.get("feedback") or {}).get("formId"),
    } - {None, ""}
    got = {f.get("form_id") for f in forms} - {None, ""}
    if wanted and got and not (wanted & got):
        return False
    return all(f.get("ok") for f in forms) if "ok" not in data else bool(data.get("ok"))


def find_ig04(root: Path, out_root: Path, slug: str, ev: Event | None = None, run_dir: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if ev is not None:
        exported = (ev.raw.get("canva") or {}).get("exported_dir")
        if exported:
            candidates.append(Path(root) / exported / "ig_04.png")
        day = str(ev.raw.get("date") or "")
        if day:
            candidates.append(Path(root) / "assets" / "canva_export" / day / "ig_04.png")
    if slug[:10].count("-") == 2:
        candidates.append(Path(root) / "assets" / "canva_export" / slug[:10] / "ig_04.png")
    candidates.append(Path(out_root) / slug / "qr" / "ig_04.png")
    if run_dir:
        candidates.append(Path(run_dir) / "ig" / "ig_04.png")
    seen: set[Path] = set()
    for raw in candidates:
        try:
            path = raw.resolve()
        except OSError:
            path = raw
        if path in seen or not raw.is_file():
            continue
        seen.add(path)
        return raw
    return None


def display_shots(kind: str, rec: dict) -> list[str]:
    """Screenshots the dashboard shows for an IG result.

    Post (kind="post"): once published, one screenshot per carousel page of the live post
    (post_p1..post_pN) when they exist. Story (kind="story"): after a successful run only the
    editor screenshot just before publishing (r08_final). Failed runs keep every screenshot.
    """
    shots = list(rec.get("shots") or [])
    if kind == "post" and rec.get("published"):
        pages = sorted((n for n in shots if n.startswith("post_p")), key=lambda n: int("".join(c for c in n if c.isdigit()) or 0))
        return pages or shots
    if kind == "story" and rec.get("status") in ("done", "warn") and "r08_final.png" in shots:
        return ["r08_final.png"]
    return shots


def ig_card_paths(root: Path, ev: Event, run_dir: Path | None) -> list[Path]:
    """ig_01..ig_04: prefer the Canva export folder (final > clean > plain), then the run's ig/ folder."""
    date = str(ev.raw.get("date") or "")
    export = Path(root) / "assets" / "canva_export" / date
    cards: list[Path] = []
    for i in range(1, 5):
        chosen = None
        for folder in (export, (Path(run_dir) / "ig") if run_dir else None):
            if not folder or not Path(folder).is_dir():
                continue
            for name in (f"ig_{i:02d}_final.png", f"ig_{i:02d}_clean.png", f"ig_{i:02d}.png"):
                p = Path(folder) / name
                if p.is_file():
                    chosen = p
                    break
            if chosen:
                break
        if chosen:
            cards.append(chosen)
    return cards


def _cert_links(slug: str, certs: dict) -> list[dict]:
    links = []
    if certs.get("pdf"):
        links.append({"label": "下載感謝狀 PDF", "href": f"/cert/{slug}/pdf?download=1"})
    if certs.get("png"):
        links.append({"label": "下載感謝狀 PNG", "href": f"/cert/{slug}/png?download=1"})
    return links


def _canva_links(job: dict) -> list[dict]:
    links = []
    if job.get("ig_design_id"):
        links.append({"label": f"IG 圖卡複本 {job['ig_design_id']}", "href": f"https://www.canva.com/design/{job['ig_design_id']}/edit"})
    if job.get("cert_design_id"):
        links.append({"label": f"感謝狀複本 {job['cert_design_id']}", "href": f"https://www.canva.com/design/{job['cert_design_id']}/edit"})
    return links


def official_cert_paths(out_root: Path, slug: str, run_dir: Path | None = None) -> dict[str, Path | None]:
    """Prefer the official typography-fixed files at the event root over an older run preview."""
    base = Path(out_root) / slug
    pdf = png = None
    for name in ("感謝狀_new.pdf", "感謝狀.pdf"):
        p = base / name
        if p.is_file():
            pdf = p
            break
    for name in ("感謝狀_new.png", "感謝狀.png"):
        p = base / name
        if p.is_file():
            png = p
            break
    if pdf is None and run_dir and (Path(run_dir) / "感謝狀.pdf").is_file():
        pdf = Path(run_dir) / "感謝狀.pdf"
    if png is None and run_dir and (Path(run_dir) / "感謝狀.png").is_file():
        png = Path(run_dir) / "感謝狀.png"
    return {"pdf": pdf, "png": png}


def members_bcc_count() -> int:
    from . import bcc_exclude
    from .pipeline import _read_bcc_file
    return len(bcc_exclude.apply(_read_bcc_file(str(default_members_bcc_file() or "") or None))[0])


def create_app(root: Path | None = None, out_root: Path | None = None, events_dir: Path | None = None) -> Flask:
    root = Path(root or ROOT)
    out_root = Path(out_root or root / "out")
    events_dir = Path(events_dir or root / "events")
    web_edits = out_root / "_web"
    app = Flask(__name__, template_folder=str(WEB_TEMPLATES))
    app.config["ROOT"] = root
    app.config["OUT"] = out_root
    app.config["EVENTS"] = events_dir
    app.config["WEB_EDITS"] = web_edits

    def state_path(slug: str) -> Path:
        return out_root / slug / "state.json"

    def empty_actions() -> dict:
        return {k: {"status": "idle", "message": "", "links": []} for k, *_ in ACTION_META}

    def load_state(slug: str) -> dict:
        # Whole read-derive-write under the lock so a status poll can't overwrite a step's result.
        with _JOBS_LOCK:
            return _load_state(slug)

    def _load_state(slug: str) -> dict:
        path = state_path(slug)
        state = _read_json(path, {})
        if not isinstance(state, dict):
            state = {}
        known = KNOWN_STATE.get(slug) or {}
        changed = False
        for key in ("signup", "feedback"):
            if not state.get(key) and known.get(key):
                state[key] = dict(known[key])
                changed = True
        if not state.get("drafts") and known.get("drafts"):
            state["drafts"] = [dict(x) for x in known["drafts"]]
            changed = True
        actions = state.setdefault("actions", empty_actions())
        for key, *_ in ACTION_META:
            actions.setdefault(key, {"status": "idle", "message": "", "links": []})
        # Mark completed work from stored IDs, without overwriting a live run.
        if state.get("signup") and actions["forms"]["status"] in ("idle", ""):
            actions["forms"] = {
                "status": "done",
                "message": "表單已建立（個人帳號）",
                "links": _form_links(state),
            }
            changed = True
        if state.get("drafts") and any(d.get("draft_id") for d in state["drafts"]) and actions["drafts"]["status"] in ("idle", ""):
            actions["drafts"] = {
                "status": "done",
                "message": _draft_message(state),
                "links": _draft_links(state),
            }
            changed = True
        if not _is_busy(slug):
            for key in ACTION_KEYS:
                if actions[key].get("status") == "running":
                    actions[key] = {**actions[key], "status": "failed", "message": "上次執行被中斷（儀表板重新啟動），請按重跑。"}
                    changed = True
            ra = state.get("runall")
            if isinstance(ra, dict) and ra.get("status") == "running":
                state["runall"] = {**ra, "status": "failed", "current": "", "message": "上次全部執行被中斷。"}
                changed = True
        if not _module_available("autopilot.form_banner") and actions["banner"]["status"] == "idle":
            actions["banner"] = {"status": "coming", "message": "Banner 模組撰寫中", "links": []}
        state.setdefault("stats", {"registrations": 0, "bcc_count": 0, "feedback_n": None, "feedback_avg": None, "note": ""})
        if apply_derived_status(slug, state):
            changed = True
        if apply_local_stats(state):
            changed = True
        if changed:
            _write_json(path, state)
        return state

    def save_state(slug: str, state: dict) -> None:
        with _JOBS_LOCK:
            _write_json(state_path(slug), state)

    def apply_local_stats(state: dict) -> bool:
        """Fill BCC count from the club preview-list file (count only, no addresses)."""
        stats = state.setdefault(
            "stats",
            {"registrations": 0, "bcc_count": 0, "feedback_n": None, "feedback_avg": None, "note": ""},
        )
        try:
            n = members_bcc_count()
        except Exception:
            n = 0
        changed = False
        if n and stats.get("bcc_count") != n:
            stats["bcc_count"] = n
            changed = True
        note = "人數只顯示合計，不會列出 Email。"
        if n:
            note = f"密件副本 {n} 人來自社員預告信名單（只計數量）。報名人數來自課前表單回覆。人數只顯示合計，不會列出 Email。"
        if stats.get("note") != note and n:
            stats["note"] = note
            changed = True
        return changed

    def _banner_shot_links(slug: str, data: dict | None, result_path: Path | None) -> list[dict]:
        links = []
        shots_dir = None
        if result_path:
            shots_dir = result_path.parent
        elif data and data.get("shots"):
            shots_dir = Path(data["shots"])
        labels = {"signup": "課前表單截圖", "feedback": "課後表單截圖"}
        for kind, name in BANNER_CHECK_SHOTS.items():
            found = None
            for folder in (shots_dir, out_root / slug / "form_banner_shots", out_root / "form_banner_shots"):
                if folder and (Path(folder) / name).is_file():
                    found = Path(folder) / name
                    break
            if not found:
                continue
            links.append({
                "label": labels[kind],
                "href": f"/banner-shot/{slug}/{name}",
            })
        return links

    def apply_derived_status(slug: str, state: dict, ev: Event | None = None) -> bool:
        """Mark completed steps from real artifacts so the dashboard stays accurate."""
        actions = state.setdefault("actions", empty_actions())
        changed = False

        data, result_path = load_banner_result(out_root, slug)
        recorded = state.get("banner") if isinstance(state.get("banner"), dict) else None
        banner_ok = False
        when = ""
        source = data
        if data and banner_covers_event(data, state):
            banner_ok = True
            when = _file_date(result_path)
        elif recorded and recorded.get("ok") and actions["banner"]["status"] != "failed":
            banner_ok = True
            source = recorded
            when = recorded.get("applied_at") or ""
        if banner_ok and actions["banner"]["status"] not in ("running",):
            msg = "兩份表單已補上黑底 Banner 與確認訊息"
            if when:
                msg += f"（{when}）"
            msg += "。"
            links = _form_links(state) + _banner_shot_links(slug, source, result_path)
            cur = actions["banner"]
            if cur.get("status") != "done" or cur.get("message") != msg or cur.get("links") != links:
                actions["banner"] = {"status": "done", "message": msg, "links": links}
                changed = True

        signup_url = (state.get("signup") or {}).get("responder_url") or ""
        if ev is None:
            try:
                ev = load_event(event_path(slug), {"form_url": signup_url, "form_short_url": signup_url} if signup_url else None)
            except Exception:
                ev = None
        run_dir = latest_run_dir(slug)
        card = find_ig04(root, out_root, slug, ev, run_dir)
        decoded = None
        if card is not None:
            try:
                from .canva_step import _decode_qr_png
                decoded = _decode_qr_png(card)
            except Exception:
                decoded = None
        recorded_qr = state.get("qr") if isinstance(state.get("qr"), dict) else None
        qr_ok = bool(signup_url and decoded and _norm_url(decoded) == _norm_url(signup_url))
        if not qr_ok and recorded_qr and recorded_qr.get("ok") and signup_url:
            rec_url = recorded_qr.get("url") or recorded_qr.get("decoded")
            qr_ok = _norm_url(rec_url) == _norm_url(signup_url)
            if qr_ok and not decoded:
                decoded = rec_url
        if qr_ok and actions["qr"]["status"] not in ("running",):
            links = []
            if signup_url:
                links.append({"label": "課前表單", "href": signup_url})
            design_id = ""
            if ev is not None:
                design_id = (state.get("canva") or {}).get("ig_design_id") or (ev.raw.get("canva") or {}).get("design_id") or ""
            if design_id:
                links.append({"label": f"Canva 工作複本 {design_id}", "href": f"https://www.canva.com/design/{design_id}/edit"})
            msg = "已依課前表單網址更新 QR，掃描 ig_04.png 與目前表單一致。"
            if card:
                msg += f" 圖卡：{card.name}"
            cur = actions["qr"]
            if cur.get("status") != "done" or cur.get("message") != msg or cur.get("links") != links:
                actions["qr"] = {"status": "done", "message": msg, "links": links}
                changed = True

        def _mark(key: str, status: str, msg: str, links: list | None = None) -> None:
            nonlocal changed
            cur = actions[key]
            links = links or []
            if cur.get("status") == "running":
                return
            if cur.get("status") != status or cur.get("message") != msg or cur.get("links") != links:
                actions[key] = {"status": status, "message": msg, "links": links}
                changed = True

        if run_dir is not None and (run_dir / "manifest.json").is_file():
            m = _read_json(run_dir / "manifest.json", {})
            chk = m.get("check") or {}
            if "error" in chk:
                if chk.get("error"):
                    _mark("preview", "failed", f"規則檢查有 {chk['error']} 項違規（見下方「規則檢查」）。預覽 {run_dir.name}")
                elif actions["preview"].get("status") in ("idle", "", "done"):
                    _mark("preview", "done", f"文案已產生，規則檢查全部通過（提醒 {chk.get('warn', 0)} 項）。預覽 {run_dir.name}")
        certs = official_cert_paths(out_root, slug, None)
        if certs["pdf"] and actions["cert"].get("status") in ("idle", "", "done", "failed"):
            _mark("cert", "done", f"感謝狀已就緒：{certs['pdf'].name}", _cert_links(slug, certs))
        # import / ig are only marked done by their own step (a blank event starts with every step 未執行).

        lt = load_linktree_result(slug)
        if lt and lt.get("ok") and actions["linktree"]["status"] not in ("running",):
            added = (lt.get("added") or {}).get("title") or ""
            hidden = "、".join(lt.get("hidden") or []) or "無"
            msg = f"已新增「{added}」；舊課前表單目前皆為隱藏（未刪除）：{hidden}"
            if lt.get("at"):
                msg += f"（{lt['at']}）"
            msg += "。"
            page = ""
            if ev is not None:
                page = str((ev["club"] or {}).get("linktree_url") or "")
            links = _linktree_links(slug, page)
            cur = actions["linktree"]
            if cur.get("status") != "done" or cur.get("message") != msg or cur.get("links") != links:
                actions["linktree"] = {"status": "done", "message": msg, "links": links}
                changed = True

        return changed

    def _linktree_dirs(slug: str) -> list[Path]:
        dirs = [out_root / slug / "linktree_shots"]
        run_dir = latest_run_dir(slug)
        if run_dir is not None:
            dirs.append(run_dir / "linktree_shots")
        return dirs

    def load_linktree_result(slug: str) -> dict | None:
        found = [(d / "linktree_result.json") for d in _linktree_dirs(slug) if (d / "linktree_result.json").is_file()]
        if not found:
            return None
        data = _read_json(max(found, key=lambda f: f.stat().st_mtime), {})
        return data if isinstance(data, dict) else None

    def _linktree_links(slug: str, page_url: str = "") -> list[dict]:
        links = []
        if str(page_url or "").startswith("https://"):
            links.append({"label": "Linktree", "href": page_url})
        for name, label in LINKTREE_SHOTS.items():
            if any((d / name).is_file() for d in _linktree_dirs(slug)):
                links.append({"label": label, "href": f"/linktree-shot/{slug}/{name}"})
        return links

    def event_path(slug: str) -> Path:
        edit = web_edits / f"{slug}.yaml"
        if edit.is_file():
            return edit
        p = events_dir / f"{slug}.yaml"
        if p.is_file():
            return p
        raise FileNotFoundError(slug)

    def load_ev(slug: str) -> Event:
        state = load_state(slug)
        overrides = {}
        if state.get("signup", {}).get("responder_url"):
            overrides["form_url"] = state["signup"]["responder_url"]
            overrides["form_short_url"] = state["signup"]["responder_url"]
        if state.get("feedback", {}).get("responder_url"):
            overrides["feedback_url"] = state["feedback"]["responder_url"]
            overrides["feedback_prefill_url"] = state["feedback"].get("prefill_url")
        return load_event(event_path(slug), overrides or None)

    def latest_run_dir(slug: str) -> Path | None:
        base = out_root / slug / "runs"
        if not base.is_dir():
            return None
        runs = sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
        for p in runs:
            if (p / "outputs" / "2_預告信.html").is_file():
                return p
        return runs[0] if runs else None

    def _preview_needs_refresh(run_dir: Path | None, slug: str) -> bool:
        """Rebuild if there is no preview, or if form URLs exist but the preview still has placeholders."""
        if run_dir is None or not (run_dir / "outputs" / "2_預告信.html").is_file():
            return True
        state = _read_json(state_path(slug), {})
        url = (state.get("signup") or {}).get("responder_url") or ""
        if url and "{{" in (run_dir / "outputs" / "2_預告信.html").read_text(encoding="utf-8"):
            return True
        return False

    def ensure_preview(slug: str) -> Path | None:
        d = latest_run_dir(slug)
        if not _preview_needs_refresh(d, slug):
            return d
        r = run_pipeline(event_path(slug), "preview", out_root)
        return r.dir

    def run_url(slug: str, run_dir: Path | None, rel: str) -> str:
        if not run_dir:
            return ""
        return url_for("run_file", slug=slug, run=run_dir.name, rel=rel)

    def pick_ig_cards(slug: str, ev: Event, run_dir: Path | None) -> list[dict]:
        date = str(ev.raw.get("date") or "")
        cards = []
        for chosen in ig_card_paths(root, ev, run_dir):
            if chosen.parent.name == date:
                href = url_for("export_card", date=date, name=chosen.name)
            else:
                href = run_url(slug, run_dir, f"ig/{chosen.name}")
            download = href + ("&" if "?" in href else "?") + "download=1"
            cards.append({"name": chosen.name, "href": href, "download": download, "path": str(chosen)})
        return cards

    def field_values(raw: dict) -> dict:
        return {k: "" if _get(raw, k) is None else str(_get(raw, k)) for k, _ in FIELDS}

    def source_label_for(ev: Event, listing_source: str | None = None) -> str:
        if ev.raw.get("notion_source") and listing_source == "notion":
            return "Notion「📣 Marketing」"
        if ev.raw.get("notion_source"):
            return "本機活動檔（原從 Notion「📣 Marketing」填入）"
        return "本機活動檔"

    def action_view(slug: str, state: dict) -> list[dict]:
        actions = state.get("actions") or empty_actions()
        views = []
        labels = {k: lab for k, lab, _ in ACTION_META}
        confirms = {k: c for k, _, c in ACTION_META}
        pills = {"idle": "p-idle", "running": "p-run", "done": "p-ok", "failed": "p-err",
                 "skipped": "p-warn", "coming": "p-come"}
        status_label = {"idle": "未執行", "running": "執行中", "done": "已完成", "failed": "失敗",
                        "skipped": "已略過", "coming": "即將推出"}
        busy = _is_busy(slug)
        for i, key in enumerate(ACTION_KEYS, start=1):
            a = actions.get(key) or {}
            status = a.get("status") or "idle"
            links = list(a.get("links") or [])
            if key == "forms":
                links = _form_links(state) or links
            if key == "drafts":
                links = _draft_links(state) or links
                if status == "done" and not a.get("message"):
                    a["message"] = _draft_message(state)
            views.append({
                "key": key,
                "no": i,
                "label": labels[key],
                "status": status,
                "status_label": status_label.get(status, status),
                "pill": pills.get(status, "p-idle"),
                "message": a.get("message") or "",
                "links": links,
                # Only failed / skipped steps get a manual 重跑 button; done steps just show ✓.
                "retry": status in ("failed", "skipped"),
                "button": "重跑",
                "confirm": confirms[key],
                "disabled": busy,
            })
        return views

    def runall_view(slug: str, state: dict) -> dict:
        ra = dict(state.get("runall") or {})
        ra.setdefault("status", "idle")
        ra["busy"] = _is_busy(slug)
        cur = ra.get("current") or ""
        ra["current_label"] = {k: lab for k, lab, _ in ACTION_META}.get(cur, "")
        return ra

    def status_payload(slug: str) -> dict:
        state = load_state(slug)
        return {"slug": slug, "actions": action_view(slug, state), "stats": state.get("stats") or {},
                "runall": runall_view(slug, state),
            "ig_draft": ig_draft_view(slug, state), "ig_story": ig_story_view(slug, state),
            "announce": announce_view(state)}

    def set_action(slug: str, key: str, **fields) -> dict:
        with _JOBS_LOCK:
            state = load_state(slug)
            cur = state.setdefault("actions", empty_actions()).setdefault(key, {"status": "idle", "message": "", "links": []})
            cur.update(fields)
            save_state(slug, state)
            return state

    def _job_key(slug: str, key: str):
        return (str(out_root), slug, key)

    def _is_busy(slug: str, key: str | None = None) -> bool:
        ra = _RUNALL.get((str(out_root), slug))
        if ra is not None and ra.is_alive():
            return True
        keys = [key] if key else ACTION_KEYS
        for k in keys:
            t = _JOBS.get(_job_key(slug, k))
            if t is not None and t.is_alive():
                return True
        return False

    def start_action(slug: str, key: str) -> dict:
        if key not in ACTION_KEYS:
            abort(404)
        jk = _job_key(slug, key)
        with _JOBS_LOCK:
            t = _JOBS.get(jk)
            if t is not None and t.is_alive():
                return status_payload(slug)
            ra = _RUNALL.get((str(out_root), slug))
            if ra is not None and ra.is_alive():
                return status_payload(slug)
            state = load_state(slug)
            if (state.get("actions") or {}).get(key, {}).get("status") == "coming":
                return status_payload(slug)
            set_action(slug, key, status="running", message="執行中…")
            t = threading.Thread(target=_run_action, args=(slug, key), daemon=True, name=f"autopilot-{key}")
            _JOBS[jk] = t
            t.start()
        return status_payload(slug)

    def start_run_all(slug: str) -> dict:
        rk = (str(out_root), slug)
        with _JOBS_LOCK:
            t = _RUNALL.get(rk)
            if (t is not None and t.is_alive()) or _is_busy(slug):
                return status_payload(slug)
            state = load_state(slug)
            state["runall"] = {"status": "running", "current": "", "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                               "finished": "", "message": "全部執行中…"}
            save_state(slug, state)
            t = threading.Thread(target=_run_all, args=(slug,), daemon=True, name="autopilot-runall")
            _RUNALL[rk] = t
            t.start()
        return status_payload(slug)

    def _set_runall(slug: str, **fields) -> None:
        with _JOBS_LOCK:
            state = load_state(slug)
            ra = state.setdefault("runall", {})
            ra.update(fields)
            save_state(slug, state)

    def _run_all(slug: str) -> None:
        """Run every step in order. Done live-service steps are left alone; a failed step makes its
        dependants 已略過 while independent steps keep going. Never sends mail / IG / LINE."""
        labels = {k: lab for k, lab, _ in ACTION_META}
        results: dict[str, str] = {}
        try:
            for key in ACTION_KEYS:
                cur = ((load_state(slug).get("actions") or {}).get(key) or {}).get("status")
                if key in SKIP_IF_DONE and cur == "done":
                    results[key] = "done"
                    continue
                if cur == "coming":
                    results[key] = "coming"
                    continue
                blocked = [d for d in STEP_DEPS.get(key, []) if results.get(d) != "done"]
                if blocked:
                    names = "、".join(f"「{labels[d]}」" for d in blocked)
                    set_action(slug, key, status="skipped", message=f"因為{names}沒有完成，先略過；修好後按重跑。", links=[])
                    results[key] = "skipped"
                    continue
                _set_runall(slug, current=key)
                set_action(slug, key, status="running", message="執行中…")
                _run_action(slug, key)
                results[key] = ((load_state(slug).get("actions") or {}).get(key) or {}).get("status") or "failed"
        except Exception as e:  # never leave the run hanging
            _set_runall(slug, status="failed", current="", finished=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        message=f"全部執行中斷：{e.__class__.__name__}: {e}")
            return
        failed = [labels[k] for k, v in results.items() if v == "failed"]
        skipped = [labels[k] for k, v in results.items() if v == "skipped"]
        if failed or skipped:
            msg = f"跑完了，但有 {len(failed)} 項失敗、{len(skipped)} 項略過，請按該項的「重跑」。"
            status = "failed"
        else:
            msg = "全部完成。"
            status = "done"
        _set_runall(slug, status=status, current="", finished=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    message=msg, results=results)

    def _run_action(slug: str, key: str) -> None:
        try:
            _dispatch(slug, key)
        except SystemExit as e:
            set_action(slug, key, status="failed", message=str(e) or "已停止")
        except Exception as e:
            set_action(slug, key, status="failed", message=f"{e.__class__.__name__}: {e}")
        # A handler that returned without recording a result counts as failed, not "running" forever.
        cur = ((load_state(slug).get("actions") or {}).get(key) or {})
        if cur.get("status") == "running":
            set_action(slug, key, status="failed", message="沒有回報結果，請看終端機訊息後重跑。")

    def _dispatch(slug: str, key: str) -> None:
        if True:
            if key == "import":
                _do_import(slug)
                return
            ev = load_ev(slug)
            state = load_state(slug)
            if key == "preview":
                _do_preview(slug, ev, state)
            elif key == "ig":
                _do_ig(slug, ev, state)
            elif key == "cert":
                _do_cert(slug, ev, state)
            elif key == "stats":
                _do_stats(slug, ev, state)
            elif key == "forms":
                _do_forms(slug, ev, state)
            elif key == "banner":
                _do_banner(slug, ev, state)
            elif key == "canva":
                _do_canva(slug, ev, state)
            elif key == "qr":
                _do_qr(slug, ev, state)
            elif key == "drafts":
                _do_drafts(slug, ev, state)
            elif key == "linktree":
                _do_linktree(slug, ev, state)
            else:
                set_action(slug, key, status="failed", message=f"未知動作：{key}")

    def _do_import(slug: str) -> None:
        from . import notion_import
        data = notion_import.list_lectures(events_dir)
        card = next((c for c in data.get("lectures") or [] if c.get("slug") == slug), None)
        src = {"notion": "Notion「📣 Marketing」", "yaml_offline": "本機活動檔（Notion 權杖未設定）",
               "yaml_fallback": "本機活動檔（Notion 暫時讀不到）", "yaml": "本機活動檔"}.get(data.get("source"), "本機活動檔")
        if card and card.get("raw") and not (card.get("path") and Path(card["path"]).is_file()):
            web_edits.mkdir(parents=True, exist_ok=True)
            notion_import.write_event_yaml(card["raw"], web_edits / f"{slug}.yaml")
        ev = load_ev(slug)  # raises if the event file is broken
        set_action(slug, "import", status="done",
                   message=f"已讀取「{ev.raw.get('title') or slug}」，來源：{src}。")

    def _do_preview(slug: str, ev: Event, state: dict) -> None:
        r = run_pipeline(event_path(slug), "preview", out_root)
        chk = r.manifest.get("check") or {}
        if chk.get("error"):
            rows = _read_json(r.dir / "check.json", [])
            bad = [f"{x.get('file')}：{x.get('rule')}" for x in rows if x.get("level") == "error"][:3]
            set_action(slug, "preview", status="failed",
                       message=f"規則檢查有 {chk['error']} 項違規：" + "；".join(bad))
            return
        set_action(slug, "preview", status="done",
                   message=f"文案已產生，規則檢查全部通過（提醒 {chk.get('warn', 0)} 項）。預覽 {r.dir.name}")

    def _do_ig(slug: str, ev: Event, state: dict) -> None:
        cards = ig_card_paths(root, ev, latest_run_dir(slug))
        names = [p.name for p in cards]
        if len(cards) < 4:
            missing = [f"ig_{i:02d}" for i in range(1, 5) if not any(n.startswith(f"ig_{i:02d}") for n in names)]
            set_action(slug, "ig", status="failed", message="缺少圖卡：" + "、".join(missing) + "（請先從 Canva 匯出）")
            return
        set_action(slug, "ig", status="done",
                   message="四張圖卡已就緒：" + "、".join(names) + "。發文請按「IG 發文」，在手機上完成（不會自動發 IG）。")

    def _do_cert(slug: str, ev: Event, state: dict) -> None:
        certs = official_cert_paths(out_root, slug, None)
        if certs["pdf"]:
            set_action(slug, "cert", status="done", message=f"已有正式感謝狀 {certs['pdf'].name}，沒有覆蓋。",
                       links=_cert_links(slug, certs))
            return
        from .certificate import make_certificate
        base = out_root / slug
        make_certificate(ev, base / "感謝狀.pdf", png_path=base / "感謝狀.png")
        certs = official_cert_paths(out_root, slug, None)
        set_action(slug, "cert", status="done", message="已產生感謝狀 感謝狀.pdf／感謝狀.png。", links=_cert_links(slug, certs))

    def _do_stats(slug: str, ev: Event, state: dict) -> None:
        stats = refresh_stats(slug, ev)
        note = stats.get("note") or ""
        if "尚無法讀取" in note:
            set_action(slug, "stats", status="failed", message=note)
            return
        fb = stats.get("feedback_n")
        msg = f"課前報名 {stats.get('registrations', 0)} 人；課後回饋 {'—' if fb is None else fb} 份（只顯示人數）。"
        set_action(slug, "stats", status="done", message=msg)

    def _do_forms(slug: str, ev: Event, state: dict) -> None:
        token = default_forms_token()
        if state.get("signup") and state.get("feedback"):
            set_action(slug, "forms", status="done", message="表單已經建過，沒有重做。", links=_form_links(state))
            return
        r = run_pipeline(
            event_path(slug), "forms", out_root,
            forms_token=str(token), forms_account=FORMS_ACCOUNT,
        )
        new_state = load_state(slug)
        # Merge form IDs written by the pipeline (it writes signup/feedback to the same file).
        pipe_state = r.manifest.get("state") or {}
        for k in ("signup", "feedback"):
            if pipe_state.get(k):
                new_state[k] = pipe_state[k]
        save_state(slug, new_state)
        set_action(slug, "forms", status="done",
                   message="表單已建立（個人帳號）",
                   links=_form_links(new_state))

    def _do_banner(slug: str, ev: Event, state: dict) -> None:
        if not _module_available("autopilot.form_banner"):
            set_action(slug, "banner", status="coming", message="Banner 模組還沒接上")
            return
        if not state.get("signup"):
            set_action(slug, "banner", status="failed", message="請先建立表單。")
            return
        from . import form_banner
        shots = out_root / slug / "form_banner_shots"
        try:
            res = form_banner.apply_from_state(
                state, ev,
                image=form_banner.default_banner(),
                shots=shots,
                required_account=FORMS_ACCOUNT,
                headless=True,  # never opens a window or steals focus
                login_wait_s=45,
            )
        except SystemExit as e:
            raise SystemExit(f"{e}　→ 請執行 run_form_banner.cmd 用個人帳號登入一次，再按重跑。") from None
        links = _form_links(state)
        ok = bool(res.get("ok", True) if isinstance(res, dict) else res)
        failed = False
        forms = []
        if isinstance(res, dict):
            forms = res.get("forms") or []
            failed = any((f.get("ok") is False) or f.get("error") for f in forms if isinstance(f, dict))
            user = res.get("user_action_needed")
            msg = "已補上黑底 Banner 與確認訊息。"
            if user and failed:
                msg = "Banner 專用 Chrome 的個人帳號登入已失效：請執行 run_form_banner.cmd 登入一次，再按重跑。"
            state = load_state(slug)
            state["banner"] = {
                "ok": bool(ok) and not failed,
                "applied_at": date.today().isoformat(),
                "forms": forms,
                "shots": str(shots),
            }
            save_state(slug, state)
            links = _form_links(state) + _banner_shot_links(slug, res, shots / "result.json" if shots else None)
            if failed:
                set_action(slug, "banner", status="failed", message=msg + " 部分表單沒有成功。", links=links)
                return
            set_action(slug, "banner", status="done", message=msg, links=links)
            return
        state = load_state(slug)
        state["banner"] = {"ok": bool(ok), "applied_at": date.today().isoformat(), "shots": str(shots)}
        save_state(slug, state)
        set_action(slug, "banner", status="done" if ok else "failed",
                   message="已補上黑底 Banner 與確認訊息。" if ok else "Banner 沒有成功。",
                   links=links)

    def _do_canva(slug: str, ev: Event, state: dict) -> None:
        """New Canva copies of the IG + certificate templates, filled and exported (see canva_job)."""
        from . import canva_job
        pl = canva_job.plan(ev)
        job = canva_job.read(out_root, slug)
        if job.get("status") == "done" and canva_job.outputs_ready(root, out_root, slug, pl["exported_dir"]):
            set_action(slug, "canva", status="done", message="Canva 複本已匯出過，沒有重做。", links=_canva_links(job))
            return
        photo = canva_job.prepare_photo(root, ev, out_root / slug / "canva" / "photo_upload.jpg")
        job = canva_job.request(ev, out_root, slug)
        job["photo_upload"] = str(photo)
        canva_job.job_path(out_root, slug).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        set_action(slug, "canva", status="running", message="已送出 Canva 工作，等待 Canva 連接器處理…")

        def _tick(j: dict) -> None:
            set_action(slug, "canva", status="running", message=f"Canva：{j.get('progress') or '處理中'}…")

        timeout = float(os.environ.get("CLUB_CANVA_TIMEOUT_S", "1800"))
        job = canva_job.wait(out_root, slug, timeout_s=timeout, on_progress=_tick)
        if job.get("status") != "done":
            reason = job.get("error") or ("等候逾時" if job.get("status") == "timeout" else "沒有完成")
            set_action(slug, "canva", status="failed", message=f"Canva 步驟失敗：{reason}", links=_canva_links(job))
            return
        if not canva_job.outputs_ready(root, out_root, slug, pl["exported_dir"]):
            set_action(slug, "canva", status="failed", message="Canva 回報完成，但找不到 ig_01～ig_04 或感謝狀 PDF。", links=_canva_links(job))
            return
        state = load_state(slug)
        state["canva"] = {k: job.get(k) for k in ("ig_design_id", "cert_design_id", "photo_asset_id", "finished_at")}
        save_state(slug, state)
        set_action(slug, "canva", status="done",
                   message="已從模板建立新複本並匯出：ig_01～ig_04、感謝狀 PDF／PNG（原模板未修改）。",
                   links=_canva_links(job))

    def _do_qr(slug: str, ev: Event, state: dict) -> None:
        from . import canva_step
        dest = out_root / slug / "qr"
        dest.mkdir(parents=True, exist_ok=True)
        fn = None
        for name in ("update_qr", "update_signup_qr", "replace_qr"):
            cand = getattr(canva_step, name, None)
            if callable(cand):
                fn = cand
                break
        if fn is not None:
            res = fn(ev, dest)
        else:
            res = canva_step.run(ev, dest)
        links = []
        url = None
        png = None
        card = None
        if isinstance(res, dict):
            qr = res.get("qr") if isinstance(res.get("qr"), dict) else res
            url = qr.get("url") or res.get("url")
            png = qr.get("png") or res.get("png")
            card = qr.get("card") or res.get("card")
            if qr.get("ok") is False and qr.get("skipped"):
                set_action(slug, "qr", status="failed", message=str(qr.get("skipped")))
                return
        if url:
            links.append({"label": "課前表單", "href": url})
        design_id = (load_state(slug).get("canva") or {}).get("ig_design_id") or (ev.raw.get("canva") or {}).get("design_id") or ""
        if design_id:
            links.append({"label": f"Canva 工作複本 {design_id}", "href": f"https://www.canva.com/design/{design_id}/edit"})
        msg = "已依課前表單網址更新 QR。"
        if png:
            msg += f" 檔案：{png}"
        if card:
            msg += f" 圖卡：{card}"
        state = load_state(slug)
        state["qr"] = {"ok": True, "url": url, "png": str(png) if png else "", "card": str(card) if card else "", "design_id": design_id}
        save_state(slug, state)
        set_action(slug, "qr", status="done", message=msg, links=links)

    def _do_drafts(slug: str, ev: Event, state: dict) -> None:
        """Create missing drafts and update existing ones in place (BCC from the form). Never sends."""
        from . import drafts_sync, render
        from .google_auth import default_oauth_dir, forms_services, services
        from .pipeline import _read_bcc_file

        ev = load_ev(slug)
        outputs = render.render_all(ev)
        club = ev["club"]["email"]
        members = _read_bcc_file(str(default_members_bcc_file() or "") or None)
        form_id = (state.get("signup") or {}).get("formId")
        if not form_id:
            set_action(slug, "drafts", status="failed", message="還沒有課前表單，抓不到報名名單。")
            return
        # Same auth as the forms step (personal forms-account token). If this fails the step fails:
        # never wipe an existing BCC because the form couldn't be read.
        svc_f = forms_services(default_forms_token(), FORMS_ACCOUNT)
        registrants, total = drafts_sync.registrant_bcc(svc_f["forms"], form_id, club)
        svc = services(default_oauth_dir(), interactive=False)
        recs = drafts_sync.sync_drafts(outputs, club, state.get("drafts") or [],
                                       {"members": members, "registrants": registrants}, svc["gmail"])
        state = load_state(slug)
        state["drafts"] = recs
        stats = state.setdefault("stats", {})
        stats["registrations"] = total
        save_state(slug, state)
        note = drafts_sync.describe(recs)
        if not registrants:
            note += f"。課前表單目前 {total} 份回覆，提醒信／課後信密件副本先留空，之後重跑會自動補上"
        set_action(slug, "drafts", status="done", message=note + "。全部未寄出。", links=_draft_links(state))

    def _do_linktree(slug: str, ev: Event, state: dict) -> None:
        from . import linktree
        form_url = (state.get("signup") or {}).get("responder_url") or ev["form_url"]
        if not form_url or "{{" in str(form_url):
            set_action(slug, "linktree", status="failed", message="請先建立課前表單。")
            return
        ev = load_ev(slug)
        plan = linktree.plan(ev, form_url)
        shots = out_root / slug / "linktree_shots"
        res = linktree.run(plan, shots)
        if isinstance(res, dict) and res.get("logged_in") is False:
            set_action(slug, "linktree", status="failed", message=res.get("message") or "Linktree 尚未登入。")
            return
        state = load_state(slug)
        if apply_derived_status(slug, state):
            save_state(slug, state)
        if (state.get("actions") or {}).get("linktree", {}).get("status") != "done":
            log = "；".join(str(x) for x in ((res or {}).get("log") or [])[:4])
            set_action(slug, "linktree", status="failed", message="同步後檢查沒通過：" + (log or "請看截圖"),
                       links=_linktree_links(slug))

    def refresh_stats(slug: str, ev: Event | None = None) -> dict:
        state = load_state(slug)
        stats = state.setdefault("stats", {"registrations": 0, "bcc_count": 0, "feedback_n": None, "feedback_avg": None, "note": ""})
        note = "人數只顯示合計，不會列出 Email。"
        try:
            ev = ev or load_ev(slug)
            club = ev["club"]["email"]
            from . import forms_api, responses
            from .google_auth import forms_services
            # Announcement BCC is the club preview list (count only). Do not
            # overwrite it with the (possibly empty) registrant list.
            members_n = members_bcc_count()
            if members_n:
                stats["bcc_count"] = members_n
                note = f"密件副本 {members_n} 人來自社員預告信名單（只計數量）。報名人數來自課前表單回覆。人數只顯示合計，不會列出 Email。"
            if state.get("signup", {}).get("formId") and default_forms_token().is_file():
                svc = forms_services(default_forms_token(), FORMS_ACCOUNT)
                rows = forms_api.responses_as_rows(svc["forms"], state["signup"]["formId"])
                stats["registrations"] = len(rows)
            elif not members_n:
                note = "尚無法讀取表單回覆；密件副本人數來自社員預告信名單（只計數量）。"
            if state.get("feedback", {}).get("formId") and default_forms_token().is_file():
                try:
                    from .forms_api import IMPROVE, OVERALL
                    if "svc" not in locals():
                        svc = forms_services(default_forms_token(), FORMS_ACCOUNT)
                    fb_rows = forms_api.responses_as_rows(svc["forms"], state["feedback"]["formId"])
                    if fb_rows:
                        s = responses.summarize_feedback(fb_rows, ev["feedback_questions"], OVERALL, IMPROVE)
                        stats["feedback_n"] = s["n"]
                        stats["feedback_avg"] = s["overall_avg"]
                        dest = out_root / slug / "課後回饋彙整.html"
                        dest.write_text(responses.feedback_html(f"{ev['session_label']}課後回饋彙整（{ev['md_week']}）", s), encoding="utf-8")
                    else:
                        stats["feedback_n"] = 0
                except Exception:
                    pass
        except Exception as e:
            members_n = members_bcc_count()
            if members_n:
                stats["bcc_count"] = members_n
                note = f"密件副本 {members_n} 人來自社員預告信名單（只計數量）。尚無法讀取表單回覆（" + e.__class__.__name__ + "）。人數只顯示合計，不會列出 Email。"
            else:
                note = "尚無法讀取表單回覆（" + e.__class__.__name__ + "）。人數只顯示合計，不會列出 Email。"
        apply_local_stats(state)
        if members_bcc_count() and not (stats.get("note") or "").startswith("密件副本"):
            stats["note"] = note
        else:
            stats["note"] = stats.get("note") or note
        save_state(slug, state)
        return stats

    def _line_view(state: dict, text: str) -> dict:
        from . import line_push
        st = line_push.public_status()
        last = (state.get("line_push") or {}).get("last") or {}
        return {"configured": st["configured"], "missing": st["missing"], "config_file": st["config_file"],
                "groups": st.get("groups") or [],
                "hash": line_push.text_hash(text.strip()) if text.strip() else "", "text": text.strip(),
                "last_at": last.get("at", ""),
                "last_same": bool(text.strip()) and last.get("hash") == line_push.text_hash(text.strip())}

    def dashboard_context(slug: str | None, msg: str = "") -> dict:
        files = sorted(list(events_dir.glob("*.yaml")) + list(web_edits.glob("*.yaml")), key=lambda p: p.stem)
        if not slug:
            slug = request.args.get("event") or (files[-1].stem if files else "")
        if not slug:
            return {
                "msg": msg, "ev": None, "slug": "", "fields": FIELDS, "field_values": {},
                "source_label": "", "stats": {}, "actions": [], "check": {"error": 0, "warn": 0, "ok": 0},
                "check_rows": [], "run_name": "", "mail": {}, "mail_downloads": {},
                "line_copy": "", "ig_copy": "", "line_download": "", "ig_copy_download": "",
                "ig_cards": [], "canva_url": "", "canva_id": "", "cert_url": "",
                "cert_png_url": "", "cert_view_url": "", "cert_pdf_download": "", "cert_png_download": "",
                "timeline": [], "ics_url": "", "feedback_url": "",
            }
        ev = load_ev(slug)
        run_dir = latest_run_dir(slug)  # opening the page never generates a preview; 「產生文案」 does
        state = load_state(slug)
        # Don't block the page on Google; stats refresh is best-effort and cached.
        if not (state.get("stats") or {}).get("note"):
            try:
                refresh_stats(slug, ev)
                state = load_state(slug)
            except Exception:
                pass
        check = {"error": 0, "warn": 0, "ok": 0}
        check_rows = []
        timeline = []
        mail = {"announce": "", "reminder": "", "followup": ""}
        mail_downloads = {"announce": "", "reminder": "", "followup": ""}
        line_copy = ig_copy = ""
        line_download = ig_copy_download = ""
        canva_url = canva_id = cert_url = cert_png_url = cert_view_url = ""
        cert_pdf_download = cert_png_download = ics_url = feedback_url = ""
        if run_dir:
            m = _read_json(run_dir / "manifest.json", {})
            check = (m.get("check") or check)
            rows = _read_json(run_dir / "check.json", [])
            marks = {"error": "✖ 違規", "warn": "▲ 提醒", "ok": "✓"}
            order = {"error": 0, "warn": 1, "ok": 2}
            check_rows = [
                {**x, "mark": marks.get(x.get("level"), x.get("level"))}
                for x in sorted(rows, key=lambda x: order.get(x.get("level"), 9))
            ]
            timeline = _read_json(run_dir / "timeline.json", [])
            def _dl(url: str) -> str:
                return (url + ("&" if "?" in url else "?") + "download=1") if url else ""
            mail = {
                "announce": run_url(slug, run_dir, "outputs/2_預告信.html"),
                "reminder": run_url(slug, run_dir, "outputs/4_提醒信.html"),
                "followup": run_url(slug, run_dir, "outputs/6_課後信.html"),
            }
            mail_downloads = {k: _dl(v) for k, v in mail.items()}
            line_p = run_dir / "outputs" / "3_Line訊息.txt"
            ig_p = run_dir / "outputs" / "3_IG文案.txt"
            line_copy = line_p.read_text(encoding="utf-8") if line_p.is_file() else ""
            ig_copy = ig_p.read_text(encoding="utf-8") if ig_p.is_file() else ""
            if line_p.is_file():
                line_download = _dl(run_url(slug, run_dir, "outputs/3_Line訊息.txt"))
            if ig_p.is_file():
                ig_copy_download = _dl(run_url(slug, run_dir, "outputs/3_IG文案.txt"))
            canva = (m.get("canva") or ev.raw.get("canva") or {})
            canva_id = canva.get("design_id") or ev.raw.get("canva", {}).get("design_id") or ""
            canva_url = canva.get("design_url") or (f"https://www.canva.com/design/{canva_id}/edit" if canva_id else "")
            if (run_dir / "timeline.ics").is_file():
                ics_url = run_url(slug, run_dir, "timeline.ics")
            if (run_dir / "課後回饋彙整.html").is_file():
                feedback_url = run_url(slug, run_dir, "課後回饋彙整.html")
        live_fb = out_root / slug / "課後回饋彙整.html"
        if live_fb.is_file():
            feedback_url = url_for("slug_file", slug=slug, rel="課後回饋彙整.html")
        certs = official_cert_paths(out_root, slug, run_dir)
        if certs["pdf"]:
            cert_url = url_for("cert_file", slug=slug, kind="pdf")
            cert_pdf_download = url_for("cert_file", slug=slug, kind="pdf", download=1)
        if certs["png"]:
            cert_png_url = url_for("cert_file", slug=slug, kind="png")
            cert_png_download = url_for("cert_file", slug=slug, kind="png", download=1)
        if certs["pdf"] or certs["png"]:
            cert_view_url = url_for("cert_view", slug=slug)
        return {
            "msg": msg,
            "ev": ev,
            "slug": slug,
            "fields": FIELDS,
            "field_values": field_values(ev.raw),
            "source_label": source_label_for(ev),
            "stats": state.get("stats") or {},
            "actions": action_view(slug, state),
            "check": check,
            "check_rows": check_rows,
            "run_name": run_dir.name if run_dir else "",
            "mail": mail,
            "mail_downloads": mail_downloads,
            "line_copy": line_copy,
            "ig_copy": ig_copy,
            "line_download": line_download,
            "ig_copy_download": ig_copy_download,
            "ig_cards": pick_ig_cards(slug, ev, run_dir) if ((state.get("actions") or {}).get("ig") or {}).get("status") == "done" else [],
            "check_errors": [x for x in check_rows if x.get("level") == "error"],
            "custom_rules": checker.load_custom_rules(),
            "rule_targets": checker.TARGETS,
            "rule_kinds": checker.KINDS,
            "runall": runall_view(slug, state),
            "ig_draft": ig_draft_view(slug, state),
            "announce": announce_view(state),
            "ig_story": ig_story_view(slug, state),
            "ig_post_url": ((ev.raw.get("ig") or {}).get("post_url") or ""),
            "ig_music": ig_music.view(ev.raw),
            "line": _line_view(state, line_copy),
            "canva_url": canva_url,
            "canva_id": canva_id,
            "cert_url": cert_url,
            "cert_png_url": cert_png_url,
            "cert_view_url": cert_view_url,
            "cert_pdf_download": cert_pdf_download,
            "cert_png_download": cert_png_download,
            "timeline": timeline,
            "ics_url": ics_url,
            "feedback_url": feedback_url,
        }

    @app.get("/favicon.ico")
    def favicon():
        icon = root / "assets" / "favicon.ico"
        if icon.is_file():
            return send_from_directory(icon.parent, icon.name, mimetype="image/x-icon")
        png = root / "assets" / "favicon.png"
        if png.is_file():
            return send_from_directory(png.parent, png.name, mimetype="image/png")
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (32, 32), (201, 141, 23)).save(buf, format="PNG")
        return Response(buf.getvalue(), mimetype="image/png")

    @app.get("/brand-logo")
    def brand_logo():
        raw = launchpad.saved_club_logo()
        if not raw:
            abort(404)
        path = Path(raw)
        return send_from_directory(path.parent, path.name)

    @app.get("/asset/<path:name>")
    def asset(name):
        return send_from_directory(root / "assets", name)

    def _wants_download() -> bool:
        return request.args.get("download") in ("1", "true", "yes")

    def _send(directory: Path, name: str, attachment_name: str | None = None):
        if _wants_download():
            return send_from_directory(
                directory, name, as_attachment=True,
                download_name=attachment_name or name,
            )
        return send_from_directory(directory, name)

    @app.get("/export/<date>/<path:name>")
    def export_card(date, name):
        if ".." in date or ".." in name or name.endswith("watermarked.png"):
            abort(404)
        return _send(root / "assets" / "canva_export" / date, name)

    def _safe_run(slug, run) -> Path:
        d = (out_root / slug / "runs" / run).resolve()
        if out_root.resolve() not in d.parents or not d.is_dir():
            abort(404)
        return d

    @app.get("/run/<slug>/<run>/file/<path:rel>")
    def run_file(slug, run, rel):
        return _send(_safe_run(slug, run), rel, Path(rel).name)

    @app.get("/file/<slug>/<path:rel>")
    def slug_file(slug, rel):
        d = (out_root / slug).resolve()
        if out_root.resolve() not in d.parents or not d.is_dir():
            abort(404)
        return _send(d, rel, Path(rel).name)

    @app.get("/cert/<slug>/view.png")
    def cert_view(slug):
        """PNG of the certificate card only, even when the export is a full page."""
        from io import BytesIO
        from PIL import Image
        from .certificate import certificate_view, render_certificate
        paths = official_cert_paths(out_root, slug, latest_run_dir(slug))
        im = None
        if paths.get("png") and paths["png"].is_file():
            try:
                im = certificate_view(Image.open(paths["png"]))
            except Exception:
                im = None
        if im is None and paths.get("pdf"):
            try:
                im = render_certificate(load_event(event_path(slug)))
            except Exception:
                im = None
        if im is None:
            abort(404)
        buf = BytesIO()
        im.save(buf, format="PNG")
        return Response(buf.getvalue(), mimetype="image/png")

    @app.get("/cert/<slug>/<kind>")
    def cert_file(slug, kind):
        if kind not in ("pdf", "png"):
            abort(404)
        paths = official_cert_paths(out_root, slug, latest_run_dir(slug))
        path = paths[kind]
        if not path or not path.is_file():
            abort(404)
        download_name = "感謝狀.pdf" if kind == "pdf" else "感謝狀.png"
        mime = "application/pdf" if kind == "pdf" else "image/png"
        if _wants_download():
            return send_from_directory(
                path.parent, path.name, as_attachment=True,
                download_name=download_name, mimetype=mime,
            )
        return send_from_directory(path.parent, path.name, mimetype=mime)

    @app.get("/linktree-shot/<slug>/<name>")
    def linktree_shot(slug, name):
        if name not in LINKTREE_SHOTS:
            abort(404)
        for folder in _linktree_dirs(slug):
            if (folder / name).is_file():
                return _send(folder, name)
        abort(404)

    @app.get("/banner-shot/<slug>/<name>")
    def banner_shot(slug, name):
        if name not in BANNER_CHECK_SHOTS.values() or ".." in name:
            abort(404)
        _, result_path = load_banner_result(out_root, slug)
        folders = []
        if result_path:
            folders.append(result_path.parent)
        folders.extend([out_root / slug / "form_banner_shots", out_root / "form_banner_shots"])
        for folder in folders:
            path = Path(folder) / name
            if path.is_file():
                return _send(path.parent, name)
        abort(404)

    def render_page(name: str, **ctx) -> str:
        from . import i18n
        from .launchpad_i18n import T as ui
        club_name = ""
        try:
            club_name = launchpad.saved_club_name()
        except Exception:
            club_name = ""
        logo = ""
        try:
            logo = launchpad.saved_club_logo()
        except Exception:
            logo = ""
        brand = {"name": club_name, "logo_url": "/brand-logo" if logo else "", "alt": club_name or "Club"}
        ctx = {**ctx, "lang": i18n.lang_from(request.cookies), "i18n_json": i18n.payload(), "ui": ui, "brand": brand}
        return app.jinja_env.get_template(name).render(**ctx)

    def render_dashboard(ctx: dict) -> str:
        try:
            show = launchpad.needs_onboarding()
        except Exception:
            show = False
        return render_page("dashboard.html", **{**ctx, "show_onboarding": show})

    def _store() -> launchpad.Store:
        return launchpad.Store(out_root, root)

    def _onboarding_body(store: launchpad.Store, progress: dict, errors: list | None = None) -> str:
        view = launchpad.wizard_view(progress, errors)
        launchpad.attach_config_path(view, store, progress)
        return render_page("onboarding.html", **view)

    @app.get("/")
    def index():
        ctx = dashboard_context(request.args.get("event"), request.args.get("msg", ""))
        return render_dashboard(ctx)

    @app.get("/event/<slug>")
    def show_event(slug):
        try:
            event_path(slug)
        except FileNotFoundError:
            abort(404)
        ctx = dashboard_context(slug, request.args.get("msg", ""))
        return render_dashboard(ctx)

    @app.get("/api/lectures")
    def api_lectures():
        from . import notion_import
        data = notion_import.list_lectures(events_dir)
        # Don't send the raw event blob to the browser (keep the payload small).
        lectures = [{k: v for k, v in c.items() if k != "raw"} for c in data["lectures"]]
        return jsonify({**data, "lectures": lectures})

    @app.post("/api/import")
    def api_import():
        from . import notion_import
        slug = ""
        if request.is_json and isinstance(request.json, dict):
            slug = (request.json.get("slug") or "")
        if not slug:
            slug = request.form.get("slug") or ""
        slug = slug.strip()
        if not slug:
            return jsonify({"error": "沒有選社課"}), 400
        data = notion_import.list_lectures(events_dir)
        card = next((c for c in data["lectures"] if c["slug"] == slug), None)
        if not card:
            return jsonify({"error": "找不到這堂社課"}), 404
        dest = events_dir / f"{slug}.yaml"
        if card.get("path") and Path(card["path"]).is_file():
            dest = Path(card["path"])
        elif card.get("raw"):
            web_edits.mkdir(parents=True, exist_ok=True)
            dest = web_edits / f"{slug}.yaml"
            notion_import.write_event_yaml(card["raw"], dest)
        # No preview here: copy is generated by step ②「產生文案」 (or 全部執行).
        src = {"notion": "Notion「📣 Marketing」", "notion_cache": "Notion 匯出（離線）",
               "yaml_offline": "本機活動檔", "yaml_fallback": "本機活動檔", "yaml": "本機活動檔"}.get(card.get("source"), "本機活動檔")
        load_state(slug)
        set_action(slug, "import", status="done", message=f"已讀取「{card.get('title') or slug}」，來源：{src}。")
        return jsonify({"slug": slug, "redirect": url_for("show_event", slug=slug)})

    @app.post("/event/<slug>/save")
    def save_event(slug):
        raw = yaml.safe_load(event_path(slug).read_text(encoding="utf-8")) or {}
        for k, _ in FIELDS:
            v = request.form.get(k, "")
            # Keep empty strings so the officer can clear a field.
            _set(raw, k, v.strip() if isinstance(v, str) else v)
        web_edits.mkdir(parents=True, exist_ok=True)
        dest = web_edits / f"{slug}.yaml"
        dest.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        run_pipeline(dest, "preview", out_root)
        return redirect(url_for("show_event", slug=slug, msg="已儲存修改並重新產生預覽。"))

    @app.post("/event/<slug>/ig-music")
    def save_ig_music(slug):
        """IG 音樂（貼文／限動） -> event YAML ig.music.<kind>. Empty song = no music."""
        try:
            raw = yaml.safe_load(event_path(slug).read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            abort(404)
        kind = request.form.get("kind", "")
        if kind not in ig_music.KINDS:
            abort(400)
        name = ig_music.KINDS[kind]
        try:
            entry = ig_music.build_entry(request.form.get("song", ""), request.form.get("start", ""),
                                         request.form.get("end", ""), kind=kind)
        except ig_music.MusicError as e:
            return redirect(url_for("show_event", slug=slug, msg=f"IG 音樂（{name}）沒有儲存：{e}"))
        music = raw.setdefault("ig", {}).setdefault("music", {})
        if entry:
            music[kind] = entry
        else:
            music.pop(kind, None)
        web_edits.mkdir(parents=True, exist_ok=True)
        dest = web_edits / f"{slug}.yaml"
        dest.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if entry:
            who = entry["title"] + (f" — {entry['artist']}" if entry["artist"] else "")
            msg = f"已儲存 IG 音樂（{name}）：{who}，{entry['start']}～{entry['end']}。"
        else:
            msg = f"已清除 IG 音樂（{name}）：不加音樂。"
        return redirect(url_for("show_event", slug=slug, msg=msg))

    @app.post("/event/<slug>/preview")
    def preview_event(slug):
        r = run_pipeline(event_path(slug), "preview", out_root)
        return redirect(url_for("show_event", slug=slug, msg=f"已產生預覽 {r.dir.name}"))

    @app.post("/event/<slug>/refresh-stats")
    def refresh_event_stats(slug):
        refresh_stats(slug)
        return redirect(url_for("show_event", slug=slug))

    @app.get("/api/status/<slug>")
    def api_status(slug):
        return jsonify(status_payload(slug))

    @app.post("/api/action/<slug>/<key>")
    def api_action(slug, key):
        try:
            event_path(slug)
        except FileNotFoundError:
            abort(404)
        return jsonify(start_action(slug, key))

    @app.post("/api/run-all/<slug>")
    def api_run_all(slug):
        try:
            event_path(slug)
        except FileNotFoundError:
            abort(404)
        return jsonify(start_run_all(slug))

    def line_text(slug: str) -> str:
        d = latest_run_dir(slug)
        p = d / "outputs" / LINE_OUTPUT if d else None
        return p.read_text(encoding="utf-8").strip() if p and p.is_file() else ""

    @app.post("/api/line/<slug>/send")
    def api_line_send(slug):
        """LINE 「直接發送」. Only from the button, after the officer confirmed the exact text.
        Never called by 全部執行. The browser must echo the hash of the text it showed."""
        from . import line_push
        try:
            event_path(slug)
        except FileNotFoundError:
            abort(404)
        body = request.get_json(silent=True) or {}
        if body.get("confirm") is not True:
            return jsonify({"ok": False, "error": "需要先確認文案。"}), 400
        text = line_text(slug)
        if not text:
            return jsonify({"ok": False, "error": "還沒有 LINE 文案，請先產生文案。"}), 400
        if body.get("hash") != line_push.text_hash(text):
            return jsonify({"ok": False, "error": "LINE 文案已更新，請重新整理頁面確認新文案後再發送。"}), 409
        st = line_push.public_status()
        if not st["configured"]:
            return jsonify({"ok": False, "error": "LINE 未設定：缺少 " + "、".join(st["missing"])}), 400
        try:
            res = line_push.push_text(text)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 502
        results = [{"group": r.get("group"), "ok": bool(r.get("ok")), "error": r.get("error", "")}
                   for r in res.get("results") or []]
        with _JOBS_LOCK:
            state = load_state(slug)
            sent = state.setdefault("line_push", {"history": []})
            entry = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "hash": line_push.text_hash(text),
                     "request_id": res.get("request_id", ""), "results": results}
            sent["history"] = (sent.get("history") or []) + [entry]
            sent["last"] = entry
            save_state(slug, state)
        if not res.get("ok"):
            bad = "、".join(f"{r['group']}（{r['error']}）" for r in results if not r["ok"])
            good = "、".join(r["group"] for r in results if r["ok"]) or "無"
            return jsonify({"ok": False, "error": f"部分群組沒發成功：{bad}；已發成功：{good}", "results": results}), 502
        return jsonify({"ok": True, "sent_at": entry["at"], "results": results})

    # ---- 「寄出預告信」: manual only (button + confirm), never in 全部執行. Sends the existing 預告信 draft once.
    def announce_view(state: dict) -> dict:
        d = next((x for x in state.get("drafts") or [] if x.get("file") == "2_預告信.html"), None) or {}
        sent = state.get("announce_sent") or {}
        return {"draft_id": d.get("draft_id", ""), "subject": d.get("subject", ""), "bcc_count": d.get("bcc_count", 0),
                "sent_at": sent.get("at", ""), "message_id": sent.get("message_id", "")}

    @app.post("/api/drafts/<slug>/send-announce")
    def api_send_announce(slug):
        from .google_auth import default_oauth_dir, services
        try:
            event_path(slug)
        except FileNotFoundError:
            abort(404)
        body = request.get_json(silent=True) or {}
        if body.get("confirm") is not True:
            return jsonify({"ok": False, "error": "需要先確認。"}), 400
        with _JOBS_LOCK:
            state = load_state(slug)
            v = announce_view(state)
            if v["sent_at"]:
                return jsonify({"ok": False, "error": f"預告信已在 {v['sent_at']} 寄出，不會再寄。"}), 409
            if not v["draft_id"]:
                return jsonify({"ok": False, "error": "還沒有預告信草稿，請先跑「Gmail 草稿」。"}), 400
            if body.get("draft_id") != v["draft_id"]:
                return jsonify({"ok": False, "error": "草稿已更新，請重新整理頁面再確認。"}), 409
            state["announce_sent"] = {"status": "sending", "at": ""}
            save_state(slug, state)
        try:
            gmail = services(default_oauth_dir(), interactive=False)["gmail"]
            msg = gmail.users().drafts().send(userId="me", body={"id": v["draft_id"]}).execute()
            mid = msg.get("id", "")
            labels = gmail.users().messages().get(userId="me", id=mid, format="minimal").execute().get("labelIds") or []
        except BaseException as e:  # unknown outcome: record it, never auto-retry
            with _JOBS_LOCK:
                st = load_state(slug)
                st["announce_sent"] = {"status": "unknown", "at": "", "error": f"{e.__class__.__name__}: {e}"}
                save_state(slug, st)
            return jsonify({"ok": False, "error": f"寄送時出錯（{e.__class__.__name__}），請先到寄件備份確認，不要直接重按。"}), 502
        at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _JOBS_LOCK:
            st = load_state(slug)
            st["announce_sent"] = {"status": "sent", "at": at, "message_id": mid, "sent_label": "SENT" in labels,
                                   "bcc_count": v["bcc_count"], "subject": v["subject"]}
            save_state(slug, st)
        return jsonify({"ok": True, "sent_at": at, "message_id": mid, "in_sent": "SENT" in labels})

    # ---- 「存成 IG 草稿（手機）」: manual only, never in 全部執行, never shares/publishes ----
    def ig_caption(slug: str) -> str:
        d = latest_run_dir(slug)
        p = d / "outputs" / "3_IG文案.txt" if d else None
        return p.read_text(encoding="utf-8").strip() if p and p.is_file() else ""

    def ig_draft_view(slug: str, state: dict) -> dict:
        v = dict(state.get("ig_draft") or {})
        t = _JOBS.get(_job_key(slug, "ig_draft"))
        v["busy"] = bool(t is not None and t.is_alive())
        if v.get("status") == "running" and not v["busy"]:
            v["status"] = "failed"
            v["message"] = "上次執行被中斷。"
        v["shot_links"] = [{"name": n, "href": f"/ig-shot/{slug}/{n}"} for n in display_shots("post", v)]
        return v

    @app.post("/api/ig/<slug>/check")
    def api_ig_check(slug):
        from . import ig_adb
        try:
            ev = load_ev(slug)
        except FileNotFoundError:
            abort(404)
        cfg = ig_adb.load_config()
        dev = ig_adb.devices(cfg)
        cards = [str(p) for p in ig_card_paths(root, ev, latest_run_dir(slug))]
        return jsonify({**dev, "cards": cards, "caption_chars": len(ig_caption(slug))})

    @app.post("/api/ig/<slug>/draft")
    def api_ig_draft(slug):
        from . import ig_adb
        try:
            ev = load_ev(slug)
        except FileNotFoundError:
            abort(404)
        body = request.get_json(silent=True) or {}
        if body.get("confirm") is not True:
            return jsonify({"ok": False, "error": "需要先確認。"}), 400
        publish = body.get("publish") is True and body.get("publish_confirm") is True  # public: ticked + 2nd confirm
        jk = _job_key(slug, "ig_draft")
        with _JOBS_LOCK:
            t = _JOBS.get(jk)
            if t is not None and t.is_alive():
                return jsonify({"ok": True, "ig_draft": ig_draft_view(slug, load_state(slug))})
            cards = ig_card_paths(root, ev, latest_run_dir(slug))
            caption = ig_caption(slug)
            music = ig_music.to_job(ig_music.get(ev.raw, "post"))
            state = load_state(slug)
            state["ig_draft"] = {"status": "running", "message": "正在手機上發佈 IG 貼文…" if publish else "正在手機上建立 IG 草稿…", "shots": []}
            save_state(slug, state)

            def work():
                shots = out_root / slug / "ig_shots"
                try:
                    res = ig_adb.save_ig_draft(cards, caption, shots, music=music, publish=publish)
                    merr = res.get("music_error") or ""
                    if not res["ok"]:
                        msg, status = "沒有完成：" + res["error"], "failed"
                    elif res.get("published"):
                        msg = "已發佈 IG 貼文（你在儀表板勾選並確認）。" + (f"⚠ {merr}，貼文沒有音樂。" if merr else "")
                        status = "warn" if merr else "done"
                    elif merr:
                        msg, status = f"⚠ {merr}。草稿已存（沒有音樂、沒有分享），請換一首或在手機上自己加。", "warn"
                    else:
                        msg, status = "已存成 IG 草稿（沒有分享），請在手機上確認。", "done"
                    post_shots = res["shots"]
                    if res.get("published"):
                        # One screenshot per carousel page of the live post (profile → newest post → swipe).
                        try:
                            from . import ig_story
                            pages = ig_story.capture_post_pages(shots, len(cards), expect_cover=cards[0] if cards else None)
                            if pages:
                                post_shots = pages
                            res["log"].append(f"已截取發佈後貼文每一頁（{len(pages)} 張）")
                        except Exception as e:
                            res["log"].append(f"發佈後逐頁截圖沒有完成：{e}")
                    rec = {"status": status, "message": msg, "music_error": merr, "shots": post_shots,
                           "log": res["log"], "at": res["at"], "published": bool(res.get("published")),
                           "seconds": res.get("seconds")}
                except Exception as e:
                    rec = {"status": "failed", "message": f"沒有完成：{e}", "shots": []}
                with _JOBS_LOCK:
                    st = load_state(slug)
                    st["ig_draft"] = rec
                    save_state(slug, st)

            t = threading.Thread(target=work, daemon=True, name="autopilot-ig-draft")
            _JOBS[jk] = t
            t.start()
        return jsonify({"ok": True, "ig_draft": ig_draft_view(slug, load_state(slug))})

    # ---- 「轉發貼文到限動（手機）」: after the officer published the post; never shares ----
    def ig_story_view(slug: str, state: dict) -> dict:
        v = dict(state.get("ig_story") or {})
        t = _JOBS.get(_job_key(slug, "ig_story"))
        v["busy"] = bool(t is not None and t.is_alive())
        if v.get("status") == "running" and not v["busy"]:
            v["status"], v["message"] = "failed", "上次執行被中斷。"
        v["shot_links"] = [{"name": n, "href": f"/ig-shot/{slug}/{n}?d=story"} for n in display_shots("story", v)]
        return v

    @app.post("/api/ig/<slug>/story")
    def api_ig_story(slug):
        from . import ig_story
        try:
            ev = load_ev(slug)
            raw_path = event_path(slug)
        except FileNotFoundError:
            abort(404)
        body = request.get_json(silent=True) or {}
        if body.get("confirm") is not True:
            return jsonify({"ok": False, "error": "需要先確認。"}), 400
        like_repost = body.get("like_repost") is True  # public actions: only when ticked in the dialog
        publish = body.get("publish") is True and body.get("publish_confirm") is True
        post_url = str(body.get("post_url") or "").strip()
        if post_url and not post_url.startswith("https://www.instagram.com/"):
            return jsonify({"ok": False, "error": "貼文網址要是 https://www.instagram.com/p/… 。"}), 400
        if post_url:  # remember it in the event YAML (ig.post_url)
            raw = yaml.safe_load(raw_path.read_text(encoding="utf-8")) or {}
            raw.setdefault("ig", {})["post_url"] = post_url
            web_edits.mkdir(parents=True, exist_ok=True)
            (web_edits / f"{slug}.yaml").write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        jk = _job_key(slug, "ig_story")
        with _JOBS_LOCK:
            t = _JOBS.get(jk)
            if t is not None and t.is_alive():
                return jsonify({"ok": True, "ig_story": ig_story_view(slug, load_state(slug))})
            state = load_state(slug)
            form_url = (state.get("signup") or {}).get("responder_url") or ev["form_url"]
            music = ig_music.to_job(ig_music.get(ev.raw, "story"))
            state["ig_story"] = {"status": "running", "message": "正在手機上準備限動…", "shots": []}
            save_state(slug, state)

            def work():
                shots = out_root / slug / "ig_story_shots"
                try:
                    cards = ig_card_paths(root, ev, latest_run_dir(slug))
                    res = ig_story.reshare_story(form_url, shots, post_url=post_url, music=music, like_repost=like_repost,
                                                 expect_cover=cards[0] if cards else None,
                                                 publish=publish)
                    parts = []
                    if res["ok"]:
                        if res["outcome"] == "published":
                            parts.append("限動已發佈（你在儀表板勾選並確認）。")
                        elif res["outcome"] == "draft":
                            parts.append("已存成限動草稿（沒有分享）。")
                        else:
                            parts.append("限動已準備好，停在編輯畫面：這台 IG 沒有限動草稿，請在手機上確認後自己按「限時動態」。程式沒有分享。")
                        parts.append("連結貼圖在右上角。" if res["sticker_ok"] else "⚠ 連結貼圖沒對準右上角，請手動拖。")
                        if res["music_error"]:
                            parts.append(f"⚠ {res['music_error']}，沒有加音樂。")
                        elif res.get("music_warn"):
                            parts.append(f"⚠ {res['music_warn']}。")
                        elif not music:
                            parts.append("限動沒有設定音樂。")
                        lr = res.get("like_repost")
                        if lr:
                            parts.append(f"按讚：{'已開' if lr['liked'] else '未確認'}；轉發：{'已開' if lr['reposted'] else '未確認'}。")
                        status = "warn" if (res["music_error"] or res.get("music_warn") or not res["sticker_ok"]) else "done"
                    else:
                        parts.append("沒有完成：" + res["error"])
                        status = "failed"
                    rec = {"status": status, "message": "".join(parts), "shots": res["shots"], "log": res["log"],
                           "at": res["at"], "outcome": res.get("outcome"), "seconds": res.get("seconds")}
                except Exception as e:
                    rec = {"status": "failed", "message": f"沒有完成：{e}", "shots": []}
                with _JOBS_LOCK:
                    st = load_state(slug)
                    st["ig_story"] = rec
                    save_state(slug, st)

            t = threading.Thread(target=work, daemon=True, name="autopilot-ig-story")
            _JOBS[jk] = t
            t.start()
        return jsonify({"ok": True, "ig_story": ig_story_view(slug, load_state(slug))})

    @app.get("/ig-shot/<slug>/<name>")
    def ig_shot(slug, name):
        if "/" in name or "\\" in name or not name.endswith(".png"):
            abort(404)
        d = out_root / slug / ("ig_story_shots" if request.args.get("d") == "story" else "ig_shots")
        if not (d / name).is_file():
            abort(404)
        return _send(d, name)

    @app.post("/event/<slug>/rules")
    def add_rule(slug):
        try:
            event_path(slug)
        except FileNotFoundError:
            abort(404)
        try:
            rule = checker.add_custom_rule({
                "name": request.form.get("name", ""),
                "target": request.form.get("target", "all"),
                "kind": request.form.get("kind", "forbid"),
                "pattern": request.form.get("pattern", ""),
                "regex": request.form.get("regex") in ("1", "on", "true"),
                "level": request.form.get("level", "error"),
            })
        except ValueError as e:
            return redirect(url_for("show_event", slug=slug, msg=f"規則沒有加入：{e}"))
        r = run_pipeline(event_path(slug), "preview", out_root)
        n = (r.manifest.get("check") or {}).get("error", 0)
        state = load_state(slug)
        save_state(slug, state)
        tail = "全部通過。" if not n else f"目前有 {n} 項違規。"
        return redirect(url_for("show_event", slug=slug, msg=f"已加入規則「{rule['name']}」並重新檢查：{tail}"))

    # Backward-compatible aliases used by the previous UI; still drafts-only, never send.
    @app.post("/drafts/<slug>")
    def drafts_alias(slug):
        start_action(slug, "drafts")
        return redirect(url_for("show_event", slug=slug, msg="正在建立 Gmail 草稿（不會寄出）…"))

    @app.post("/linktree/<slug>")
    def linktree_alias(slug):
        start_action(slug, "linktree")
        return redirect(url_for("show_event", slug=slug, msg="正在背景同步 Linktree…"))

    @app.get("/onboarding/template.yaml")
    def onboarding_template():
        body = launchpad.blank_template_yaml()
        return Response(
            body,
            mimetype="text/yaml; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=autopilot_local.template.yaml"},
        )

    @app.get("/onboarding")
    def onboarding_page():
        try:
            store = _store()
        except launchpad.UnsafeConfigPath:
            abort(400)
        return _onboarding_body(store, store.load_progress())

    @app.post("/onboarding")
    def onboarding_submit():
        try:
            store = _store()
        except launchpad.UnsafeConfigPath:
            abort(400)
        progress = store.load_progress()
        progress, errors = launchpad.handle_action(progress, request.form, store)
        store.save_progress(progress)
        if errors:
            return _onboarding_body(store, progress, errors), 400
        return redirect(url_for("onboarding_page"))

    @app.get("/modules")
    def modules_page():
        try:
            store = _store()
        except launchpad.UnsafeConfigPath:
            abort(400)
        focus = request.args.get("focus") or ""
        notice = request.args.get("notice") or ""
        return render_page("modules.html", **launchpad.modules_view(store.load_modules(), focus=focus, notice=notice))

    @app.post("/modules/<module_id>")
    def modules_submit(module_id):
        if module_id not in launchpad.MODULE_IDS:
            abort(404)
        try:
            store = _store()
        except launchpad.UnsafeConfigPath:
            abort(400)
        state = store.load_modules()
        state, banner = launchpad.handle_module(state, module_id, request.form)
        if banner.get("errors"):
            view = launchpad.modules_view(state, focus=module_id, banner=banner)
            return render_page("modules.html", **view), 400
        store.save_modules(state)
        action = (request.form.get("action") or "").strip()
        if action == "toggle":
            notice = "on" if state[module_id]["enabled"] else "off"
        elif action == "test":
            notice = "tested"
        elif action == "save":
            notice = "saved"
        else:
            notice = ""
        return redirect(url_for("modules_page", focus=module_id, notice=notice) + "#mod-" + module_id)

    def _remember_line_bot(info: dict, token: str, secret: str) -> None:
        from . import line_capture
        from .launchpad_i18n import T as ui
        line_capture.save_secrets(token, secret)
        try:
            store = _store()
        except launchpad.UnsafeConfigPath:
            return
        progress = store.load_progress()
        bucket = progress["services"]["line"]
        bucket["token_saved"] = "yes"
        bucket["bot_name"] = info.get("display_name") or ""
        bucket["bot_picture"] = info.get("picture_url") or ""
        bucket["token_mask"] = info.get("masked") or ""
        bucket["last_check"] = {"status": "ok", "message": ui["line_token_ok"]}
        store.save_progress(progress)

    @app.post("/api/line/bot-info")
    def line_bot_info():
        from . import line_capture
        body = request.get_json(silent=True) or {}
        token = str(body.get("token") or "")
        secret = str(body.get("secret") or "")
        info = line_capture.bot_info(token)
        if info.get("ok") and secret.strip():
            _remember_line_bot(info, token, secret)
        elif info.get("ok"):
            _remember_line_bot(info, token, "")
        safe = {k: v for k, v in info.items() if k != "masked"}
        safe["masked"] = info.get("masked") or ""
        return jsonify(safe), (200 if info.get("ok") else 400)

    @app.post("/api/line/capture/start")
    def line_capture_start():
        from . import line_capture
        port = int(request.environ.get("SERVER_PORT") or 5050)
        result = line_capture.begin_capture(port)
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.get("/api/line/capture/status")
    def line_capture_status():
        from . import line_capture
        data = line_capture.snapshot()
        return jsonify({
            "group_id": data.get("group_id") or "",
            "group_name": data.get("group_name") or "",
            "mode": data.get("mode") or "",
            "message": data.get("error") or "",
        })

    @app.post("/api/line/capture/confirm")
    def line_capture_confirm():
        from . import line_capture
        from .launchpad_i18n import T as ui
        result = line_capture.finish_capture()
        if result.get("group_id"):
            try:
                store = _store()
                progress = store.load_progress()
                bucket = progress["services"]["line"]
                bucket["group_id"] = result["group_id"]
                bucket["group_name"] = result.get("group_name") or bucket.get("group_name") or ""
                bucket["last_check"] = {"status": "ok", "message": ui["line_group_saved"]}
                store.save_progress(progress)
            except launchpad.UnsafeConfigPath:
                pass
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.post("/api/line/webhook")
    def line_webhook():
        from . import line_capture
        raw = request.get_data() or b""
        secret = str(line_capture.load_secrets().get("channel_secret") or "")
        header = request.headers.get("X-Line-Signature") or ""
        if not line_capture.signature_ok(raw, secret, header):
            return jsonify({"ok": False}), 401
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return jsonify({"ok": False}), 400
        if not isinstance(payload, dict):
            return jsonify({"ok": False}), 400
        groups = line_capture.group_ids_from_payload(payload)
        if groups:
            line_capture.remember_group(groups[0])
        return jsonify({"ok": True})

    @app.post("/api/ig/detect")
    def ig_detect():
        from .phone_setup import detect
        return jsonify(detect())

    return app


def _form_links(state: dict) -> list[dict]:
    links = []
    if state.get("signup", {}).get("edit_url"):
        links.append({"label": "課前表單編輯頁", "href": state["signup"]["edit_url"]})
    if state.get("signup", {}).get("responder_url"):
        links.append({"label": "課前填寫頁", "href": state["signup"]["responder_url"]})
    if state.get("feedback", {}).get("edit_url"):
        links.append({"label": "課後表單編輯頁", "href": state["feedback"]["edit_url"]})
    if state.get("feedback", {}).get("responder_url"):
        links.append({"label": "課後填寫頁", "href": state["feedback"]["responder_url"]})
    return links


def _draft_links(state: dict) -> list[dict]:
    from .local_settings import get as _setting
    inbox = str(_setting("club_email", "CLUB_GOOGLE_ACCOUNT", "") or "").strip()
    href = "https://mail.google.com/mail/u/#drafts"
    if "@" in inbox:
        href = f"https://mail.google.com/mail/u/?authuser={inbox}#drafts"
    links = []
    for d in state.get("drafts") or []:
        did = d.get("draft_id")
        if not did:
            continue
        label = d.get("label") or d.get("file") or "草稿"
        if not str(label).endswith("草稿"):
            label = f"{label}草稿"
        links.append({"label": label, "href": href})
    return links


def _draft_message(state: dict) -> str:
    parts = []
    for d in state.get("drafts") or []:
        if d.get("draft_id"):
            bcc = f"，密件副本 {d['bcc_count']} 人" if isinstance(d.get("bcc_count"), int) else ""
            if d.get("excluded_count"):
                bcc += f"（已排除 {d['excluded_count']} 人）"
            parts.append(f"{d.get('label') or d.get('file')} {d['draft_id']}{bcc}")
    extra = "｜".join(parts)
    base = "3 封草稿已在社團信箱（未寄出）"
    return f"{base}：{extra}" if extra else base


app = create_app()
