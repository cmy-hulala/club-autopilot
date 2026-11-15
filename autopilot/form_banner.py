"""Set the club header banner on a Google Form via the editor UI.

The Forms API cannot set a header image. The official path is:

    edit page → top-right palette (Customize theme) → Header → Upload

Do not use imageItem / Insert → Image, and do not pick a stock
「選擇標題圖片」 gallery image. Forms are always edited with the personal
account (AUTOPILOT_FORMS_ACCOUNT / local config, see local_settings.py);
the club account must never be used.

Optional confirmation text is written only when the caller supplies it
(the pipeline passes the wording from the event file / 1_報名表單.md and
5_課後表單.md). Questions and other settings are left untouched.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from .local_settings import forms_account as _forms_account

DEFAULT_ACCOUNT = _forms_account().lower()
DESKTOP_BANNER = Path.home() / "OneDrive" / "Desktop" / "創創社" / "模板" / "表單Banner-nycuiec.jpg"
CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path.home() / "AppData" / "Local" / "Google" / "Chrome" / "Application" / "chrome.exe",
)


@dataclass
class BannerJob:
    form_id: str
    confirmation: str | None = None
    responder_url: str | None = None
    kind: str = ""
    edit_url: str = ""
    result: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.form_id = (self.form_id or "").strip()
        if not self.form_id:
            raise ValueError("form_id is required")
        if not self.edit_url:
            self.edit_url = edit_url(self.form_id)
        if self.confirmation:
            self.confirmation = self.confirmation.strip() or None


def edit_url(form_id: str) -> str:
    return f"https://docs.google.com/forms/d/{form_id}/edit"


def default_banner() -> Path:
    env = os.environ.get("CLUB_FORM_BANNER")
    if env:
        return Path(env)
    repo = ROOT / "assets" / "form_banner.jpg"
    if repo.is_file():
        return repo
    return DESKTOP_BANNER


def forbidden_accounts(*extra: str) -> set[str]:
    """Inboxes that must not edit a form. Comes from saved settings and the event file, not a fixed address."""
    found: set[str] = set()
    try:
        from .local_settings import club_email
        saved = club_email().strip().lower()
    except Exception:
        saved = ""
    if "@" in saved:
        found.add(saved)
    for item in extra:
        text = str(item or "").strip().lower()
        if "@" in text:
            found.add(text)
    return found


def default_profile() -> Path:
    env = os.environ.get("CLUB_FORM_BANNER_PROFILE")
    if env:
        return Path(env)
    from .local_settings import private_dir
    return private_dir() / "chrome-form-banner"


def confirmation_from_spec(text: str) -> str | None:
    """Extract the confirmation sentence from a 1_報名表單.md / 5_課後表單.md body."""
    m = re.search(r"##\s*確認訊息\s*`([^`]+)`", text)
    return m.group(1).strip() if m else None


def assert_black_banner(image: Path) -> None:
    if not image.is_file():
        raise SystemExit(f"找不到 Banner 圖片：{image}")
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(image) as im:
        im = im.convert("RGB")
        w, h = im.size
        strip = im.crop((0, 0, w, max(1, min(40, h))))
        r, g, b = strip.resize((1, 1)).getpixel((0, 0))
        avg = (r + g + b) / 3
    if avg > 40:
        raise SystemExit(f"Banner 看起來不是黑底（上緣平均亮度 {avg:.0f}）：{image}")


def jobs_from_state(state: dict, ev=None) -> list[BannerJob]:
    jobs: list[BannerJob] = []
    mapping = (
        ("signup", "signup_confirmation"),
        ("feedback", "feedback_confirmation"),
    )
    for kind, conf_key in mapping:
        info = state.get(kind) or {}
        fid = info.get("formId")
        if not fid:
            continue
        conf = None
        if ev is not None:
            conf = ev[conf_key]
        jobs.append(BannerJob(
            form_id=fid,
            confirmation=conf,
            responder_url=info.get("responder_url"),
            kind=kind,
            edit_url=info.get("edit_url") or edit_url(fid),
        ))
    if not jobs:
        raise SystemExit("state.json 裡沒有課前或課後表單 ID。")
    return jobs


def jobs_from_event(event_path: Path, out_root: Path | None = None) -> list[BannerJob]:
    from .event import load_event

    event_path = Path(event_path)
    ev = load_event(event_path)
    root = Path(out_root or ROOT / "out")
    state_path = root / event_path.stem / "state.json"
    if not state_path.is_file():
        raise SystemExit(f"找不到 {state_path}，請先建表，或改用 --form-id。")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    return jobs_from_state(state, ev)


def _same_account(a: str, b: str) -> bool:
    return a.strip().lower() == b.strip().lower()


def _chrome_exe() -> Path:
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).is_file():
        return Path(env)
    for p in CHROME_CANDIDATES:
        if p.is_file():
            return p
    raise SystemExit("找不到 Google Chrome，無法開表單編輯頁。")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chrome_pids_for_profile(profile: Path) -> list[int]:
    if os.name != "nt":
        return []
    needle = str(profile).replace("'", "''")
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{needle}*' }} | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd],
            text=True, stderr=subprocess.DEVNULL, timeout=15,
        )
    except Exception:
        return []
    pids = []
    for line in out.split():
        if line.strip().isdigit():
            pids.append(int(line.strip()))
    return pids


def _stop_profile_chrome(profile: Path) -> None:
    """Close only Chrome processes that use this dedicated profile, never the user's everyday Chrome."""
    pids = _chrome_pids_for_profile(profile)
    if not pids:
        return
    print(f"先關掉這個 Banner 設定檔裡已開著的 Chrome（{len(pids)} 個程序），不影響你平常的瀏覽器。")
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(20):
        if not _chrome_pids_for_profile(profile):
            return
        time.sleep(0.25)


