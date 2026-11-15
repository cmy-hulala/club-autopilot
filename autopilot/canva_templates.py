"""Read a Canva design ID from a pasted URL. The default templates stay in the program."""
from __future__ import annotations

import re

from .canva_job import CERT_TEMPLATE, IG_TEMPLATE

# Canva design URLs look like https://www.canva.com/design/DAGzIgWU-E4/edit
_DESIGN = re.compile(r"/design/([A-Za-z0-9_-]+)")
_BARE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


def design_id_from_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    match = _DESIGN.search(raw)
    if match:
        return match.group(1)
    if _BARE.fullmatch(raw) and raw.startswith("DA"):
        return raw
    return ""


def ig_template_id(event_value: str | None = None, use_sample: bool = False) -> str:
    if use_sample:
        return IG_TEMPLATE
    parsed = design_id_from_text(event_value or "")
    if parsed:
        return parsed
    try:
        from .local_settings import get
        saved = get("canva_ig_template_design_id")
        if saved:
            return str(saved).strip()
    except Exception:
        pass
    return IG_TEMPLATE


def cert_template_id(event_value: str | None = None) -> str:
    parsed = design_id_from_text(event_value or "")
    if parsed:
        return parsed
    try:
        from .local_settings import get
        saved = get("canva_cert_template_design_id")
        if saved:
            return str(saved).strip()
    except Exception:
        pass
    return CERT_TEMPLATE
