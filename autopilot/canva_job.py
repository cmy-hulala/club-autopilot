"""Canva step for the dashboard: new copies of the club templates, filled per event, exported.

The dashboard process has no Canva credentials of its own (no CANVA_ACCESS_TOKEN, no `canva` CLI
login), and Canva's public Connect REST API cannot duplicate a design or edit its text. The step
therefore hands the work to the official Canva AI connector (the Canva MCP server) through a job
file, and the connector-side worker (Grok Bot) does:

1. upload the speaker photo (the only image the event starts with) as a Canva asset;
2. copy pages 1-4 of the IG template and page 1 of the certificate template into NEW designs
   (the originals are never edited);
3. replace the per-event text (``plan(ev)``) and swap the photo on IG page 1;
4. export IG pages 1-4 as PNG and the certificate as PDF + PNG, and drop them where the rest of
   the pipeline reads them (``event.canva.exported_dir`` and out/<slug>/感謝狀.*);
5. mark the job done with ``python -m autopilot.canva_job complete``.

The dashboard step writes ``out/<slug>/canva/job.json`` (status "pending") and waits for it.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from .event import Event, load_event

ROOT = Path(__file__).resolve().parent.parent
IG_TEMPLATE = "DAHSJ-8pyXw"     # 社團 IG 模板（pages 1-4 = 社課介紹 layout）— never edited
IG_TEMPLATE_PAGES = [1, 2, 3, 4]
CERT_TEMPLATE = "DAGzIgWU-E4"   # 社團正式感謝狀 — never edited; page 1 = latest speaker
CERT_TEMPLATE_PAGES = [1]
# The template's photo frame shows its image rotated 90° counter-clockwise, so the upload is
# pre-rotated clockwise and appears upright (checked on the 2026-11-15 scratch copy DAHa-a3hfA4).
IG_PHOTO_ROTATE = -90
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

# Text in the templates as they are today (the 11/04 周世恩 session) -> what each event fills in.
IG_TEMPLATE_TEXT = {
    "name_en": "Shih-En Chou",
    "name_zh": "周世恩",
    "role": "QSearch 共同創辦人暨執行長",
    "session": "社課介紹#5",
    "time": "11/04 19:00～21：00",
    "title": "從學生小工具到十年公司：QSearch 如何長成社群數據平台",
    "room": "交映樓 703",
    "h1": "<從學生 Project 到真正創業>",
    "h2": "<公司不是照原本的 Idea 長大的>",
    "h3": "<從Engineer到CEO>",
    "b1": "QSearch 最早怎麼開始？第一批客戶、第一筆收入與真正成立公司的關鍵時刻是什麼？",
    "b2": "從 Facebook、資料隱私、海外市場到今天的 AI 浪潮，面對平台與產業一次次改變，QSearch 如何判斷哪些事情該堅持、哪些必須重新來過？",
    "b3": "當工作從寫 Code 變成找客戶、管理團隊與做決策，一個技術背景的創辦人需要重新學會哪些事情？",
    "s1t": "19:00–19:20", "s1": "開場、破冰",
    "s2t": "19:20–20:30", "s2": "主題演講",
    "s3t": "20:30-21:00", "s3": "主題座談",
    "s4t": "21:00–21:30", "s4": "自由交流",
}
CERT_TEMPLATE_TEXT = {
    "title": "從學生小工具到十年公司：QSearch 如何長成社群數據平台",
    "date": "November 4, 2026",
    "name_en": "Shih-En Chou",
    "name_zh": "周世恩",
}


def _names(ev: Event) -> tuple[str, str]:
    sp = ev.raw.get("speaker") or {}
    full = str(sp.get("name") or "")
    zh = str(sp.get("name_zh") or "").strip()
    en = str(sp.get("name_en") or "").strip()
    if not en:
        en = full.replace(zh, "").strip() if zh else full
    return zh or full, en or full


def ig_title(ev: Event) -> str:
    card = ev.raw.get("ig_card") or {}
    if card.get("title"):
        return str(card["title"])
    short = str(ev.raw.get("short_title") or "").strip()
    full = str(ev.raw.get("title") or "").strip()
    sub = full.split("：", 1)[1].strip() if "：" in full else ""
    return f"{short}：{sub}" if short and sub else full


def ig_title_two_lines(ev: Event) -> str:
    t = ig_title(ev)
    return t.replace("：", "：\n", 1) if "：" in t else t


def prepare_photo(root: Path, ev: Event, dest: Path) -> Path:
    """Upright speaker photo, pre-rotated for the template frame, ready to upload."""
    from PIL import Image, ImageOps

    src = Path(root) / str(ev.raw.get("photo") or "")
    if not src.is_file():
        raise SystemExit(f"找不到講者照片：{ev.raw.get('photo')}")
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.rotate(IG_PHOTO_ROTATE, expand=True).save(dest, format="JPEG", quality=92)
    return dest


def plan(ev: Event) -> dict:
    """Find -> replace pairs for each new copy. Pure function of the event (testable)."""
    r = ev.raw
    zh, en = _names(ev)
    d = r.get("date")
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    end = str(r.get("end") or "").replace(":", "：")
    card = r.get("ig_card") or {}
    hl = r.get("highlights") or []
    heads = list(card.get("heads") or [h.get("title", "") for h in hl])
    bodies = list(card.get("bodies") or [h.get("body", "") for h in hl])
    heads += [""] * (3 - len(heads))
    bodies += [""] * (3 - len(bodies))
    sched = list(r.get("schedule") or [])
    t = IG_TEMPLATE_TEXT
    new = {
        "name_en": en, "name_zh": zh, "role": str((r.get("speaker") or {}).get("title") or ""),
        "session": f"社課介紹#{r.get('session_no')}",
        "time": f"{d.month:02d}/{d.day:02d} {r.get('start')}～{end}",
        "title": ig_title(ev), "room": str(r.get("room") or ""),
        "h1": f"<{heads[0]}>", "h2": f"<{heads[1]}>", "h3": f"<{heads[2]}>",
        # template body order: b1 under h1, b2 under h2, b3 under h3
        "b1": bodies[0], "b2": bodies[1], "b3": bodies[2],
    }
    for i in range(4):
        if i < len(sched):
            a, b, label = sched[i][0], sched[i][1], sched[i][2]
            sep = "-" if i == 2 else "–"
            new[f"s{i+1}t"] = f"{a}{sep}{b}"
            new[f"s{i+1}"] = str(label)
    ig = [{"find": t[k], "replace": new[k]} for k in t
          if k != "title" and k in new and new[k] and new[k] != t[k]]
    cert_new = {
        "title": str(r.get("title") or ""),
        "date": f"{MONTHS[d.month - 1]} {d.day}, {d.year}",
        "name_en": en, "name_zh": zh,
    }
    cert = [{"find": CERT_TEMPLATE_TEXT[k], "replace": cert_new[k]} for k in CERT_TEMPLATE_TEXT]
    cv = r.get("canva") or {}
    return {
        "ig": {"template": cv.get("template_design_id") or IG_TEMPLATE, "pages": IG_TEMPLATE_PAGES,
               "title": f"IG 社課介紹#{r.get('session_no')} {zh}（{d.isoformat()}）", "replace": ig,
               "photo_page": 1,
               # every title box gets the same two-line title and the page-1 box width
               "title_find": t["title"], "title_text": ig_title_two_lines(ev)},
        "cert": {"template": cv.get("certificate_template_id") or CERT_TEMPLATE, "pages": CERT_TEMPLATE_PAGES,
                 "title": f"感謝狀 {zh}（{d.isoformat()}）", "replace": cert},
        "photo": str(r.get("photo") or ""),
        "exported_dir": str(cv.get("exported_dir") or f"assets/canva_export/{d.isoformat()}"),
    }


def job_path(out_root: Path, slug: str) -> Path:
    return Path(out_root) / slug / "canva" / "job.json"


def outputs_ready(root: Path, out_root: Path, slug: str, exported_dir: str) -> bool:
    ex = Path(root) / exported_dir
    base = Path(out_root) / slug
    return all((ex / f"ig_{i:02d}.png").is_file() for i in range(1, 5)) and (base / "感謝狀.pdf").is_file()


def request(ev: Event, out_root: Path, slug: str) -> dict:
    p = job_path(out_root, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    job = {"slug": slug, "status": "pending", "requested_at": datetime.now().isoformat(timespec="seconds"),
           "plan": plan(ev)}
    p.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def read(out_root: Path, slug: str) -> dict:
    p = job_path(out_root, slug)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def wait(out_root: Path, slug: str, timeout_s: float = 1800, poll_s: float = 3, on_progress=None) -> dict:
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout_s:
        job = read(out_root, slug)
        st = job.get("status")
        if st in ("done", "failed"):
            return job
        if on_progress and job.get("progress") != last:
            last = job.get("progress")
            on_progress(job)
        time.sleep(poll_s)
    return {**read(out_root, slug), "status": "timeout"}


def progress(out_root: Path, slug: str, text: str, **fields) -> dict:
    job = read(out_root, slug)
    job.update(fields)
    job["progress"] = text
    job.setdefault("log", []).append(f"{datetime.now():%H:%M:%S} {text}")
    job["status"] = job.get("status") if job.get("status") in ("done", "failed") else "working"
    job_path(out_root, slug).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not url.startswith(("http://", "https://")):  # a file the worker already placed locally
        src = Path(url)
        if src.resolve() != dest.resolve():
            dest.write_bytes(src.read_bytes())
        return dest
    with urllib.request.urlopen(url, timeout=120) as r:
        dest.write_bytes(r.read())
    return dest


def complete(out_root: Path, slug: str, root: Path, ig_design: str, cert_design: str,
             ig_urls: list[str], cert_pdf: str, cert_png: str, asset_id: str = "") -> dict:
    """Download the connector's exports into place and mark the job done."""
    job = read(out_root, slug)
    ex = Path(root) / job["plan"]["exported_dir"]
    ex.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    if len(ig_urls) == 1 and ig_urls[0].split("?")[0].endswith(".zip"):
        z = _download(ig_urls[0], ex / "_ig.zip")
        with zipfile.ZipFile(z) as zf:
            names = sorted(n for n in zf.namelist() if n.lower().endswith(".png"))
            for i, n in enumerate(names[:4], start=1):
                (ex / f"ig_{i:02d}.png").write_bytes(zf.read(n))
        z.unlink()
    else:
        for i, u in enumerate(ig_urls[:4], start=1):
            _download(u, ex / f"ig_{i:02d}.png")
    files += [str(ex / f"ig_{i:02d}.png") for i in range(1, 5)]
    base = Path(out_root) / slug
    _download(cert_pdf, base / "感謝狀.pdf")
    _download(cert_png, base / "感謝狀.png")
    files += [str(base / "感謝狀.pdf"), str(base / "感謝狀.png")]
    job.update(status="done", finished_at=datetime.now().isoformat(timespec="seconds"),
               ig_design_id=ig_design, cert_design_id=cert_design, photo_asset_id=asset_id, files=files)
    job.setdefault("log", []).append(f"{datetime.now():%H:%M:%S} 匯出完成，檔案已放好")
    job["progress"] = "完成"
    job_path(out_root, slug).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return job


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Canva connector job helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("plan"); sp.add_argument("event")
    sp = sub.add_parser("progress"); sp.add_argument("slug"); sp.add_argument("text")
    sp.add_argument("--ig-design", default=""); sp.add_argument("--cert-design", default="")
    sp = sub.add_parser("complete"); sp.add_argument("slug")
    sp.add_argument("--ig-design", required=True); sp.add_argument("--cert-design", required=True)
    sp.add_argument("--ig-url", action="append", required=True)
    sp.add_argument("--cert-pdf", required=True); sp.add_argument("--cert-png", required=True)
    sp.add_argument("--asset-id", default="")
    sp = sub.add_parser("fail"); sp.add_argument("slug"); sp.add_argument("reason")
    a = ap.parse_args(argv)
    out_root = ROOT / "out"
    if a.cmd == "plan":
        print(json.dumps(plan(load_event(a.event)), ensure_ascii=False, indent=2))
    elif a.cmd == "progress":
        extra = {k: v for k, v in (("ig_design_id", a.ig_design), ("cert_design_id", a.cert_design)) if v}
        print(progress(out_root, a.slug, a.text, **extra).get("progress"))
    elif a.cmd == "complete":
        j = complete(out_root, a.slug, ROOT, a.ig_design, a.cert_design, a.ig_url, a.cert_pdf, a.cert_png, a.asset_id)
        print(j["status"], len(j["files"]), "files")
    elif a.cmd == "fail":
        job = read(out_root, a.slug)
        job.update(status="failed", error=a.reason)
        job_path(out_root, a.slug).write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        print("failed")


if __name__ == "__main__":
    main()
