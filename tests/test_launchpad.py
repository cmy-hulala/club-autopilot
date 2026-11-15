"""Onboarding wizard and module catalog. Nothing here may write inside the git repo."""
import hashlib
import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import yaml

from autopilot import i18n, launchpad
from autopilot.launchpad_i18n import EN, T
from autopilot.web import create_app

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "2026-11-25-candice.yaml"
FAKE_SETTINGS = ROOT / "tests" / "local_settings_fake.yaml"
CJK = i18n.CJK
VOID = ("input", "img", "br", "meta", "link", "hr", "source")
PROFILE = {
    "club_name": "North Campus Builders",
    "contact_name": "Club Officer",
    "contact_email": "officer@example.com",
    "brand_color": "#1255aa",
    "tagline": "We build on Thursdays",
}


class _Texts(HTMLParser):
    SKIP = {"script", "style", "pre", "textarea", "iframe"}

    def __init__(self):
        super().__init__()
        self.stack, self.out = [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        skip = tag in self.SKIP or "data-no-i18n" in a or bool(self.stack and self.stack[-1])
        if not skip:
            for key in ("placeholder", "title", "alt", "data-confirm"):
                if a.get(key):
                    self.out.append(a[key])
        if tag not in VOID:
            self.stack.append(skip)

    def handle_endtag(self, tag):
        if tag not in VOID and self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if data.strip() and not (self.stack and self.stack[-1]):
            self.out.append(data)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _untranslated(html: str) -> list[str]:
    parser = _Texts()
    parser.feed(html)
    return sorted({t.strip() for t in parser.out if CJK.search(i18n.translate(t))})


@pytest.fixture
def app(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv("AUTOPILOT_PRIVATE_DIR", str(private))
    monkeypatch.setenv("AUTOPILOT_ONBOARDING_CONFIG", str(private / "autopilot_local.yaml"))
    application = create_app(root=ROOT, out_root=tmp_path / "out", events_dir=ROOT / "tests" / "fixtures")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def _stub_checks(monkeypatch):
    def _check(step, settings):
        return {"status": "stub", "message": T["check_stub"]}

    monkeypatch.setattr(launchpad, "check_service", _check)


def _block_live_sends(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("live send")

    monkeypatch.setattr("autopilot.line_push.push_text", boom)
    monkeypatch.setattr("autopilot.gmail_api.create_draft", boom)
    monkeypatch.setattr("autopilot.forms_api.create_form", boom)
    monkeypatch.setattr("autopilot.linktree.run", boom)


def _post(client, **data):
    return client.post("/onboarding", data=data, follow_redirects=True)


def test_header_and_finish_use_the_saved_club_not_a_hardcoded_one(client, app):
    home = client.get("/").get_data(as_text=True)
    header = home.split("<header", 1)[1].split("</header>", 1)[0]
    assert "社團活動自動化" in header
    assert "陽明交大" not in header
    assert "NYCU" not in header
    assert "club_logo.png" not in header
    store = launchpad.Store(app.config["OUT"], app.config["ROOT"])
    progress = launchpad.fresh_progress()
    progress["current"] = "done"
    progress["profile"] = dict(PROFILE)
    store.save_progress(progress)
    html = client.get("/onboarding").get_data(as_text=True)
    assert "North Campus Builders" in html
    assert "完成設定並儲存" in html
    assert "不會上傳" in html
    assert "不會寄出或發送" in html
    assert "autopilot_local.yaml" in html
    assert "寫入本機設定" not in html
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert "陽明交大" not in header
    assert "North Campus Builders" in header
    fav = client.get("/favicon.ico")
    assert fav.status_code == 200
    logo = ROOT / "assets" / "club_logo.png"
    assert fav.data != logo.read_bytes()


def test_strings_translate_and_do_not_clobber_existing_copy():
    assert i18n.translate("創創社") == "NYCU IEC"
    assert i18n.translate("陽明交大創創社社課自動化") == "NYCU IEC lecture automation"
    assert i18n.translate("取消") == "Cancel"
    assert i18n.translate("完成") == "done"
    assert i18n.translate("講者感謝狀") == "Speaker certificate"
    assert not CJK.search(i18n.translate("步驟 2 / 10"))
    for key, zh in T.items():
        assert not CJK.search(i18n.translate(zh)), (key, zh, i18n.translate(zh))
        if CJK.search(zh):
            assert i18n.PHRASES[zh] == EN[key]


def test_config_destination_never_points_inside_the_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_PRIVATE_DIR", str(tmp_path / "priv"))
    monkeypatch.delenv("AUTOPILOT_ONBOARDING_CONFIG", raising=False)
    dest = launchpad.config_destination()
    assert dest == (tmp_path / "priv" / "autopilot_local.yaml").resolve()
    assert not launchpad.is_inside_repo(dest)
    monkeypatch.setenv("AUTOPILOT_ONBOARDING_CONFIG", str(ROOT / "config" / "local.yaml"))
    with pytest.raises(launchpad.UnsafeConfigPath):
        launchpad.config_destination()
    monkeypatch.setenv("AUTOPILOT_PRIVATE_DIR", str(ROOT / "config"))
    with pytest.raises(launchpad.UnsafeConfigPath):
        launchpad.private_dir()


def test_sample_copy_is_not_allowed_into_tracked_events():
    assert launchpad.output_allowed(ROOT / "out" / "_web" / launchpad.SAMPLE_NAME, ROOT)
    assert not launchpad.output_allowed(ROOT / "events" / launchpad.SAMPLE_NAME, ROOT)
    assert launchpad.output_allowed(Path("/tmp/sample-club-lecture.yaml"), ROOT)


def test_wizard_validates_profile_and_resumes(client, app):
    first = client.get("/onboarding")
    assert first.status_code == 200
    assert "歡迎" in first.get_data(as_text=True)
    skipped = _post(client, action="goto", step="done")
    assert skipped.status_code == 400
    assert "請先完成前面的步驟" in skipped.get_data(as_text=True)
    welcome = _post(client, action="next", step="welcome")
    assert welcome.status_code == 200
    assert "社團名稱" in welcome.get_data(as_text=True)
    bad = _post(client, action="next", step="profile", club_name="", contact_email="not-an-email", brand_color="red")
    assert bad.status_code == 400
    html = bad.get_data(as_text=True)
    assert "請填社團名稱" in html
    assert "聯絡 Email 格式不對" in html
    assert "品牌色請用" in html
    ok = _post(client, action="next", step="profile", **PROFILE)
    assert ok.status_code == 200
    assert "不會連到 Google" in ok.get_data(as_text=True)
    resumed = app.test_client().get("/onboarding")
    body = resumed.get_data(as_text=True)
    assert "不會連到 Google" in body
    assert "North Campus Builders" in body
    back = _post(client, action="back")
    assert "社團名稱" in back.get_data(as_text=True)
    reset = _post(client, action="reset")
    assert "歡迎使用社團自動駕駛" in reset.get_data(as_text=True)


def test_wizard_dry_run_sends_nothing_and_writes_outside_the_repo(client, tmp_path, monkeypatch):
    _stub_checks(monkeypatch)
    _block_live_sends(monkeypatch)
    before_fixture = _sha(FIXTURE)
    before_fake = _sha(FAKE_SETTINGS)
    events_before = sorted(p.name for p in (ROOT / "events").iterdir())
    _post(client, action="next", step="welcome")
    _post(client, action="next", step="profile", **PROFILE)
    for step, extra in (
        ("google", {"forms_account": "officer@example.com", "club_email": "club@example.com"}),
        ("line", {"group_name": "", "group_id": ""}),
        ("linktree", {"url": "https://linktr.ee/north-campus"}),
        ("instagram", {"adb_serial": ""}),
        ("canva", {"note": "Thursday template"}),
    ):
        tested = _post(client, action="test", step=step, **extra)
        assert tested.status_code == 200
        assert "模擬測試" in tested.get_data(as_text=True) or "沒有憑證" in tested.get_data(as_text=True)
        nxt = _post(client, action="next", step=step, **extra)
        assert nxt.status_code == 200, nxt.get_data(as_text=True)
    blocked = _post(client, action="next", step="sample")
    assert blocked.status_code == 400
    assert "請先匯入範例活動" in blocked.get_data(as_text=True)
    imported = _post(client, action="import_sample", step="sample")
    assert imported.status_code == 200
    assert "已匯入範例活動" in imported.get_data(as_text=True)
    assert (tmp_path / "private" / "events" / "sample-club-lecture.yaml").is_file()
    assert (tmp_path / "out" / "_web" / "sample-club-lecture.yaml").is_file()
    assert not (ROOT / "events" / "sample-club-lecture.yaml").exists()
    _post(client, action="next", step="sample")
    dry = _post(client, action="dry_run", step="dryrun")
    assert dry.status_code == 200
    html = dry.get_data(as_text=True)
    assert "沒有寄信、沒有發 LINE、沒有發 Instagram" in html
    assert "預覽演練沒有送出任何東西" in html
    _post(client, action="next", step="dryrun")
    done = _post(client, action="finish", step="done")
    assert done.status_code == 200
    saved_page = done.get_data(as_text=True)
    assert "沒有上傳" in saved_page
    assert "不會寄出或發送" in saved_page or "沒有寄出或發送" in saved_page
    config_path = tmp_path / "private" / "autopilot_local.yaml"
    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["club_name"] == "North Campus Builders"
    assert saved["contact_email"] == "officer@example.com"
    assert saved["dry_run_sent"] is False
    assert saved["onboarding_completed"] is True
    assert saved["linktree_url"] == "https://linktr.ee/north-campus"
    assert "channel_access_token" not in saved
    assert "refresh_token" not in saved
    blob = config_path.read_text(encoding="utf-8")
    assert "super-secret" not in blob
    progress = json.loads((tmp_path / "private" / "onboarding_progress.json").read_text(encoding="utf-8"))
    assert progress["finished"] is True
    assert progress["dry_run"]["sent"] is False
    assert progress["dry_run"]["ok"] is True
    event = client.get("/event/sample-club-lecture")
    assert event.status_code == 200
    progress = json.loads((tmp_path / "private" / "onboarding_progress.json").read_text(encoding="utf-8"))
    assert progress["finished"] is True
    home = client.get("/")
    home_html = home.get_data(as_text=True)
    assert "從 Notion 匯入" in home_html
    assert 'href="/onboarding">第一次使用？從初次設定開始。' not in home_html
    assert _sha(FIXTURE) == before_fixture
    assert _sha(FAKE_SETTINGS) == before_fake
    assert sorted(p.name for p in (ROOT / "events").iterdir()) == events_before
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", "events", "tests/fixtures", "tests/local_settings_fake.yaml"],
        cwd=ROOT, text=True,
    )
    assert dirty == ""


def test_write_config_keeps_existing_keys_and_blank_fields(tmp_path, monkeypatch):
    private = tmp_path / "priv"
    private.mkdir()
    monkeypatch.setenv("AUTOPILOT_PRIVATE_DIR", str(private))
    monkeypatch.setenv("AUTOPILOT_ONBOARDING_CONFIG", str(private / "autopilot_local.yaml"))
    dest = private / "autopilot_local.yaml"
    dest.write_text("forms_account: keep-me@example.com\nnotion_page_id: abc123\n", encoding="utf-8")
    store = launchpad.Store(tmp_path / "out", ROOT)
    progress = launchpad.fresh_progress()
    progress["profile"].update(PROFILE)
    progress["services"]["google"]["forms_account"] = ""
    store.write_config(progress, store.load_modules())
    data = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert data["forms_account"] == "keep-me@example.com"
    assert data["notion_page_id"] == "abc123"
    assert data["club_name"] == "North Campus Builders"
    assert data["dry_run_sent"] is False


def test_template_download_has_no_secrets(client):
    response = client.get("/onboarding/template.yaml")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "Your Club Name" in text
    assert "officer@example.com" in text
    for secret in ("channel_access_token", "client_secret", "refresh_token", "private_key"):
        assert secret not in text
    assert not (ROOT / "autopilot_local.yaml").exists()
    assert not (ROOT / "autopilot_local.template.yaml").exists()


def test_connection_checks_are_stubs_without_credentials_and_never_send(monkeypatch):
    monkeypatch.setattr(launchpad, "_token_candidates", lambda: [])
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.setattr(launchpad.shutil, "which", lambda _name: None)

    def boom(*_a, **_k):
        raise AssertionError("live send")

    monkeypatch.setattr("autopilot.line_push.push_text", boom)
    monkeypatch.setattr("autopilot.line_push.public_status", lambda: {"configured": False, "missing": ["LINE_CHANNEL_ACCESS_TOKEN"], "groups": []})
    google = launchpad.check_service("google", {})
    assert google["status"] == "stub"
    assert google["message"] == T["check_stub"]
    line = launchpad.check_service("line", {})
    assert line["status"] == "stub"
    phone = launchpad.check_service("instagram", {"adb_serial": "ABC123"})
    assert phone["status"] == "stub"
    assert phone["message"] == T["check_phone_stub"]
    notion = launchpad.check_service("notion", {})
    assert notion["status"] == "stub"
    canva = launchpad.check_service("canva", {})
    assert canva == {"status": "stub", "message": T["check_canva"]}
    banner = launchpad.check_service("banner", {})
    assert banner["status"] == "stub"
    empty_url = launchpad.check_service("linktree", {"url": ""})
    assert empty_url["status"] == "stub"
    good_url = launchpad.check_service("linktree", {"url": "https://linktr.ee/example"})
    assert good_url["status"] == "ok"
    monkeypatch.setattr("autopilot.line_push.public_status", lambda: {"configured": True, "missing": [], "groups": ["Club"]})
    ready = launchpad.check_service("line", {})
    assert ready["status"] == "ok"
    assert "token" not in ready["message"].lower()


def test_google_file_check_does_not_echo_the_secret(tmp_path, monkeypatch):
    secret = "super-secret-token-value"
    good = tmp_path / "token-officer.json"
    good.write_text(json.dumps({"token": secret, "refresh_token": secret}), encoding="utf-8")
    monkeypatch.setattr(launchpad, "_token_candidates", lambda: [good])
    found = launchpad.check_service("gmail", {})
    assert found["status"] == "ok"
    assert secret not in found["message"]
    inside = ROOT / "out" / "_lp_token_test.json"
    inside.parent.mkdir(parents=True, exist_ok=True)
    try:
        inside.write_text(json.dumps({"token": secret}), encoding="utf-8")
        monkeypatch.setattr(launchpad, "_token_candidates", lambda: [inside])
        repo = launchpad.check_service("google_forms", {})
        assert repo["status"] == "stub"
        assert repo["message"] == T["check_repo"]
        assert secret not in repo["message"]
    finally:
        inside.unlink(missing_ok=True)


def test_phone_check_detects_a_device_without_tapping(monkeypatch):
    monkeypatch.setattr(launchpad.shutil, "which", lambda name: "/usr/bin/adb" if name == "adb" else None)

    class _Proc:
        stdout = "List of devices attached\nABC123\tdevice\n"

    monkeypatch.setattr(launchpad.subprocess, "run", lambda *_a, **_k: _Proc())
    assert launchpad.check_service("instagram", {"adb_serial": "ABC123"})["status"] == "ok"
    assert launchpad.check_service("instagram", {"adb_serial": ""})["status"] == "ok"
    missed = launchpad.check_service("instagram", {"adb_serial": "NOPE"})
    assert missed["status"] == "error"
    assert missed["message"] == T["check_phone_miss"]


def test_module_catalog_toggle_validation_and_test(client, tmp_path, monkeypatch):
    monkeypatch.setattr(launchpad, "check_service", lambda *_a, **_k: {"status": "stub", "message": T["check_stub"]})
    page = client.get("/modules")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for name in ("Notion 匯入", "Google 表單", "Gmail 草稿", "LINE 推播", "Linktree", "Canva 圖卡", "講者感謝狀"):
        assert name in html
    assert "已啟用" in html
    assert "測試連線" in html
    bad = client.post("/modules/google_forms", data={"action": "save", "forms_account": "not-an-email"})
    assert bad.status_code == 400
    assert "個人帳號 Email 格式不對" in bad.get_data(as_text=True)
    assert "value=\"not-an-email\"" in bad.get_data(as_text=True) or "not-an-email" in bad.get_data(as_text=True)
    saved = client.post(
        "/modules/google_forms",
        data={"action": "save", "forms_account": "officer@example.com"},
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert "已儲存這個模組的設定" in saved.get_data(as_text=True)
    tested = client.post("/modules/line", data={"action": "test", "group_name": "", "group_id": ""}, follow_redirects=True)
    assert "沒有憑證，這是模擬測試" in tested.get_data(as_text=True)
    assert "role=\"status\"" in tested.get_data(as_text=True)
    spaced = client.post("/modules/line", data={"action": "save", "group_name": "Club", "group_id": "has space"})
    assert spaced.status_code == 400
    assert "群組 ID 不能有空白" in spaced.get_data(as_text=True)
    off = client.post("/modules/line", data={"action": "toggle", "enabled": "0"}, follow_redirects=True)
    assert "已停用這個模組" in off.get_data(as_text=True)
    assert "已停用" in off.get_data(as_text=True)
    state = yaml.safe_load((tmp_path / "private" / "modules_state.yaml").read_text(encoding="utf-8"))
    assert state["modules"]["line"]["enabled"] is False
    assert state["modules"]["google_forms"]["settings"]["forms_account"] == "officer@example.com"
    assert client.post("/modules/not-a-module", data={"action": "toggle", "enabled": "0"}).status_code == 404
    home = client.get("/")
    assert "從 Notion 匯入" in home.get_data(as_text=True)


def test_onboarding_and_module_pages_translate(client):
    client.set_cookie("lang", "en")
    for path in ("/onboarding", "/modules", "/"):
        html = client.get(path).get_data(as_text=True)
        left = _untranslated(html)
        assert not left, (path, left)


def test_send_routes_are_unchanged(client):
    rules = [rule.rule for rule in client.application.url_map.iter_rules()]
    sends = [rule for rule in rules if "send" in rule.lower()]
    assert sorted(sends) == ["/api/drafts/<slug>/send-announce", "/api/line/<slug>/send"]
