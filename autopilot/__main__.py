"""python -m autopilot run events/<slug>.yaml (example: tests/fixtures/2026-11-25-candice.yaml) [--google] [--forms-only] [--linktree]"""
from __future__ import annotations

import argparse
import json
import sys

from .local_settings import forms_account
from .pipeline import run_pipeline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="autopilot", description="Club Autopilot：一份活動檔 → 全部社課宣傳草稿（不寄出）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="跑整條流程")
    r.add_argument("event")
    r.add_argument("--google", action="store_true", help="真的建表單與 Gmail 草稿（需要社團 OAuth）")
    r.add_argument("--forms-only", action="store_true",
                   help="只建課前／課後表單，不建 Gmail 草稿（用 --forms-token 指定個人帳號 token）")
    r.add_argument("--forms-token", help="--forms-only 用的 token 檔路徑（不放 repo、不印出內容）")
    r.add_argument("--forms-account", default=forms_account(),
                   help="--forms-only 必須為此帳號，否則停止（預設 AUTOPILOT_FORMS_ACCOUNT／本機設定）")
    r.add_argument("--oauth-dir", help="OAuth 資料夾（預設為私密資料夾裡的 google-oauth）")
    r.add_argument("--members-bcc-file", help="社員密件副本清單（私密檔，不放 repo）")
    r.add_argument("--linktree", action="store_true", help="用瀏覽器同步 Linktree（需要人登入）")
    r.add_argument("--banner", action="store_true",
                   help="建表後用瀏覽器補社團黑底 Banner（與確認訊息）；只使用 --forms-account，絕不用社團帳號")
    r.add_argument("--out", help="輸出資料夾（預設 out/）")
    lf = sub.add_parser("list-forms", help="列出帳號 Drive 裡最近的 Google 表單（只讀，不刪）")
    lf.add_argument("--forms-token", help="個人帳號 token 檔")
    lf.add_argument("--forms-account", default=forms_account())
    lf.add_argument("--oauth-dir", help="社團 OAuth 資料夾（列出社團帳號的表單）")
    w = sub.add_parser("web", help="開 Flask 介面")
    w.add_argument("--port", type=int, default=5050)
    sb = sub.add_parser("set-banner", help="用瀏覽器把社團黑底 Banner 補上 Google 表單（個人帳號，不改題目）")
    sb.add_argument("--event", help="活動 YAML；從 out/<slug>/state.json 讀表單 ID，並用活動檔的確認訊息")
    sb.add_argument("--out", help="輸出根目錄（預設 out/）")
    sb.add_argument("--form-id", action="append", dest="form_ids", help="表單 ID，可重複")
    sb.add_argument("--responder-url", action="append", dest="responder_urls", help="填寫頁網址，與 --form-id 對應")
    sb.add_argument("--confirmation", action="append", dest="confirmations",
                    help="確認訊息，與 --form-id 對應；省略且未用 --event 時不改確認訊息")
    sb.add_argument("--image", help="Banner 圖片路徑（預設 assets/form_banner.jpg）")
    sb.add_argument("--account", default=forms_account(), help="必須為此帳號，否則會等你切換（預設 AUTOPILOT_FORMS_ACCOUNT／本機設定）")
    sb.add_argument("--profile", help="Chrome 設定檔資料夾（預設為私密資料夾裡的 chrome-form-banner）")
    sb.add_argument("--shots", help="截圖資料夾")
    sb.add_argument("--login-wait", type=int, default=600, help="等待登入的秒數（預設 600）")
    sb.add_argument("--confirmation-only", action="store_true", help="只寫確認訊息，不改已上的 Banner")
    sd = sub.add_parser("sync-drafts", help="建立／就地更新 3 封 Gmail 草稿（密件副本：預告信＝社員名單，提醒信／課後信＝課前表單回覆）；不寄出")
    sd.add_argument("--event", required=True)
    sd.add_argument("--dry-run", action="store_true", help="只比對、不改草稿")
    sd.add_argument("--out", help="輸出根目錄（預設 out/）")
    au = sub.add_parser("auth", help="只做本機設定裡社團信箱的 Google 授權（存 token.json），不建表單、不建草稿")
    au.add_argument("--oauth-dir", help="OAuth 資料夾（預設為私密資料夾裡的 google-oauth）")
    au.add_argument("--timeout", type=int, default=600, help="等瀏覽器授權的秒數（預設 600）")
    au.add_argument("--force", action="store_true", help="即使 token 還有效也重新授權")
    a = ap.parse_args(argv)
    if a.cmd == "auth":
        from pathlib import Path
        from .google_auth import authorize, default_oauth_dir
        d = Path(a.oauth_dir) if a.oauth_dir else default_oauth_dir()
        email = authorize(d, timeout_s=a.timeout, force=a.force)
        print(f"授權成功：{email}。token 已存到 {d / 'token.json'}。沒有建立任何表單或草稿。")
        return 0
    if a.cmd == "sync-drafts":
        return _sync_drafts_cli(a)
    if a.cmd == "web":
        from .web import app
        app.run(host="127.0.0.1", port=a.port, debug=False)
        return 0
    if a.cmd == "set-banner":
        from .form_banner import main_cli
        return main_cli(a)
    if a.cmd == "list-forms":
        from pathlib import Path
        from . import forms_api
        from .google_auth import default_oauth_dir, forms_services, services
        if a.forms_token and a.oauth_dir:
            sys.exit("list-forms 請只選 --forms-token 或 --oauth-dir 其中一個。")
        if a.forms_token:
            svc = forms_services(Path(a.forms_token), a.forms_account)
        else:
            svc = services(Path(a.oauth_dir) if a.oauth_dir else default_oauth_dir())
        print(f"帳號：{svc['account']}")
        rows = forms_api.list_recent_forms(svc["drive"])
        if not rows:
            print("（沒有表單）")
        for f in rows:
            owners = ", ".join(o.get("emailAddress", "") for o in f.get("owners") or [])
            print(f"{f.get('createdTime','')}  {f.get('name','')}  id={f.get('id','')}  {f.get('webViewLink','')}  owners={owners}")
        return 0
    if a.forms_only and a.google:
        sys.exit("不能同時用 --google 和 --forms-only。")
    if a.forms_only and not a.forms_token:
        sys.exit("--forms-only 需要 --forms-token。")
    if a.forms_only:
        mode = "forms"
    elif a.google:
        mode = "google"
    else:
        mode = "preview"
    run = run_pipeline(a.event, mode, a.out, a.oauth_dir, a.members_bcc_file, a.linktree,
                       forms_token=a.forms_token, forms_account=a.forms_account,
                       do_banner=a.banner)
    m = run.manifest
    print(f"輸出：{run.dir}")
    print(f"規則檢查：錯誤 {m['check']['error']}｜提醒 {m['check']['warn']}｜通過 {m['check']['ok']}")
    if m.get("state", {}).get("signup"):
        s = m["state"]["signup"]
        print(f"  課前表單  編輯 {s.get('edit_url')}  填寫 {s.get('responder_url')}")
    if m.get("state", {}).get("feedback"):
        f = m["state"]["feedback"]
        print(f"  課後表單  編輯 {f.get('edit_url')}  填寫 {f.get('responder_url')}")
        if f.get("prefill_url"):
            print(f"  評分預填  {f['prefill_url']}")
    for s in m["steps"]:
        print(f"  [{s['status']}] {s['step']}  {s['seconds']}s {s['error']}")
    for d in m.get("drafts") or []:
        print(f"  草稿 {d['subject']}  密件副本 {d['bcc_count']} 位  {'Gmail id ' + d['draft_id'] if d['draft_id'] else '(預覽 .eml)'}")
    if mode == "forms":
        print("（--forms-only：沒有建立 Gmail 草稿）")
    return 1 if m["check"]["error"] else 0


