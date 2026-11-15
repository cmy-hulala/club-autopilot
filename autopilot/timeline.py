"""Feature 8: schedule every draft relative to the lecture date. Drafts only; nothing is sent."""
from __future__ import annotations

import datetime as dt

from .event import Event, md_week

STEPS = [
    # (offset days, time, key, label, output file, audience)
    (-7, "12:00", "announce", "D-7 預告信＋IG＋LINE", "2_預告信.html", "社員名單（密件副本，私密檔）"),
    (0, "12:00", "reminder", "D0 當天提醒信", "4_提醒信.html", "報名者（從課前表單回覆抓）"),
    (1, "12:00", "followup", "D+1 課後信＋課後表單", "6_課後信.html", "有參加的報名者"),
]


def build(ev: Event) -> list[dict]:
    d: dt.date = ev["date_obj"]
    rows = []
    for off, hhmm, key, label, file, audience in STEPS:
        day = d + dt.timedelta(days=off)
        rows.append({
            "key": key, "label": label, "date": day.isoformat(), "when": f"{md_week(day)} {hhmm}",
            "file": file, "audience": audience, "action": "建 Gmail 草稿（不寄出），由幹部檢查後手動寄",
        })
    rows.insert(1, {"key": "social", "label": "D-7 IG 草稿＋LINE 推播預覽", "date": rows[0]["date"],
                    "when": rows[0]["when"], "file": "3_IG文案.txt / 3_Line訊息.txt", "audience": "IG／LINE 群組",
                    "action": "只產生文案與圖卡；發佈與推播由幹部手動"})
    return rows


def to_ics(ev: Event, rows: list[dict]) -> str:
    """Officer-side calendar reminders (not attendee invites)."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//club-autopilot//EN", "CALSCALE:GREGORIAN"]
    for r in rows:
        day = r["date"].replace("-", "")
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev.raw['date']}-{r['key']}@club-autopilot",
            f"DTSTART;TZID=Asia/Taipei:{day}T120000",
            f"DTEND;TZID=Asia/Taipei:{day}T121500",
            f"SUMMARY:[{(ev['club'] or {}).get('short') or (ev['club'] or {}).get('name_zh') or '社團'}] {r['label']}（檢查草稿後手動寄出）",
            f"DESCRIPTION:{r['file']} → {r['audience']}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
