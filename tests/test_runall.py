"""全部執行 / 重跑 / LINE 直接發送 / 自訂規則 / IG 提醒 — all with mocked live steps."""
import json
import time
from pathlib import Path

import pytest

from autopilot import checker, line_push
from autopilot import web as webmod
from autopilot.pipeline import run_pipeline
from autopilot.web import ACTION_KEYS, STEP_DEPS, create_app

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "tests" / "fixtures" / "2026-11-25-candice.yaml"
SLUG = "2026-11-25-candice"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr("autopilot.notion_import.load_token", lambda: None)
    monkeypatch.setenv("CLUB_RULES_FILE", str(tmp_path / "rules.yaml"))
    monkeypatch.delenv("LINE_CHANNEL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("LINE_GROUP_ID", raising=False)
    monkeypatch.delenv("LINE_GROUP_IDS", raising=False)
    monkeypatch.setenv("LINE_BOT_CONFIG", str(tmp_path / "no-line.json"))
    monkeypatch.setattr(webmod, "members_bcc_count", lambda: 0)


def _app(tmp_path):
    out = tmp_path / "out"
    run_pipeline(EV, "preview", out)
    app = create_app(root=ROOT, out_root=out, events_dir=ROOT / "tests" / "fixtures")
    app.config["TESTING"] = True
    return app


def _wait(c, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = c.get(f"/api/status/{SLUG}").get_json()
        if not d["runall"]["busy"] and not any(a["status"] == "running" for a in d["actions"]):
            return d
        time.sleep(0.2)
    raise AssertionError("run-all did not finish")


def _by_key(d):
    return {a["key"]: a for a in d["actions"]}


class Live:
    """Records calls to every live-service step so we can prove nothing is duplicated."""

    def __init__(self, monkeypatch, fail=()):
        self.calls = []
        self.fail = set(fail)

        def guard(name):
            self.calls.append(name)
            if name in self.fail:
                raise RuntimeError(f"{name} boom")

        def fake_forms(*a, **k):
            guard("forms_api")
            raise AssertionError("forms must be reused from state, never re-created")

        def fake_banner(state, ev, **k):
            assert k.get("headless") is True, "banner must run headless from the dashboard"
            assert k.get("required_account") == "officer@example.com"
            guard("banner")
            shots = Path(k["shots"]); shots.mkdir(parents=True, exist_ok=True)
            res = {"ok": True, "forms": [{"form_id": state["signup"]["formId"], "ok": True},
                                         {"form_id": state["feedback"]["formId"], "ok": True}]}
            (shots / "result.json").write_text(json.dumps(res), encoding="utf-8")
            return res

        def fake_qr(ev, dest):
            guard("qr")
            return {"ok": True, "url": ev["form_url"], "png": "qr.png"}

        def fake_linktree(plan, shots, **k):
            guard("linktree")
            shots.mkdir(parents=True, exist_ok=True)
            (shots / "linktree_result.json").write_text(json.dumps(
                {"ok": True, "added": {"title": "11/25 課前表單"}, "hidden": []}), encoding="utf-8")
            return {"ok": True, "log": []}

        def fake_forms_services(*a, **k):
            assert a[1] == "officer@example.com"
            return {"forms": object(), "account": a[1]}

        def fake_rows(*a, **k):
            guard("stats")
            raise RuntimeError("no network in tests")

        def no_draft(*a, **k):
            raise AssertionError("drafts already exist in state; must not create Gmail drafts")

        self.updated = []

        def fake_get(gmail, did):
            return {"subject": "old", "to": "nycu.iec@gmail.com", "bcc": [], "html": "old"}

        def fake_update(gmail, did, msg):
            self.updated.append(did)
            return {"id": did}

        monkeypatch.setattr("autopilot.forms_api.create_form", fake_forms)
        monkeypatch.setattr("autopilot.form_banner.apply_from_state", fake_banner)
        monkeypatch.setattr("autopilot.canva_step.update_qr", fake_qr)

        def fake_canva_wait(out_root, slug, **k):
            guard("canva")
            return {"status": "done", "ig_design_id": "DTESTIG0001", "cert_design_id": "DTESTCE0001"}

        monkeypatch.setattr("autopilot.canva_job.prepare_photo", lambda root, ev, dest: dest)
        monkeypatch.setattr("autopilot.canva_job.wait", fake_canva_wait)
        monkeypatch.setattr("autopilot.canva_job.outputs_ready", lambda *a, **k: "canva" in self.calls)
        monkeypatch.setattr("autopilot.linktree.run", fake_linktree)
        monkeypatch.setattr("autopilot.google_auth.forms_services", fake_forms_services)
        monkeypatch.setattr("autopilot.google_auth.services", lambda *a, **k: {"gmail": object()})
        monkeypatch.setattr("autopilot.forms_api.responses_as_rows", fake_rows)
        monkeypatch.setattr("autopilot.drafts_sync.registrant_bcc", lambda *a, **k: ([], 0))
        monkeypatch.setattr("autopilot.drafts_sync.create_draft", no_draft)
        monkeypatch.setattr("autopilot.drafts_sync.get_draft", fake_get)
        monkeypatch.setattr("autopilot.drafts_sync.update_draft", fake_update)
        monkeypatch.setattr(line_push, "push_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LINE must never send in run-all")))


