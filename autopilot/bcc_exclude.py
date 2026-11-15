"""Private BCC exclude list: addresses that must never be in any BCC (預告信／提醒信／課後信).

The file lives outside the repo (repo is public): <private folder>\\密件副本\\bcc_exclude.txt,
or CLUB_BCC_EXCLUDE_FILE. One entry per line, '#' starts a comment:
  someone@example.com   -> that exact address
  someone               -> any address whose local part is 'someone' (any domain)
Matching is case-insensitive. Never log or print the entries or matched addresses — counts only."""
from __future__ import annotations

import os
from pathlib import Path


def default_path() -> Path:
    env = os.environ.get("CLUB_BCC_EXCLUDE_FILE")
    if env:
        return Path(env)
    from .local_settings import private_dir
    return private_dir() / "密件副本" / "bcc_exclude.txt"


def load(path: str | Path | None = None) -> tuple[set[str], set[str]]:
    """(full addresses, local parts), lower-cased. Missing file -> nothing excluded."""
    p = Path(path) if path else default_path()
    emails, locals_ = set(), set()
    if not p.is_file():
        return emails, locals_
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        e = line.split("#", 1)[0].strip().lower()
        if not e:
            continue
        (emails if "@" in e else locals_).add(e)
    return emails, locals_


def is_excluded(addr: str, rules: tuple[set[str], set[str]]) -> bool:
    a = (addr or "").strip().lower()
    emails, locals_ = rules
    return a in emails or a.split("@", 1)[0] in locals_


def apply(addrs: list[str], path: str | Path | None = None) -> tuple[list[str], int]:
    """(kept addresses in order, how many were excluded)."""
    rules = load(path)
    kept = [a for a in addrs or [] if not is_excluded(a, rules)]
    return kept, len(addrs or []) - len(kept)
