import pytest
from pathlib import Path

import yaml

from autopilot.event import load_event
from autopilot.notion_import import list_from_yaml, list_lectures
from autopilot.pipeline import run_pipeline
from autopilot.web import create_app

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "tests" / "fixtures" / "2026-11-25-candice.yaml"
SLUG = "2026-11-25-candice"


def _client(tmp_path):
    app = create_app(root=ROOT, out_root=tmp_path, events_dir=ROOT / "tests" / "fixtures")
    app.config["TESTING"] = True
    return app.test_client()


def test_yaml_fallback_lists_candice_without_notion_token(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.setattr("autopilot.notion_import.load_token", lambda: None)
    data = list_lectures(ROOT / "tests" / "fixtures")
    assert data["source"] == "yaml_offline"
    assert data["token_present"] is False
    slugs = [c["slug"] for c in data["lectures"]]
    assert SLUG in slugs
    card = next(c for c in data["lectures"] if c["slug"] == SLUG)
    assert "謝慈" in card["speaker"]
    assert card["date"] == "2026-11-25"
    # list_from_yaml is the offline building block
    assert any(c["slug"] == SLUG for c in list_from_yaml(ROOT / "tests" / "fixtures"))


def test_index_shows_import_and_readonly_event(tmp_path):
    # Seed a preview so the first GET does not have to run the full pipeline.
    run_pipeline(EV, "preview", tmp_path)
    c = _client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "從 Notion 匯入" in html
    assert "Club Autopilot" in html
    header = html.split("<header", 1)[1].split("</header>", 1)[0]
    assert "club_logo.png" not in header
    assert "陽明交大" not in header
    assert "社團活動自動化" in header
    assert "謝慈" in html
    assert "從職涯迷惘" in html
    assert "截止提醒" not in html
    # Fields are shown read-only; the editor is hidden until toggled.
    assert "id=\"kv-ro\"" in html
    assert "id=\"edit-form\" class=\"hidden\"" in html
    # No deadline-reminder email tab or file
    assert "4a_截止提醒信" not in html
    assert "預告信" in html and "當天提醒信" in html and "課後信" in html


def test_api_lectures_and_known_forms_drafts_marked_done(tmp_path, monkeypatch):
    monkeypatch.setattr("autopilot.notion_import.load_token", lambda: None)
    run_pipeline(EV, "preview", tmp_path)
    c = _client(tmp_path)
    r = c.get("/api/lectures")
    assert r.status_code == 200
    data = r.get_json()
    assert data["source"] == "yaml_offline"
    assert SLUG in [x["slug"] for x in data["lectures"]]
    # Accessing the event page seeds known form / draft IDs into state.json
    page = c.get(f"/event/{SLUG}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "FAKESIGNUPFORMID-0000000000000000000000000" in html
    assert "FAKEFEEDBACKFORMID-000000000000000000000000" in html
    assert "r1000000000000000001" in html
    assert "r-1000000000000000002" in html
    assert "r-1000000000000000003" in html
    assert "已完成" in html
    st = c.get(f"/api/status/{SLUG}")
    body = st.get_json()
    status = {a["key"]: a["status"] for a in body["actions"]}
    assert status["forms"] == "done"
    assert status["drafts"] == "done"
    # State persisted on disk
    saved = (tmp_path / SLUG / "state.json").read_text(encoding="utf-8")
    assert "FAKESIGNUPFORMID-0000000000000000000000000" in saved
    assert "r1000000000000000001" in saved


def test_no_send_route(tmp_path):
    c = _client(tmp_path)
    rules = [rule.rule for rule in c.application.url_map.iter_rules()]
    # The only send route is the LINE 「直接發送」 button (manual, confirmed). No mail/IG send routes.
    sends = [r for r in rules if "send" in r.lower()]
    assert sorted(sends) == ["/api/drafts/<slug>/send-announce", "/api/line/<slug>/send"]
    assert c.post("/send").status_code == 404
    assert c.post(f"/event/{SLUG}/send").status_code == 404


def test_checker_violations_render_red(tmp_path):
    import json
    run_pipeline(EV, "preview", tmp_path)
    c = _client(tmp_path)
    # First load may refresh the preview once form URLs are seeded into state.
    assert c.get(f"/event/{SLUG}").status_code == 200
    runs = sorted((tmp_path / SLUG / "runs").iterdir(), key=lambda p: p.name, reverse=True)
    latest = next(p for p in runs if (p / "check.json").is_file())
    rows = json.loads((latest / "check.json").read_text(encoding="utf-8"))
    rows.insert(0, {"level": "error", "file": "2_預告信.html", "rule": "信裡不寫費用", "detail": "測試用違規"})
    (latest / "check.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
    manifest["check"]["error"] = manifest["check"].get("error", 0) + 1
    (latest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    # Keep placeholders out so the page does not regenerate and wipe the injection.
    html_path = latest / "outputs" / "2_預告信.html"
    html_path.write_text(html_path.read_text(encoding="utf-8").replace("{{", "［［"), encoding="utf-8")
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "違規" in html
    assert "測試用違規" in html
    assert "tr class=\"error\"" in html or "class=\"error\"" in html



def test_import_selects_existing_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr("autopilot.notion_import.load_token", lambda: None)
    c = _client(tmp_path)
    r = c.post("/api/import", data={"slug": SLUG})
    assert r.status_code == 200
    body = r.get_json()
    assert body["slug"] == SLUG
    assert SLUG in body["redirect"]
    # Import only reads the lecture; copy is generated by its own step (no preview here)
    assert not list((tmp_path / SLUG / "runs").glob("*-preview"))
    st = c.get(f"/api/status/{SLUG}").get_json()
    acts = {a["key"]: a for a in st["actions"]}
    assert acts["import"]["status"] == "done"
    assert all(acts[k]["status"] == "idle" for k in ("preview", "canva", "ig", "cert"))


def test_blank_start_from_notion_export(tmp_path, monkeypatch):
    """Nothing imported yet: the lecture comes from the Notion export, opening the page creates nothing."""
    monkeypatch.setattr("autopilot.notion_import.load_token", lambda: None)
    cache = tmp_path / "notion_cache"
    cache.mkdir()
    (cache / f"{SLUG}.yaml").write_text(
        (ROOT / "samples" / "notion_export" / f"{SLUG}.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("CLUB_NOTION_CACHE", str(cache))
    empty = tmp_path / "events"
    empty.mkdir()
    out = tmp_path / "out"
    app = create_app(root=ROOT, out_root=out, events_dir=empty)
    c = app.test_client()
    data = list_lectures(empty)
    assert data["source"] == "notion_cache" and data["lectures"][0]["slug"] == SLUG
    r = c.post("/api/import", data={"slug": SLUG})
    assert r.status_code == 200
    assert (out / "_web" / f"{SLUG}.yaml").is_file()
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    acts = {a["key"]: a for a in c.get(f"/api/status/{SLUG}").get_json()["actions"]}
    assert acts["import"]["status"] == "done" and "Notion 匯出（離線）" in acts["import"]["message"]
    assert not (out / SLUG / "runs").exists()          # no auto preview on page open
    assert "還沒有匯出的圖卡" in html and "<figure>\n" not in html   # no cards before the Canva/IG steps run


def test_save_edit_writes_web_copy(tmp_path):
    run_pipeline(EV, "preview", tmp_path)
    c = _client(tmp_path)
    ev = load_event(EV)
    r = c.post(f"/event/{SLUG}/save", data={
        "session_no": ev.raw["session_no"],
        "title": ev.raw["title"],
        "speaker.name": ev.raw["speaker"]["name"],
        "speaker.title": ev.raw["speaker"]["title"],
        "date": str(ev.raw["date"]),
        "start": ev.raw["start"],
        "end": ev.raw["end"],
        "room": "交映樓 703（測試）",
        "deadline": str(ev.raw["deadline"]),
        "hook": ev.raw.get("hook") or "",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    edited = yaml.safe_load((tmp_path / "_web" / f"{SLUG}.yaml").read_text(encoding="utf-8"))
    assert edited["room"] == "交映樓 703（測試）"


def test_banner_and_qr_status_derived_from_artifacts(tmp_path, monkeypatch):
    import json

    run_pipeline(EV, "preview", tmp_path)
    c = _client(tmp_path)
    assert c.get(f"/event/{SLUG}").status_code == 200

    shots = tmp_path / "form_banner_shots"
    shots.mkdir()
    (shots / "form_banner_check_signup.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 20)
    (shots / "form_banner_check_feedback.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 20)
    (shots / "result.json").write_text(json.dumps({
        "ok": True,
        "shots": str(shots),
        "forms": [
            {"form_id": "FAKESIGNUPFORMID-0000000000000000000000000", "kind": "signup",
             "ok": True, "banner": True, "confirmation": True},
            {"form_id": "FAKEFEEDBACKFORMID-000000000000000000000000", "kind": "feedback",
             "ok": True, "banner": True, "confirmation": True},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    qr_dir = tmp_path / SLUG / "qr"
    qr_dir.mkdir(parents=True, exist_ok=True)
    (qr_dir / "ig_04.png").write_bytes(b"fake-ig04")
    signup = "https://docs.google.com/forms/d/e/1FAIpQLSFAKEFORMID-signup-0000000000000000000000000000/viewform"
    monkeypatch.setattr("autopilot.canva_step._decode_qr_png", lambda path: signup)

    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    body = c.get(f"/api/status/{SLUG}").get_json()
    status = {a["key"]: a for a in body["actions"]}
    assert status["forms"]["status"] == "done"
    assert status["drafts"]["status"] == "done"
    assert status["banner"]["status"] == "done"
    assert "Banner" in status["banner"]["message"]
    assert "確認訊息" in status["banner"]["message"]
    assert status["qr"]["status"] == "done"
    assert "ig_04" in status["qr"]["message"]
    assert status["linktree"]["status"] != "done"
    assert "課前表單截圖" in html
    assert "課後表單截圖" in html
    assert "DAHa81CU1n0" in html
    shot = c.get(f"/banner-shot/{SLUG}/form_banner_check_signup.png")
    assert shot.status_code == 200
    assert c.get(f"/banner-shot/{SLUG}/login-wait.png").status_code == 404


def test_official_certificate_preferred_and_downloadable(tmp_path):
    from PIL import Image

    run_pipeline(EV, "preview", tmp_path)
    runs = sorted((tmp_path / SLUG / "runs").iterdir(), key=lambda p: p.name, reverse=True)
    latest = next(p for p in runs if (p / "感謝狀.pdf").is_file())
    (latest / "感謝狀.pdf").write_bytes(b"%PDF-1.4 old-run-preview\n")
    official = tmp_path / SLUG / "感謝狀_new.pdf"
    official.write_bytes(b"%PDF-1.4 official-cert\n" + b"X" * 80)
    Image.new("RGB", (12, 12), "white").save(tmp_path / SLUG / "感謝狀_new.png")

    c = _client(tmp_path)
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "下載 PDF" in html
    assert "下載 PNG" in html
    assert f"/cert/{SLUG}/pdf" in html
    # The certificate pane shows the card image, not a full-page PDF viewer.
    cert_block = html.split("<h2>講者感謝狀</h2>", 1)[1].split("<dialog", 1)[0]
    assert "form_banner_check" not in cert_block
    assert "cert-frame" in cert_block
    assert f"/cert/{SLUG}/view.png" in cert_block
    assert "object" not in cert_block
    assert 'class="preview' not in cert_block
    assert "在瀏覽器開啟 PDF" in cert_block
    view = c.get(f"/cert/{SLUG}/view.png")
    assert view.status_code == 200
    assert view.mimetype == "image/png"
    assert view.data[:8] == b"\x89PNG\r\n\x1a\n"

    preview = c.get(f"/cert/{SLUG}/pdf")
    assert preview.status_code == 200
    assert preview.data.startswith(b"%PDF-1.4 official-cert")
    assert "attachment" not in (preview.headers.get("Content-Disposition") or "").lower()

    downloaded = c.get(f"/cert/{SLUG}/pdf?download=1")
    assert downloaded.status_code == 200
    assert downloaded.data.startswith(b"%PDF-1.4 official-cert")
    cd = downloaded.headers.get("Content-Disposition", "")
    assert "attachment" in cd.lower()
    assert "感謝狀.pdf" in cd or "UTF-8''" in cd

    png = c.get(f"/cert/{SLUG}/png?download=1")
    assert png.status_code == 200
    assert "attachment" in (png.headers.get("Content-Disposition") or "").lower()


def test_bcc_count_from_members_file(tmp_path, monkeypatch):
    addrs = "\n".join(f"member{i:02d}@example.com" for i in range(21))
    bcc = tmp_path / "preview-bcc.txt"
    bcc.write_text(addrs + "\n", encoding="utf-8")
    monkeypatch.setenv("CLUB_MEMBERS_BCC_FILE", str(bcc))
    run_pipeline(EV, "preview", tmp_path)
    c = _client(tmp_path)
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    body = c.get(f"/api/status/{SLUG}").get_json()
    assert body["stats"]["bcc_count"] == 21
    assert ">21<" in html or "\n      21" in html or "id=\"stat-bcc\">21<" in html
    assert "member00@example.com" not in html


def test_previews_use_real_output_files_and_downloads(tmp_path):
    from urllib.parse import unquote

    run_pipeline(EV, "preview", tmp_path)
    c = _client(tmp_path)
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    plain = unquote(html)
    assert "outputs/2_預告信.html" in plain
    assert "outputs/4_提醒信.html" in plain
    assert "outputs/6_課後信.html" in plain
    assert "下載這封信 .html" in html
    assert "download=1" in html
    assert "下載 LINE 文案" in html
    assert "下載 IG 文案" in html
    assert 'id="mail-frame"' in html
    assert "iframe" in html
    # Pull the real HTML file through the run route (inline, then as a download).
    marker = 'id="mail-frame" src="'
    start = html.find(marker)
    assert start != -1
    rel = html[start + len(marker):].split('"', 1)[0]
    page = c.get(rel)
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "<html" in body.lower() or "charset" in body.lower() or "創創" in body or "社課" in body
    dl = c.get(rel + ("&" if "?" in rel else "?") + "download=1")
    assert dl.status_code == 200
    assert "attachment" in (dl.headers.get("Content-Disposition") or "").lower()


def test_linktree_status_derived_from_result(tmp_path):
    import json

    run_pipeline(EV, "preview", tmp_path)
    c = _client(tmp_path)
    shots = tmp_path / SLUG / "linktree_shots"
    shots.mkdir(parents=True)
    (shots / "linktree_02_after.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"z" * 20)
    (shots / "linktree_result.json").write_text(json.dumps({
        "ok": True, "logged_in": True,
        "added": {"title": "115-1 陽明交大創創社｜11/25 課前表單", "visible": True},
        "hidden": ["115-1 陽明交大創創社｜11/04 課前表單"], "at": "2026-11-15 15:00",
    }, ensure_ascii=False), encoding="utf-8")
    body = c.get(f"/api/status/{SLUG}").get_json()
    lt = {a["key"]: a for a in body["actions"]}["linktree"]
    assert lt["status"] == "done"
    assert "11/25" in lt["message"] and "11/04" in lt["message"]
    assert c.get(f"/linktree-shot/{SLUG}/linktree_02_after.png").status_code == 200
    assert c.get(f"/linktree-shot/{SLUG}/x.png").status_code == 404


@pytest.mark.fresh
def test_reset_event_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr("autopilot.notion_import.load_token", lambda: None)
    from autopilot import web as webmod
    assert webmod.KNOWN_STATE == {}
    c = _client(tmp_path)
    status = {a["key"]: a["status"] for a in c.get(f"/api/status/{SLUG}").get_json()["actions"]}
    assert status["forms"] == "idle" and status["drafts"] == "idle"
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "FAKESIGNUPFORMID-0000000000000000000000000" not in html


class _FakeGmail:
    def __init__(self):
        self.sent, self._get = [], False

    def users(self):
        return self

    def drafts(self):
        return self

    def messages(self):
        return self

    def send(self, userId, body):
        self.sent.append(body["id"])
        return self

    def get(self, userId, id, format):
        self._get = True
        return self

    def execute(self):
        if self._get:
            self._get = False
            return {"labelIds": ["SENT"]}
        return {"id": "m123"}


def test_send_announce_once_with_confirm(tmp_path, monkeypatch):
    import json as _json
    g = _FakeGmail()
    monkeypatch.setattr("autopilot.google_auth.services", lambda *a, **k: {"gmail": g})
    c = _client(tmp_path)
    c.get(f"/event/{SLUG}")
    url = f"/api/drafts/{SLUG}/send-announce"
    assert c.post(url, json={}).status_code == 400 and not g.sent
    assert c.post(url, json={"confirm": True, "draft_id": "old"}).status_code == 409 and not g.sent
    r = c.post(url, json={"confirm": True, "draft_id": "r1000000000000000001"})
    assert r.status_code == 200 and r.get_json()["in_sent"] and g.sent == ["r1000000000000000001"]
    assert c.post(url, json={"confirm": True, "draft_id": "r1000000000000000001"}).status_code == 409
    assert g.sent == ["r1000000000000000001"]
    st = _json.loads((tmp_path / SLUG / "state.json").read_text(encoding="utf-8"))
    assert st["announce_sent"]["message_id"] == "m123"


def test_index_with_no_event_imported(tmp_path, monkeypatch):
    """Blank start: no events and no Notion export yet -> the dashboard still opens (no 500)."""
    monkeypatch.setattr("autopilot.notion_import.load_token", lambda: None)
    empty = tmp_path / "events"
    empty.mkdir()
    app = create_app(root=ROOT, out_root=tmp_path / "out", events_dir=empty)
    for lang in ("zh", "en"):
        c = app.test_client()
        c.set_cookie("lang", lang)
        r = c.get("/")
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