def _wait_cdp(port: int, timeout: float = 20) -> None:
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).read()
            return
        except Exception as e:
            last = e
            time.sleep(0.25)
    raise SystemExit(f"Chrome 除錯埠沒開起來（:{port}）：{last}")


def _shot(page, shots: Path, name: str) -> Path | None:
    shots.mkdir(parents=True, exist_ok=True)
    path = shots / name
    try:
        page.screenshot(path=str(path), full_page=False)
        print(f"  截圖 {path}")
        return path
    except Exception as e:
        print(f"  截圖失敗 {name}: {e}")
        return None


def _click_first(page, selectors: list[str], timeout: int = 4000) -> str | None:
    for sel in selectors:
        loc = page.locator(sel)
        try:
            n = loc.count()
        except Exception:
            continue
        for i in range(n):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                box = el.bounding_box()
                if not box or box["width"] < 4 or box["height"] < 4:
                    continue
                el.click(timeout=timeout)
                return sel
            except Exception:
                continue
    return None


def _click_named(page, names: tuple[str, ...], *, toolbar: bool = False, timeout: int = 4000) -> str | None:
    """Click a visible button whose aria-label, tooltip, or accessible name matches (case-insensitive)."""
    lowered = [n.lower() for n in names]
    # CSS attribute selectors are case-sensitive unless the i flag is used.
    sels = []
    for n in names:
        sels.append(f'[aria-label="{n}" i]')
        sels.append(f'[data-tooltip="{n}" i]')
    hit = None
    loc = page.locator(", ".join(sels))
    try:
        n = loc.count()
    except Exception:
        n = 0
    for i in range(n):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            box = el.bounding_box()
            if not box or box["width"] < 4 or box["height"] < 4:
                continue
            if toolbar and box["y"] > 80:
                continue
            hit = (el, box, el.get_attribute("aria-label") or el.get_attribute("data-tooltip") or names[0])
            if toolbar:
                break
        except Exception:
            continue
    if hit is None:
        for name in names:
            try:
                el = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I)).first
                if el.count() and el.is_visible():
                    box = el.bounding_box()
                    if box:
                        hit = (el, box, name)
                        break
            except Exception:
                continue
    if hit is None:
        return None
    el, box, label = hit
    print(f"  點擊「{label}」 at ({box['x']:.0f},{box['y']:.0f})")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    return str(label)


def _click_text(page, texts: tuple[str, ...], timeout: int = 2000) -> bool:
    for text in texts:
        try:
            page.get_by_text(text, exact=True).first.click(timeout=timeout)
            return True
        except Exception:
            try:
                page.get_by_role("button", name=text).first.click(timeout=timeout)
                return True
            except Exception:
                continue
    return False


def _collect_emails(page) -> list[str]:
    emails: list[str] = []
    try:
        labels = page.evaluate(
            """() => Array.from(document.querySelectorAll('[aria-label], [data-email], [data-identifier]'))
                .map(el => (el.getAttribute('aria-label') || el.getAttribute('data-email') || el.getAttribute('data-identifier') || ''))
                .filter(Boolean)"""
        )
        for text in labels or []:
            emails.extend(re.findall(r"[\w.+-]+@gmail\.com", text, re.I))
    except Exception:
        pass
    try:
        html = page.content()
        emails.extend(re.findall(r"[\w.+-]+@gmail\.com", html, re.I))
    except Exception:
        pass
    # de-dupe, keep order
    seen = set()
    out = []
    for e in emails:
        k = e.lower()
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def _signed_in_email(page) -> str | None:
    emails = _collect_emails(page)
    if not emails:
        return None
    # Prefer a personal gmail over workspace-looking addresses if several appear
    for e in emails:
        if e.lower() == DEFAULT_ACCOUNT:
            return e
    return emails[0]


