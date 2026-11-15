"""Google Forms via the official Forms API (replaces the hard-coded run_signup_form.py / run_feedback_form.py).

Builds the same question set as 報名表單規範.md. Things the API cannot do (banner image, custom
confirmation message) are written into the spec file as manual steps.
"""
from __future__ import annotations

from .event import Event


def _text(title, help_="", required=True, paragraph=False):
    item = {"title": title, "questionItem": {"question": {"required": required, "textQuestion": {"paragraph": paragraph}}}}
    if help_:
        item["description"] = help_
    return item


def _choice(title, options, kind="RADIO", help_="", required=True):
    opts = [o if isinstance(o, dict) else {"value": o} for o in options]
    item = {"title": title, "questionItem": {"question": {"required": required, "choiceQuestion": {"type": kind, "options": opts}}}}
    if help_:
        item["description"] = help_
    return item


def _scale(title, required=True):
    return {"title": title, "questionItem": {"question": {"required": required,
            "scaleQuestion": {"low": 1, "high": 10, "lowLabel": "非常不同意", "highLabel": "非常同意"}}}}


def _club_short(ev: Event) -> str:
    club = ev["club"] if isinstance(ev["club"], dict) else {}
    return str(club.get("short") or club.get("name_zh") or "社團").strip() or "社團"


def signup_items(ev: Event) -> list[dict]:
    short = _club_short(ev)
    return [
        {"title": ev["title"], "textItem": {}},
        _text("姓名"),
        _text("學校／系級", "例：學校、科系、年級"),
        _text("聯絡方式", "手機或 Line ID，活動臨時異動時聯絡用"),
        _choice("身分類別", ev["identity_options"], help_="費用請於活動當天現場繳交。"),
        _choice("是否參加本期社課", [{"value": "會參加", "goToAction": "NEXT_SECTION"},
                                  {"value": "無法參加", "goToAction": "SUBMIT_FORM"}]),
        {"title": "活動相關", "pageBreakItem": {}},
        _choice("如何得知本次活動", [f"{short} Email", f"{short} Line 群組", "Instagram", "朋友／幹部邀請", "其他"], "CHECKBOX", required=False),
        _text("最想詢問講者的問題", required=False, paragraph=True),
        _choice("是否同意活動攝影及照片使用", ["同意", "不同意（拍攝時會避開你）"],
                help_=f"活動中會拍照紀錄，照片可能用於{short}的社群與活動紀錄"),
        _text("其他備註", required=False, paragraph=True),
    ]


OVERALL = "整體而言，您對於活動的滿意度為？"
IMPROVE = "這次活動中，你認為可以更好的地方？"


def feedback_items(ev: Event) -> list[dict]:
    return [_text("姓名"), {"title": "活動滿意度調查", "pageBreakItem": {}}] + \
        [_scale(q) for q in ev["feedback_questions"]] + [_scale(OVERALL), _text(IMPROVE, required=False, paragraph=True)]


def signup_description(ev: Event) -> str:
    return (f"{ev['session_label']}「{ev['title']}」已開放報名，歡迎踴躍參與！\n"
            f"講者：{ev['speaker']['name']}｜{ev['speaker']['title']}\n"
            f"時間：{ev['md_week']}{ev['time_range']}｜地點：{ev['location']}\n\n"
            + "\n".join(ev["fee_lines"]) + f"\n\n報名截止：{ev['deadline_text']}")


def create_form(forms, title: str, description: str, items: list[dict], collect_email: bool = True) -> dict:
    form = forms.forms().create(body={"info": {"title": title, "documentTitle": title}}).execute()
    fid = form["formId"]
    reqs = [{"updateFormInfo": {"info": {"description": description}, "updateMask": "description"}}]
    if collect_email:
        reqs.append({"updateSettings": {"settings": {"emailCollectionType": "RESPONDER_INPUT"}, "updateMask": "emailCollectionType"}})
    reqs += [{"createItem": {"item": it, "location": {"index": i}}} for i, it in enumerate(items)]
    forms.forms().batchUpdate(formId=fid, body={"requests": reqs}).execute()
    try:  # forms created after 2026 need explicit publishing
        forms.forms().setPublishSettings(formId=fid, body={"publishSettings": {"publishState": {"isPublished": True, "isAcceptingResponses": True}}}).execute()
    except Exception:
        pass
    full = forms.forms().get(formId=fid).execute()
    return {"formId": fid, "edit_url": f"https://docs.google.com/forms/d/{fid}/edit",
            "responder_url": full.get("responderUri", ""), "form": full}


def prefill_url(form: dict, question_title: str) -> str:
    for it in form.get("items", []):
        if it.get("title") == question_title:
            qid = it["questionItem"]["question"]["questionId"]
            return f"{form['responderUri']}?usp=pp_url&entry.{int(qid, 16)}="
    return ""


def responses_as_rows(forms, form_id: str) -> list[dict]:
    form = forms.forms().get(formId=form_id).execute()
    titles = {it["questionItem"]["question"]["questionId"]: it["title"]
              for it in form.get("items", []) if "questionItem" in it}
    rows, token = [], None
    while True:
        resp = forms.forms().responses().list(formId=form_id, pageToken=token).execute()
        for r in resp.get("responses", []):
            row = {"Email": r.get("respondentEmail", "")}
            for qid, ans in r.get("answers", {}).items():
                vals = [a.get("value", "") for a in ans.get("textAnswers", {}).get("answers", [])]
                row[titles.get(qid, qid)] = "、".join(vals)
            rows.append(row)
        token = resp.get("nextPageToken")
        if not token:
            return rows



def list_recent_forms(drive, page_size: int = 20) -> list[dict]:
    """Newest Google Forms in this Drive. Read-only; does not delete or modify anything."""
    res = drive.files().list(
        q="mimeType='application/vnd.google-apps.form' and trashed=false",
        orderBy="createdTime desc",
        pageSize=page_size,
        fields="files(id,name,createdTime,modifiedTime,webViewLink,owners(emailAddress,displayName))",
    ).execute()
    return res.get("files", [])