def test_every_step_listed_in_order(tmp_path):
    c = _app(tmp_path).test_client()
    d = c.get(f"/api/status/{SLUG}").get_json()
    assert [a["key"] for a in d["actions"]] == ACTION_KEYS
    assert ACTION_KEYS == ["import", "preview", "canva", "forms", "banner", "qr", "ig", "cert", "drafts", "linktree", "stats"]
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "全部執行" in html
    for label in ("匯入活動資料", "產生文案", "Canva 圖卡＋感謝狀", "建立課前／課後表單", "Banner", "Canva QR", "IG 圖卡", "感謝狀",
                  "Gmail 草稿", "Linktree", "報名／回饋人數"):
        assert label in html
    assert "時間軸" not in html
    # deps only point backwards
    for k, deps in STEP_DEPS.items():
        assert all(ACTION_KEYS.index(x) < ACTION_KEYS.index(k) for x in deps)


def test_run_all_success_no_duplicates_and_retry_only_on_failed(tmp_path, monkeypatch):
    live = Live(monkeypatch)
    # Real ig_01–ig_04 live in gitignored Canva exports. This test only checks run-all order.
    monkeypatch.setattr(
        webmod, "ig_card_paths",
        lambda root, ev, run_dir: [Path(f"ig_{i:02d}.png") for i in range(1, 5)],
    )
    c = _app(tmp_path).test_client()
    c.post(f"/api/run-all/{SLUG}")
    d = _wait(c)
    acts = _by_key(d)
    # forms and drafts come from state: no Forms API / Gmail calls
    assert "forms_api" not in live.calls
    assert acts["forms"]["status"] == "done" and acts["drafts"]["status"] == "done"
    # existing drafts were updated in place (same ids), none created
    assert sorted(live.updated) == sorted(["r1000000000000000001", "r-1000000000000000002", "r-1000000000000000003"])
    assert "先留空" in acts["drafts"]["message"]
    assert acts["banner"]["status"] == "done" and acts["linktree"]["status"] == "done"
    # stats fails (mocked network) -> only that one has 重跑
    assert acts["stats"]["status"] == "failed"
    assert [k for k, a in acts.items() if a["retry"]] == ["stats"]
    assert all(not a["retry"] for k, a in acts.items() if a["status"] == "done")
    assert d["runall"]["status"] == "failed" and "重跑" in d["runall"]["message"]
    n_banner, n_lt = live.calls.count("banner"), live.calls.count("linktree")
    # second run-all: done live steps are not re-run
    c.post(f"/api/run-all/{SLUG}")
    _wait(c)
    assert live.calls.count("banner") == n_banner
    assert live.calls.count("linktree") == n_lt
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    import re
    assert re.findall(r'data-action="([a-z]+)"', html) == ["stats"]


