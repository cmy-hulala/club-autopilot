"""IG 音樂設定（貼文／限動）＋手機流程加音樂（全部 mock，不碰手機）。"""
import yaml
import pytest

from autopilot import ig_adb, ig_music
from autopilot.web import create_app
from autopilot.pipeline import ROOT

SLUG = "2026-11-25-candice"


# ---------- settings ----------
def test_parse_mmss_and_entry():
    assert ig_music.parse_mmss("1:05") == 65
    with pytest.raises(ig_music.MusicError):
        ig_music.parse_mmss("65")
    e = ig_music.build_entry("Step by Step／Tim", "0:30", "0:45")
    assert e == {"song": "Step by Step／Tim", "title": "Step by Step", "artist": "Tim", "url": "",
                 "start": "0:30", "end": "0:45"}
    assert ig_music.build_entry("", "", "") == {}
    with pytest.raises(ig_music.MusicError):
        ig_music.build_entry("a", "0:30", "0:32")  # < 5 s
    with pytest.raises(ig_music.MusicError):
        ig_music.build_entry("a", "0:00", "2:00")  # > 90 s
    assert ig_music.build_entry("a", "0:00", "0:30")["end"] == "0:30"           # post: 30 s ok
    assert ig_music.build_entry("a", "0:00", "0:45", kind="story")["end"] == "0:45"
    with pytest.raises(ig_music.MusicError, match="5～45"):
        ig_music.build_entry("a", "0:00", "0:50", kind="story")                 # story: max 45 s
    assert ig_music.build_entry("a", "0:10", "0:25", kind="story")["end"] == "0:25"


def test_youtube_link_derives_title_and_artist():
    fake = lambda url: {"title": "Tim Tsai - Step by Step (Official Music Video)", "author_name": "TimVEVO"}
    e = ig_music.build_entry("https://youtu.be/abc", "0:10", "0:25", fetch=fake)
    assert (e["title"], e["artist"], e["url"]) == ("Step by Step", "Tim Tsai", "https://youtu.be/abc")
    fake2 = lambda url: {"title": "小幸運", "author_name": "Hebe Tien - Topic"}
    e2 = ig_music.parse_song("www.youtube.com/watch?v=x", fetch=fake2)
    assert (e2["title"], e2["artist"]) == ("小幸運", "Hebe Tien")

    def boom(url):
        raise OSError("offline")
    with pytest.raises(ig_music.MusicError):
        ig_music.parse_song("https://youtu.be/abc", fetch=boom)


def test_job_from_entry():
    j = ig_music.to_job({"title": "Step by Step", "artist": "Tim", "start": "0:30", "end": "0:45"})
    assert j == {"title": "Step by Step", "artist": "Tim", "query": "Step by Step Tim", "start_s": 30,
                 "dur_s": 15, "label": "Step by Step — Tim"}
    assert ig_music.to_job({}) is None


# ---------- dashboard ----------
def _client(tmp_path):
    app = create_app(root=ROOT, out_root=tmp_path, events_dir=ROOT / "tests" / "fixtures")
    app.config["TESTING"] = True
    return app.test_client()


def test_dashboard_saves_post_and_story_music_to_event_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(ig_music, "_oembed", lambda u: {"title": "A - B", "author_name": "c"})
    c = _client(tmp_path)
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "IG 音樂" in html and "轉發貼文到限動" in html and "目前：不加音樂" in html
    r = c.post(f"/event/{SLUG}/ig-music", data={"kind": "post", "song": "Step by Step／Tim", "start": "0:30", "end": "0:45"})
    assert r.status_code == 302
    r = c.post(f"/event/{SLUG}/ig-music", data={"kind": "story", "song": "https://youtu.be/x", "start": "0:00", "end": "0:15"})
    raw = yaml.safe_load((tmp_path / "_web" / f"{SLUG}.yaml").read_text(encoding="utf-8"))
    assert raw["ig"]["music"]["post"]["title"] == "Step by Step"
    assert raw["ig"]["music"]["story"]["title"] == "B" and raw["ig"]["music"]["story"]["artist"] == "A"
    assert raw["title"]  # rest of the event kept
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "目前：Step by Step — Tim，0:30～0:45（15 秒）" in html
    # bad time -> not saved, message shown
    r = c.post(f"/event/{SLUG}/ig-music", data={"kind": "post", "song": "x", "start": "0:30", "end": "0:31"}, follow_redirects=True)
    assert "沒有儲存" in r.get_data(as_text=True)
    # clearing
    c.post(f"/event/{SLUG}/ig-music", data={"kind": "post", "song": "", "start": "", "end": ""})
    raw = yaml.safe_load((tmp_path / "_web" / f"{SLUG}.yaml").read_text(encoding="utf-8"))
    assert "post" not in raw["ig"]["music"] and "story" in raw["ig"]["music"]


