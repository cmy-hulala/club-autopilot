"""Orchestrates one run. Feature 3: every run gets its own folder; nothing old is ever deleted."""
from __future__ import annotations

import datetime as dt
import html
import json
import shutil
import time
from pathlib import Path

from . import canva_step, checker, linktree, render, responses, timeline
from .certificate import make_certificate
from .event import Event, load_event

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


class Run:
    def __init__(self, ev: Event, mode: str, out_root: Path):
        self.ev, self.mode = ev, mode
        self.slug = ev.path.stem if ev.path else str(ev.raw["date"])
        self.base = out_root / self.slug
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.dir = self.base / "runs" / f"{stamp}-{mode}"
        n = 1
        while self.dir.exists():  # never overwrite
            n += 1; self.dir = self.base / "runs" / f"{stamp}-{mode}-{n}"
        self.dir.mkdir(parents=True)
        self.steps: list[dict] = []
        self.manifest: dict = {"event": self.slug, "mode": mode, "dir": str(self.dir), "created": stamp}

    def step(self, name):
        run = self

        class _S:
            def __enter__(s):
                s.t = time.time(); return s

            def __exit__(s, et, e, tb):
                run.steps.append({"step": name, "seconds": round(time.time() - s.t, 2),
                                  "status": "error" if et else "ok", "error": repr(e) if e else ""})
                return False
        return _S()

    def write(self, rel: str, data, binary=False) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            p.write_bytes(data)
        else:
            p.write_text(data, encoding="utf-8")
        return p


def _state_path(base: Path) -> Path:
    return base / "state.json"  # form IDs / URLs only. No tokens, no emails.


