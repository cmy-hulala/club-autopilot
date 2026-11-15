"""Gmail drafts only. There is deliberately no send function in this project."""
from __future__ import annotations

import base64
from email.message import EmailMessage


def build_message(subject: str, html: str, to: str, bcc: list[str] | None = None, sender: str | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = to
    if sender:
        msg["From"] = sender
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg.set_content("請用支援 HTML 的信箱開啟這封信。")
    msg.add_alternative(html, subtype="html")
    return msg


def create_draft(gmail, msg: EmailMessage) -> dict:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()


def update_draft(gmail, draft_id: str, msg: EmailMessage) -> dict:
    """Replace an existing draft's message in place (same draft id; still never sent)."""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail.users().drafts().update(userId="me", id=draft_id, body={"id": draft_id, "message": {"raw": raw}}).execute()


def get_draft(gmail, draft_id: str) -> dict | None:
    """Return {"subject", "to", "bcc": [..], "html"} for a draft, or None if it no longer exists."""
    from email import message_from_bytes, policy
    try:
        d = gmail.users().drafts().get(userId="me", id=draft_id, format="raw").execute()
    except Exception as e:  # googleapiclient HttpError 404 -> gone (sent or deleted)
        status = getattr(getattr(e, "resp", None), "status", None)
        if str(status) == "404":
            return None
        raise
    raw = (d.get("message") or {}).get("raw") or ""
    m = message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)
    html = ""
    for part in m.walk():
        if part.get_content_type() == "text/html":
            html = part.get_content()
            break
    bcc = [x.strip() for x in str(m.get("Bcc") or "").split(",") if x.strip()]
    return {"subject": str(m.get("Subject") or ""), "to": str(m.get("To") or ""), "bcc": bcc, "html": html}


def needs_update(current: dict | None, subject: str, html: str, bcc: list[str], to: str) -> list[str]:
    """What differs between a stored draft and what we want (names only, never addresses)."""
    if current is None:
        return ["missing"]
    diff = []
    if current.get("subject") != subject:
        diff.append("subject")
    if (current.get("html") or "").strip() != (html or "").strip():
        diff.append("html")
    if sorted(x.lower() for x in current.get("bcc") or []) != sorted(x.lower() for x in bcc):
        diff.append("bcc")
    if (current.get("to") or "").strip().lower() != to.strip().lower():
        diff.append("to")
    return diff
