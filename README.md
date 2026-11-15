# Club Autopilot

**A few clicks on one dashboard run a student club's whole weekly lecture workflow: sign-up form, Canva cards, QR code, emails, LINE, Linktree and Instagram. Every public action waits for a human to confirm it.**

Built solo for **STEMergent Hacks 2026** (Hackathon pathway, working prototype) for **陽明交大創創社 / NYCU Innovation & Entrepreneurship Club (NYCU IEC)**. It was demoed live on the club's real lecture of 2026-11-25 (Session 6, speaker 謝慈 Candice).

**LaunchPad Hacks** judges: use the `launchpad` branch. Setup with fake data, the first-time wizard, and a dry-run are in [LaunchPad judges](#launchpad-judges). That branch is not merged into `main`.

▶ **Demo video (2:16):** https://youtu.be/mQXyeGE0Whc

---

## The problem

Every week, a club officer prepares one lecture by hand. That means a Google Form, an announcement email to members, a reminder and a follow-up, LINE messages to two groups, four Instagram cards plus a post and a story, a QR code, a Linktree update and a speaker certificate. That is about ten tools.

The real cost is **attention, not time**. Switching between tasks all week leaves officers with no energy to polish the details, and no headspace to think about where the club should go next.

## What it does

From one Flask dashboard (`http://127.0.0.1:5050`, Chinese/English toggle):

| Step | What happens | Where |
|---|---|---|
| 1. Import | Load the next lecture (speaker, time, room, hook) from the club's Notion page, or from an offline export | `notion_import.py` |
| 2. Copy + rule check | Generate the announcement, reminder and follow-up emails, LINE and IG copy; check them against the club's style rules | `render.py`, `checker.py` |
| 3. Canva | Prepare a job file. Club templates are **copied** (never edited), filled with the speaker, title and photo, and exported as 4 IG cards and a certificate | `canva_job.py`, `scripts/canva_ops.py` |
| 4. Google Forms | Create the pre-lecture sign-up form and the post-lecture feedback form (reused if they already exist) | `forms_api.py` |
| 5. Form banner | Add the club header banner and confirmation message (the Forms API cannot do this, so headless Playwright does it) | `form_banner.py` |
| 6. QR | Stamp a QR code for the live sign-up form onto IG card 4 | `canva_step.py` |
| 7. Certificate | Speaker thank-you certificate (PNG/PDF) | `certificate.py` |
| 8. Gmail drafts | Create three drafts in the club inbox. The BCC list comes from the members list or form responses, minus a private exclusion list | `gmail_api.py`, `drafts_sync.py`, `bcc_exclude.py` |
| 9. Linktree | Add this week's form link and hide older form links (hidden, not deleted) | `linktree.py` |
| 10. Counts | Registration and feedback counts (numbers only) | `responses.py` |

**Run all** does steps 1–10 in the background. It creates and prepares things; it never emails, posts or sends anything.

**Publish (needs you)**, where each button has its own confirm dialog:

- **Send to LINE**: pushes the shown text to the club's LINE groups through the Messaging API.
- **Send announcement email**: sends the existing announcement draft once, by BCC. A second send is refused, and the Gmail message ID is recorded.
- **Save IG draft (phone)**: saves an IG draft by default. With *publish* ticked and a second confirm, it drives the club's Android phone over adb/uiautomator. It uploads the 4 cards, adds the configured song, pastes the caption and publishes.
- **Reshare post to Story (phone)**: saves a story draft by default. With *publish* ticked and confirmed, it opens the newest post and checks that it matches this week's cover card. It then likes and reposts it, reshares it to a story with a sign-up link sticker and music, and publishes.

## Architecture

```mermaid
flowchart LR
  N[Notion page / offline export] --> I[Import]
  I --> E[(events/&lt;slug&gt;.yaml)]
  E --> D[Flask dashboard<br/>autopilot/web.py]
  D -->|Run all: prepare only| P[pipeline.py]
  P --> R[render + checker<br/>emails, LINE, IG copy]
  P --> C[canva_job.json] -.-> AI[AI assistant via<br/>Canva connector] -.-> CV[IG cards + certificate PNG]
  P --> F[Google Forms API<br/>+ Playwright banner]
  P --> Q[QR on ig_04]
  P --> G[Gmail API: 3 drafts<br/>BCC minus exclusions]
  P --> L[Linktree via Playwright]
  D -->|confirm| LN[LINE Messaging API push]
  D -->|confirm| GM[Gmail: send announcement once]
  D -->|confirm| IG[Instagram post + story<br/>adb / uiautomator on Android]
  S[(Private folder outside repo:<br/>OAuth tokens, LINE token,<br/>BCC lists, local settings)] -.-> P
  S -.-> D
```

Each run writes to a new timestamped folder `out/<event>/runs/<timestamp>-<mode>/`. Old outputs are never deleted.

## Setup and run

Python 3 (developed on 3.13). The phone steps also need Android `adb`; `scrcpy` is optional, for watching.

```bash
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate   (or just run run_web.cmd)
pip install -r requirements.txt
python -m playwright install chromium

python -m autopilot web --port 5050          # dashboard at http://127.0.0.1:5050
python -m autopilot run tests/fixtures/2026-11-25-candice.yaml   # preview only, no Google/LINE/IG
pytest -q
```

Private settings live **outside the repo**, in `%USERPROFILE%\創創社-私密\` (or wherever environment variables point):

| What | Where |
|---|---|
| Personal Google account that owns the Forms | env `AUTOPILOT_FORMS_ACCOUNT`, or `forms_account:` in `autopilot_local.yaml` |
| Notion page id / token | env `NOTION_PAGE_ID`, `NOTION_TOKEN`, or `autopilot_local.yaml` |
| Google OAuth tokens | `創創社-私密\google-oauth\` (the code refuses an OAuth folder inside the repo) |
| LINE channel token and group IDs | env `LINE_CHANNEL_ACCESS_TOKEN`, `LINE_GROUP_IDS`, or `創創社-私密\line\line_bot.json` |
| BCC exclusion list | env `CLUB_BCC_EXCLUDE_FILE`, or `創創社-私密\密件副本\bcc_exclude.txt` |

See `autopilot/local_settings.py` for the format. Without these settings, the preview, the copy and the tests all still work.

## LaunchPad judges

This section is for the **LaunchPad Hacks** submission on the `launchpad` branch. You do not need Google, LINE, Linktree, Instagram, or Canva credentials. The wizard and the module page use a **simulated connection test** when those credentials are missing, and the dry-run **does not send anything**.

`main` is left unchanged. Check out `launchpad` and stay on it.

### Install

```bash
git clone https://github.com/cmy-hulala/club-autopilot.git
cd club-autopilot
git checkout launchpad

python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                       # 135 tests, including onboarding and the module catalog
```

Playwright's browser download is only needed later, for a real Linktree sync or a real form banner. The judge path below does not open a browser.

### Run the dashboard

```bash
python -m autopilot web --port 5050
```

Open http://127.0.0.1:5050 . The first screen links to **初次設定** (first-time setup). The same pages are:

- http://127.0.0.1:5050/onboarding
- http://127.0.0.1:5050/modules

Use the 中文 / English toggle in the header. Generated lecture text stays Chinese.

### Configure with fake data

1. **Welcome.** Press **下一步**. Progress is saved on this computer, so a refresh continues at the same step.
2. **Club profile.** Example: club name `North Campus Builders`, contact `Club Officer`, email `officer@example.com`, color `#1255aa`, tagline `We build on Thursdays`.
3. **Google Forms and Gmail.** Leave the emails blank, or use `officer@example.com` and `club@example.com`. Press **測試連線**. With no OAuth file, the result is a yellow **模擬通過** (simulated pass). It does not call Google. Then press **下一步**.
4. **LINE.** Leave the group blank. Do not paste a channel token into the form. **測試連線** is simulated and does not push. Then **下一步**.
5. **Linktree.** Optional: `https://linktr.ee/north-campus`. The test only checks the URL. It does not open a browser.
6. **Instagram / phone.** Leave the serial blank. With no `adb` device, the test is simulated and does not tap a phone.
7. **Canva.** Optional note: `Thursday template`. The test only checks that the job-file module imports. This program stores no Canva password.
8. **Import a sample event.** Press **匯入範例活動**. This copies the included demo lecture into the private folder (and into gitignored `out/_web/` so the dashboard can open it). It does not modify `events/` or the fixture file.
9. **Preview dry-run.** Press **開始預覽演練**. This runs the pipeline in `preview` mode only. It writes local files. It does not create forms, Gmail drafts, LINE pushes, Linktree edits, or Instagram posts. The page says that nothing was sent.
10. **Save.** Press **寫入本機設定**.

The club file is written **outside the git repo**:

| System | Default file |
|---|---|
| Windows | `%USERPROFILE%\創創社-私密\autopilot_local.yaml` |
| macOS / Linux | `~/創創社-私密/autopilot_local.yaml` |

To put that folder somewhere else (useful on a judge laptop):

```bash
# Windows PowerShell
$env:AUTOPILOT_PRIVATE_DIR = "$env:USERPROFILE\autopilot-private"
$env:AUTOPILOT_ONBOARDING_CONFIG = "$env:USERPROFILE\autopilot-private\autopilot_local.yaml"

# macOS / Linux
export AUTOPILOT_PRIVATE_DIR="$HOME/autopilot-private"
export AUTOPILOT_ONBOARDING_CONFIG="$HOME/autopilot-private/autopilot_local.yaml"
```

If `AUTOPILOT_LOCAL_CONFIG` points at a file **inside** the repo, onboarding will not overwrite it. It writes to the private folder instead.

**下載空白範本** downloads `autopilot_local.template.yaml` with fake placeholders only. Save that copy outside the repo if you want to edit it by hand. Do not commit it.

Closing the browser and opening `/onboarding` again resumes the saved step. **重設進度** clears the wizard progress and does not delete a settings file already written.

### Module catalog

Open http://127.0.0.1:5050/modules .

- Each card shows **已啟用** or **已停用**, and the last test badge (**未測試**, **模擬通過**, **已通過**, **測試失敗**).
- **啟用 / 停用** saves immediately. Turning a module off does **not** remove the dashboard's existing manual buttons or change **全部執行**. Those stay as they were, so a saved toggle cannot silently skip a live step.
- Each card has a settings form. A bad email or a group ID with a space stays on the card with a red error and does not save.
- **測試連線** uses the same checks as the wizard. Success is green when a local credential is really present (the file is not printed and no API is called). A missing credential is a yellow simulated pass. A broken credential file or a phone serial that `adb` cannot see is red.
- Reload the page. The toggle and the saved email are still there.

Then go back to http://127.0.0.1:5050 . **在儀表板打開範例活動** opens the imported sample. **從 Notion 匯入** and **全部執行** behave as before. **全部執行** still only prepares files.

### What the judge path never does

- It does not email, push LINE, publish Instagram, log into Canva, or edit Linktree.
- It does not write tokens, member lists, or `autopilot_local.yaml` into the git repository.
- It does not print OAuth tokens, LINE tokens, or Notion tokens. A credential file that sits inside the repo is ignored and the test stays simulated.

### Limits of this submission

- Simulated checks do not prove a real Google, LINE, Linktree, Instagram, or Canva account works. A green **已通過** on Google or Gmail only means a token file outside the repo was readable JSON.
- LINE's green result only means a token and a group ID are configured. The test does not push.
- Linktree's test does not log in. Instagram's test runs `adb devices` at most, and never taps the phone.
- Module on/off is stored for the club. It does not change the existing pipeline, on purpose.
- The sample lecture is the included 2026-11-25 demo event, not a new club's real calendar.
- The private folder on Windows uses the name `創創社-私密`, same as the rest of this project. Set `AUTOPILOT_PRIVATE_DIR` if you want a different folder.

## Safety design

- **Run all never publishes.** Its confirm dialog says so: nothing is emailed, posted or sent from that button.
- **Every public action is a separate button with its own confirm dialog** (LINE, email). Instagram publishing also needs a *publish* box ticked plus a second confirm; otherwise it only saves a draft.
- **LINE is only ever sent from its button**, never as part of a pipeline run.
- **Email:** the announcement is sent once, from an existing draft; a duplicate send is refused. The reminder and follow-up stay as drafts.
- **BCC privacy:** recipients are always BCC. A private exclusion list (full address or local-part match, case-insensitive) is applied to every BCC, and the UI and logs show only counts.
- **Instagram guard:** before every tap, the phone flow reads the screen and stops if the tap would land on Share/Post/Remove at the wrong moment. It also stops if the phone is locked. The story flow checks that the post it opened matches this week's cover before liking or resharing.
- **Canva templates are never edited.** New copies are made for each lecture.
- **Accounts:** the forms step refuses any account other than the configured personal one, and never uses the club account for forms.
- **No secrets in the repo:** tokens, member lists, exclusion lists and local settings are git-ignored and live outside the repo.

## Tests

```bash
pytest -q        # 135 tests
```

Google, Gmail, LINE, Linktree and adb are all mocked. The tests cover the pipeline, the rule checker, the form banner, the QR stamp, BCC exclusion, the dashboard routes (including confirm and send-once), the i18n of every UI string, IG music and story logic, run-all ordering, the first-time setup wizard, and the module catalog.

## Honest disclosures

**Pre-existing work.** This project builds on my own pre-existing personal scripts for club work, written before the hackathon. They were one-off Python scripts with hard-coded lecture numbers, form IDs and paths: a Google OAuth helper, a Forms API script for one sign-up form, a feedback-form script, a Gmail-draft script, HTML letter templates, and an experimental Playwright script for Canva. Those scripts are **not** in this repo; their logic was rewritten here.

**New during the hackathon:**

- An event-file-driven pipeline with no hard-coded lecture data.
- The Flask dashboard with Notion import, Run all, per-step status and Chinese/English UI.
- The orchestration and the separate, confirmed Publish actions.
- Rebuilt email templates and a rule checker for the club's style guide.
- The Canva job flow (copy template → fill → export).
- Automatic Google Form banner and confirmation message.
- The page-4 QR stamp, including the tilted-frame fix.
- The certificate renderer.
- Gmail drafts with in-place updates, plus the private BCC exclusion list.
- LINE push to the club groups.
- Linktree sync.
- Instagram carousel publishing with music, and the story reshare with a link sticker, both via adb.
- The test suite.

**Canva step.** Canva has no public API for copying a template and editing its text, so the dashboard writes a job file. An **AI assistant then performs the Canva step through Canva's official connector**: it copies the templates into new designs, fills them, and exports PNG/PDF. The dashboard process holds no Canva credentials.

**AI assistance.** AI coding assistants were used heavily to write, refactor and test the code, and to drive the Notion/Canva connectors under my direction. Lecture content comes from the club's own Notion page.

**Third-party services and tools:**

- Google Forms API and Gmail API (OAuth)
- LINE Messaging API
- Linktree (web UI via Playwright)
- Instagram (Android app via adb/uiautomator)
- Canva (via its connector)
- Notion API
- Playwright
- scrcpy (screen mirroring for the recording)
- edge-tts (demo video narration)
- Python, Flask, Jinja2, PyYAML, Pillow, qrcode, OpenCV

## Limitations

- **Instagram automation is tied to one phone.** Screen positions were measured on one Android phone (1080×2400) with the Chinese UI, so an IG app update or a different phone may need re-measuring (`config/ig_adb.yaml`). Instagram has no public API for music or link stickers on personal and creator posts, which is why it uses adb.
- **Canva needs an AI assistant with the Canva connector** to run the job file. Without one, the officer does that step by hand.
- **Linktree and the Google Forms banner use browser automation**, so a UI change on either site can break them. Linktree needs a one-time human login.
- **Windows first.** It was built and run on Windows 11 (the `.cmd` helpers); the core and the tests also run on Linux.
- **Built around one club.** Templates and rules follow NYCU IEC's style guide, so another club would need its own templates and rules.

## License

MIT, see `LICENSE`.
