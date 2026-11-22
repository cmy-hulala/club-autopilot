#!/usr/bin/env python3
"""Offline one-click check for Club Autopilot.

Creates .venv when needed, installs requirements only if imports are missing,
starts ``python -m autopilot web`` on a free local port, hits the home page,
onboarding, and favicon, stops that process, then runs pytest.

Live Google, LINE, Notion, Linktree, Instagram, and Canva calls are not made.
The last line is ``VERIFY PASS`` or ``VERIFY FAIL``.
"""
from __future__ import annotations

import importlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMPORTS = (
    "flask",
    "jinja2",
    "yaml",
    "PIL",
    "pytest",
    "qrcode",
    "cv2",
    "playwright",
    "googleapiclient",
    "google_auth_oauthlib",
)
# Prefixes the app uses for tokens and private paths. Scrubbed for the server and pytest.
SECRET_PREFIXES = (
    "NOTION",
    "LINE_",
    "GOOGLE",
    "CLUB_",
    "AUTOPILOT_",
    "AWS_",
    "OPENAI",
    "ANTHROPIC",
)
SKIPPED = (
    "Google Forms API, Gmail API, and Google OAuth (no token; tests mock these)",
    "LINE Messaging API (no channel token; nothing is pushed)",
    "Notion API (no token; the smoke page does not import a live lecture)",
    "Linktree login and browser automation",
    "Playwright Chromium download (the Python package is enough; the browser is not installed)",
    "Instagram and adb (no phone)",
    "Canva connector (no account, no design is opened or edited)",
)


def say(message: str = "") -> None:
    print(message, flush=True)


def venv_python(root: Path) -> Path:
    folder = "Scripts" if os.name == "nt" else "bin"
    name = "python.exe" if os.name == "nt" else "python"
    return root / ".venv" / folder / name


def in_project_venv(root: Path) -> bool:
    try:
        return Path(sys.prefix).resolve() == (root / ".venv").resolve()
    except OSError:
        return False


def relaunch_in_venv(root: Path) -> None:
    """Re-exec inside .venv so installs do not touch the system Python."""
    if in_project_venv(root):
        return
    py = venv_python(root)
    if not py.is_file():
        say(f"Creating virtual environment: {root / '.venv'}")
        proc = subprocess.run(
            [sys.executable, "-m", "venv", str(root / ".venv")],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0 or not py.is_file():
            detail = (proc.stderr or proc.stdout or "").strip()
            if detail:
                say(detail)
            say("Could not create .venv. On Debian/Ubuntu install python3-venv, then run this script again.")
            say("VERIFY FAIL")
            raise SystemExit(1)
    say(f"Using {py}")
    os.environ["VIRTUAL_ENV"] = str(root / ".venv")
    script = str(Path(__file__).resolve())
    os.execv(str(py), [str(py), script, *sys.argv[1:]])


def missing_imports() -> list[str]:
    missing = []
    for name in IMPORTS:
        try:
            importlib.import_module(name)
        except Exception:
            missing.append(name)
    return missing


def dependencies_ready(root: Path) -> bool:
    missing = missing_imports()
    if not missing:
        say("Dependencies: already installed. pip was not run.")
        return True
    say("Dependencies missing: " + ", ".join(missing))
    say("Running: python -m pip install -r requirements.txt")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "-r",
            str(root / "requirements.txt"),
        ],
        cwd=root,
    )
    if proc.returncode != 0:
        say("Dependencies: FAIL")
        return False
    missing = missing_imports()
    if missing:
        say("Dependencies still missing after pip: " + ", ".join(missing))
        say("Dependencies: FAIL")
        return False
    say("Dependencies: installed.")
    return True


def scrubbed_env(extra: dict[str, str]) -> dict[str, str]:
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith(SECRET_PREFIXES):
            continue
        env[key] = value
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("FLASK_DEBUG", None)
    env.pop("FLASK_ENV", None)
    env.update(extra)
    return env


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LocalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlparse(newurl).hostname
        if host not in ("127.0.0.1", "localhost"):
            raise RuntimeError(f"refused redirect off localhost: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str) -> tuple[int, str, bytes]:
    opener = urllib.request.build_opener(_LocalRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "club-autopilot-verify"})
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, ctype, body


def tail(path: Path, limit: int = 40) -> str:
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-limit:])


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=5)


