"""Load and enrich one event file. Nothing about a specific 堂次 is hard-coded."""
from __future__ import annotations

import datetime as dt
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ZH_NUM = "零一二三四五六七八九十"
WEEKDAY = "一二三四五六日"
FONT = "Arial,'Noto Sans TC','Microsoft JhengHei',sans-serif"
FEE_LINES = ["社員：免費", "非社員：NT$300", "幹部邀請：優惠"]
FEE_ONE_LINE = "社員免費｜非社員 NT$300｜幹部邀請優惠"
IDENTITY_OPTIONS = ["社員", "非社員", "幹部邀請  (優惠)"]
SLOGAN_1 = "We build. We learn. We venture."
SLOGAN_2 = "Building founders. Backing builders."
PLACEHOLDER_FORM = "{{課前表單網址}}"
PLACEHOLDER_SHORT = "{{表單短網址}}"
PLACEHOLDER_PREFILL = "{{評分預填網址}}"
PLACEHOLDER_FEEDBACK = "{{課後表單網址}}"


def zh_number(n: int) -> str:
    if n <= 10:
        return ZH_NUM[n]
    if n < 20:
        return "十" + (ZH_NUM[n - 10] if n > 10 else "")
    tens, ones = divmod(n, 10)
    return ZH_NUM[tens] + "十" + (ZH_NUM[ones] if ones else "")


def md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def md_week(d: dt.date) -> str:
    return f"{d.month}/{d.day}（{WEEKDAY[d.weekday()]}）"


def q(s: str) -> str:
    return urllib.parse.quote(s, safe="")


@dataclass
class Event:
    raw: dict[str, Any]
    path: Path | None = None
    ctx: dict[str, Any] = field(default_factory=dict)

    @property
    def slug(self) -> str:
        r = self.raw
        return r.get("slug") or f"{r['date']}-{self.path.stem.split('-', 3)[-1] if self.path else 'event'}"

    def __getitem__(self, k):
        return self.ctx[k]


def _as_date(v) -> dt.date:
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def _as_dt(v) -> dt.datetime:
    if isinstance(v, dt.datetime):
        return v
    return dt.datetime.strptime(str(v), "%Y-%m-%d %H:%M")


REQUIRED = ["semester", "session_no", "title", "speaker", "date", "start", "end", "room", "deadline", "highlights"]


def load_event(path: str | Path, overrides: dict | None = None) -> Event:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if overrides:
        raw.update({k: v for k, v in overrides.items() if v is not None})
    return build_event(raw, path)


def build_event(raw: dict, path: Path | None = None) -> Event:
    missing = [k for k in REQUIRED if not raw.get(k)]
    if missing:
        raise ValueError("活動設定檔缺少欄位：" + ", ".join(missing))
    ev = Event(raw=raw, path=path)
    d = _as_date(raw["date"])
    deadline = _as_dt(raw["deadline"])
    title = raw["title"].strip()
    if "：" in title:
        main, sub = title.split("：", 1)
    else:
        main, sub = title, ""
    sp = raw["speaker"]
    location = f"{raw.get('campus', '').strip()} {raw['room']}".strip()
    n_zh = zh_number(int(raw["session_no"]))
    form_url = raw.get("form_url") or PLACEHOLDER_FORM
    short_url = raw.get("form_short_url") or raw.get("form_url") or PLACEHOLDER_SHORT
    s_compact = raw["start"].replace(":", "") + "00"
    e_compact = raw["end"].replace(":", "") + "00"
    dates = f"{d:%Y%m%d}T{s_compact}/{d:%Y%m%d}T{e_compact}"
    cal_text = f"創創社第{n_zh}堂社課｜{title}"
    cal_details = f"講者：{sp['name']}｜{sp['title']}\n課前表單：{short_url}"
    calendar_url = (
        "https://calendar.google.com/calendar/render?action=TEMPLATE"
        f"&text={q(cal_text)}&dates={dates}&ctz=Asia/Taipei"
        f"&location={q(location)}&details={q(cal_details)}"
    )
    maps_url = "https://www.google.com/maps/search/?api=1&query=" + q(location)
    club = raw.get("club", {})
    ev.ctx = {
        **raw,
        "club": club,
        "date_obj": d,
        "md": md(d),
        "md_week": md_week(d),
        "mmdd": f"{d.month:02d}/{d.day:02d}",
        "weekday": WEEKDAY[d.weekday()],
        "deadline_obj": deadline,
        "deadline_text": f"{md_week(deadline.date())}{deadline:%H:%M}",
        "n_zh": n_zh,
        "session_label": f"第{n_zh}堂社課",
        "title_main": main,
        "title_sub": sub,
        "location": location,
        "time_range": f"{raw['start']}–{raw['end']}",
        "form_url": form_url,
        "short_url": short_url,
        "feedback_url": raw.get("feedback_url") or PLACEHOLDER_FEEDBACK,
        "prefill_url": raw.get("feedback_prefill_url") or PLACEHOLDER_PREFILL,
        "calendar_url": calendar_url,
        "maps_url": maps_url,
        "font": FONT,
        "fee_lines": FEE_LINES,
        "fee_one_line": FEE_ONE_LINE,
        "identity_options": IDENTITY_OPTIONS,
        "slogan1": SLOGAN_1,
        "slogan2": SLOGAN_2,
        "assumed": raw.get("assumed", []),
        "short_title": raw.get("short_title") or main,
        "hook_lead": str(raw.get("hook_lead", "")).replace("{session}", f"第{n_zh}堂社課"),
        "signup_form_title": f"{raw['semester']} 陽明交大創創社｜{d.month:02d}/{d.day:02d} 課前表單",
        "feedback_form_title": f"{raw['semester']} 陽明交大創創社｜{d.month:02d}/{d.day:02d} 課後調查",
        "signup_confirmation": f"已收到你的回覆，會參加的夥伴，我們 {md_week(d)}{raw['start']}–{raw['end']} 在{raw['room']} 見!",
        "feedback_confirmation": f"已收到你的回饋，謝謝參與 {md_week(d)} 的夥伴！期待下次再與你相聚。",
    }
    return ev


def slug_for(path: Path) -> str:
    return path.stem
