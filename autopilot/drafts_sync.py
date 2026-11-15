"""Create or update the three Gmail drafts in the club mailbox. Never sends.

BCC sources (HTML信件規範.md: 收件人＝社團信箱自己，名單放密件副本):
- 預告信: the members list file in %USERPROFILE%\\創創社-私密\\密件副本\\ (newest *預告信*.txt).
- 提醒信: 課前表單 respondents who did not answer 「無法參加」.
- 課後信: the same 課前表單 attendees (the spec has no separate list; 課後表單 is only filled after class).
If the form has 0 responses, those two drafts keep an empty BCC.

Existing drafts are updated in place (same draft id) only when subject / HTML / BCC / To changed,
so re-running never creates duplicates. Logs and return values carry counts only, never addresses.
"""
from __future__ import annotations

from . import bcc_exclude, render
from .gmail_api import build_message, create_draft, get_draft, needs_update, update_draft

DRAFT_PLAN = (
    ("2_預告信.html", "預告信", "members"),
    ("4_提醒信.html", "當天提醒信", "registrants"),
    ("6_課後信.html", "課後信", "registrants"),
)


def registrant_bcc(forms_svc, form_id: str, club: str) -> tuple[list[str], int]:
    """(attending emails, total responses) from the 課前表單."""
    from . import forms_api, responses
    rows = forms_api.responses_as_rows(forms_svc, form_id)
    return responses.registrants_bcc(rows, attending_only=True, exclude={club.lower()}), len(rows)


def sync_drafts(outputs: dict, club: str, drafts: list[dict], bccs: dict, gmail, dry_run: bool = False) -> list[dict]:
    """Return the new drafts list (file, label, subject, bcc_count, draft_id, action)."""
    by_file = {d.get("file"): dict(d) for d in drafts or [] if d.get("file")}
    result = []
    for fname, label, who in DRAFT_PLAN:
        html = outputs[fname]
        subject = render.subject_of(html)
        body = render.body_of(html)
        bcc, excluded = bcc_exclude.apply(list(bccs.get(who) or []))
        msg = build_message(subject, body, to=club, bcc=bcc, sender=club)
        rec = by_file.get(fname) or {"file": fname}
        rec.update({"file": fname, "label": label, "subject": subject, "bcc_count": len(bcc), "bcc_source": who,
                    "excluded_count": excluded})
        did = rec.get("draft_id")
        if did:
            cur = get_draft(gmail, did)
            diff = needs_update(cur, subject, body, bcc, club)
            if not diff:
                rec["action"] = "unchanged"
            elif cur is None:
                rec["action"] = "would-create" if dry_run else "created"
                if not dry_run:
                    rec["draft_id"] = create_draft(gmail, msg).get("id")
            else:
                rec["action"] = ("would-update:" if dry_run else "updated:") + ",".join(diff)
                if not dry_run:
                    update_draft(gmail, did, msg)
        else:
            rec["action"] = "would-create" if dry_run else "created"
            if not dry_run:
                rec["draft_id"] = create_draft(gmail, msg).get("id")
        result.append(rec)
    return result


ACTION_LABEL = {"unchanged": "未變", "created": "新建", "would-create": "將新建"}


def describe(recs: list[dict]) -> str:
    parts = []
    for r in recs:
        a = r.get("action", "")
        word = ACTION_LABEL.get(a) or ("已更新" if a.startswith("updated") else "將更新" if a.startswith("would-update") else a)
        ex = f"，已排除 {r['excluded_count']} 人" if r.get("excluded_count") else ""
        parts.append(f"{r['label']} 密件副本 {r['bcc_count']} 人{ex}（{word}）")
    return "｜".join(parts)
