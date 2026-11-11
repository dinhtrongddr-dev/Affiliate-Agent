# Selective upstream sources

This project does not install or vendor the complete upstream applications.
Each source is fetched with Git partial clone and sparse checkout; only the
listed files are present in the working tree.

## LUNO Automation

- Repository: https://github.com/TonCD/luno-automation
- Commit: 0065ad0
- License: MIT; see vendor/luno-automation/LICENSE
- Retained source file: backend/shopee_uploader.py
- Retained functions: persistent browser login, session check, video upload,
  caption/hashtags, cover selection, product attachment, scheduling, draft
  submission and publish submission.
- Removed from the source file: cookie-file login, standalone demo/main,
  CSV/JSON logging and unused FFmpeg helpers.
- Excluded from checkout: Electron frontend, FastAPI server, TikTok uploader,
  bulk upload, batch engine, desktop packaging and unrelated utilities.
- Local hardening: dedicated persistent Chrome profile; ambiguous product
  matches are rejected; publish result stays unconfirmed until a real Shopee
  success state can be verified.

## Shopee Skill

- Repository: https://github.com/bridgemekong/shopee-skill
- Commit: 209c53f
- License: MIT; see vendor/shopee-skill/LICENSE
- Retained files: scripts/shopee_affiliate.py, scripts/common.py,
  scripts/config.py, assets/credentials.example.json.
- common.py is reduced to HTTP and JSON output helpers.
- config.py is scoped to the Shopee Affiliate credential section.
- Excluded: task queue, caption composer, social posting integrations,
  Shopee Seller API, unrelated social API clients and skill documentation.
- Local hardening: Affiliate Open API endpoint is set to the Vietnam host;
  credentials template contains only the Affiliate API section.

## Dependency policy

- Reuse the machine's installed Python, Chrome, FFmpeg and OpenClaw.
- Keep only packages imported by Affiliate Agent or its selected Shopee
  uploader: httpx, watchdog, rapidfuzz, pydantic-settings and Playwright.
- Shopee Affiliate API helper scripts use Python standard library only.
- Do not install FastAPI/Uvicorn or any upstream desktop UI stack.
