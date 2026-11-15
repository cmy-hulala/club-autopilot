"""Feature 12: Linktree sync — add this week's form link, hide last week's.

Linktree has no public API for editing links, so this drives linktr.ee/admin/links with Playwright
in a *headless* Chrome (no window, no focus stealing) that uses its own profile folder outside the repo.
Signing in is a one-time human step: `python -m autopilot.linktree login` (run_linktree_login.cmd)
opens a plain Chrome window on that profile; password / Google / 2FA are always done by a human.
Older 課前表單 links are hidden (card menu → Hide), never archived or deleted.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from .event import Event

PROFILE = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "club-autopilot" / "linktree-profile"
ADMIN = "https://linktr.ee/admin"
OLD_LINK_PATTERN = r"陽明交大創創社｜\d{2}/\d{2} 課前表單"


def current_public_links(public_url: str) -> list[dict]:
    """Read-only: list the links currently shown on the public Linktree page."""
    import urllib.request
    try:
        try:
            html = urllib.request.urlopen(urllib.request.Request(public_url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read().decode("utf-8", "ignore")
        except Exception:  # Linktree's CDN rejects Python's TLS fingerprint; curl (bundled with Windows 10+) works
            import subprocess
            html = subprocess.run(["curl", "-s", public_url], capture_output=True, timeout=30).stdout.decode("utf-8", "ignore")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        links = json.loads(m.group(1))["props"]["pageProps"]["links"] if m else []
        return [{"title": l.get("title"), "url": l.get("url")} for l in links if l.get("title")]
    except Exception as e:
        return [{"error": f"{e.__class__.__name__}"}]


def plan(ev: Event, form_url: str, read_public: bool = True) -> dict:
    title = ev["signup_form_title"]
    public = current_public_links(ev["club"].get("linktree_url", "")) if read_public and ev["club"].get("linktree_url") else []
    to_hide = [l for l in public if l.get("title") and re.search(OLD_LINK_PATTERN, l["title"]) and l["title"] != title]
    return {
        "public_page": ev["club"].get("linktree_url"),
        "current_links": public,
        "will_hide": to_hide,
        "add": {"title": title, "url": form_url},
        "hide_where_title_matches": OLD_LINK_PATTERN,
        "keep_visible": [form_url],
        "profile_dir": str(PROFILE),
        "needs_human": ["第一次要執行 run_linktree_login.cmd，用社團 Linktree 帳號登入（不是個人帳號）；登入狀態會保存在這個資料夾以外的 profile，之後同步都在背景跑"],
    }


CARD_JS = """t => { let e = t;
  while (e.parentElement && e.parentElement.querySelectorAll('[data-testid=content-block-menu-trigger]').length === 1) e = e.parentElement;
  return e; }"""


class NotLoggedIn(RuntimeError):
    pass


def _cards(page) -> list[dict]:
    """Every link/collection card in the editor: its first-line title, full text, and whether it is hidden."""
    out = []
    trig = page.get_by_test_id("content-block-menu-trigger")
    for i in range(trig.count()):
        t = trig.nth(i)
        card = t.evaluate_handle(CARD_JS).as_element()
        text = (card.inner_text() or "").strip()
        hidden = card.query_selector("button[aria-label='Show link']") is not None
        out.append({"i": i, "title": text.splitlines()[0] if text else "", "text": text, "hidden": hidden})
    return out


def _open_editor(page, timeout_ms: int = 30000) -> None:
    page.goto(ADMIN + "/links", wait_until="domcontentloaded", timeout=60000)
    try:
        page.get_by_test_id("LinksEditor").wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        url = page.url
        if "/admin" not in url or re.search(r"login|signin|auth", url, re.I):
            raise NotLoggedIn(url)
        # Older/other layouts: the dashboard has an "Edit" button that opens the editor.
        page.goto(ADMIN, wait_until="domcontentloaded")
        page.get_by_role("button", name="Edit", exact=True).first.click()
        page.get_by_test_id("LinksEditor").wait_for(state="visible", timeout=timeout_ms)
    page.wait_for_timeout(2500)  # cards render after the editor shell


def launch(pw, headless: bool = True):
    kw = dict(headless=headless, viewport={"width": 1280, "height": 1000})
    if headless:
        kw["args"] = ["--headless=new"]  # never shows a window or takes focus
    try:  # prefer the installed Google Chrome; fall back to Playwright's bundled Chromium
        return pw.chromium.launch_persistent_context(str(PROFILE), channel="chrome", **kw)
    except Exception:
        return pw.chromium.launch_persistent_context(str(PROFILE), **kw)


LOGIN_HELP = ("Linktree 尚未登入。請執行 run_linktree_login.cmd（會開一般的 Chrome 視窗），"
              "用社團帳號 Continue with Google 登入後關掉視窗，再同步一次。")


def login_window() -> None:
    """Open a *plain* Chrome (not automation-controlled, so Google sign-in works) on the program's profile."""
    import shutil
    import subprocess
    chrome = shutil.which("chrome") or next((str(c) for c in [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ] if c.is_file()), "chrome")
    PROFILE.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([chrome, f"--user-data-dir={PROFILE}", "--no-first-run", "https://linktr.ee/login"])


