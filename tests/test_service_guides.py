"""Guided LINE, phone, Linktree and Canva onboarding. No live LINE or adb calls."""
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from autopilot import i18n, line_capture, phone_setup
from autopilot.canva_templates import IG_TEMPLATE, cert_template_id, design_id_from_text, ig_template_id
from autopilot.certificate import CANVA_TEMPLATE_DESIGN, certificate_template_id
from autopilot.launchpad_i18n import T
from autopilot.web import create_app

ROOT = Path(__file__).resolve().parent.parent

@pytest.fixture
def client(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv("AUTOPILOT_PRIVATE_DIR", str(private))
    monkeypatch.setenv("AUTOPILOT_ONBOARDING_CONFIG", str(private / "autopilot_local.yaml"))
    app = create_app(root=ROOT, out_root=tmp_path / "out", events_dir=ROOT / "tests" / "fixtures")
    app.config["TESTING"] = True
    return app.test_client()


CJK = i18n.CJK
VOID = ("input", "img", "br", "meta", "link", "hr", "source")


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


def _untranslated(html: str) -> list[str]:
    parser = _Texts()
    parser.feed(html)
    return sorted({t.strip() for t in parser.out if CJK.search(i18n.translate(t))})


def test_webhook_group_parse_and_signature():
    body = json.dumps({
        "events": [
            {"type": "message", "source": {"type": "user", "userId": "U1"}},
            {"type": "message", "source": {"type": "group", "groupId": "Cgroup1"}, "message": {"type": "text", "text": "綁定"}},
            {"type": "message", "source": {"type": "group", "groupId": "Cgroup1"}},
        ]
    }).encode()
    assert line_capture.group_ids_from_payload(json.loads(body)) == ["Cgroup1"]
    secret = "channel-secret"
    good = line_capture.sign(body, secret)
    assert line_capture.signature_ok(body, secret, good)
    assert not line_capture.signature_ok(body, secret, "nope")
    assert not line_capture.signature_ok(body, "", good)
    assert line_capture.mask_secret(secret) == "••••cret"
    assert "channel-secret" not in line_capture.mask_secret(secret)


def test_bot_info_and_webhook_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.delenv("LINE_BOT_CONFIG", raising=False)
    seen = {}

    def opener(req, timeout=20):
        class Resp:
            def __init__(self, status, payload):
                self.status = status
                self._raw = json.dumps(payload).encode()

            def read(self):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        url = req.full_url
        auth = req.get_header("Authorization") or ""
        assert "good-token" not in url
        if url.endswith("/v2/bot/info"):
            if auth.endswith("good-token"):
                return Resp(200, {"displayName": "Club Bot", "pictureUrl": "https://example.com/bot.png", "basicId": "@club"})
            return Resp(401, {})
        if url.endswith("/webhook/endpoint") and req.get_method() == "GET":
            return Resp(200, {"endpoint": "https://old.example/hook", "active": True})
        if url.endswith("/webhook/endpoint") and req.get_method() == "PUT":
            seen["put"] = json.loads(req.data.decode())
            return Resp(200, {})
        if "/group/Cgroup1/summary" in url:
            return Resp(200, {"groupName": "Club room"})
        return Resp(404, {})

    bad = line_capture.bot_info("nope", opener)
    assert bad["ok"] is False
    assert "nope" not in bad["message"]
    good = line_capture.bot_info("good-token", opener)
    assert good["ok"] is True and good["display_name"] == "Club Bot"
    assert good["masked"] == "••••oken"
    line_capture.save_secrets("good-token", "channel-secret")
    saved = json.loads(line_capture.secrets_path().read_text(encoding="utf-8"))
    assert saved["channel_access_token"] == "good-token"
    started = line_capture.begin_capture(5050, opener, starter=lambda port: {"ok": True, "url": "https://demo.trycloudflare.com"})
    assert started["mode"] == "tunnel"
    assert seen["put"]["endpoint"] == "https://demo.trycloudflare.com/api/line/webhook"
    line_capture.remember_group("Cgroup1")
    done = line_capture.finish_capture(opener)
    assert done["group_name"] == "Club room"
    assert seen["put"]["endpoint"] == "https://old.example/hook"
    manual = line_capture.begin_capture(5050, opener, starter=lambda port: {"ok": False, "url": ""})
    assert manual["mode"] == "manual"
    assert line_capture.parse_tunnel_url("log https://abc.trycloudflare.com ready") == "https://abc.trycloudflare.com"


def test_webhook_route_rejects_bad_signature(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOPILOT_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.delenv("LINE_BOT_CONFIG", raising=False)
    line_capture.reset_state()
    line_capture.save_secrets("good-token", "channel-secret")
    body = json.dumps({"events": [{"type": "message", "source": {"type": "group", "groupId": "Cfromhook"}}]}).encode()
    bad = client.post("/api/line/webhook", data=body, headers={"X-Line-Signature": "nope", "Content-Type": "application/json"})
    assert bad.status_code == 401
    ok = client.post("/api/line/webhook", data=body, headers={
        "X-Line-Signature": line_capture.sign(body, "channel-secret"),
        "Content-Type": "application/json",
    })
    assert ok.status_code == 200
    assert line_capture.snapshot()["group_id"] == "Cfromhook"


def test_phone_detect_states():
    text = "List of devices attached\nABC123\tunauthorized\n"
    parsed = phone_setup.describe_devices(text, True)
    assert parsed["kind"] == "unauthorized"
    assert parsed["message"] == T["adb_unauthorized"]
    many = phone_setup.describe_devices("List\nONE\tdevice\nTWO\tdevice\n", True)
    assert many["kind"] == "many"
    assert [d["serial"] for d in many["devices"]] == ["ONE", "TWO"]
    one = phone_setup.describe_devices("List\nPHONE1\tdevice\n", True)
    assert one["serial"] == "PHONE1"
    assert phone_setup.describe_devices("", False)["kind"] == "no-adb"


def test_canva_design_id_and_defaults():
    assert design_id_from_text("https://www.canva.com/design/DAGzIgWU-E4/edit") == "DAGzIgWU-E4"
    assert design_id_from_text("https://www.canva.com/design/DAHSJ-8pyXw/view?utm=1") == "DAHSJ-8pyXw"
    assert design_id_from_text("not a link") == ""
    assert ig_template_id(use_sample=True) == IG_TEMPLATE
    assert cert_template_id("") == CANVA_TEMPLATE_DESIGN
    assert certificate_template_id() == CANVA_TEMPLATE_DESIGN


def test_onboarding_sample_is_fictional_and_explained(client):
    from autopilot.event import load_event
    import autopilot.launchpad as launchpad
    path = launchpad.sample_source()
    text = path.read_text(encoding="utf-8")
    assert path.as_posix().endswith("samples/demo-club/lecture.yaml")
    assert "示範大學創業社" in text
    assert "example.com" in text
    assert "nycu.iec" not in text and "謝慈" not in text
    assert (path.parent / "speaker.png").is_file()
    assert (path.parent / "logo.png").is_file()
    load_event(path)
    client.post("/onboarding", data={"action": "next", "step": "welcome"})
    client.post("/onboarding", data={
        "action": "next", "step": "profile",
        "club_name": "North Campus Builders", "contact_name": "Club Officer",
        "contact_email": "officer@example.com", "brand_color": "#1255aa", "tagline": "Hello",
    })
    for step in ("google", "line", "linktree", "instagram", "canva"):
        client.post("/onboarding", data={"action": "skip", "step": step})
    page = client.get("/onboarding").get_data(as_text=True)
    assert "這是什麼" in page and "按下之後會怎樣" in page
    assert "下一步拿它做什麼" in page and "之後怎麼換成自己的活動" in page
    assert "示範大學創業社" in page and "林可恩" in page
    assert "2027-06-16" in page


def test_action_buttons_are_not_pills(client):
    pages = [
        client.get("/onboarding").get_data(as_text=True),
        client.get("/modules").get_data(as_text=True),
        client.get("/").get_data(as_text=True),
    ]
    for html in pages:
        assert "border-radius:999px" not in html
        assert 'class="action-col"' in html
        assert 'class="page-layout"' in html
        assert 'class="btn"' in html or "btn ghost" in html or "btn dark" in html


def test_skip_line_and_guides_translate(client):
    client.post("/onboarding", data={"action": "next", "step": "welcome"})
    client.post("/onboarding", data={
        "action": "next", "step": "profile",
        "club_name": "North Campus Builders", "contact_name": "Club Officer",
        "contact_email": "officer@example.com", "brand_color": "#1255aa", "tagline": "Hello",
    })
    client.post("/onboarding", data={"action": "test", "step": "google", "forms_account": "", "club_email": ""})
    page = client.post("/onboarding", data={"action": "next", "step": "google", "forms_account": "", "club_email": ""}, follow_redirects=True)
    html = page.get_data(as_text=True)
    assert "你已經有 LINE 官方帳號嗎" in html
    assert "iPhone" in html or "Android" in html or "LINE" in html
    assert not _untranslated(html), _untranslated(html)
    chosen = client.post("/onboarding", data={"action": "account_choice", "step": "line", "choice": "no"}, follow_redirects=True)
    body = chosen.get_data(as_text=True)
    assert "https://entry.line.biz/" in body
    assert "https://developers.line.biz/console/" in body
    assert "允許加入群組" in body
    assert not _untranslated(body)
    skipped = client.post("/onboarding", data={"action": "skip", "step": "line"}, follow_redirects=True)
    assert "Linktree" in skipped.get_data(as_text=True)
    fav = client.get("/favicon.ico")
    assert fav.status_code == 200
    assert fav.mimetype == "image/png"
