"""Features 7 & 9: registrants -> BCC list; feedback responses -> summary page."""
from __future__ import annotations

import html
import re
import statistics
from collections import Counter

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def registrants_bcc(rows: list[dict], attending_only: bool = True, exclude: set[str] | None = None) -> list[str]:
    """rows: [{'Email':..., '是否參加本期社課': '會參加'|...}] -> deduped, validated list (order kept)."""
    exclude = {e.lower() for e in (exclude or set())}
    seen, out = set(), []
    for r in rows:
        email = (r.get("Email") or r.get("email") or r.get("respondentEmail") or "").strip()
        if not EMAIL_RE.match(email) or email.lower() in exclude or email.lower() in seen:
            continue
        if attending_only and "無法" in str(r.get("是否參加本期社課", "")):
            continue
        seen.add(email.lower()); out.append(email)
    return out


def summarize_feedback(rows: list[dict], scale_questions: list[str], overall: str, comment: str) -> dict:
    s = {"n": len(rows), "questions": [], "comments": []}
    for qn in scale_questions + [overall]:
        vals = [int(r[qn]) for r in rows if str(r.get(qn, "")).strip().isdigit()]
        dist = Counter(vals)
        s["questions"].append({
            "q": qn, "n": len(vals), "avg": round(statistics.mean(vals), 2) if vals else None,
            "median": statistics.median(vals) if vals else None,
            "dist": [dist.get(i, 0) for i in range(1, 11)], "overall": qn == overall,
        })
    s["comments"] = [str(r[comment]).strip() for r in rows if str(r.get(comment, "")).strip()]
    ov = next(qq for qq in s["questions"] if qq["overall"])
    vals = [int(r[overall]) for r in rows if str(r.get(overall, "")).isdigit()]
    s["nps_like"] = round(100 * (sum(v >= 9 for v in vals) - sum(v <= 6 for v in vals)) / len(vals)) if vals else None
    s["overall_avg"] = ov["avg"]
    return s


def feedback_html(title: str, s: dict) -> str:
    def bar(dist):
        mx = max(dist) or 1
        cells = "".join(
            f'<div style="display:inline-block;width:26px;margin-right:3px;text-align:center;vertical-align:bottom">'
            f'<div style="background:#c98d17;height:{int(60*v/mx)}px"></div><small>{i+1}</small></div>' for i, v in enumerate(dist))
        return f'<div style="height:80px;display:flex;align-items:flex-end">{cells}</div>'
    qs = "".join(
        f'<tr><td>{"⭐ " if q["overall"] else ""}{html.escape(q["q"])}</td><td><b>{q["avg"]}</b></td><td>{q["median"]}</td><td>{q["n"]}</td><td>{bar(q["dist"])}</td></tr>'
        for q in s["questions"])
    cm = "".join(f"<li>{html.escape(c)}</li>" for c in s["comments"]) or "<li>（沒有）</li>"
    return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>
<body style="font-family:Arial,'Noto Sans TC','Microsoft JhengHei',sans-serif;max-width:900px;margin:30px auto;color:#222">
<h1 style="color:#8a4f00">{html.escape(title)}</h1>
<p>回覆數 <b>{s['n']}</b>｜整體滿意度平均 <b>{s['overall_avg']}</b> / 10｜推薦指數（9–10 減 1–6）<b>{s['nps_like']}</b></p>
<table border="1" cellpadding="8" style="border-collapse:collapse;width:100%"><tr style="background:#f5f5f5"><th>題目</th><th>平均</th><th>中位數</th><th>n</th><th>分布 1–10</th></tr>{qs}</table>
<h2>可以更好的地方</h2><ul>{cm}</ul>
<p style="color:#555;font-size:13px">只顯示彙整結果，不顯示填答者姓名與 Email。</p></body>"""
