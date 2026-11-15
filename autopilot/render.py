"""Feature 2: render all six weekly tasks from one Event."""
from __future__ import annotations

import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .event import Event

TEMPLATES = Path(__file__).parent / "templates"

# output filename -> template
OUTPUTS = {
    "1_報名表單.md": "signup_form.md.j2",
    "2_預告信.html": "announce.html.j2",
    "3_Line訊息.txt": "line.txt.j2",
    "3_IG文案.txt": "ig.txt.j2",
    "4_提醒信.html": "reminder.html.j2",
    "5_課後表單.md": "feedback_form.md.j2",
    "6_課後信.html": "followup.html.j2",
}

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
    trim_blocks=False,
)


def render_all(ev: Event) -> dict[str, str]:
    out = {}
    for name, tpl in OUTPUTS.items():
        text = _env.get_template(tpl).render(**ev.ctx)
        if name.endswith(".html"):
            text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
        out[name] = text
    return out


def subject_of(html: str) -> str:
    m = re.search(r"<!--\s*主旨：(.+?)\s*-->", html)
    return m.group(1).strip() if m else ""


def body_of(html: str) -> str:
    html = re.sub(r"^\s*<!--\s*主旨：.*?-->\s*", "", html, count=1, flags=re.S)
    html = re.sub(r"<!--\s*檢查清單.*?-->", "", html, flags=re.S)
    return html.strip()