def run(p: dict, shots: Path, headless: bool = True, login_wait_s: int = 0) -> dict:
    """Add this week's form link (skip if present) and hide — never delete — older 課前表單 links.

    Runs headless by default. Writes before/after screenshots and linktree_result.json into `shots`.
    """
    from playwright.sync_api import sync_playwright

    if "{{" in p["add"]["url"]:
        sys.exit("表單還沒建好（網址是佔位符），先跑 Google 步驟再同步 Linktree。")
    shots.mkdir(parents=True, exist_ok=True)
    title, url = p["add"]["title"], p["add"]["url"]
    form_id = re.search(r"/forms/d/e/([^/]+)", url)
    log: list[str] = []

    def is_new(c: dict) -> bool:
        return title in c["text"] or url in c["text"] or bool(form_id and form_id.group(1) in c["text"])

    with sync_playwright() as pw:
        ctx = launch(pw, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            try:
                _open_editor(page)
            except NotLoggedIn:
                page.screenshot(path=str(shots / "linktree_not_logged_in.png"))
                result = {"ok": False, "logged_in": False, "message": LOGIN_HELP}
                (shots / "linktree_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                print(LOGIN_HELP, flush=True)
                return {"log": [LOGIN_HELP], **result}
            page.screenshot(path=str(shots / "linktree_01_before.png"), full_page=True)
            before = _cards(page)

            # 1) add the new link unless it is already there
            if any(is_new(c) for c in before):
                log.append("已存在，略過新增：" + title)
            else:
                page.get_by_role("button", name="Add", exact=True).first.click()
                box = page.get_by_placeholder(re.compile("Paste a link", re.I)).first
                box.wait_for(state="visible", timeout=15000)
                box.fill(url)
                pick = page.get_by_role("dialog").get_by_role("button", name=re.compile(r"^Link")).first
                pick.wait_for(state="visible", timeout=20000)
                pick.click()
                page.wait_for_timeout(4000)
                page.keyboard.press("Escape")
                page.wait_for_timeout(1000)
                log.append("新增：" + title)

            # rename the new card if Linktree fetched a different title
            cur = _cards(page)
            mine = next((c for c in cur if is_new(c)), None)
            if mine and mine["title"] != title:
                try:
                    page.get_by_test_id("content-block-menu-trigger").nth(mine["i"]).click()
                    page.get_by_role("menuitem", name="Edit", exact=True).click()
                    tb = page.get_by_role("dialog").get_by_role("textbox").first
                    tb.fill(title); tb.press("Enter"); page.wait_for_timeout(1500)
                    page.keyboard.press("Escape")
                    log.append("改標題：" + title)
                except Exception as e:  # cosmetic; not fatal
                    log.append(f"標題沒自動改到（{e.__class__.__name__}），可手動改")

            # 2) hide older 課前表單 links (Hide in the card menu; never Archive/Delete).
            # Reload first: a just-added card has no menu yet, which would merge it with its neighbour.
            _open_editor(page)
            for c in _cards(page):
                if is_new(c) or c["hidden"] or not re.search(p["hide_where_title_matches"], c["title"]):
                    continue
                page.get_by_test_id("content-block-menu-trigger").nth(c["i"]).click()
                page.get_by_role("menuitem", name="Hide", exact=True).click()
                page.wait_for_timeout(2000)
                log.append("隱藏：" + c["title"])

            # verify from a fresh load
            _open_editor(page)
            after = _cards(page)
            page.screenshot(path=str(shots / "linktree_02_after.png"), full_page=True)
            mine = next((c for c in after if is_new(c)), None)
            stale = [c["title"] for c in after if not is_new(c) and not c["hidden"]
                     and re.search(p["hide_where_title_matches"], c["title"])]
            result = {
                "ok": bool(mine and not mine["hidden"] and not stale),
                "logged_in": True,
                "added": {"title": mine["title"], "visible": not mine["hidden"]} if mine else None,
                "hidden": [c["title"] for c in after if c["hidden"] and re.search(p["hide_where_title_matches"], c["title"])],
                "still_visible_old": stale,
                "cards_before": len(before), "cards_after": len(after),
                "log": log,
                "at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
                "shots": ["linktree_01_before.png", "linktree_02_after.png"],
            }
            (shots / "linktree_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"log": log, **result}
        finally:
            ctx.close()


if __name__ == "__main__":
    from .event import load_event

    if sys.argv[1:2] == ["login"]:
        login_window(); sys.exit(0)
    ev = load_event(sys.argv[1])
    print(json.dumps(plan(ev, ev["form_url"]), ensure_ascii=False, indent=2))