def test_failed_step_skips_dependants_but_others_continue(tmp_path, monkeypatch):
    live = Live(monkeypatch, fail={"qr"})
    # Pretend the QR card doesn't match yet so the qr step really runs.
    monkeypatch.setattr("autopilot.canva_step._decode_qr_png", lambda p: None)
    c = _app(tmp_path).test_client()
    c.post(f"/api/run-all/{SLUG}")
    acts = _by_key(_wait(c))
    assert acts["qr"]["status"] == "failed" and "qr boom" in acts["qr"]["message"]
    assert acts["ig"]["status"] == "skipped" and acts["ig"]["retry"]
    assert acts["linktree"]["status"] == "done"  # independent of qr
    assert acts["cert"]["status"] == "done"
    # manual retry of qr after fixing it
    live.fail.clear()
    c.post(f"/api/action/{SLUG}/qr")
    acts = _by_key(_wait(c))
    assert acts["qr"]["status"] == "done" and not acts["qr"]["retry"]


def test_line_send_requires_config_confirm_and_hash(tmp_path, monkeypatch):
    c = _app(tmp_path).test_client()
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "LINE 未設定" in html and "LINE_CHANNEL_ACCESS_TOKEN" in html
    assert "115-1交大創創社" in html and "陽交大創創社資訊分享群" in html
    r = c.post(f"/api/line/{SLUG}/send", json={"confirm": True, "hash": "x"})
    assert r.status_code in (400, 409)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
    sent = []

    class Resp:
        status = 200
        headers = {"x-line-request-id": "req-1"}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req, timeout=0):
        sent.append(json.loads(req.data.decode("utf-8")))
        assert req.get_header("Authorization") == "Bearer test-token"
        return Resp()

    real_push = line_push.push_text
    monkeypatch.setattr(line_push, "push_text", lambda text: real_push(text, opener=opener))
    from autopilot.web import LINE_OUTPUT
    run_dir = sorted((tmp_path / "out" / SLUG / "runs").iterdir())[-1]
    text = (run_dir / "outputs" / LINE_OUTPUT).read_text(encoding="utf-8").strip()
    assert c.post(f"/api/line/{SLUG}/send", json={"hash": line_push.text_hash(text)}).status_code == 400  # no confirm
    assert c.post(f"/api/line/{SLUG}/send", json={"confirm": True, "hash": "stale"}).status_code == 409
    assert sent == []
    r = c.post(f"/api/line/{SLUG}/send", json={"confirm": True, "hash": line_push.text_hash(text)})
    assert r.get_json()["ok"] is True
    assert [m["to"] for m in sent] == ["CFAKEGROUPID0000000000000000000001", "CFAKEGROUPID0000000000000000000002"]
    assert all(m["messages"] == [{"type": "text", "text": text}] for m in sent)
    saved = (tmp_path / "out" / SLUG / "state.json").read_text(encoding="utf-8")
    assert "test-token" not in saved


def test_rules_only_errors_shown_and_custom_rule_added(tmp_path):
    c = _app(tmp_path).test_client()
    html = c.get(f"/event/{SLUG}").get_data(as_text=True).split('<script id="i18n-data"')[0]
    assert "展開明細" not in html and "▲ 提醒" not in html and "違規 0" not in html
    r = c.post(f"/event/{SLUG}/rules", data={"name": "不寫社課兩字", "target": "line", "kind": "forbid",
                                              "pattern": "社課", "level": "error"})
    assert r.status_code == 302
    rules = checker.load_custom_rules()
    assert rules and rules[0]["name"] == "不寫社課兩字"
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "自訂：不寫社課兩字" in html and "違規" in html
    # duplicate name rejected
    with pytest.raises(ValueError):
        checker.add_custom_rule({"name": "不寫社課兩字", "pattern": "x"})


def test_ig_button_reminds_phone_and_lists_cards(tmp_path):
    c = _app(tmp_path).test_client()
    html = c.get(f"/event/{SLUG}").get_data(as_text=True)
    assert "請連接手機，在手機上完成 IG 發文／草稿" in html and "存成 IG 草稿（手機）" in html
    assert "ig_01" in html and "ig_04" in html


def test_banner_chrome_args_headless():
    from autopilot.form_banner import chrome_args
    a = chrome_args(Path("/p"), 9222, "https://x", headless=True)
    assert "--headless=new" in a and "--start-maximized" not in a
    assert "--start-maximized" in chrome_args(Path("/p"), 9222, "https://x")
