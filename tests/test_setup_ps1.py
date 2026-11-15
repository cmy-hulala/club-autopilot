"""The Windows one-liner scripts stay compatible with PowerShell 5.1."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ONE_LINER = (
    'powershell -ExecutionPolicy Bypass -c '
    '"irm https://raw.githubusercontent.com/cmy-hulala/club-autopilot/launchpad/setup.ps1 | iex"'
)


def _text(name: str) -> str:
    raw = (ROOT / name).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), name + " must be UTF-8 with BOM for Windows PowerShell 5.1"
    return raw.decode("utf-8-sig")


def test_setup_ps1_is_a_powershell_51_installer():
    text = _text("setup.ps1")
    assert "launchpad" in text
    assert "5050" in text
    assert "/onboarding" in text
    assert "Python.Python.3.12" in text
    assert "Git.Git" in text
    assert "WindowsApps" in text
    assert "-m pip" in text
    assert "-m venv" in text
    assert "playwright install chromium" in text
    assert "GetEnvironmentVariable" in text
    assert "Machine" in text and "User" in text
    assert "$PSScriptRoot" in text
    assert "club-autopilot" in text
    assert "$HOME" in text
    assert "System32" in text
    assert "&&" not in text
    assert "||" not in text
    assert "??" not in text
    assert "checkout main" not in text
    assert "--branch $Branch" in text or "--branch launchpad" in text or "checkout $Branch" in text
    assert "pull --ff-only origin $Branch" in text
    assert "platform-tools-latest-windows.zip" in text
    assert "club-autopilot-tools" in text


def test_start_ps1_relaunches_onboarding():
    text = _text("start.ps1")
    assert "5050" in text
    assert "/onboarding" in text
    assert ".venv" in text
    assert "$PSScriptRoot" in text
    assert "club-autopilot" in text
    assert "&&" not in text
    assert "||" not in text
    assert "??" not in text
    assert "pull --ff-only origin $Branch" in text
    assert "$Branch = 'launchpad'" in text


def test_readme_leads_with_the_windows_one_liner():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert ONE_LINER in readme
    assert "start.ps1" in readme
    assert "Microsoft Store" in readme
    assert "System32" in readme
