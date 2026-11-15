"""Drafts BCC sync, LINE config, club rules, IG adb (all mocked; nothing real is sent or tapped)."""
import base64
import json
from email import message_from_bytes, policy
from pathlib import Path

import pytest

from autopilot import checker, drafts_sync, ig_adb, line_push, render
from autopilot.event import load_event

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "tests" / "fixtures" / "2026-11-25-candice.yaml"
CLUB = "nycu.iec@gmail.com"


class FakeGmail:
    def __init__(self):
        self.store, self.created, self.updated, self.n = {}, [], [], 0

    def users(self): return self
    def drafts(self): return self

    def create(self, userId, body):
        self.n += 1
        did = f"r-new{self.n}"
        self.store[did] = body["message"]["raw"]
        self.created.append(did)
        return _Exec({"id": did})

    def update(self, userId, id, body):
        self.store[id] = body["message"]["raw"]
        self.updated.append(id)
        return _Exec({"id": id})

    def get(self, userId, id, format):
        if id not in self.store:
            err = Exception("404"); err.resp = type("R", (), {"status": 404})()
            return _Raise(err)
        return _Exec({"id": id, "message": {"raw": self.store[id]}})


class _Exec:
    def __init__(self, v): self.v = v
    def execute(self): return self.v


class _Raise:
    def __init__(self, e): self.e = e
    def execute(self): raise self.e


def _outputs():
    ev = load_event(EV, {"form_url": "https://forms.gle/x", "form_short_url": "https://forms.gle/x"})
    return render.render_all(ev)


def _bcc(gm, did):
    raw = gm.store[did]
    m = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)
    return m["To"], [x.strip() for x in str(m.get("Bcc") or "").split(",") if x.strip()]


def test_drafts_create_then_update_in_place_never_duplicate():
    gm, out = FakeGmail(), _outputs()
    bccs = {"members": ["a@x.com", "b@x.com"], "registrants": []}
    recs = drafts_sync.sync_drafts(out, CLUB, [], bccs, gm)
    assert len(gm.created) == 3 and [r["bcc_count"] for r in recs] == [2, 0, 0]
    # second run, nothing changed -> no writes at all
    recs2 = drafts_sync.sync_drafts(out, CLUB, recs, bccs, gm)
    assert [r["action"] for r in recs2] == ["unchanged"] * 3 and len(gm.created) == 3 and not gm.updated
    # registrants arrive -> 提醒信 + 課後信 updated in place, same ids; 預告信 untouched
    bccs["registrants"] = ["c@x.com", "d@x.com", "e@x.com"]
    recs3 = drafts_sync.sync_drafts(out, CLUB, recs2, bccs, gm)
    assert [r["draft_id"] for r in recs3] == [r["draft_id"] for r in recs]
    assert gm.updated == [recs[1]["draft_id"], recs[2]["draft_id"]]
    assert [r["bcc_count"] for r in recs3] == [2, 3, 3]
    to, bcc = _bcc(gm, recs[1]["draft_id"])
    assert to == CLUB and len(bcc) == 3
    # describe never contains addresses
    assert "@" not in drafts_sync.describe(recs3)


def test_dry_run_changes_nothing():
    gm, out = FakeGmail(), _outputs()
    recs = drafts_sync.sync_drafts(out, CLUB, [], {"members": [], "registrants": []}, gm, dry_run=True)
    assert not gm.created and all(r["action"] == "would-create" for r in recs)


def test_registrant_bcc_excludes_decliners_and_club(monkeypatch):
    rows = [{"Email": "a@x.com", "是否參加本期社課": "會參加"}, {"Email": "b@x.com", "是否參加本期社課": "無法參加"},
            {"Email": CLUB, "是否參加本期社課": "會參加"}, {"Email": "A@x.com", "是否參加本期社課": "會參加"}]
    monkeypatch.setattr("autopilot.forms_api.responses_as_rows", lambda svc, fid: rows)
    emails, total = drafts_sync.registrant_bcc(object(), "fid", CLUB)
    assert emails == ["a@x.com"] and total == 4


