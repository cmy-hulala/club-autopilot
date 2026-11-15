"""Exercise the --google path end to end with fake Google services (no network, nothing sent)."""
from pathlib import Path

from autopilot import pipeline

EV = Path(__file__).resolve().parent / "fixtures" / "2026-11-25-candice.yaml"


class Exec:
    def __init__(self, v): self.v = v
    def execute(self): return self.v


class FakeForms:
    def __init__(self): self.n = 0; self.created = {}; self.batches = []
    def forms(self): return self
    def create(self, body):
        self.n += 1; fid = f"FORM{self.n}"
        self.created[fid] = {"formId": fid, "info": body["info"], "items": [], "responderUri": f"https://docs.google.com/forms/d/e/{fid}/viewform"}
        return Exec({"formId": fid})
    def batchUpdate(self, formId, body):
        self.batches.append(body)
        for i, r in enumerate(x for x in body["requests"] if "createItem" in x):
            it = dict(r["createItem"]["item"])
            if "questionItem" in it:
                it["questionItem"] = {"question": {**it["questionItem"]["question"], "questionId": f"{0x1a2b + i:x}"}}
            self.created[formId]["items"].append(it)
        return Exec({})
    def setPublishSettings(self, formId, body): return Exec({})
    def get(self, formId): return Exec(self.created[formId])
    def responses(self): return self
    def list(self, formId, pageToken=None):
        items = {it["title"]: it["questionItem"]["question"]["questionId"] for it in self.created[formId]["items"] if "questionItem" in it}
        if "是否參加本期社課" in items:
            rs = [{"respondentEmail": f"s{i}@example.com", "answers": {items["是否參加本期社課"]: {"textAnswers": {"answers": [{"value": "會參加" if i != 2 else "無法參加"}]}}}} for i in range(5)]
        else:
            rs = []
        return Exec({"responses": rs})


class FakeGmail:
    def __init__(self): self.drafts_made = []
    def users(self): return self
    def drafts(self): return self
    def create(self, userId, body):
        self.drafts_made.append(body); return Exec({"id": f"r{len(self.drafts_made)}"})


def test_google_mode_creates_forms_and_drafts_only(tmp_path, monkeypatch):
    forms, gmail = FakeForms(), FakeGmail()
    import autopilot.google_auth as ga
    monkeypatch.setattr(ga, "services", lambda d: {"forms": forms, "gmail": gmail, "drive": None, "account": "nycu.iec@gmail.com"})
    r = pipeline.run_pipeline(EV, "google", tmp_path, oauth_dir=str(tmp_path / "x"))
    m = r.manifest
    assert all(s["status"] == "ok" for s in m["steps"]), m["steps"]
    assert len(gmail.drafts_made) == 3
    assert [d["bcc_count"] for d in m["drafts"]] == [0, 4, 4]
    assert [d["file"] for d in m["drafts"]] == ["2_預告信.html", "4_提醒信.html", "6_課後信.html"]
    html = (r.dir / "outputs" / "2_預告信.html").read_text(encoding="utf-8")
    assert "FORM1/viewform" in html and "{{課前表單網址}}" not in html
    fu = (r.dir / "outputs" / "6_課後信.html").read_text(encoding="utf-8")
    assert "FORM2/viewform" in fu and "{{課後表單網址}}" not in fu
    assert "填寫課後回饋表單" in fu and "給這堂社課打個分數" not in fu
    assert fu.count("background-color:#c98d17") == 1
    assert forms.created["FORM1"]["info"]["title"] == "115-1 陽明交大創創社｜11/25 課前表單"
    # second run reuses the forms (no duplicates)
    pipeline.run_pipeline(EV, "google", tmp_path, oauth_dir=str(tmp_path / "x"))
    assert forms.n == 2



def test_forms_only_creates_forms_without_drafts(tmp_path, monkeypatch):
    from autopilot import google_auth as ga
    forms, gmail = FakeForms(), FakeGmail()
    def fake_forms_services(token_path, expected_account):
        assert expected_account == "officer@example.com"
        return {"forms": forms, "gmail": None, "drive": None, "account": expected_account}
    monkeypatch.setattr(ga, "forms_services", fake_forms_services)
    token = tmp_path / "outside" / "token-officer.json"
    # path is only passed through; forms_services is mocked so the file need not be a real token
    r = pipeline.run_pipeline(EV, "forms", tmp_path, forms_token=str(token))
    m = r.manifest
    assert all(s["status"] == "ok" for s in m["steps"]), m["steps"]
    assert forms.n == 2
    assert m["drafts"] == []
    assert gmail.drafts_made == []
    assert "Gmail 草稿" not in [s["step"] for s in m["steps"]]
    html = (r.dir / "outputs" / "2_預告信.html").read_text(encoding="utf-8")
    assert "FORM1/viewform" in html and "{{課前表單網址}}" not in html
    assert not (r.dir / "drafts").exists() or not list((r.dir / "drafts").glob("*.eml"))
    assert not (r.dir / "outputs" / "4a_截止提醒信.html").exists()