# ---------- phone flow (mock adb) ----------
class LibAdb:
    """Edit screen -> music picker -> search -> results -> trim -> done. library = songs IG has."""

    def __init__(self, library, chip=True):
        self.cmds, self.library, self.chip = [], library, chip
        self.screen, self.typed, self.attached, self.dur, self.swipes = "edit", "", None, 15, []
        self.picked = None

    def nodes(self):
        n = []
        if self.screen == "edit":
            if self.attached:
                n.append(('音訊', self.attached, "audio_pill", (285, 118, 795, 323)))
            else:
                n.append(("", "建議的音訊", "audio_pill_empty_background", (285, 118, 795, 323)))
                n.append(("建議音訊", "", "audio_suggested_text", (470, 271, 610, 323)))
        elif self.screen == "picker":
            n.append(("搜尋音樂", "", "search_box", (60, 300, 1020, 400)))
            if self.typed:
                for i, (t, a) in enumerate(s for s in self.library if self.typed.split("%s")[0].lower() in s[0].lower()):
                    y = 500 + i * 200
                    n.append((t, "", "row_title", (200, y, 900, y + 60)))
                    n.append((a, "", "row_artist", (200, y + 70, 900, y + 120)))
                # a different suggested song that must never be picked
                n.append(("Other Song", "", "row_title", (200, 1800, 900, 1860)))
        elif self.screen == "trim":
            if self.chip:
                n.append((str(self.dur), "", "dur", (100, 1700, 200, 1780)))
            n.append(("完成", "", "done", (900, 150, 1040, 220)))
        elif self.screen == "durs":
            for i, d in enumerate([5, 10, 15, 20, 30, 60, 90]):
                n.append((str(d), "", "opt", (100, 1000 + i * 100, 980, 1080 + i * 100)))
        return n

    def __call__(self, args, binary=False, timeout=60):
        cmd = args[1:]
        self.cmds.append(cmd)
        if cmd[:2] == ["exec-out", "screencap"]:
            return b"png"
        if cmd[:2] == ["exec-out", "cat"]:
            return "<hierarchy>" + "".join(
                f'<node text="{t}" content-desc="{d}" resource-id="x:id/{r}" bounds="[{b[0]},{b[1]}][{b[2]},{b[3]}]" />'
                for t, d, r, b in self.nodes()) + "</hierarchy>"
        if cmd[:3] == ["shell", "input", "text"]:
            self.typed = cmd[3]
        if cmd[:3] == ["shell", "input", "swipe"]:
            self.swipes.append(cmd[3:])
        if cmd[:3] == ["shell", "input", "keyevent"] and cmd[3] == "4":
            self.screen = "edit"
        if cmd[:3] == ["shell", "input", "tap"]:
            x, y = int(cmd[3]), int(cmd[4])
            for t, d, r, b in self.nodes():
                if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                    if self.screen == "edit" and "audio_pill" in r:
                        self.screen = "picker"
                    elif self.screen == "picker" and r == "row_title":
                        self.picked, self.screen = t, "trim"
                    elif self.screen == "trim" and r == "dur":
                        self.screen = "durs"
                    elif self.screen == "durs":
                        self.dur, self.screen = int(t), "trim"
                    elif self.screen == "trim" and r == "done":
                        self.attached, self.screen = self.picked, "edit"
                    break
        return b"" if binary else ""


def _ph(adb, tmp_path, pasted=None):
    paste = (lambda c, t: pasted.append(t)) if pasted is not None else (lambda c, t: None)
    return ig_adb.Phone(ig_adb.load_config(), tmp_path / "s", adb, lambda s: None, paste)