def test_line_groups_default_and_private_file(tmp_path, monkeypatch):
    for k in ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_GROUP_ID", "LINE_GROUP_IDS"):
        monkeypatch.delenv(k, raising=False)
    local = tmp_path / "autopilot_local.yaml"
    local.write_text("line_groups:\n  - {name: Club group A, id: CFAKEGROUPA}\n  - {name: Club group B, id: CFAKEGROUPB}\n",
                     encoding="utf-8")
    monkeypatch.setenv("AUTOPILOT_LOCAL_CONFIG", str(local))
    cfgp = tmp_path / "line_bot.json"
    monkeypatch.setenv("LINE_BOT_CONFIG", str(cfgp))
    st = line_push.public_status()
    assert not st["configured"] and st["missing"] == ["LINE_CHANNEL_ACCESS_TOKEN"]
    assert st["groups"] == ["Club group A", "Club group B"]
    cfgp.write_text(json.dumps({"channel_access_token": "t"}), encoding="utf-8")
    assert line_push.public_status()["configured"]
    cfgp.write_text(json.dumps({"channel_access_token": "t", "group_id": "CFAKEGROUPB"}), encoding="utf-8")
    assert line_push.public_status()["groups"] == ["Club group B"]
    cfgp.write_text(json.dumps({"channel_access_token": "t", "group_ids": ["CFAKEGROUPA", "CFAKEGROUPB"]}), encoding="utf-8")
    assert len(line_push.load_config()["groups"]) == 2


def test_public_repo_has_no_line_group_ids():
    import yaml
    data = yaml.safe_load((ROOT / "config" / "line_groups.yaml").read_text(encoding="utf-8")) or {}
    assert not data.get("groups")


def test_club_rules_catch_violations():
    ev = load_event(EV, {"form_url": "https://forms.gle/x", "form_short_url": "https://forms.gle/x"})
    out = render.render_all(ev)
    assert checker.summary(checker.check(ev, out, rules=[]))["error"] == 0
    bad = dict(out)
    bad["2_預告信.html"] = out["2_預告信.html"].replace("填寫課前表單", "報名").replace(
        "https://forms.gle/x", "https://docs.google.com/forms/d/e/x/viewform?usp=pp_url&entry.1=社員")
    bad["4_提醒信.html"] = out["4_提醒信.html"].replace("</p>", "社員免費 NT$300</p>", 1)
    bad["1_報名表單.md"] = out["1_報名表單.md"] + "\n報到時段 18:30\n講者：None\n"
    rules = {f.rule for f in checker.check(ev, bad, rules=[]) if f.level == "error"}
    assert "社團規則：預告信有「填寫課前表單」按鈕" in rules
    assert "社團規則：信件連結不預填身分（無 pp_url／entry）" in rules
    assert "信裡不寫費用" in rules
    assert "不寫報到時段" in rules
    assert "社團規則：講者／地點缺資料要用佔位符，不留空或 None" in rules


class FakeAdb:
    """Simulates adb; records every command. UI dump puts 分享 at a configurable spot."""

    def __init__(self, devices="List of devices attached\nHT123\tdevice\n", share_at=None, draft=True):
        self.cmds, self.devs, self.share_at, self.draft = [], devices, share_at, draft
        self.backs = 0

    def __call__(self, args, binary=False, timeout=60):
        cmd = args[1:]
        self.cmds.append(cmd)
        if cmd == ["devices"]:
            return self.devs
        if cmd[:2] == ["exec-out", "screencap"]:
            return b"\x89PNG fake"
        if cmd[:2] == ["exec-out", "cat"]:
            nodes = []
            if self.share_at:
                x, y = self.share_at
                nodes.append(f'<node text="分享" content-desc="" bounds="[{x-50},{y-30}][{x+50},{y+30}]" />')
            if self.draft and self.backs >= 2:
                nodes.append('<node text="儲存草稿" content-desc="" bounds="[100,1800][980,1900]" />')
            return "<hierarchy>" + "".join(nodes) + "</hierarchy>"
        if cmd[:2] == ["shell", "wm"]:
            return "Physical size: 1080x2400"
        if cmd[:3] == ["shell", "input", "keyevent"]:
            self.backs += 1
        return b"" if binary else ""


def _cards(tmp_path):
    cards = []
    for i in range(1, 5):
        p = tmp_path / f"ig_0{i}.png"; p.write_bytes(b"png"); cards.append(p)
    return cards


def test_ig_no_phone_stops_before_anything(tmp_path):
    adb = FakeAdb(devices="List of devices attached\n\n")
    res = ig_adb.devices(ig_adb.load_config(), adb)
    assert not res["ok"] and res["state"] == "none"
    adb2 = FakeAdb(devices="List of devices attached\nHT1\tunauthorized\n")
    assert ig_adb.devices(ig_adb.load_config(), adb2)["state"] == "unauthorized"
    with pytest.raises(ig_adb.IGStop):
        ig_adb.save_ig_draft(_cards(tmp_path), "文案", tmp_path / "s", runner=adb, sleep=lambda s: None, paste=lambda c, t: None)
    assert all(c == ["devices"] for c in adb.cmds)


