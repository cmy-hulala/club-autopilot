from pathlib import Path

from autopilot import checker, render, responses
from autopilot.event import load_event
from autopilot.pipeline import run_pipeline

EV = Path(__file__).resolve().parent / "fixtures" / "2026-11-25-candice.yaml"


def test_no_hardcoded_session_everything_from_event():
    ev = load_event(EV)
    out = render.render_all(ev)
    assert "第六堂社課預告" in render.subject_of(out["2_預告信.html"])
    ev2 = load_event(EV, {"session_no": 7, "date": "2026-12-09", "deadline": "2026-12-08 23:59"})
    out2 = render.render_all(ev2)
    assert "第七堂" in out2["2_預告信.html"] and "12/9（三）" in out2["2_預告信.html"]
    assert "第六堂" not in out2["2_預告信.html"]


def test_checker_passes_clean_outputs():
    ev = load_event(EV)
    f = checker.check(ev, render.render_all(ev))
    assert checker.summary(f)["error"] == 0


def test_checker_flags_violations_red():
    ev = load_event(EV)
    out = render.render_all(ev)
    bad = out["2_預告信.html"].replace("創創社的夥伴們", '<style>p{}</style><p style="color:red">非社員 NT$300，18:30 報到</p>創創社的夥伴們')
    bad = bad.replace("</td></tr>\n</table>", "<p>someone@gmail.com</p></td></tr>\n</table>", 1)
    rules = {x.rule for x in checker.check(ev, {"2_預告信.html": bad}) if x.level == "error"}
    assert "不用 <style>" in rules
    assert "信裡不寫費用" in rules
    assert "不寫報到時段" in rules
    assert "不含個人 Email／社員名單" in rules
    assert any("字型" in r for r in rules)
    line = out["3_Line訊息.txt"].replace("幹部邀請優惠", "幹部另計")
    assert any(x.level == "error" for x in checker.check(ev, {"3_Line訊息.txt": line}))


def test_bcc_dedupes_and_skips_not_attending():
    rows = [{"Email": "a@example.com", "是否參加本期社課": "會參加"},
            {"Email": "A@example.com", "是否參加本期社課": "會參加"},
            {"Email": "b@example.com", "是否參加本期社課": "無法參加"},
            {"Email": "bad", "是否參加本期社課": "會參加"},
            {"Email": "nycu.iec@gmail.com", "是否參加本期社課": "會參加"}]
    assert responses.registrants_bcc(rows, exclude={"nycu.iec@gmail.com"}) == ["a@example.com"]


def test_preview_never_deletes_previous_runs(tmp_path):
    r1 = run_pipeline(EV, "preview", tmp_path)
    r2 = run_pipeline(EV, "preview", tmp_path)
    assert r1.dir != r2.dir and r1.dir.exists() and r2.dir.exists()
    assert (r1.dir / "outputs" / "2_預告信.html").exists()
    eml = (r2.dir / "drafts" / "4_提醒信.eml").read_text(encoding="utf-8", errors="ignore")
    assert "example.com" not in eml  # BCC is hidden in preview files



def test_preview_omits_deadline_reminder(tmp_path):
    import json
    ev = load_event(EV)
    names = set(render.render_all(ev))
    assert "4a_截止提醒信.html" not in names
    assert {"2_預告信.html", "4_提醒信.html", "6_課後信.html"} <= names
    r = run_pipeline(EV, "preview", tmp_path)
    out_names = {p.name for p in (r.dir / "outputs").iterdir()}
    assert "4a_截止提醒信.html" not in out_names
    draft_names = {p.name for p in (r.dir / "drafts").iterdir()}
    assert draft_names == {"2_預告信.eml", "4_提醒信.eml", "6_課後信.eml"}
    keys = [row["key"] for row in json.loads((r.dir / "timeline.json").read_text(encoding="utf-8"))]
    assert keys == ["announce", "social", "reminder", "followup"]
    assert [d["file"] for d in r.manifest["drafts"]] == ["2_預告信.html", "4_提醒信.html", "6_課後信.html"]



def test_followup_uses_single_feedback_button():
    ev = load_event(EV)
    html = render.render_all(ev)["6_課後信.html"]
    assert "第六堂社課回饋表單" in html
    assert "歡迎大家花 1 分鐘提供意見，一起讓下一次活動更好！" in html
    assert "填寫課後回饋表單" in html
    assert html.count("background-color:#c98d17") == 1
    assert "給這堂社課打個分數" not in html
    assert "{{課後表單網址}}" in html
    assert "entry." not in html