JOB = ig_music.to_job({"title": "Step by Step", "artist": "Tim", "start": "0:30", "end": "1:00"})


def test_add_music_searches_sets_length_and_start(tmp_path):
    adb = LibAdb([("Step by Step", "Tim")])
    ph = _ph(adb, tmp_path)
    ph.cfg["type_mode"] = "adb"  # English keyboard: type directly
    ph.fast = False  # check per-step screenshots
    ph.add_music(JOB)
    assert adb.attached == "Step by Step" and adb.dur == 30
    assert adb.typed == "Step%sby%sStep%sTim"
    (x0, y, x1, _, _), = adb.swipes
    assert int(x0) - int(x1) == int(30 * ig_adb.load_config()["music"]["px_per_s"])  # estimated drag
    for f in ("05c_music_search.png", "05d_music_trim.png", "05e_music.png"):
        assert f in ph.shot_files
    assert ph.log[-1].startswith("已加音樂：Step by Step — Tim")


def test_song_not_in_ig_stops_music_and_never_picks_another(tmp_path):
    adb = LibAdb([("Step by Step", "Somebody Else")])  # same title, wrong artist
    ph = _ph(adb, tmp_path)
    with pytest.raises(ig_adb.MusicNotFound, match="IG 找不到這首歌"):
        ph.add_music(JOB)
    assert adb.picked is None and adb.attached is None and adb.screen == "edit"


def test_no_music_configured_does_nothing(tmp_path):
    adb = LibAdb([])
    ph = _ph(adb, tmp_path)
    ph.add_music(None)
    assert not [c for c in adb.cmds if c[:2] == ["shell", "input"]]


def test_chinese_song_is_pasted_not_typed(tmp_path):
    adb = LibAdb([("小幸運", "田馥甄")])
    pasted = []
    ph = _ph(adb, tmp_path, pasted)
    job = ig_music.to_job({"title": "小幸運", "artist": "田馥甄", "start": "0:00", "end": "0:15"})
    with pytest.raises(ig_adb.MusicNotFound):  # the fake only "types" via adb text, so paste leaves no results
        ph.add_music(job)
    assert pasted == ["小幸運 田馥甄"] and not [c for c in adb.cmds if c[:3] == ["shell", "input", "text"]]


def test_full_draft_reports_music_not_found_but_keeps_draft(tmp_path, monkeypatch):
    cfg = ig_adb.load_config()
    cfg["steps"] = [{"do": "no_music"}, {"do": "music"}]
    adb = LibAdb([])
    adb_devices = "List of devices attached\nHT1\tdevice\n"
    real = adb.__call__

    def runner(args, binary=False, timeout=60):
        if args[1:] == ["devices"]:
            return adb_devices
        if args[1:3] == ["shell", "wm"]:
            return "Physical size: 1080x2400"
        return real(args, binary, timeout)
    cards = []
    for i in range(1, 5):
        p = tmp_path / f"ig_0{i}.png"; p.write_bytes(b"png"); cards.append(p)
    res = ig_adb.save_ig_draft(cards, "cap", tmp_path / "shots", cfg=cfg, runner=runner,
                               sleep=lambda s: None, paste=lambda c, t: None, music=JOB)
    assert res["ok"] and res["music_error"].startswith("IG 找不到這首歌")


def test_config_adds_post_music_on_caption_screen():
    steps = ig_adb.load_config()["steps"]
    names = [s.get("do") for s in steps]
    assert "music" not in names and names.index("no_music") < names.index("caption_music")
    cap = next(i for i, s in enumerate(steps) if s.get("name") == "06_caption_screen")
    assert names.index("caption_music") == cap + 1 and names.index("caption_music") < names.index("paste_caption")


def test_ascii_is_pasted_by_default_because_phone_keyboard_is_chinese(tmp_path):
    adb = LibAdb([("Step by Step", "Tim")])
    pasted = []
    ph = _ph(adb, tmp_path, pasted)
    with pytest.raises(ig_adb.MusicNotFound):  # fake only reacts to adb text
        ph.add_music(JOB)
    assert pasted == ["Step by Step Tim"]


