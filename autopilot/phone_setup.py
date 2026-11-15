"""Find an Android phone for the Instagram step. adb is never asked to tap the screen here."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .launchpad_i18n import T


def parse_adb_devices(stdout: str) -> list[dict]:
    rows = []
    for line in (stdout or "").splitlines():
        if "\t" not in line:
            continue
        serial, state = line.split("\t", 1)
        serial, state = serial.strip(), state.strip()
        if serial and state:
            rows.append({"serial": serial, "state": state})
    return rows


def classify_devices(rows: list[dict]) -> str:
    ready = [row for row in rows if row.get("state") == "device"]
    blocked = [row for row in rows if row.get("state") == "unauthorized"]
    if blocked and not ready:
        return "unauthorized"
    if len(ready) == 1:
        return "one"
    if len(ready) > 1:
        return "many"
    if rows:
        return "offline"
    return "none"


def tools_dir() -> Path | None:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    return Path(local) / "club-autopilot-tools" / "platform-tools"


def find_adb() -> str:
    found = shutil.which("adb")
    if found:
        return found
    folder = tools_dir()
    if not folder:
        return ""
    for name in ("adb.exe", "adb"):
        path = folder / name
        if path.is_file():
            return str(path)
    return ""


def describe_devices(stdout: str, adb_present: bool) -> dict:
    if not adb_present:
        return {"ok": False, "kind": "no-adb", "devices": [], "serial": "", "message": T["adb_missing"]}
    rows = parse_adb_devices(stdout)
    kind = classify_devices(rows)
    ready = [row for row in rows if row.get("state") == "device"]
    message = {
        "unauthorized": T["adb_unauthorized"],
        "one": T["adb_found_one"],
        "many": T["adb_found_many"],
        "offline": T["adb_none"],
        "none": T["adb_none"],
    }[kind]
    serial = ready[0]["serial"] if kind == "one" else ""
    return {"ok": kind in ("one", "many"), "kind": kind, "devices": rows, "serial": serial, "message": message}


def detect(run=None) -> dict:
    adb = find_adb()
    if not adb:
        return describe_devices("", False)
    if run is None:
        import subprocess
        proc = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=8)
        stdout = proc.stdout or ""
    else:
        stdout = run(adb)
    return describe_devices(stdout, True)