def _editor_ready(page) -> bool:
    url = page.url or ""
    if "/edit" not in url or "accounts.google.com" in url:
        return False
    # Responder / request-access views are not the editor.
    try:
        if page.get_by_text(re.compile(r"要求編輯權限|Request edit access")).count():
            # The phrase can theoretically appear elsewhere; require the palette too.
            palette = page.locator(
                '[aria-label="Customize theme"], [aria-label="自訂主題"], [data-tooltip="Customize theme"], [data-tooltip="自訂主題"]'
            )
            if not palette.count():
                return False
    except Exception:
        pass
    try:
        palette = page.locator(
            '[aria-label="Customize theme"], [aria-label="自訂主題"], [data-tooltip="Customize theme"], [data-tooltip="自訂主題"]'
        )
        if palette.count():
            return True
    except Exception:
        pass
    # Send / 傳送 button is a reliable editor chrome marker
    try:
        if page.get_by_role("button", name=re.compile(r"^(Send|傳送)$")).count():
            return True
    except Exception:
        pass
    return "/edit" in url and "viewform" not in url


def _dump_labels(page, limit: int = 80) -> list[str]:
    try:
        labels = page.evaluate(
            f"""() => Array.from(document.querySelectorAll('[aria-label],button,[role="button"],[role="tab"]'))
              .slice(0, {limit})
              .map(el => (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\\s+/g, ' '))
              .filter(t => t)
            """
        )
        return list(labels or [])
    except Exception:
        return []


def _ensure_account(page, required: str, login_wait_s: int, shots: Path) -> str:
    forbidden = forbidden_accounts()
    required_l = required.lower()
    deadline = time.time() + login_wait_s
    warned_login = False
    warned_wrong = False
    while time.time() < deadline:
        url = page.url or ""
        if "accounts.google.com" in url or "signin" in url.lower():
            if not warned_login:
                print(f"請在打開的 Chrome 視窗用個人帳號 {required} 登入（不要用社團信箱）。登入後程式會自動繼續，最多等 {login_wait_s // 60} 分鐘…")
                warned_login = True
            _shot(page, shots, "login-wait.png")
            page.wait_for_timeout(2000)
            continue
        email = _signed_in_email(page)
        if email and email.lower() in forbidden:
            if not warned_wrong:
                print(f"目前登入的是社團帳號 {email}。請切換成個人帳號 {required}，不要用社團帳號編輯表單。程式會等你切換。")
                warned_wrong = True
            _shot(page, shots, "wrong-account.png")
            page.wait_for_timeout(3000)
            continue
        if email and not _same_account(email, required):
            if not warned_wrong:
                print(f"目前登入的是 {email}，請切換成 {required}（不要用社團帳號）。程式會等你切換。")
                warned_wrong = True
            _shot(page, shots, "wrong-account.png")
            page.wait_for_timeout(3000)
            continue
        if _editor_ready(page) and email and _same_account(email, required):
            print(f"已確認登入帳號：{email}")
            return email
        if _editor_ready(page) and not email:
            # Editor is up but the email isn't in the DOM yet; try once more after a short wait.
            page.wait_for_timeout(1500)
            email = _signed_in_email(page)
            if email and _same_account(email, required):
                print(f"已確認登入帳號：{email}")
                return email
            if email and email.lower() in forbidden:
                continue
            if email and not _same_account(email, required):
                continue
            # Last resort: editor is ready, no email found. Do not proceed silently.
            print(f"編輯頁已開啟，但還讀不到登入帳號；請確認右上角是 {DEFAULT_ACCOUNT}。")
            _shot(page, shots, "account-unknown.png")
            page.wait_for_timeout(3000)
            continue
        page.wait_for_timeout(1500)
    raise SystemExit(f"等待以 {required} 登入並開啟表單編輯頁逾時。")


def _choose_image_button(page):
    """The Theme panel's Header → Choose image control (not the Text style 'Header' font box)."""
    locs = (
        page.get_by_role("button", name=re.compile(r"Choose image", re.I)),
        page.locator('[role="button"]:has-text("Choose image")'),
        page.locator('button:has-text("Choose image")'),
        page.get_by_text(re.compile(r"^Choose image$", re.I)),
        page.get_by_role("button", name=re.compile(r"選擇圖片")),
        page.locator('[role="button"]:has-text("選擇圖片")'),
        page.get_by_text(re.compile(r"^選擇圖片$")),
    )
    for loc in locs:
        try:
            el = loc.first
            if el.count() and el.is_visible():
                box = el.bounding_box()
                if box and box["width"] >= 8 and box["height"] >= 8:
                    return el, box
        except Exception:
            continue
    return None, None


def _theme_panel_visible(page) -> bool:
    # 'Header' also labels the font dropdown; require Choose image (or a Theme panel title).
    el, _ = _choose_image_button(page)
    if el is not None:
        return True
    try:
        loc = page.get_by_text(re.compile(r"^Theme$|^主題$"))
        return bool(loc.count() and loc.first.is_visible())
    except Exception:
        return False


