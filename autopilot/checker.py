"""Feature 6: rule checker. Every rule comes from 要求/HTML信件規範.md and 報名表單規範.md."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from .event import Event, FONT, SLOGAN_1, SLOGAN_2
from .render import body_of, subject_of

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
FEE_RE = re.compile(r"NT\$|免費|參加費|社費|匯款|費用")
CHECKIN_RE = re.compile(r"18:30|18：30|報到")


@dataclass
class Finding:
    file: str
    level: str  # error | warn | ok
    rule: str
    detail: str = ""


def _html_rules(name: str, html: str, ev: Event, out: list[Finding]):
    body = body_of(html)
    subj = subject_of(html)
    add = lambda lvl, rule, d="": out.append(Finding(name, lvl, rule, d))
    add("ok" if subj else "error", "第一行有 <!-- 主旨：… -->", subj)
    # 使用者規則：信件主旨一律用社課全名（長標題）；短標題只用在 IG 圖卡。
    full = str(ev.raw.get("title") or "")
    add("ok" if full and subj.endswith("｜" + full) else "error", "社團規則：信件主旨用社課全名（長標題）", subj)
    add("error" if re.search(r"<style", body, re.I) else "ok", "不用 <style>")
    add("error" if re.search(r"\sclass=", body, re.I) else "ok", "不用 class")
    add("error" if re.search(r"display:\s*(flex|grid)", body, re.I) else "ok", "不用 flex／grid")
    add("error" if re.search(r"<script|<form", body, re.I) else "ok", "不用 JavaScript／<form>")
    size = len(body.encode("utf-8"))
    add("error" if size >= 100_000 else "ok", "整封小於 100 KB", f"{size/1024:.1f} KB")
    # every element carrying text styling must declare the club font stack
    styled = re.findall(r"<(p|h2|h3|a|td|div|span)\b[^>]*style=\"([^\"]*)\"[^>]*>\s*[^<\s]", body)
    missing = [t for t, st in styled if "font-family" not in st]
    bad_font = [st for _, st in styled if "font-family" in st and FONT not in st]
    add("error" if missing else "ok", "每個文字元素都寫字型 Arial/Noto Sans TC/Microsoft JhengHei",
        f"缺 {len(missing)} 處" if missing else f"{len(styled)} 處都有")
    add("error" if bad_font else "ok", "字型字串與實寄版一致", f"{len(bad_font)} 處不同" if bad_font else "")
    text = re.sub(r"<[^>]+>", " ", body)
    fee = FEE_RE.findall(text)
    add("error" if fee else "ok", "信裡不寫費用", "、".join(sorted(set(fee))))
    ci = CHECKIN_RE.findall(text)
    add("error" if ci else "ok", "不寫報到時段", "、".join(sorted(set(ci))))
    golds = len(re.findall(r"background-color:#c98d17", body))
    if name.startswith("2_"):
        add("ok" if golds == 1 else "error", "只有一組金色主要按鈕", f"{golds} 個")
        add("ok" if "calendar.google.com/calendar/render" in body else "error", "有「加到 Google 日曆」按鈕")
        add("ok" if "ctz=Asia/Taipei" in body else "error", "日曆時區 Asia/Taipei")
        add("ok" if ev["deadline_text"] in body else "error", "按鈕下方有截止小字", ev["deadline_text"])
    if name.startswith("4_"):
        add("ok" if "google.com/maps/search" in body else "error", "有「開啟地圖導航」")
    if name.startswith("6_"):
        add("ok" if golds == 1 else "error", "只有一組金色主要按鈕", f"{golds} 個")
        add("ok" if "填寫課後回饋表單" in body else "error", "有「填寫課後回饋表單」按鈕")
        add("ok" if ev["session_label"] + "回饋表單" in body else "error", "有「第N堂社課回饋表單」標題", ev["session_label"] + "回饋表單")
    # 社團規則（創創社助理 2026-11 對齊；HTML信件規範.md）
    if name.startswith("2_"):
        add("ok" if "填寫課前表單" in body else "error", "社團規則：預告信有「填寫課前表單」按鈕")
        add("ok" if "加到 Google 日曆" in body else "error", "社團規則：預告信有「加到 Google 日曆」按鈕")
    if name.startswith(("2_", "4_")):
        pre = re.findall(r"usp=pp_url|entry\.\d+=", body)
        add("error" if pre else "ok", "社團規則：信件連結不預填身分（無 pp_url／entry）", f"{len(pre)} 處" if pre else "")
    if name.startswith("6_"):
        ids = sorted(set(re.findall(r"entry\.(\d+)=", body)))
        add("error" if len(ids) > 1 else "ok", "社團規則：課後信只預填整體滿意度一題", "、".join(ids))
    add("ok" if SLOGAN_1 in body and SLOGAN_2 in body else "error", "頁尾兩句口號")
    ig = str((ev["club"] or {}).get("instagram") or "")
    add("ok" if ig and ig in body else "error", "頁尾 IG 連結")
    add("ok" if "max-width:600px" in body else "error", "內容最寬 600px")


def _text_rules(name: str, text: str, ev: Event, out: list[Finding]):
    add = lambda lvl, rule, d="": out.append(Finding(name, lvl, rule, d))
    if name.startswith(("1_", "3_")):
        has_fee = ("社員：免費" in text and "非社員：NT$300" in text and "幹部邀請：優惠" in text) or \
                  ("社員免費" in text and "NT$300" in text and "幹部邀請優惠" in text)
        add("ok" if has_fee else "error", "參加費寫法：社員免費／非社員 NT$300／幹部邀請優惠")
        add("error" if re.search(r"另計|創創社員|一般外部參與者", text) else "ok", "不用舊的費用說法")
    if name.startswith("1_"):
        add("ok" if "幹部邀請  (優惠)" in text else "error", "身分選項「幹部邀請  (優惠)」原文")
        add("ok" if "已開放報名" in text and "踴躍參與" in text else "error", "說明用「已開放報名」「踴躍參與」")
    if name.startswith("3_"):
        add("ok" if SLOGAN_1 in text and SLOGAN_2 in text else "error", "結尾兩句口號")
    if name.startswith(("1_", "3_", "5_")):
        ci = re.findall(r"18:30|18：30|報到時段|建議報到", text)
        add("error" if ci else "ok", "不寫報到時段", "、".join(sorted(set(ci))))
    if name.startswith(("1_", "5_")):
        pre = re.findall(r"usp=pp_url|entry\.\d+=", text)
        add("error" if pre else "ok", "社團規則：表單不預填身分", f"{len(pre)} 處" if pre else "")


RULES_FILE = Path(__file__).resolve().parent.parent / "rules" / "custom_rules.yaml"
TARGETS = {"all": "全部", "email": "Email（.html）", "line": "LINE 文案", "ig": "IG 文案", "form": "報名表單"}
KINDS = {"forbid": "不能出現", "require": "一定要出現"}


def rules_file() -> Path:
    env = os.environ.get("CLUB_RULES_FILE")
    return Path(env) if env else RULES_FILE


def load_custom_rules(path: Path | None = None) -> list[dict]:
    p = Path(path or rules_file())
    if not p.is_file():
        return []
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    rules = data.get("rules") if isinstance(data, dict) else data
    return [r for r in (rules or []) if isinstance(r, dict) and r.get("pattern") and r.get("name")]


def validate_rule(rule: dict) -> dict:
    name = str(rule.get("name") or "").strip()
    pattern = str(rule.get("pattern") or "")
    if not name:
        raise ValueError("請填規則名稱。")
    if not pattern.strip():
        raise ValueError("請填要檢查的文字。")
    kind = rule.get("kind") or "forbid"
    if kind not in KINDS:
        raise ValueError("規則類型只能是 forbid 或 require。")
    level = rule.get("level") or "error"
    if level not in ("error", "warn"):
        raise ValueError("等級只能是 error 或 warn。")
    target = str(rule.get("target") or "all").strip() or "all"
    use_re = bool(rule.get("regex"))
    if use_re:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"正規表示式有誤：{e}") from None
    return {"name": name, "target": target, "kind": kind, "pattern": pattern, "regex": use_re, "level": level}


def add_custom_rule(rule: dict, path: Path | None = None) -> dict:
    rule = validate_rule(rule)
    p = Path(path or rules_file())
    rules = load_custom_rules(p)
    if any(r.get("name") == rule["name"] for r in rules):
        raise ValueError(f"已經有同名規則「{rule['name']}」。")
    rules.append(rule)
    p.parent.mkdir(parents=True, exist_ok=True)
    header = ""
    if p.is_file():
        header = "".join(l for l in p.read_text(encoding="utf-8").splitlines(True) if l.startswith("#"))
    p.write_text(header + yaml.safe_dump({"rules": rules}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return rule


def _target_match(target: str, name: str) -> bool:
    t = (target or "all").strip()
    if t == "all":
        return True
    if t == "email":
        return name.endswith(".html")
    if t == "line":
        return "Line" in name or "LINE" in name
    if t == "ig":
        return "IG" in name
    if t == "form":
        return name.startswith(("1_", "5_"))
    return name.startswith(t)


def _custom_rules(name: str, text: str, rules: list[dict], out: list[Finding]):
    plain = re.sub(r"<[^>]+>", " ", body_of(text)) if name.endswith(".html") else text
    for r in rules:
        if not _target_match(str(r.get("target") or "all"), name):
            continue
        pat = str(r["pattern"])
        try:
            found = re.findall(pat, plain) if r.get("regex") else ([pat] if pat in plain else [])
        except re.error:
            continue
        bad = bool(found) if (r.get("kind") or "forbid") == "forbid" else not found
        level = r.get("level") if r.get("level") in ("error", "warn") else "error"
        detail = ("出現：" + "、".join(sorted({str(x) for x in found}))[:80]) if (bad and found) else ("缺少：" + pat if bad else "")
        out.append(Finding(name, level if bad else "ok", f"自訂：{r['name']}", detail))


def check(ev: Event, outputs: dict[str, str], rules: list[dict] | None = None) -> list[Finding]:
    out: list[Finding] = []
    custom = load_custom_rules() if rules is None else rules
    allowed = {ev["club"].get("email", "").lower()}
    for name, text in outputs.items():
        if name.endswith(".html"):
            _html_rules(name, text, ev, out)
        else:
            _text_rules(name, text, ev, out)
        _custom_rules(name, text, custom, out)
        emails = {e for e in EMAIL_RE.findall(text) if e.lower() not in allowed}
        out.append(Finding(name, "error" if emails else "ok", "不含個人 Email／社員名單", f"{len(emails)} 個" if emails else ""))
        empty = re.findall(r"(?<![A-Za-z])(None|null|undefined|nan)(?![A-Za-z])", re.sub(r"<[^>]+>", " ", text))
        empty += re.findall(r"(?:講者|地點|教室)[：:]\s*(?:\n|<|｜|$)", text)
        out.append(Finding(name, "error" if empty else "ok", "社團規則：講者／地點缺資料要用佔位符，不留空或 None",
                           "、".join(sorted(set(empty)))[:60] if empty else ""))
        ph = sorted(set(re.findall(r"\{\{[^}]+\}\}", text)))
        if ph:
            out.append(Finding(name, "warn", "還有佔位符待回填（建表後自動替換）", "、".join(ph)))
    _form_rules(ev, out)
    for k in ev["assumed"]:
        out.append(Finding("event.yaml", "warn", f"「{k}」Notion 沒寫，沿用前期做法，請確認", str(ev.raw.get(k))))
    return out


IDENTITY_OPTIONS = ["社員", "非社員", "幹部邀請  (優惠)"]
FEE_LINES = ("社員：免費", "非社員：NT$300", "幹部邀請：優惠")


def _form_rules(ev: Event, out: list[Finding]):
    """Check what the Forms API step will actually create (報名表單規範.md)."""
    try:
        from . import forms_api
        items = forms_api.signup_items(ev)
        desc = forms_api.signup_description(ev)
    except Exception as e:  # pragma: no cover - defensive
        out.append(Finding("forms_api", "warn", "無法檢查表單題目", repr(e)))
        return
    opts = None
    for it in items:
        q = (it.get("createItem") or {}).get("item") or it
        if q.get("title") == "身分類別":
            co = (q.get("questionItem") or {}).get("question", {}).get("choiceQuestion", {})
            opts = [o.get("value") for o in co.get("options") or []]
    out.append(Finding("forms_api", "ok" if opts == IDENTITY_OPTIONS else "error",
                       "社團規則：身分類別三項「社員／非社員／幹部邀請  (優惠)」", "、".join(opts or [])))
    fee_ok = all(x in desc for x in FEE_LINES)
    out.append(Finding("forms_api", "ok" if fee_ok else "error", "社團規則：表單說明有三行參加費"))
    blob = json.dumps(items, ensure_ascii=False) + desc
    ci = re.findall(r"18:30|報到時段", blob)
    out.append(Finding("forms_api", "error" if ci else "ok", "社團規則：表單不寫報到時段"))


def summary(findings: list[Finding]) -> dict:
    return {
        "error": sum(f.level == "error" for f in findings),
        "warn": sum(f.level == "warn" for f in findings),
        "ok": sum(f.level == "ok" for f in findings),
    }


def to_rows(findings):
    return [asdict(f) for f in findings]
