"""Private BCC exclude list — fake addresses only."""
from autopilot import bcc_exclude, drafts_sync


def _file(tmp_path):
    p = tmp_path / "bcc_exclude.txt"
    p.write_text("# 不寄\nBlocked.Person@Example.com\n\nnolocal   # any domain\n", encoding="utf-8")
    return p


def test_load_and_apply_case_insensitive(tmp_path):
    p = _file(tmp_path)
    addrs = ["ok@example.com", "blocked.person@example.COM", "NoLocal@gmail.com", "nolocal@nycu.edu.tw",
             "nolocal2@example.com", "other@example.com"]
    kept, n = bcc_exclude.apply(addrs, p)
    assert kept == ["ok@example.com", "nolocal2@example.com", "other@example.com"] and n == 3


def test_missing_file_excludes_nothing(tmp_path):
    assert bcc_exclude.apply(["a@example.com"], tmp_path / "none.txt") == (["a@example.com"], 0)


def test_env_path(tmp_path, monkeypatch):
    p = _file(tmp_path)
    monkeypatch.setenv("CLUB_BCC_EXCLUDE_FILE", str(p))
    assert bcc_exclude.default_path() == p
    assert bcc_exclude.apply(["nolocal@x.org", "y@x.org"]) == (["y@x.org"], 1)


def test_sync_drafts_applies_exclude_to_every_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("CLUB_BCC_EXCLUDE_FILE", str(_file(tmp_path)))
    html = "<html><head><title>主旨</title></head><body>內文</body></html>"
    outputs = {f: html for f, _, _ in drafts_sync.DRAFT_PLAN}
    bccs = {"members": ["m1@example.com", "BLOCKED.person@example.com"],
            "registrants": ["r1@example.com", "nolocal@example.org"]}
    sent = []
    monkeypatch.setattr(drafts_sync, "create_draft", lambda g, msg: sent.append(msg) or {"id": "d"})
    recs = drafts_sync.sync_drafts(outputs, "club@example.com", [], bccs, gmail=None)
    assert [r["bcc_count"] for r in recs] == [1, 1, 1]
    assert [r["excluded_count"] for r in recs] == [1, 1, 1]
    for m in sent:
        b = m["Bcc"].lower()
        assert "blocked.person" not in b and "nolocal@" not in b
    assert "已排除 1 人" in drafts_sync.describe(recs)