def start_server(port: int, env: dict[str, str], log_fh) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "autopilot", "web", "--port", str(port)]
    kwargs = {
        "cwd": str(ROOT),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def run_smoke(root: Path, private: Path) -> tuple[bool, str]:
    cfg = private / "autopilot_local.yaml"
    cfg.write_text(
        "forms_account: officer@example.com\n"
        "club_email: club@example.com\n"
        "notion_page_id: \"\"\n"
        "line_groups: []\n",
        encoding="utf-8",
    )
    env = scrubbed_env({
        "AUTOPILOT_PRIVATE_DIR": str(private),
        "AUTOPILOT_LOCAL_CONFIG": str(cfg),
        "AUTOPILOT_ONBOARDING_CONFIG": str(cfg),
        "AUTOPILOT_FORMS_ACCOUNT": "officer@example.com",
        "CLUB_NOTION_CACHE": str(private / "no-notion"),
        "VIRTUAL_ENV": str(root / ".venv"),
    })
    port = free_port()
    log_path = private.parent / "web.log"
    say(f"Smoke: python -m autopilot web --port {port}")
    proc = None
    log_fh = log_path.open("w", encoding="utf-8")
    try:
        proc = start_server(port, env, log_fh)
        base = f"http://127.0.0.1:{port}"
        home = wait_http(proc, base + "/", log_path)
        if home[0] != 200 or b"Club Autopilot" not in home[2]:
            raise RuntimeError(f"GET / -> {home[0]} (expected 200 with Club Autopilot)\n{tail(log_path)}")
        onboarding = fetch(base + "/onboarding")
        if onboarding[0] != 200 or "新社團初次設定".encode() not in onboarding[2]:
            raise RuntimeError(f"GET /onboarding -> {onboarding[0]} (expected 200)\n{tail(log_path)}")
        icon = fetch(base + "/favicon.ico")
        icon_ok = icon[0] == 200 and (icon[1].startswith("image/") or len(icon[2]) > 32)
        if not icon_ok:
            raise RuntimeError(f"GET /favicon.ico -> {icon[0]} {icon[1]}\n{tail(log_path)}")
        if proc.poll() is not None:
            raise RuntimeError(f"server exited during smoke with {proc.returncode}\n{tail(log_path)}")
        stop_process(proc)
        if proc.poll() is None:
            raise RuntimeError("server still running after shutdown")
        return True, (
            f"127.0.0.1:{port} GET / {home[0]}, GET /onboarding {onboarding[0]}, "
            f"GET /favicon.ico {icon[0]}; server stopped"
        )
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        return False, detail
    finally:
        if proc is not None and proc.poll() is None:
            try:
                stop_process(proc)
            except Exception as exc:
                say(f"Smoke: shutdown problem: {exc}")
        log_fh.close()


def wait_http(proc: subprocess.Popen, url: str, log_path: Path, timeout: float = 45) -> tuple[int, str, bytes]:
    deadline = time.time() + timeout
    last = "not up yet"
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited {proc.returncode} before {url}\n{tail(log_path)}")
        try:
            return fetch(url)
        except Exception as exc:
            last = str(exc)
            time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {url}: {last}\n{tail(log_path)}")


def run_tests(root: Path, private: Path) -> tuple[bool, str]:
    say("Running: python -m pytest -q")
    env = scrubbed_env({
        "AUTOPILOT_PRIVATE_DIR": str(private),
        "VIRTUAL_ENV": str(root / ".venv"),
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        lines.append(line)
    code = proc.wait()
    summary = ""
    for line in reversed(lines):
        text = line.strip()
        if text and any(word in text for word in ("passed", "failed", "error")):
            summary = text
            break
    if not summary:
        summary = f"pytest exit {code}"
    return code == 0, summary


def main() -> int:
    os.chdir(ROOT)
    relaunch_in_venv(ROOT)
    say("Club Autopilot offline verify")
    say(f"Repo: {ROOT}")
    say(f"Python: {sys.executable}")
    say("Skipped (no credentials, no live calls):")
    for item in SKIPPED:
        say(f"  - {item}")
    say("")
    if not dependencies_ready(ROOT):
        say("Smoke: SKIPPED  dependencies failed")
        say("Tests: SKIPPED  dependencies failed")
        say("VERIFY FAIL")
        return 1
    with tempfile.TemporaryDirectory(prefix="autopilot-verify-") as tmp:
        private = Path(tmp) / "private"
        private.mkdir()
        smoke_ok, smoke_detail = run_smoke(ROOT, private)
        say(f"Smoke: {'PASS' if smoke_ok else 'FAIL'}  {smoke_detail}")
        test_private = Path(tmp) / "pytest-private"
        test_private.mkdir()
        tests_ok, tests_detail = run_tests(ROOT, test_private)
        say(f"Tests: {'PASS' if tests_ok else 'FAIL'}  {tests_detail}")
    if smoke_ok and tests_ok:
        say("VERIFY PASS")
        return 0
    say("VERIFY FAIL")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        say("VERIFY FAIL")
        sys.exit(1)