def _load_json(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _read_bcc_file(path: str | None) -> list[str]:
    if not path:
        return []
    from .responses import registrants_bcc
    lines = Path(path).read_text(encoding="utf-8").replace(",", "\n").split()
    return registrants_bcc([{"Email": x} for x in lines], attending_only=False)


def run_pipeline(event_path: str | Path, mode: str = "preview", out_root: Path | None = None,
                 oauth_dir: str | None = None, members_bcc_file: str | None = None,
                 do_linktree: bool = False, overrides: dict | None = None,
                 forms_token: str | None = None,
                 forms_account: str | None = None,
                 do_banner: bool = False) -> Run:
    if not forms_account:
        from .local_settings import forms_account as _fa
        forms_account = _fa()
    out_root = Path(out_root or ROOT / "out")
    event_path = Path(event_path)
    ev = load_event(event_path, overrides)
    r = Run(ev, mode, out_root)
    state = _load_json(_state_path(r.base), {})
    shutil.copy2(event_path, r.dir / "event.yaml")
    svc = None

    if mode not in ("google", "forms") and state.get("signup"):  # reuse form URLs from an earlier Google/forms run
        ev = load_event(event_path, {**(overrides or {}), "form_url": state["signup"]["responder_url"],
                                     "form_short_url": state["signup"]["responder_url"],
                                     "feedback_url": state.get("feedback", {}).get("responder_url"),
                                     "feedback_prefill_url": state.get("feedback", {}).get("prefill_url")})
        r.ev = ev

    if mode in ("google", "forms"):
        import sys
        from . import forms_api
        if mode == "google":
            from .google_auth import default_oauth_dir, services
            with r.step("Google 登入（社團信箱）"):
                svc = services(Path(oauth_dir) if oauth_dir else default_oauth_dir())
        else:
            from .google_auth import forms_services
            if not forms_token:
                sys.exit("--forms-only 需要 --forms-token")
            with r.step("Google 登入（個人建表帳號）"):
                svc = forms_services(Path(forms_token), forms_account)
        with r.step("建立課前表單（Forms API）"):
            if not state.get("signup"):
                f = forms_api.create_form(svc["forms"], ev["signup_form_title"], forms_api.signup_description(ev), forms_api.signup_items(ev))
                state["signup"] = {k: f[k] for k in ("formId", "edit_url", "responder_url")}
        with r.step("建立課後表單（Forms API）"):
            if not state.get("feedback"):
                f = forms_api.create_form(svc["forms"], ev["feedback_form_title"], "", forms_api.feedback_items(ev))
                state["feedback"] = {k: f[k] for k in ("formId", "edit_url", "responder_url")}
                state["feedback"]["prefill_url"] = forms_api.prefill_url(f["form"], forms_api.OVERALL)
        _state_path(r.base).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        ev = load_event(event_path, {**(overrides or {}),
                                     "form_url": state["signup"]["responder_url"],
                                     "form_short_url": state["signup"]["responder_url"],
                                     "feedback_url": state["feedback"].get("responder_url"),
                                     "feedback_prefill_url": state["feedback"].get("prefill_url")})
        r.ev = ev
        if do_banner:
            with r.step("表單 Banner 與確認訊息（瀏覽器）"):
                from . import form_banner
                res = form_banner.apply_from_state(
                    state, ev,
                    image=form_banner.default_banner(),
                    shots=r.dir / "form_banner_shots",
                    required_account=forms_account if mode == "forms" else form_banner.DEFAULT_ACCOUNT,
                )
                r.manifest["form_banner"] = res

    with r.step("產生六項任務（模板）"):
        outputs = render.render_all(ev)
        for name, text in outputs.items():
            r.write(f"outputs/{name}", text)
    with r.step("規則檢查"):
        findings = checker.check(ev, outputs)
        r.write("check.json", json.dumps(checker.to_rows(findings), ensure_ascii=False, indent=2))
        r.manifest["check"] = checker.summary(findings)
    with r.step("時間軸（D-7／D0／D+1）"):
        tl = timeline.build(ev)
        r.write("timeline.json", json.dumps(tl, ensure_ascii=False, indent=2))
        r.write("timeline.ics", timeline.to_ics(ev, tl))
    with r.step("Canva IG 圖卡"):
        r.manifest["canva"] = canva_step.run(ev, r.dir / "ig")
    with r.step("講者感謝狀 PDF"):
        make_certificate(ev, r.dir / "感謝狀.pdf", png_path=r.dir / "感謝狀.png")
    with r.step("抓報名名單 → 提醒信密件副本"):
        if mode == "google":
            from . import forms_api
            rows = forms_api.responses_as_rows(svc["forms"], state["signup"]["formId"])
        else:
            rows = _load_json(SAMPLES / "signup_responses_sample.json", [])
        club = ev["club"]["email"]
        bcc_reg = responses.registrants_bcc(rows, attending_only=True, exclude={club})
        r.manifest["registrants"] = {"responses": len(rows), "bcc_count": len(bcc_reg), "source": "form" if mode == "google" else "samples (fake)"}
    with r.step("課後回饋彙整頁"):
        from .forms_api import IMPROVE, OVERALL
        if mode == "google" and state.get("feedback"):
            from . import forms_api
            fb_rows = forms_api.responses_as_rows(svc["forms"], state["feedback"]["formId"])
        else:
            fb_rows = _load_json(SAMPLES / "feedback_responses_sample.json", [])
        if fb_rows:
            s = responses.summarize_feedback(fb_rows, ev["feedback_questions"], OVERALL, IMPROVE)
            r.write("課後回饋彙整.html", responses.feedback_html(f"{ev['session_label']}課後回饋彙整（{ev['md_week']}）", s))
            r.manifest["feedback"] = {"n": s["n"], "overall_avg": s["overall_avg"]}
    if mode == "forms":
        r.manifest["drafts"] = []
    else:
        with r.step("Gmail 草稿（不寄出）"):
            from .gmail_api import build_message, create_draft
            members = _read_bcc_file(members_bcc_file)
            plan = [("2_預告信.html", members), ("4_提醒信.html", bcc_reg), ("6_課後信.html", bcc_reg)]
            drafts = []
            from . import bcc_exclude
            for fname, bcc in plan:
                bcc, excluded = bcc_exclude.apply(bcc)
                h = outputs[fname]
                msg = build_message(render.subject_of(h), render.body_of(h), to=club, bcc=bcc, sender=club)
                if mode == "google":
                    d = create_draft(svc["gmail"], msg)
                    drafts.append({"file": fname, "subject": msg["Subject"], "bcc_count": len(bcc), "excluded_count": excluded, "draft_id": d.get("id")})
                else:
                    del msg["Bcc"]
                    msg["X-Club-Autopilot-Bcc"] = f"{len(bcc)} addresses (hidden in preview)"
                    r.write(f"drafts/{fname.replace('.html', '.eml')}", msg.as_bytes(), binary=True)
                    drafts.append({"file": fname, "subject": msg["Subject"], "bcc_count": len(bcc), "excluded_count": excluded, "draft_id": None})
            r.manifest["drafts"] = drafts
    with r.step("Linktree 同步"):
        lp = linktree.plan(ev, ev["form_url"])
        r.write("linktree_plan.json", json.dumps(lp, ensure_ascii=False, indent=2))
        r.manifest["linktree"] = {"planned_add": lp["add"], "will_hide": lp["will_hide"], "executed": False}
        if do_linktree:
            res = linktree.run(lp, r.base / "linktree_shots")
            r.manifest["linktree"].update(executed=bool(res.get("logged_in", True)), ok=res.get("ok"), log=res["log"])

    r.manifest["steps"] = r.steps
    r.manifest["state"] = state
    r.write("manifest.json", json.dumps(r.manifest, ensure_ascii=False, indent=2, default=str))
    (r.base / "latest.json").write_text(json.dumps({"dir": str(r.dir)}, ensure_ascii=False), encoding="utf-8")
    return r
