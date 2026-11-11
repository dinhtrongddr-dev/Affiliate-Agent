# AI Affiliate Agent (Windows)

Local-first affiliate video workflow for Shopee Video and Facebook Page Reels.

## Architecture

- Call the existing ChatGPT2API service directly at http://127.0.0.1:13000/v1 using OpenAI Chat Completions. 9Router and OpenClaw are not dependencies of this agent and are left untouched.
- LUNO Automation is a Git partial/sparse clone pinned at commit 0065ad0. Only the selected Shopee upload functions and MIT license remain in the working tree.
- Shopee Skill is a Git partial/sparse clone pinned at commit 209c53f. Only the Shopee Affiliate API client, minimal HTTP/config helpers, credentials example and MIT license remain.
- Python 3.12 virtual environment is stored in .venv on D:.
- Reuse the installed Chrome, FFmpeg and ffprobe.
- Playwright uses a dedicated persistent Chrome profile under profiles\shopee. Raw cookies are not exported.
- Media is stored on E:\AffiliateAgentMedia.
- The inbox watcher registers new videos in SQLite. It does not publish or automatically run AI jobs.

## Pipeline

1. Drop an owned/licensed video into E:\AffiliateAgentMedia\inbox.
2. The watcher or scan command registers it by SHA-256 and probes its metadata.
3. The process command extracts three keyframes and sends them to the configured vision model.
4. The model returns a visual summary and Shopee search keywords.
5. The Affiliate API searches candidate offers; fuzzy matching rejects weak or ambiguous matches.
6. The system creates a tracked affiliate short link and generates Vietnamese copy.
7. A review package is written to outbox\shopee\<job_id> with the video, caption, keyframes, manifest.json and a browser-ready review.html page.
8. Facebook readiness is checked separately; a 9:16 copy is prepared only if the existing QA rules pass.
9. A human reviews the package. Only an explicit shopee-draft --confirm command uploads it to Creator Center as a draft.
10. Automatic public publishing remains disabled by default. Shopee is draft-only; Facebook publishing is separately gated.

## Local setup

1. Run scripts\configure-local.ps1 in a local PowerShell window. It imports the existing ChatGPT2API local auth key without echoing it and optionally prompts for Shopee Affiliate credentials.
2. The helper writes AA_AI_API_KEY to .env and stores Shopee App ID/Secret in config\shopee-skill\credentials.json. It enables the Affiliate API only when both Shopee credentials are supplied.
3. Verify the setup with the credentials command. Do not paste secrets into ChatGPT.
4. Log in manually to Shopee Creator Center using scripts\open-shopee-login.cmd, or:

       .\.venv\Scripts\python.exe -m agent_core.shopee_login

   Complete CAPTCHA/OTP yourself. Close the login window after Creator Center is ready.

## Repository layout

    services/agent_core/   Python package (CLI, workflow, clients)
    tests/                 unittest suite (also runs under pytest)
    scripts/               configure-local.ps1, start-media-watcher.cmd, open-shopee-login.cmd
    config/                .env.example, Shopee credentials template
    vendor/                pinned sparse upstream clones (see vendor/UPSTREAMS.md)
    state/, logs/, cache/, profiles/   runtime data (git-ignored)

Install the package in editable mode once so PYTHONPATH is no longer needed:

    .\.venv\Scripts\python.exe -m pip install -e .
    .\.venv\Scriptsffiliate-agent.exe doctor

Run tests:

    .\.venv\Scripts\python.exe -m unittest discover -s tests

## Commands

PowerShell:

    cd D:\AffiliateAgent
    $env:PYTHONPATH = 'D:\AffiliateAgent\services'

    .\.venv\Scripts\python.exe -m agent_core.cli doctor
    .\.venv\Scripts\python.exe -m agent_core.cli credentials
    .\.venv\Scripts\python.exe -m agent_core.cli scan
    .\.venv\Scripts\python.exe -m agent_core.cli status

    # Process all newly discovered or waiting jobs
    .\.venv\Scripts\python.exe -m agent_core.cli process-all

    # Inspect one job, candidates, caption and history
    .\.venv\Scripts\python.exe -m agent_core.cli show JOB_ID

    # If the automatic product match is ambiguous, choose a displayed candidate
    .\.venv\Scripts\python.exe -m agent_core.cli select-product JOB_ID ITEM_ID --confirm

    # Upload to Shopee Creator Center as a non-public draft
    .\.venv\Scripts\python.exe -m agent_core.cli shopee-draft JOB_ID --confirm

    # Public Facebook Reel upload; requires both Meta configuration and AA_AUTO_PUBLISH=true
    .\.venv\Scripts\python.exe -m agent_core.cli facebook-publish JOB_ID --confirm

The --confirm flag is required for product selection, draft upload and Facebook publishing.
Facebook publishing is public and also requires AA_META_ENABLED=true and AA_AUTO_PUBLISH=true.
A draft upload is still an external account action, so review the package first.
An uncertain upload is marked for reconciliation; the agent refuses blind retries.

## Minimal Python dependencies

The AI connection uses the existing ChatGPT2API OpenAI-compatible endpoint; no 9Router-specific adapter or extra gateway package is needed. Only packages used by the agent are listed in services/requirements-agent.txt:
httpx, watchdog, rapidfuzz, pydantic-settings and Playwright.
Shopee Affiliate API helper scripts use Python standard library only.

## Pipeline states

DISCOVERED -> ANALYZING -> ANALYZED -> PRODUCT_MATCHED
-> READY_FOR_REVIEW / NEEDS_PRODUCT_REVIEW / WAITING_FOR_CREDENTIALS
-> DRAFT_SUBMITTED_UNCONFIRMED

A platform post is tracked independently for Shopee and Facebook. A failed
platform does not roll back a successful post on the other platform.

## Safety

- AUTO_PUBLISH is false by default.
- Shopee Affiliate API and Meta publishing are disabled by default.
- No password, cookie, Page token or API secret belongs in source code.
- Shopee login is stored in a dedicated local Chrome profile; raw cookies are not exported.
- Product selection refuses to silently choose between ambiguous offers.
- Unknown upload results must be reconciled before retrying; no blind reposts.
- CAPTCHA, OTP and account security checks are handled by the account owner.
- Only media the user owns or is licensed to use may be repurposed.