def _open_theme_panel(page) -> None:
    if _theme_panel_visible(page):
        return
    clicked = _click_named(
        page,
        ("Customize Theme", "Customize theme", "Customise Theme", "自訂主題"),
        toolbar=True,
        timeout=5000,
    )
    if not clicked:
        labels = _dump_labels(page)
        print("找不到調色盤按鈕。畫面上的標籤：", labels[:40], file=sys.stderr)
        raise SystemExit("找不到右上角調色盤（Customize Theme / 自訂主題）。")
    # The theme side panel can take a moment to render.
    for _ in range(15):
        page.wait_for_timeout(400)
        if _theme_panel_visible(page):
            return
    raise SystemExit("已點調色盤，但沒有出現 Theme / Header 面板。")


def _open_header_picker(page) -> None:
    # Official path: Theme panel → Header → Choose image → Upload.
    # Never click the Text style "Header" font dropdown (that was a previous miss).
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:
        pass
    if not _theme_panel_visible(page):
        _open_theme_panel(page)
    el, box = _choose_image_button(page)
    if el is None:
        raise SystemExit("主題面板已開，但找不到 Header 的 Choose image。")
    print(f"  點擊 Choose image at ({box['x']:.0f},{box['y']:.0f})")
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    # The picker dialog often appears blank for a few seconds, then fills an iframe.
    page.wait_for_timeout(500)


def _frames(page):
    try:
        return list(page.frames)
    except Exception:
        return []


def _frame_click_upload(frame) -> bool:
    names = (
        re.compile(r"^Upload$|^Uploads$|^上傳$|^已上傳$", re.I),
    )
    for loc in (
        frame.get_by_role("tab", name=re.compile(r"Upload|上傳", re.I)),
        frame.get_by_role("button", name=re.compile(r"Upload|上傳", re.I)),
        frame.get_by_text(re.compile(r"^Upload$|^Uploads$|^上傳$|^已上傳$", re.I)),
        frame.locator('[role="tab"]:has-text("Upload"), [role="tab"]:has-text("上傳")'),
        frame.locator('button:has-text("Upload"), button:has-text("上傳")'),
    ):
        try:
            el = loc.first
            if el.count() and el.is_visible():
                el.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def _frame_file_input(frame):
    try:
        inp = frame.locator('input[type="file"]')
        if inp.count():
            return inp.first
    except Exception:
        pass
    return None


def _visible_text(frame_or_page, pattern: str, timeout: float = 0):
    loc = frame_or_page.get_by_text(re.compile(pattern, re.I))
    try:
        n = loc.count()
    except Exception:
        return None, None
    for i in range(n):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            box = el.bounding_box()
            if box and box["width"] >= 8 and box["height"] >= 8:
                return el, box
        except Exception:
            continue
    return None, None