def test_ig_flow_pushes_cover_last_taps_config_and_saves_draft(tmp_path):
    adb, pasted = FakeAdb(), []
    res = ig_adb.save_ig_draft(_cards(tmp_path), "中文文案", tmp_path / "s", runner=adb, sleep=lambda s: None,
                               paste=lambda c, t: pasted.append(t))
    assert res["ok"], res
    pushes = [c[1] for c in adb.cmds if c[0] == "push"]
    assert [Path(p).name for p in pushes] == ["ig_04.png", "ig_03.png", "ig_02.png", "ig_01.png"]
    taps = [tuple(map(int, c[3:5])) for c in adb.cmds if c[:3] == ["shell", "input", "tap"]]
    assert taps[0] == (60, 175) and (75, 1253) in taps and (965, 1396) in taps
    assert taps.index((75, 1253)) < taps.index((965, 1396))  # original ratio before multi-select
    assert (540, 1850) in taps  # 儲存草稿 found via UI dump
    assert taps[-1] == (71, 179)  # leave the new-post screen afterwards
    assert (60, 175) == taps[0] and (135, 1530) not in taps  # never the camera tile
    assert pasted == ["中文文案"]
    assert taps.index((989, 179)) > taps.index((187, 980))  # 完成 after caption
    assert "final.png" in res["shots"] and (tmp_path / "s" / "ig_result.json").is_file()  # fast mode: final only


def test_ig_never_taps_share(tmp_path):
    adb = FakeAdb(share_at=(977, 178))  # pretend 下一步 position shows 分享
    res = ig_adb.save_ig_draft(_cards(tmp_path), "x", tmp_path / "s", runner=adb, sleep=lambda s: None,
                               paste=lambda c, t: None)
    assert not res["ok"] and "分享" in res["error"]
    taps = [tuple(map(int, c[3:5])) for c in adb.cmds if c[:3] == ["shell", "input", "tap"]]
    assert (977, 178) not in taps


def test_ig_wrong_resolution_stops(tmp_path):
    adb = FakeAdb()
    orig = adb.__call__
    def runner(args, binary=False, timeout=60):
        if args[1:3] == ["shell", "wm"]:
            return "Physical size: 1440x3200"
        return orig(args, binary, timeout)
    res = ig_adb.save_ig_draft(_cards(tmp_path), "x", tmp_path / "s", runner=runner, sleep=lambda s: None,
                               paste=lambda c, t: None)
    assert not res["ok"] and "解析度" in res["error"]


def test_dashboard_gmail_auth_never_opens_browser(tmp_path):
    from autopilot.google_auth import get_credentials
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        get_credentials(tmp_path, interactive=False)
    assert "run_real_google.cmd" in str(e.value)


def test_auth_only_saves_token_for_club_account(tmp_path, monkeypatch):
    from autopilot import google_auth
    monkeypatch.setenv("CLUB_GOOGLE_ACCOUNT", "nycu.iec@gmail.com")
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    calls = {}

    class Creds:
        valid = True
        def to_json(self): return '{"token": "x"}'

    class Flow:
        def run_local_server(self, **k):
            calls.update(k)
            return Creds()

    monkeypatch.setattr("google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
                        classmethod(lambda cls, path, scopes: Flow()))
    email = {"v": "nycu.iec@gmail.com"}

    class G:
        def users(self): return self
        def getProfile(self, userId): return self
        def execute(self): return {"emailAddress": email["v"]}

    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: G())
    assert google_auth.authorize(tmp_path, timeout_s=600, force=True) == "nycu.iec@gmail.com"
    assert calls["timeout_seconds"] == 600 and (tmp_path / "token.json").is_file()
    (tmp_path / "token.json").unlink()
    email["v"] = "someone@gmail.com"
    with pytest.raises(SystemExit):
        google_auth.authorize(tmp_path, force=True)
    assert not (tmp_path / "token.json").exists()


def test_all_email_subjects_use_long_title():
    ev = load_event(EV)
    out = render.render_all(ev)
    for f in ("2_預告信.html", "4_提醒信.html", "6_課後信.html"):
        assert render.subject_of(out[f]).endswith("｜" + ev.raw["title"])


class MusicAdb(FakeAdb):
    """Edit screen with an audio pill: 'suggested' (not attached) or 'attached'; remove sheet after pill tap."""

    def __init__(self, music, removable=True):
        super().__init__()
        self.music, self.sheet, self.removable = music, False, removable

    def __call__(self, args, binary=False, timeout=60):
        cmd = args[1:]
        if cmd[:2] == ["exec-out", "cat"]:
            self.cmds.append(cmd)
            nodes = []
            if self.music == "suggested":
                nodes.append('<node text="" content-desc="建議的音訊。某歌" resource-id="com.instagram.android:id/audio_pill_empty_background" bounds="[285,118][795,323]" />')
                nodes.append('<node text="建議音訊" content-desc="" resource-id="com.instagram.android:id/audio_suggested_text" bounds="[470,271][610,323]" />')
            elif self.music == "attached":
                nodes.append('<node text="Step by Step" content-desc="音訊：Step by Step" resource-id="com.instagram.android:id/audio_pill" bounds="[285,118][795,323]" />')
            if self.sheet and self.removable:
                nodes.append('<node text="移除" content-desc="" resource-id="x" bounds="[100,2000][980,2100]" />')
            return "<hierarchy>" + "".join(nodes) + "</hierarchy>"
        if cmd[:3] == ["shell", "input", "tap"]:
            x, y = int(cmd[3]), int(cmd[4])
            if self.music == "attached" and 285 <= x <= 795 and 118 <= y <= 323:
                self.sheet = True
            elif self.sheet and 2000 <= y <= 2100:
                self.music, self.sheet = "none", False
        return super().__call__(args, binary, timeout)


