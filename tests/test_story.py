"""「轉發貼文到限動（手機）」 — mock adb only, never a real phone."""
import io
import pytest
from PIL import Image, ImageDraw

from autopilot import ig_adb, ig_story
from autopilot.web import create_app
from autopilot.pipeline import ROOT

SLUG = "2026-11-25-candice"
URL = "https://docs.google.com/forms/d/e/FAKEFORMID/viewform"


def post_png(liked=False, reposted=False, y=2000):
    im = Image.new("RGB", (1080, 2400), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((0, 300, 1080, 1700), fill=(120, 110, 100))          # photo
    d.text((40, 1800), "1.9K view insights", fill="black")
    d.rectangle((720, 1780, 1040, 1850), fill=(74, 93, 249))          # blue promote button
    if liked:
        d.ellipse((37, y - 22, 87, y + 22), fill=(254, 29, 55))
    else:
        d.ellipse((37, y - 22, 87, y + 22), outline=(38, 38, 38), width=5)
    d.ellipse((180, y - 24, 230, y + 24), outline=(38, 38, 38), width=5)
    d.rectangle((258, y - 24, 308, y + 24), outline=(38, 38, 38), width=5)
    if reposted:
        d.line((274, y, 282, y + 8, 296, y - 8), fill=(38, 38, 38), width=5)
    d.polygon([(412, y - 22), (462, y - 22), (437, y + 24)], outline=(38, 38, 38), width=5)
    d.rectangle((997, y - 24, 1037, y + 24), outline=(38, 38, 38), width=5)
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


@pytest.mark.parametrize("liked,reposted", [(False, False), (True, True), (True, False)])
def test_find_action_row_and_states(liked, reposted):
    r = ig_story.find_action_row(post_png(liked, reposted), {})
    assert r and abs(r["y"] - 2000) <= 8 and r["liked"] == liked and r["reposted"] == reposted


def test_no_row_on_blank_screen():
    b = io.BytesIO(); Image.new("RGB", (1080, 2400), "white").save(b, "PNG")
    assert ig_story.find_action_row(b.getvalue(), {}) is None


def N(text="", desc="", rid="", box=(0, 0, 0, 0)):
    return f'<node text="{text}" content-desc="{desc}" resource-id="x:id/{rid}" bounds="[{box[0]},{box[1]}][{box[2]},{box[3]}]" />'


class StoryAdb:
    """profile -> post (unreadable) -> share sheet (unreadable) -> editor -> sticker tray -> link sheet -> editor."""

    def __init__(self, liked=False, reposted=False, draft_option=False, suggested=True):
        self.cmds, self.screen = [], "home"
        self.liked, self.reposted, self.draft_option, self.suggested = liked, reposted, draft_option, suggested
        self.url, self.cta, self.focus, self.link = "", "", None, None
        self.dialog = False
        self.taps = []

    POST = (108, 492, 972, 1643)

    def xml(self):
        s = self.screen
        if s in ("post", "sheet"):
            return "ERROR: could not get idle state."
        n = []
        if s == "profile":
            n += [N(desc="陽明交大創新創業社的4張相片，第1橫列、第1直欄", rid="image_button", box=(0, 1665, 356, 2140)),
                  N(desc="陽明交大創新創業社的3張相片，第1橫列、第2直欄", rid="image_button", box=(362, 1665, 718, 2140))]
        if s in ("editor", "tray", "linksheet"):
            n += [N(desc="貼圖", box=(937, 271, 1049, 383)), N(desc="音樂", box=(937, 403, 1049, 515)),
                  N(desc="取消", rid="cancel_button", box=(10, 139, 143, 272)),
                  N(text="限時動態", rid="your_story_share_shortcut_button", box=(31, 2051, 470, 2162)),
                  N(text="摯友", rid="close_friends_story_share_shortcut_button", box=(485, 2051, 923, 2162)),
                  N(desc="分享到", rid="story_share_recipient_picker_button", box=(938, 2051, 1049, 2162)),
                  N(desc="貼圖. 按住即可調整位置。", box=self.POST)]
            if self.link:
                n.append(N(desc="貼圖. 按住即可調整位置。", box=self.link))
            if self.suggested and s == "editor":
                n.append(N(desc="come on", rid="suggested_music_header", box=(418, 162, 693, 247)))
        if s == "tray":
            n.append(N(desc="連結貼圖", rid="sticker_sheet_redesign_item", box=(730, 1234, 948, 1369)))
        if s == "linksheet":
            n += [N(text=self.url or "http://example.com", rid="link_sticker_list_web_url_edit_text", box=(41, 496, 1039, 570)),
                  N(text="自訂貼圖文字", rid="link_sticker_custom_cta_row_title", box=(102, 712, 384, 781)),
                  N(text=self.cta, rid="link_sticker_custom_cta_edit_text", box=(41, 772, 1039, 846)),
                  N(rid="link_sticker_list_done_icon_button", box=(948, 314, 1070, 436))]
        if self.dialog:
            n += [N(text="捨棄相片？", box=(308, 950, 772, 1010)), N(text="捨棄", box=(247, 1233, 833, 1375)),
                  N(text="繼續編輯", box=(247, 1376, 833, 1518))]
            if self.draft_option:
                n.append(N(text="儲存草稿", box=(247, 1100, 833, 1200)))
        return "<hierarchy>" + "".join(n) + "</hierarchy>"

    def paste(self, cfg, text):
        if self.focus == "url":
            self.url = text
        elif self.focus == "cta":
            self.cta = text

    def __call__(self, args, binary=False, timeout=60):
        cmd = args[1:]
        self.cmds.append(cmd)
        if cmd == ["devices"]:
            return "List of devices attached\nCN1\tdevice\n"
        if cmd[:2] == ["shell", "wm"]:
            return "Physical size: 1080x2400"
        if cmd[:2] == ["exec-out", "screencap"]:
            return post_png(self.liked, self.reposted) if self.screen == "post" else post_png() if False else b"\x89PNG"
        if cmd[:2] == ["exec-out", "cat"]:
            if getattr(self, "drop_dumps", 0):
                self.drop_dumps -= 1
                return ""
            return self.xml()
        if cmd[:2] == ["shell", "monkey"]:
            self.screen = "home"
        if cmd[:3] == ["shell", "am", "start"]:
            self.screen = "post"
        if cmd[:3] == ["shell", "input", "swipe"]:
            x1, y1, x2, y2 = map(int, cmd[3:7])
            if self.link:
                w, h = self.link[2] - self.link[0], self.link[3] - self.link[1]
                cx, cy = x2 - (20 if x2 > x1 else -20 if x2 < x1 else 0), y2 - (20 if y2 > y1 else -20 if y2 < y1 else 0)
                self.link = (cx - w // 2, cy - h // 2, cx - w // 2 + w, cy - h // 2 + h)
                self.drop_dumps = getattr(self, "fail_after_swipe", 0)
        if cmd[:3] == ["shell", "input", "tap"]:
            x, y = int(cmd[3]), int(cmd[4])
            self.taps.append((self.screen, x, y))
            s = self.screen
            if self.dialog:
                if 1376 <= y <= 1518:
                    self.dialog = False
                elif 1233 <= y <= 1375:
                    self.dialog, self.screen = False, "post"
                elif self.draft_option and 1100 <= y <= 1200:
                    self.dialog, self.screen = False, "saved"
            elif s == "home" and y > 2100:
                self.screen = "profile"
            elif s == "profile" and y > 1600:
                self.screen = "post"
            elif s == "post" and abs(y - 2000) < 30:
                if abs(x - 62) < 30:
                    self.liked = not self.liked
                elif abs(x - 283) < 30:
                    self.reposted = not self.reposted
                elif abs(x - 437) < 30:
                    self.screen = "sheet"
            elif s == "sheet" and x < 200 and y > 1950:
                self.screen = "editor"
            elif s == "editor":
                if 31 <= x <= 470 and 2051 <= y <= 2162:
                    self.published = getattr(self, "published", 0) + 1
                    self.screen = "post"
                elif 937 <= x <= 1049 and 271 <= y <= 383:
                    self.screen = "tray"
                elif 10 <= x <= 143 and 139 <= y <= 272:
                    self.dialog = True
                elif 700 <= x <= 730 and 162 <= y <= 247:
                    self.suggested = False
            elif s == "tray" and 730 <= x <= 948:
                self.screen = "linksheet"
            elif s == "linksheet":
                if 496 <= y <= 570:
                    self.focus = "url"
                elif 712 <= y <= 781 or 772 <= y <= 846:
                    self.focus = "cta"
                elif 948 <= x and 314 <= y <= 436:
                    self.screen, self.link = "editor", (350, 1011, 728, 1123)
        return b"" if binary else ""


def _run(adb, **kw):
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    return ig_story.reshare_story(URL, d / "s", runner=adb, sleep=lambda s: None, paste=adb.paste, **kw), d


def _forbidden_taps(adb):
    bad = [(31, 2051, 470, 2162), (485, 2051, 923, 2162), (938, 2051, 1049, 2162)]
    return [t for t in adb.taps if t[0] in ("editor",) and any(b[0] <= t[1] <= b[2] and b[1] <= t[2] <= b[3] for b in bad)]


def test_full_reshare_stops_in_editor_without_sharing():
    adb = StoryAdb()
    res, _ = _run(adb)
    assert res["ok"], res
    assert res["outcome"] == "ready" and adb.screen == "editor"   # no draft option -> stays, user shares
    assert adb.url == URL and adb.cta == "報名表單"
    assert res["sticker_ok"] and adb.link[2] >= 950 and adb.link[1] <= 520
    assert 0.75 <= res["scale"] <= 0.85
    assert not adb.suggested
    assert not _forbidden_taps(adb)
    assert not adb.liked and not adb.reposted  # like/repost not requested -> untouched
    assert any("限動沒有設定音樂" in x for x in res["log"])


def test_story_draft_is_used_when_ig_offers_it():
    adb = StoryAdb(draft_option=True)
    res, _ = _run(adb)
    assert res["outcome"] == "draft" and adb.screen == "saved"


def test_like_repost_only_when_asked_and_never_toggles_off():
    adb = StoryAdb(liked=True, reposted=True)
    res, _ = _run(adb, like_repost=True)
    assert res["ok"] and adb.liked and adb.reposted
    assert res["like_repost"]["tapped"] == []
    adb2 = StoryAdb()
    res2, _ = _run(adb2, like_repost=True)
    assert adb2.liked and adb2.reposted and set(res2["like_repost"]["tapped"]) == {"like", "repost"}


def test_post_url_opens_directly():
    adb = StoryAdb()
    res, _ = _run(adb, post_url="https://www.instagram.com/p/FAKE/")
    assert res["ok"] and any(c[:3] == ["shell", "am", "start"] for c in adb.cmds)


def test_story_guard_blocks_share_buttons(tmp_path):
    adb = StoryAdb()
    adb.screen = "editor"
    ph = ig_story.StoryPhone(ig_adb.load_config(), tmp_path, adb, lambda s: None, adb.paste)
    for x, y in [(250, 2100), (700, 2100), (990, 2100)]:
        with pytest.raises(ig_adb.IGStop):
            ph.tap(x, y, "test")
    assert not adb.taps


def test_stale_dump_is_not_reused(tmp_path):
    adb = StoryAdb()
    adb.screen = "post"
    ph = ig_story.StoryPhone(ig_adb.load_config(), tmp_path, adb, lambda s: None, adb.paste)
    assert ph.ui() == [] and ph.dump_ok is False
    assert ["shell", "rm", "-f", "/sdcard/club_ui.xml"] in adb.cmds


def test_dashboard_story_endpoint(tmp_path, monkeypatch):
    app = create_app(root=ROOT, out_root=tmp_path, events_dir=ROOT / "tests" / "fixtures")
    app.config["TESTING"] = True
    c = app.test_client()
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "轉發貼文到限動（手機）" in html and "公開動作" in html
    assert c.post(f"/api/ig/{SLUG}/story", json={}).status_code == 400
    assert c.post(f"/api/ig/{SLUG}/story", json={"confirm": True, "post_url": "http://evil"}).status_code == 400
    seen = {}

    def fake(form_url, shots, **kw):
        seen.update(kw, form_url=form_url)
        return {"ok": True, "error": "", "music_error": "", "outcome": "ready", "sticker_ok": True, "scale": 0.8,
                "like_repost": None, "log": [], "shots": ["r08_final.png"], "at": "now"}
    monkeypatch.setattr(ig_story, "reshare_story", fake)
    r = c.post(f"/api/ig/{SLUG}/story", json={"confirm": True})
    assert r.get_json()["ok"]
    import time
    for _ in range(50):
        st = c.get(f"/api/status/{SLUG}").get_json()["ig_story"]
        if not st["busy"]:
            break
        time.sleep(0.05)
    assert st["status"] == "done" and "自己按「限時動態」" in st["message"] and seen["like_repost"] is False


def test_locked_phone_stops_before_any_tap():
    adb = StoryAdb()
    real = adb.__call__

    def runner(args, binary=False, timeout=60):
        if args[1:3] == ["shell", "dumpsys"] and args[3] == "window":
            return "    isKeyguardShowing=true"
        return real(args, binary, timeout)
    import tempfile, pathlib
    res = ig_story.reshare_story(URL, pathlib.Path(tempfile.mkdtemp()), runner=runner, sleep=lambda s: None, paste=adb.paste)
    assert not res["ok"] and "解鎖" in res["error"] and not adb.taps


def test_discard_taps_button_not_headline():
    adb = StoryAdb()
    res, _ = _run(adb, discard=True)
    assert res["outcome"] == "discarded" and adb.screen == "post", res
    assert ("editor", 540, 980) not in adb.taps


def test_sticker_check_survives_failed_dump_after_drag():
    adb = StoryAdb()
    adb.fail_after_swipe = 1
    res, _ = _run(adb)
    assert res["sticker_ok"], res["log"]


def test_publish_story_only_when_asked():
    adb = StoryAdb()
    res, _ = _run(adb, publish=True)
    assert res["ok"] and res["outcome"] == "published", res
    assert getattr(adb, "published", 0) == 1
    adb2 = StoryAdb()
    res2, _ = _run(adb2, discard=True)
    assert getattr(adb2, "published", 0) == 0 and res2["outcome"] == "discarded"


def test_cover_distance_tells_this_post_from_another(tmp_path):
    import io
    import random
    from PIL import Image, ImageDraw
    def card(seed):
        rnd = random.Random(seed)
        im = Image.new("RGB", (1080, 1350), (200, 200, 200))
        d = ImageDraw.Draw(im)
        for _ in range(60):
            x, y = rnd.randrange(300, 760), rnd.randrange(420, 1030)
            d.rectangle((x, y, x + 40, y + 40), fill=tuple(rnd.randrange(256) for _ in range(3)))
        return im
    cover = tmp_path / "ig_01.png"
    card(1).save(cover)
    def page(im):
        shot = Image.new("RGB", (1080, 2400), "white")
        shot.paste(im, (0, 383))
        b = io.BytesIO(); shot.save(b, "PNG")
        return b.getvalue()
    assert ig_story.cover_distance(page(card(1)), cover) < 3
    assert ig_story.cover_distance(page(card(2)), cover) > ig_story.COVER_MAX


def test_wrong_post_stops_before_like_or_share(tmp_path, monkeypatch):
    monkeypatch.setattr(ig_story, "cover_distance", lambda png, cover: 48.0)
    adb = StoryAdb()
    res, _ = _run(adb, like_repost=True, expect_cover=tmp_path / "ig_01.png")
    assert not res["ok"] and "不是這期的新貼文" in res["error"]
    assert res["like_repost"] is None and not _forbidden_taps(adb)


def test_capture_post_pages_one_shot_per_page(tmp_path, monkeypatch):
    monkeypatch.setattr(ig_story, "cover_distance", lambda png, cover: 1.0)
    adb = StoryAdb()
    names = ig_story.capture_post_pages(tmp_path / "s", 4, expect_cover=tmp_path / "ig_01.png",
                                        runner=adb, sleep=lambda s: None)
    assert names == ["post_p1.png", "post_p2.png", "post_p3.png", "post_p4.png"]
    assert all((tmp_path / "s" / n).is_file() for n in names)
    assert not adb.liked and not adb.reposted  # read-only: swipes only


def test_capture_post_pages_skips_wrong_post(tmp_path, monkeypatch):
    monkeypatch.setattr(ig_story, "cover_distance", lambda png, cover: 48.0)
    names = ig_story.capture_post_pages(tmp_path / "s", 4, expect_cover=tmp_path / "ig_01.png",
                                        runner=StoryAdb(), sleep=lambda s: None)
    assert names == []


def test_dashboard_shows_story_final_and_post_pages_only():
    from autopilot.web import display_shots
    story = {"status": "done", "shots": ["r01_post.png", "r08_final.png", "r10_end.png"]}
    assert display_shots("story", story) == ["r08_final.png"]
    assert display_shots("story", {**story, "status": "failed"}) == story["shots"]
    post = {"published": True, "shots": ["post_p2.png", "post_p1.png", "post_p4.png", "post_p3.png", "final.png"]}
    assert display_shots("post", post) == ["post_p1.png", "post_p2.png", "post_p3.png", "post_p4.png"]
    assert display_shots("post", {"published": True, "shots": ["final.png"]}) == ["final.png"]
    assert display_shots("post", {"published": False, "shots": ["a.png"]}) == ["a.png"]
