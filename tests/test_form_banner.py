from pathlib import Path

from autopilot import form_banner
from autopilot.__main__ import main
from autopilot.event import load_event
from autopilot.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "tests" / "fixtures" / "2026-11-25-candice.yaml"
SPEC = """# 報名表單規格｜第六堂社課（11/25（三））

## 確認訊息

`已收到你的回覆，會參加的夥伴，我們 11/25（三）19:00–21:30 在交映樓 703 見!`

## 網址
"""


def test_confirmation_from_spec():
    assert form_banner.confirmation_from_spec(SPEC) == (
        "已收到你的回覆，會參加的夥伴，我們 11/25（三）19:00–21:30 在交映樓 703 見!"
    )
    assert form_banner.confirmation_from_spec("# no section") is None


def test_default_banner_is_the_black_club_header():
    path = form_banner.default_banner()
    assert path.is_file()
    form_banner.assert_black_banner(path)


def test_jobs_from_state_use_event_confirmation_not_hardcoded_ids():
    ev = load_event(EV)
    state = {
        "signup": {"formId": "SIGNUP_ID", "responder_url": "https://example.com/s", "edit_url": "https://example.com/s/edit"},
        "feedback": {"formId": "FEEDBACK_ID", "responder_url": "https://example.com/f"},
    }
    jobs = form_banner.jobs_from_state(state, ev)
    assert [j.form_id for j in jobs] == ["SIGNUP_ID", "FEEDBACK_ID"]
    assert [j.kind for j in jobs] == ["signup", "feedback"]
    assert jobs[0].confirmation == ev["signup_confirmation"]
    assert jobs[1].confirmation == ev["feedback_confirmation"]
    assert "11/25" in jobs[0].confirmation
    assert jobs[1].edit_url.endswith("/FEEDBACK_ID/edit")


def test_jobs_without_event_leave_confirmation_empty():
    jobs = form_banner.jobs_from_state({"signup": {"formId": "X"}})
    assert jobs[0].confirmation is None


def test_module_has_no_hardcoded_lecture_or_form_ids():
    src = Path(form_banner.__file__).read_text(encoding="utf-8")
    assert "FAKESIGNUPFORMID-0000000000000000000000000" not in src
    assert "FAKEFEEDBACKFORMID-000000000000000000000000" not in src
    assert "11/25" not in src
    assert "謝慈" not in src


def test_set_banner_cli_requires_event_or_form_id():
    try:
        main(["set-banner"])
    except SystemExit as e:
        assert e.code not in (0, None)
    else:
        raise AssertionError("expected SystemExit")


def test_pipeline_banner_step_is_opt_in_and_uses_state(tmp_path, monkeypatch):
    from autopilot import google_auth as ga

    class Exec:
        def __init__(self, v): self.v = v
        def execute(self): return self.v

    class FakeForms:
        def __init__(self): self.n = 0; self.created = {}
        def forms(self): return self
        def create(self, body):
            self.n += 1
            fid = f"FORM{self.n}"
            self.created[fid] = {"formId": fid, "info": body["info"], "items": [], "responderUri": f"https://docs.google.com/forms/d/e/{fid}/viewform"}
            return Exec({"formId": fid})
        def batchUpdate(self, formId, body):
            for r in (x for x in body["requests"] if "createItem" in x):
                it = dict(r["createItem"]["item"])
                if "questionItem" in it:
                    it["questionItem"] = {"question": {**it["questionItem"]["question"], "questionId": "abc"}}
                self.created[formId].setdefault("items", []).append(it)
            return Exec({})
        def setPublishSettings(self, formId, body): return Exec({})
        def get(self, formId): return Exec(self.created[formId])
        def responses(self): return self
        def list(self, formId, pageToken=None): return Exec({"responses": []})

    forms = FakeForms()
    monkeypatch.setattr(ga, "forms_services", lambda token, account: {"forms": forms, "gmail": None, "drive": None, "account": account})

    called = {}

    def fake_apply(state, ev, image=None, **kwargs):
        called["state"] = state
        called["confirmations"] = (ev["signup_confirmation"], ev["feedback_confirmation"])
        called["account"] = kwargs.get("required_account")
        return {"forms": [{"form_id": state["signup"]["formId"], "banner": True}]}

    monkeypatch.setattr(form_banner, "apply_from_state", fake_apply)
    token = tmp_path / "token-officer.json"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("{}", encoding="utf-8")

    r0 = run_pipeline(EV, "forms", tmp_path, forms_token=str(token), do_banner=False)
    assert "form_banner" not in r0.manifest
    assert "表單 Banner" not in [s["step"] for s in r0.manifest["steps"]]

    r = run_pipeline(EV, "forms", tmp_path, forms_token=str(token), do_banner=True)
    assert called["state"]["signup"]["formId"] == "FORM1"
    assert called["account"] == "officer@example.com"
    assert called["confirmations"][0].startswith("已收到你的回覆")
    assert r.manifest["form_banner"]["forms"][0]["banner"] is True