def _phone(adb, tmp_path):
    cfg = ig_adb.load_config()
    cfg["fast"] = False  # these check the per-step screenshots
    return ig_adb.Phone(cfg, tmp_path / "s", adb, lambda s: None, lambda c, t: None)


def test_music_suggestion_is_left_alone(tmp_path):
    adb = MusicAdb("suggested")
    ph = _phone(adb, tmp_path)
    ph.no_music()
    assert not [c for c in adb.cmds if c[:3] == ["shell", "input", "tap"]]
    assert "沒有加入音樂" in ph.log[-1]


def test_attached_music_is_removed_and_verified(tmp_path):
    adb = MusicAdb("attached")
    ph = _phone(adb, tmp_path)
    ph.no_music()
    assert adb.music == "none" and "05b_no_music.png" in ph.shot_files
    assert ph.log[-1] == "已移除 IG 自動加入的音樂"


def test_attached_music_without_remove_button_stops(tmp_path):
    adb = MusicAdb("attached", removable=False)
    with pytest.raises(ig_adb.IGStop):
        _phone(adb, tmp_path).no_music()


def test_config_has_no_music_step_after_edit_and_no_add_music():
    steps = ig_adb.load_config()["steps"]
    names = [s.get("do") for s in steps]
    assert names.index("no_music") == names.index("shot", names.index("no_music") - 1) + 1
    assert not any("音樂" in str(s.get("name", "")) and s.get("do") == "tap" for s in steps)


def test_fast_mode_skips_guard_dumps_for_verified_taps(tmp_path):
    adb = FakeAdb()
    res = ig_adb.save_ig_draft(_cards(tmp_path), "x", tmp_path / "s", runner=adb, sleep=lambda s: None,
                               paste=lambda c, t: None)
    assert res["ok"]
    dumps = sum(1 for c in adb.cmds if c[:3] == ["shell", "uiautomator", "dump"])
    taps = sum(1 for c in adb.cmds if c[:3] == ["shell", "input", "tap"])
    guarded = sum(1 for s in ig_adb.load_config()["steps"] if s.get("guard"))
    assert dumps < taps and dumps <= guarded + 8 and res["shots"] == ["final.png"]


class PublishAdb(FakeAdb):
    """分享 appears on the caption screen after 完成（✓）; tapping it publishes once."""

    def __init__(self):
        super().__init__(draft=False)
        self.caption_done = False
        self.shares = 0

    def __call__(self, args, binary=False, timeout=60):
        cmd = args[1:]
        if cmd[:3] == ["shell", "input", "tap"]:
            x, y = int(cmd[3]), int(cmd[4])
            if (x, y) == (989, 179):
                self.caption_done = True
            elif self.caption_done and 2170 <= y <= 2230:
                self.shares += 1
                self.caption_done = False
        if cmd[:2] == ["exec-out", "cat"] and self.caption_done:
            self.cmds.append(cmd)
            return '<hierarchy><node text="分享" content-desc="" bounds="[40,2170][1040,2230]" /></hierarchy>'
        return super().__call__(args, binary, timeout)


def test_ig_publish_only_when_asked_taps_share_once(tmp_path):
    adb = PublishAdb()
    res = ig_adb.save_ig_draft(_cards(tmp_path), "x", tmp_path / "s", runner=adb, sleep=lambda s: None,
                               paste=lambda c, t: None, publish=True)
    assert res["ok"] and res["published"], res
    assert adb.shares == 1
    taps = [tuple(map(int, c[3:5])) for c in adb.cmds if c[:3] == ["shell", "input", "tap"]]
    assert (333, 1396) not in taps and (71, 179) not in taps


def test_ig_default_still_never_publishes(tmp_path):
    adb = PublishAdb()
    res = ig_adb.save_ig_draft(_cards(tmp_path), "x", tmp_path / "s", runner=adb, sleep=lambda s: None,
                               paste=lambda c, t: None)
    assert adb.shares == 0 and not res.get("published")
