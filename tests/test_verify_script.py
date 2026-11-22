"""scripts/verify is the offline check every change must run."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SENTENCE = (
    "Every code change must be verified by running this script, "
    "and its output must be included as evidence."
)


def test_verify_scripts_share_one_implementation():
    bash = (ROOT / "scripts" / "verify").read_text(encoding="utf-8")
    assert "verify.py" in bash
    assert "VERIFY FAIL" in bash
    raw = (ROOT / "scripts" / "verify.ps1").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "verify.ps1 must be UTF-8 with BOM for Windows PowerShell 5.1"
    ps1 = raw.decode("utf-8-sig")
    assert "verify.py" in ps1
    assert "VERIFY FAIL" in ps1
    assert "&&" not in ps1
    assert "||" not in ps1
    assert "??" not in ps1
    py = (ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")
    assert "VERIFY PASS" in py
    assert "VERIFY FAIL" in py
    assert '"-m", "autopilot", "web"' in py
    for label in ("Google", "LINE", "Notion", "Linktree", "Playwright", "Instagram", "Canva"):
        assert label in py


def test_readme_and_agents_require_verify_output():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "bash scripts/verify" in readme
    assert "scripts/verify.ps1" in readme
    assert SENTENCE in readme
    assert SENTENCE in agents
    assert "bash scripts/verify" in agents
    assert "VERIFY PASS" in agents
