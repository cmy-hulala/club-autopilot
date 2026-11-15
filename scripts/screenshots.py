"""Capture demo screenshots of the Flask UI and key outputs (for the demo video)."""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots")
BASE = "http://127.0.0.1:5050"
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1366, "height": 900}, device_scale_factor=1)
    pg.goto(BASE + "/?fresh=1"); pg.wait_for_timeout(500)
    pg.screenshot(path=str(OUT / "01_event_form.png"), full_page=True)
    pg.click("text=② 預覽全部產出"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT / "02_run_top.png"))
    pg.screenshot(path=str(OUT / "03_run_full.png"), full_page=True)
    run_url = pg.url
    for sel, name in [("text=時間軸（只建草稿）", "04_timeline.png"), ("text=IG 圖卡（Canva）", "05_ig_cards.png"),
                      ("text=Linktree", "06_linktree.png")]:
        el = pg.locator(f"h2:has-text('{sel[5:]}')").first
        el.scroll_into_view_if_needed(); pg.wait_for_timeout(300)
        el.locator("xpath=..").screenshot(path=str(OUT / name))
    files = run_url + "/file/"
    for rel, name, h in [("outputs/2_預告信.html", "07_announce_email.png", 1800), ("outputs/6_課後信.html", "08_followup_email.png", 900),
                         ("outputs/4_提醒信.html", "09_reminder_email.png", 1100), ("課後回饋彙整.html", "10_feedback_summary.png", 900)]:
        q = b.new_page(viewport={"width": 700 if "email" in name else 1000, "height": h})
        q.goto(files + rel); q.wait_for_timeout(800); q.screenshot(path=str(OUT / name), full_page=True); q.close()
    # rule checker catching a violation: inject a fee into the edited YAML and re-run
    pg.goto(BASE + "/"); pg.wait_for_timeout(300)
    pg.fill("input[name='hook']", "非社員 NT$300！在最迷惘的 20 幾歲，應該如何找到自己的職涯方向？")
    pg.click("text=② 預覽全部產出"); pg.wait_for_load_state("networkidle"); pg.wait_for_timeout(800)
    card = pg.locator("h2:has-text('規則檢查')").locator("xpath=..")
    card.screenshot(path=str(OUT / "11_checker_flags_red.png"))
    # restore
    pg.goto(BASE + "/?fresh=1"); pg.click("text=② 預覽全部產出"); pg.wait_for_load_state("networkidle")
    b.close()
print("saved to", OUT)
