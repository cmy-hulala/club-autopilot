# Notes for contributors and agents

`main` is the frozen STEMergent submission. Do ongoing work on `launchpad`.

## Required verification

Every code change must be verified by running this script, and its output must be included as evidence.

- macOS / Linux: `bash scripts/verify`
- Windows: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1`

The script checks Python dependencies, starts `python -m autopilot web` on a free local port, requests `/`, `/onboarding`, and `/favicon.ico`, shuts that server down, then runs `pytest`. It does not call Google, LINE, Notion, Linktree, Instagram, Canva, or any other live service, and it does not read real tokens. Skipped integrations are printed near the top.

A passing run ends with `VERIFY PASS` and exit code 0. A failing run ends with `VERIFY FAIL` and a non-zero exit code. Paste the full output into the pull request or the written report. Do not describe the change as verified without that output.