def test_story_length_capped_to_phone_max_with_warning(tmp_path):
    adb = LibAdb([("Step by Step", "Tim")])
    adb_opts = [5, 10, 15]

    orig = adb.nodes
    def nodes():
        if adb.screen == "durs":
            return [(str(d), "", "opt", (100, 1000 + i * 100, 980, 1080 + i * 100)) for i, d in enumerate(adb_opts)]
        return orig()
    adb.nodes = nodes
    ph = _ph(adb, tmp_path)
    ph.cfg["type_mode"] = "adb"
    job = ig_music.to_job({"title": "Step by Step", "artist": "Tim", "start": "0:00", "end": "0:40"})
    ph.add_music(job)
    assert adb.dur == 15 and ph.music_max == 15 and "最長只能 15 秒" in ph.music_warn


class PickerAdb:
    """Story music editor: circled-number picker (5..max) + waveform/progress bar (fake screenshot)."""

    def __init__(self, maxlen=45, song_len=338, start=106.0):
        self.cmds, self.v, self.maxlen, self.open = [], 18, maxlen, False
        self.song_len, self.start = song_len, start

    def __call__(self, args, binary=False, timeout=60):
        cmd = args[1:]
        self.cmds.append(cmd)
        if cmd[:2] == ["exec-out", "cat"]:
            n = [f'<node text="" content-desc="{self.v}秒的片段" resource-id="x" bounds="[90,1752][166,1828]" />']
            if self.open:
                n.append(f'<node text="{self.v}秒" content-desc="" resource-id="x:id/numberpicker_input" bounds="[458,1768][621,1890]" />')
                n.append('<node text="完成" content-desc="" resource-id="x:id/music_duration_picker_done_button" bounds="[41,2125][1039,2170]" />')
            return "<hierarchy>" + "".join(n) + "</hierarchy>"
        if cmd[:2] == ["exec-out", "screencap"]:
            from PIL import Image, ImageDraw
            im = Image.new("RGB", (1080, 2400), (80, 80, 80))
            d = ImageDraw.Draw(im)
            d.rectangle((213, 1750, 866, 1752), fill="white")
            hx = 213 + int(self.start / self.song_len * 653)
            d.rectangle((hx - 7, 1745, hx + 7, 1757), fill="white")
            import io
            b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()
        if cmd[:3] == ["shell", "input", "tap"]:
            y = int(cmd[4])
            if 1752 <= y <= 1828:
                self.open = True
            elif y >= 2125:
                self.open = False
        if cmd[:3] == ["shell", "input", "swipe"]:
            x1, y1, x2, y2, ms = map(int, cmd[3:8])
            if self.open:
                if ms <= 200:
                    self.v = min(self.maxlen, self.v + 8)
                else:
                    self.v = max(5, min(self.maxlen, self.v - round((y2 - y1) / 194)))
            elif y1 == y2:  # waveform drag: 9 px per second after 20 px slop
                moved = (abs(x1 - x2) - 20) / 9.0
                self.start = max(0, min(self.song_len - 5, self.start + (moved if x2 < x1 else -moved)))
        return b"" if binary else ""


def _pphone(adb, tmp_path):
    return ig_adb.Phone(ig_adb.load_config(), tmp_path / "s", adb, lambda s: None, lambda c, t: None)


def test_picker_reads_max_and_sets_length(tmp_path):
    adb = PickerAdb(maxlen=45)
    ph = _pphone(adb, tmp_path)
    use = ph._set_length(ph._chip(ph.ui()), 34)
    assert use == 34 and adb.v == 34 and ph.music_max == 45 and not ph.music_warn and not adb.open


def test_picker_caps_to_phone_max(tmp_path):
    adb = PickerAdb(maxlen=15)
    ph = _pphone(adb, tmp_path)
    assert ph._set_length(ph._chip(ph.ui()), 40) == 15 and adb.v == 15
    assert "最長只能 15 秒" in ph.music_warn


def test_start_time_closed_loop_from_progress_bar(tmp_path):
    adb = PickerAdb(start=106.0)
    ph = _pphone(adb, tmp_path)
    ph._set_start(205, 338)
    assert abs(adb.start - 205) <= 3 and not ph.music_warn