def _wait_select_header(page, timeout_s: float = 20):
    """Wait until the Select Header dialog is actually painted (it starts blank)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        el, box = _visible_text(page, r"^Select Header$|^Select header$|^選擇頁首$|^選擇標題$")
        if el is not None:
            # Also wait until the Upload row is visible in the left nav.
            up, ubox = _visible_text(page, r"^Upload$|^上傳$")
            if up is not None:
                return up, ubox
        for fr in _frames(page):
            if fr == page.main_frame:
                continue
            el, box = _visible_text(fr, r"^Select Header$|^Select header$|^選擇頁首$|^選擇標題$")
            up, ubox = _visible_text(fr, r"^Upload$|^上傳$")
            if up is not None:
                return up, ubox
        page.wait_for_timeout(300)
    raise SystemExit("選擇頁首的視窗是空的，等不到可見的 Upload。")


def _set_banner_file(page, image: Path) -> None:
    image = image.resolve()
    from playwright.sync_api import TimeoutError as PWTimeout

    up, ubox = _wait_select_header(page, timeout_s=25)
    print(f"  點擊 Upload at ({ubox['x']:.0f},{ubox['y']:.0f})")
    page.mouse.click(ubox["x"] + ubox["width"] / 2, ubox["y"] + ubox["height"] / 2)
    page.wait_for_timeout(800)

    # After switching to Upload, a Browse / drop zone appears. The file input
    # may be hidden but attached in the same dialog (not the form's insert-image input).
    deadline = time.time() + 15
    while time.time() < deadline:
        # Prefer a file input that lives near the visible Browse button / Upload pane.
        for fr in _frames(page):
            browse, bbox = _visible_text(fr, r"^Browse$|^Browse files$|^瀏覽$|^上傳檔案$|^Select a file from your device$")
            inp = _frame_file_input(fr)
            if inp is not None and (browse is not None or _visible_text(fr, r"^Upload$|^上傳$")[0] is not None):
                # If this is the main editor frame, require Browse so we don't
                # feed the 'insert image' question control by mistake.
                if fr == page.main_frame and browse is None:
                    continue
                try:
                    inp.set_input_files(str(image))
                    print(f"  已選檔案 {image.name}")
                    return
                except Exception as e:
                    print(f"  set_input_files: {e}")
        # Click Browse if it's visible and wait for a native chooser.
        for fr in _frames(page):
            browse, bbox = _visible_text(fr, r"^Browse$|^Browse files$|^瀏覽$|^上傳檔案$")
            if browse is None:
                continue
            try:
                with page.expect_file_chooser(timeout=8000) as fc:
                    print(f"  點擊 Browse at ({bbox['x']:.0f},{bbox['y']:.0f})")
                    page.mouse.click(bbox["x"] + bbox["width"] / 2, bbox["y"] + bbox["height"] / 2)
                fc.value.set_files(str(image))
                print(f"  已選檔案 {image.name}")
                return
            except PWTimeout:
                continue
            except Exception as e:
                print(f"  Browse: {e}")
                continue
        page.wait_for_timeout(400)
    raise SystemExit("打不開 Banner 上傳（沒有檔案選擇視窗，也沒有 input[type=file]）。")


def _save_position_header(page, timeout_s: float = 30) -> None:
    """After the file is chosen, Forms opens a 'Position Header' crop dialog (it starts with a spinner)."""
    deadline = time.time() + timeout_s
    saw = False
    while time.time() < deadline:
        title, _ = _visible_text(page, r"^Position Header$|^Reposition Header$|^調整頁首$|^定位頁首$|^重新調整頁首$")
        if title is not None:
            saw = True
            save, sbox = _visible_text(page, r"^Save$|^儲存$")
            # Wait for the preview image to finish loading (spinner goes away).
            preview = False
            try:
                imgs = page.locator('[role="dialog"] img, [aria-modal="true"] img')
                for i in range(imgs.count()):
                    box = imgs.nth(i).bounding_box()
                    if box and box["width"] >= 80 and box["height"] >= 40:
                        preview = True
                        break
            except Exception:
                pass
            if save is not None and preview:
                print(f"  點擊 Position Header Save at ({sbox['x']:.0f},{sbox['y']:.0f})")
                page.mouse.click(sbox["x"] + sbox["width"] / 2, sbox["y"] + sbox["height"] / 2)
                page.wait_for_timeout(1200)
                # Wait for the dialog to close.
                for _ in range(20):
                    still, _ = _visible_text(page, r"^Position Header$|^Reposition Header$|^調整頁首$|^定位頁首$")
                    if still is None:
                        return
                    page.wait_for_timeout(200)
                return
        elif saw:
            return
        page.wait_for_timeout(400)
    if not saw:
        print("  沒有出現 Position Header，繼續（也許已經套用）")
        return
    raise SystemExit("Position Header 視窗一直在載入，或沒有按到儲存。")


def _close_theme_panel(page) -> None:
    # Click the Theme panel's X, not Escape (Escape cancels Position Header).
    try:
        close = page.locator('[aria-label="Close"], [aria-label="關閉"], [aria-label="Close theme"], [aria-label="關閉主題"]')
        # The Theme panel close is on the right; prefer a visible small X near "Theme".
        for i in range(close.count()):
            el = close.nth(i)
            if not el.is_visible():
                continue
            box = el.bounding_box()
            if box and box["x"] > 800:
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_timeout(400)
                return
    except Exception:
        pass
    try:
        heading = page.get_by_text(re.compile(r"^Theme$|^主題$"), exact=True).first
        if heading.count() and heading.is_visible():
            box = heading.bounding_box()
            if box:
                page.mouse.click(box["x"] + 280, box["y"] + box["height"] / 2)
                page.wait_for_timeout(400)
    except Exception:
        pass


def upload_header(page, image: Path, shots: Path, prefix: str) -> None:
    _shot(page, shots, f"{prefix}-01-editor.png")
    _open_theme_panel(page)
    _shot(page, shots, f"{prefix}-02-theme.png")
    _open_header_picker(page)
    try:
        _wait_select_header(page, timeout_s=25)
    except SystemExit:
        _shot(page, shots, f"{prefix}-03-picker.png")
        raise
    _shot(page, shots, f"{prefix}-03-picker.png")
    _set_banner_file(page, image)
    _save_position_header(page)
    _close_theme_panel(page)
    _shot(page, shots, f"{prefix}-04-banner.png")


def set_confirmation(page, text: str, shots: Path, prefix: str) -> None:
    # Current editor: Settings is a top tab; Presentation is a collapsed accordion.
    # The confirmation textarea exists in the DOM while collapsed, but is not visible.
    _close_theme_panel(page)
    opened = False
    try:
        tab = page.get_by_role("tab", name=re.compile(r"^Settings$|^設定$"))
        if tab.count() and tab.first.is_visible():
            tab.first.click(timeout=4000)
            opened = True
    except Exception:
        pass
    if not opened:
        opened = bool(_click_named(page, ("Settings", "設定"), timeout=4000))
    if not opened:
        raise SystemExit("找不到設定（Settings 分頁），確認訊息沒有改。")
    page.wait_for_timeout(800)
    # Presentation is a div accordion with no aria-expanded. Click the wide row,
    # then Edit (the confirmation textarea is hidden until then).
    row = page.evaluate(
        """() => {
          const nodes = [...document.querySelectorAll('div')];
          const hits = nodes.map(e => {
            const t = (e.innerText || '').trim();
            const r = e.getBoundingClientRect();
            const ok = (t.startsWith('Presentation') || t.startsWith('顯示') || t.startsWith('簡報'))
              && (t.includes('Manage how the form') || t.includes('顯示方式') || t.includes('presented'))
              && r.width > 400 && r.height > 30 && r.height < 100 && r.y > 0 && r.x >= 0;
            return ok ? {x:r.x, y:r.y, w:r.width, h:r.height} : null;
          }).filter(Boolean);
          if (!hits.length) return null;
          hits.sort((a,b) => a.h - b.h || a.w - b.w);
          return hits[0];
        }"""
    )
    print(f"  Presentation 列：{row}")
    if not row:
        _shot(page, shots, f"{prefix}-05-settings-fail.png")
        raise SystemExit("找不到 Presentation 區塊，確認訊息沒有改。")
    # Click the chevron on the right of the smallest matching row (not the wrapper).
    cx, cy = row["x"] + row["w"] - 18, row["y"] + row["h"] / 2
    print(f"  點擊 Presentation 箭頭 at ({cx:.0f},{cy:.0f})")
    page.mouse.click(cx, cy)
    page.wait_for_timeout(800)
    # Confirmation message sits under AFTER SUBMISSION, just below the fold.
    # Don't overscroll: a large wheel jump skips past the field.
    page.evaluate(
        """() => {
          const el = [...document.querySelectorAll('div,span')].find(e => {
            const t = (e.innerText || '').trim();
            return t === 'AFTER SUBMISSION' || t === '提交後' || t === '送出後';
          });
          if (el) el.scrollIntoView({block: 'start', behavior: 'instant'});
        }"""
    )
    page.wait_for_timeout(500)
    _shot(page, shots, f"{prefix}-05a-presentation.png")
    # Ignore "Edit" matches in the top toolbar (y is small).
    edit, ebox = None, None
    loc = page.get_by_text(re.compile(r"^Edit$|^編輯$"))
    try:
        n = loc.count()
    except Exception:
        n = 0
    for i in range(n):
        el = loc.nth(i)
        try:
            if not el.is_visible():
                continue
            box = el.bounding_box()
            if not box or box["width"] < 8 or box["height"] < 8:
                continue
            if box["y"] < 120:
                continue
            edit, ebox = el, box
            break
        except Exception:
            continue
    if edit is not None:
        print(f"  點擊 Edit at ({ebox['x']:.0f},{ebox['y']:.0f})")
        page.mouse.click(ebox["x"] + ebox["width"] / 2, ebox["y"] + ebox["height"] / 2)
        page.wait_for_timeout(600)
    box = page.get_by_label(re.compile(r"Confirmation message|確認訊息"))
    try:
        box.first.scroll_into_view_if_needed(timeout=4000)
        box.first.wait_for(state="visible", timeout=8000)
        box.first.click(timeout=4000)
        box.first.fill(text, timeout=8000)
    except Exception:
        _shot(page, shots, f"{prefix}-05-settings-fail.png")
        raise SystemExit("展開 Presentation 並按 Edit 後，仍找不到可見的確認訊息欄位。")
    page.wait_for_timeout(400)
    # After Edit, a Save button is shown; click it so the new wording sticks.
    if not _click_first(page, [
        'button:has-text("Save")',
        'button:has-text("儲存")',
    ], timeout=4000):
        save, sbox = _visible_text(page, r"^Save$|^儲存$")
        if save is not None:
            page.mouse.click(sbox["x"] + sbox["width"] / 2, sbox["y"] + sbox["height"] / 2)
    page.wait_for_timeout(500)
    _shot(page, shots, f"{prefix}-05-settings.png")
    # Return to the Questions tab so the next form starts from the editor.
    try:
        q = page.get_by_role("tab", name=re.compile(r"^Questions$|^問題$"))
        if q.count() and q.first.is_visible():
            q.first.click(timeout=3000)
            page.wait_for_timeout(400)
    except Exception:
        pass


def screenshot_responder(page, url: str, dest: Path, shots: Path, prefix: str) -> Path | None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        # The header is often a CSS background, not <img>. Capture the top of the page.
        page.screenshot(path=str(dest), full_page=False)
        print(f"  填寫頁頁首截圖 {dest}")
        _shot(page, shots, f"{prefix}-06-viewform.png")
        return dest
    except Exception as e:
        print(f"  填寫頁截圖失敗：{e}")
        _shot(page, shots, f"{prefix}-06-viewform.png")
        return None


def chrome_args(profile: Path, port: int, start_url: str, headless: bool = False) -> list[str]:
    args = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        # Dashboard / 全部執行: no window, never steals focus. Same profile, so the saved login is reused.
        args += ["--headless=new", "--window-size=1680,1050"]
    else:
        args.append("--start-maximized")
    return args + [start_url]


def _launch_chrome(profile: Path, start_url: str, headless: bool = False) -> tuple[subprocess.Popen, int]:
    profile.mkdir(parents=True, exist_ok=True)
    _stop_profile_chrome(profile)
    port = _free_port()
    exe = _chrome_exe()
    proc = subprocess.Popen(
        [str(exe)] + chrome_args(profile, port, start_url, headless),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_cdp(port, timeout=25)
    except Exception:
        if proc.poll() is None:
            proc.terminate()
        raise
    return proc, port


def apply_jobs(
    jobs: list[BannerJob],
    image: Path | None = None,
    *,
    required_account: str = DEFAULT_ACCOUNT,
    profile: Path | None = None,
    shots: Path | None = None,
    login_wait_s: int = 600,
    check_paths: dict[str, Path] | None = None,
    confirmation_only: bool = False,
    headless: bool = False,
    also_forbidden: str = "",
) -> dict:
    if required_account.lower() in forbidden_accounts(also_forbidden):
        raise SystemExit(f"表單 Banner 不能用社團帳號。請改用 {DEFAULT_ACCOUNT}。")
    image = Path(image or default_banner())
    assert_black_banner(image)
    profile = Path(profile or default_profile())
    shots = Path(shots or (ROOT / "out" / "form_banner_shots"))
    shots.mkdir(parents=True, exist_ok=True)
    if not jobs:
        raise SystemExit("沒有要處理的表單。")

    print(f"Banner：{image}")
    print(f"Chrome 設定檔：{profile}")
    print(f"必要帳號：{required_account}（絕不用社團帳號）")
    proc = None
    results = []
    user_action = False
    try:
        from playwright.sync_api import sync_playwright

        proc, port = _launch_chrome(profile, jobs[0].edit_url, headless=headless)
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = next((pg for pg in ctx.pages if "forms" in (pg.url or "") or "accounts.google.com" in (pg.url or "")), None)
            page = page or (ctx.pages[0] if ctx.pages else ctx.new_page())
            # Don't force a smaller viewport: the Theme panel docks on the right
            # of the real Chrome window and would fall outside a 1280px frame.

            for i, job in enumerate(jobs, start=1):
                prefix = f"{i:02d}-{job.kind or job.form_id[:8]}"
                print(f"\n=== {job.kind or 'form'}  {job.form_id} ===")
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(200)
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                if "/edit" not in (page.url or "") or job.form_id not in (page.url or ""):
                    page.goto(job.edit_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
                if "accounts.google.com" in (page.url or "") or not _editor_ready(page):
                    user_action = True
                email = _ensure_account(page, required_account, login_wait_s, shots)
                if job.form_id not in (page.url or ""):
                    page.goto(job.edit_url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(1500)
                    email = _ensure_account(page, required_account, login_wait_s, shots)
                if not _editor_ready(page):
                    raise SystemExit(f"打不開編輯頁：{job.edit_url}（目前 {page.url}）")
                rec = {
                    "form_id": job.form_id,
                    "kind": job.kind,
                    "account": email,
                    "banner": False,
                    "confirmation": False,
                    "confirmation_text": job.confirmation,
                    "responder_shot": None,
                    "error": "",
                }
                try:
                    if confirmation_only:
                        rec["banner"] = "skipped"
                    else:
                        upload_header(page, image, shots, prefix)
                        rec["banner"] = True
                    if job.confirmation:
                        try:
                            set_confirmation(page, job.confirmation, shots, prefix)
                            rec["confirmation"] = True
                        except (Exception, SystemExit) as e:
                            rec["error"] = f"confirmation: {e}"
                            _shot(page, shots, f"{prefix}-fail.png")
                            print(f"  確認訊息失敗：{rec['error']}", file=sys.stderr)
                    dest = None
                    if job.responder_url:
                        preferred = (check_paths or {}).get(job.kind) or (check_paths or {}).get(job.form_id)
                        dest = Path(preferred) if preferred else shots / f"form_banner_check_{job.kind or job.form_id[:8]}.png"
                        screenshot_responder(page, job.responder_url, dest, shots, prefix)
                        rec["responder_shot"] = str(dest)
                    rec["ok"] = (rec.get("banner") in (True, "skipped")
                                 and not rec.get("error")
                                 and ((not rec.get("confirmation_text")) or rec.get("confirmation")))
                except (Exception, SystemExit) as e:
                    rec["error"] = f"{e.__class__.__name__}: {e}"
                    rec["ok"] = False
                    _shot(page, shots, f"{prefix}-fail.png")
                    print(f"  失敗：{rec['error']}", file=sys.stderr)
                results.append(rec)
                job.result = rec
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            except Exception:
                pass
            # Make sure the dedicated profile's Chrome is gone so the next run can start cleanly.
            _stop_profile_chrome(profile)

    summary = {
        "image": str(image),
        "profile": str(profile),
        "account": required_account,
        "user_action_needed": user_action,
        "shots": str(shots),
        "forms": results,
        "ok": all(f.get("ok") for f in results) if results else False,
    }
    (shots / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def apply_from_state(
    state: dict,
    ev,
    image: Path | None = None,
    *,
    shots: Path | None = None,
    required_account: str = DEFAULT_ACCOUNT,
    profile: Path | None = None,
    login_wait_s: int = 600,
    check_paths: dict[str, Path] | None = None,
    headless: bool = False,
) -> dict:
    club_email = ""
    try:
        club = ev["club"] if ev is not None else {}
        if isinstance(club, dict):
            club_email = str(club.get("email") or "")
    except Exception:
        club_email = ""
    return apply_jobs(
        jobs_from_state(state, ev),
        image,
        required_account=required_account,
        profile=profile,
        shots=shots,
        login_wait_s=login_wait_s,
        check_paths=check_paths,
        headless=headless,
        also_forbidden=club_email,
    )


def main_cli(args) -> int:
    image = Path(args.image) if getattr(args, "image", None) else default_banner()
    profile = Path(args.profile) if getattr(args, "profile", None) else default_profile()
    shots = Path(args.shots) if getattr(args, "shots", None) else (ROOT / "out" / "form_banner_shots")
    account = getattr(args, "account", None) or DEFAULT_ACCOUNT
    login_wait = int(getattr(args, "login_wait", 600) or 600)

    if getattr(args, "event", None):
        jobs = jobs_from_event(Path(args.event), Path(args.out) if getattr(args, "out", None) else None)
        # --form-id can further restrict, but usually --event is enough.
        if getattr(args, "form_ids", None):
            wanted = set(args.form_ids)
            jobs = [j for j in jobs if j.form_id in wanted]
            if not jobs:
                raise SystemExit("--form-id 與 --event 的 state.json 對不上。")
    else:
        ids = getattr(args, "form_ids", None) or []
        if not ids:
            raise SystemExit("請提供 --event 或至少一個 --form-id。")
        urls = getattr(args, "responder_urls", None) or []
        confs = getattr(args, "confirmations", None) or []
        jobs = []
        for i, fid in enumerate(ids):
            jobs.append(BannerJob(
                form_id=fid,
                confirmation=confs[i] if i < len(confs) else None,
                responder_url=urls[i] if i < len(urls) else None,
            ))

    confirmation_only = bool(getattr(args, "confirmation_only", False))
    event_email = ""
    if getattr(args, "event", None):
        try:
            from .event import load_event
            club = load_event(args.event)["club"]
            if isinstance(club, dict):
                event_email = str(club.get("email") or "")
        except Exception:
            event_email = ""
    summary = apply_jobs(
        jobs,
        image,
        required_account=account,
        profile=profile,
        shots=shots,
        login_wait_s=login_wait,
        confirmation_only=confirmation_only,
        also_forbidden=event_email,
    )
    failed = []
    for f in summary["forms"]:
        banner_ok = f.get("banner") in (True, "skipped")
        conf_ok = (not f.get("confirmation_text")) or f.get("confirmation")
        if not banner_ok or not conf_ok or f.get("error"):
            failed.append(f)
        if f.get("banner") == "skipped":
            banner_txt = "略過（已有）"
        elif f.get("banner"):
            banner_txt = "是"
        else:
            banner_txt = "否"
        flag = "OK" if f not in failed else "FAIL"
        conf = "有寫確認訊息" if f.get("confirmation") else "沒改確認訊息"
        print(f"[{flag}] {f.get('kind') or ''} {f['form_id']}  Banner={banner_txt}  {conf}  {f.get('error','')}")
        if f.get("responder_shot"):
            print(f"      填寫頁截圖：{f['responder_shot']}")
    if summary.get("user_action_needed"):
        print("這次過程中曾經需要在 Chrome 視窗登入或切換帳號。")
    return 1 if failed else 0