def _sync_drafts_cli(a) -> int:
    """Prints counts and draft ids only — never addresses."""
    from pathlib import Path
    from . import drafts_sync, render
    from .event import load_event
    from .google_auth import default_oauth_dir, forms_services, services
    from .pipeline import ROOT, _read_bcc_file
    from .web import FORMS_ACCOUNT, default_forms_token, default_members_bcc_file
    ev_path = Path(a.event)
    out = Path(a.out or ROOT / "out") / ev_path.stem
    state_p = out / "state.json"
    state = json.loads(state_p.read_text(encoding="utf-8"))
    ev = load_event(ev_path, {"form_url": state["signup"]["responder_url"], "form_short_url": state["signup"]["responder_url"],
                              "feedback_url": state.get("feedback", {}).get("responder_url"),
                              "feedback_prefill_url": state.get("feedback", {}).get("prefill_url")})
    outputs = render.render_all(ev)
    club = ev["club"]["email"]
    members = _read_bcc_file(str(default_members_bcc_file() or "") or None)
    svc_f = forms_services(default_forms_token(), FORMS_ACCOUNT)
    regs, total = drafts_sync.registrant_bcc(svc_f["forms"], state["signup"]["formId"], club)
    svc = services(default_oauth_dir(), interactive=False)
    recs = drafts_sync.sync_drafts(outputs, club, state.get("drafts") or [], {"members": members, "registrants": regs},
                                   svc["gmail"], dry_run=a.dry_run)
    print(f"課前表單回覆 {total} 份；會參加 {len(regs)} 人；社員名單 {len(members)} 人")
    for r in recs:
        ex = f"（已排除 {r['excluded_count']} 人）" if r.get("excluded_count") else ""
        print(f"  {r['label']}  {r.get('draft_id')}  密件副本 {r['bcc_count']} 人{ex}  {r['action']}")
    if not a.dry_run:
        state["drafts"] = recs
        state_p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
