# Deployment status — 2026-11-11

## Working

- Windows desktop connected; OpenClaw 2026.10.3 is reused.
- Node 24.21.0, Python 3.12.10, FFmpeg 8.1 and Chrome are available.
- Project: D:\AffiliateAgent.
- Media root: E:\AffiliateAgentMedia.
- LUNO is a sparse clone pinned at 0065ad0; only the selected Shopee uploader functions and MIT license remain.
- Shopee Skill is a sparse clone pinned at 209c53f; only Affiliate API client, minimal helpers, credentials example and MIT license remain.
- Full upstream UI/server/task queue and unrelated integrations are excluded.
- Python environment was pruned; FastAPI, Uvicorn, APScheduler and Tenacity are absent.
- SQLite connections now close cleanly; schema migrations add analysis, candidates, review and readiness fields.
- Video intake uses SHA-256 deduplication and ffprobe metadata.
- Three keyframes can be extracted for multimodal AI analysis.
- AI client now calls ChatGPT2API directly through OpenAI Chat Completions, including image_url blocks and ChatGPT2API thinking_effort=extended. AffiliateAgent uses the model gpt-6-astra-wm; the API's normalizer maps extended to the supported deep-reasoning mode. The local ChatGPT2API model list and authenticated text call were verified; an actual Vision request using a local QA video frame returned a description.
- Workflow supports analysis, Shopee offer search, fuzzy product ranking, tracked affiliate link creation and Vietnamese content generation.
- Ambiguous product candidates can be manually selected; the agent does not silently choose a weak match.
- Review package includes source copy, preview frames, caption.txt, manifest.json and static review.html.
- Facebook 9:16 QA is evaluated independently from Shopee media integrity.
- Shopee Creator Center upload is explicitly gated and saves as a non-public draft.
- Facebook Reel publishing command is wired with explicit confirmation, Meta credentials and AUTO_PUBLISH gates.
- Uncertain Shopee upload results are recorded for reconciliation; blind retries are blocked.
- Inbox watcher runs via scripts/start-media-watcher.cmd.
- Unit tests and mocked end-to-end pipeline test: 15 passed.
- Affiliate API dry-run passed without a network request.
- AUTO_PUBLISH remains false.

## Waiting for account setup

- Shopee Affiliate App ID and App Secret in config\shopee-skill\credentials.json.
- AA_SHOPEE_AFFILIATE_ENABLED=true after those credentials are configured.
- Manual Shopee Creator Center login in the dedicated Chrome profile.
- Meta Graph API version, Facebook Page ID and Page Access Token in .env.

## Not yet verified live

- AI visual analysis against the user's actual affiliate video (only a local QA test frame has been used so far).
- Live Shopee Affiliate offer search and short-link creation.
- Actual Creator Center draft upload and success-state reconciliation.
- Facebook live publishing and Meta Page permissions.
- Automatic startup after Windows reboot.
- Final video rendering/transcoding beyond safe copying and platform QA.

## Safety defaults

- AUTO_PUBLISH=false.
- Shopee Affiliate API and Meta publishing are disabled by default.
- No credentials are stored in source code or sent through ChatGPT.
- Product matches below threshold or with a small score margin require human selection.
- Unknown upload outcomes must be reconciled before retrying.
