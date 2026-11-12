# Muse Video Engine

Package: `services/agent_core/videogen/`. Page: **🎞 Video AI** (`/generation`).

## What it does

1. When a trend/product video is planned and a video provider is configured, the AI script marks the 1–3 most valuable scenes `visual: "generate"`:
   - Each such scene gets an English `gen_prompt` and runs 5–8 s.
   - The script can also define one fixed `character`.
2. `engine.plan_job` creates one row per clip in `gen_tasks`. When the script defines a character, it first adds a **character reference image** row; every clip then gets that image plus the product photo as references.
3. The worker advances active rows about every 20 s (`engine.poll`). Rendering does not block the worker, and the job waits in `WAITING_FOR_GENERATION`.
4. A downloaded clip must pass QC: it has a video stream, lasts at least 1.5 s, is not mostly black and is not frozen.
   - A failed or rejected clip is retried once.
   - If the retry also fails, that scene falls back to the user's clips, then stock footage, then the product image.
5. When no clip is still pending, the job switches to `GENERATION_DONE` and the `render_generated` task builds the timeline with the AI clips. The render then works as before (voice, subtitles, music, transitions, loudness). Muse audio is muted.
6. Each clip that passes QC is also added to the job's **Clip & hình** library, tagged AI.

## Restart safety and no duplicates

- The row id is the idempotency key, computed from the job, scene, prompt, references and attempt number. It is also sent as the `Idempotency-Key` header.
- A row that already has a remote id is only polled, never sent again.
- A row left in `SUBMITTING` by a crash is re-sent with the same key.
- Writing a new script cancels the job's open rows.
- `AA_VIDEOGEN_DAILY_LIMIT` caps clips plus reference images per day locally, before anything is sent.

## Providers

| `AA_VIDEOGEN_PROVIDER` | What | Status |
|---|---|---|
| `muse2api` | Self-hosted gateway in front of **your own** muse.ai account, e.g. [czg86389-hub/muse2api](https://github.com/czg86389-hub/muse2api) (MIT). The app calls `POST /v1/videos`, `GET /v1/videos/{id}`, `POST /v1/images/tasks`, `GET /v1/images/tasks/{id}` and `GET /v1/models`. | Contract-tested against a fake gateway; **not yet tested against real Muse** |
| `http` | The older generic contract (`POST /generations`, `GET /generations/{id}`) | Unchanged |

Muse has no official video API. Every muse2api gateway reverse-engineers the muse.ai website, so it can break whenever Muse changes the site, and automated use may break Muse's terms. For that reason this app:

- uses one account only;
- does not rotate accounts or proxies, and does not use random delays to avoid detection;
- keeps the code of any gateway out of this repository.

The gateway runs as its own service, the same way ChatGPT2API does.

## Setup

The muse2api gateway is **bundled in the app** (`services/muse_gateway`, MIT, see its `UPSTREAM.md`); there is nothing to install separately.
- When `AA_VIDEOGEN_PROVIDER=muse2api` and `AA_MUSE_EMBEDDED=true` (the default), the app's worker starts the gateway as a hidden child process on `127.0.0.1:18620`.
- The worker restarts the gateway if it crashes and stops it when the app exits.
- On first start the app creates the gateway's API key and stores it in `.env`.
- The gateway runs its own headless Chrome profile in `state/muse2api/profiles`.
- The gateway keeps its account file, tasks and media in `state/muse2api`, which is git-ignored. That account file holds the session in plain text.

Steps:
1. Use your own muse.ai account. Do not use the Meta account that administers your Facebook Page.
2. On **🎞 Video AI → Đăng nhập Muse bằng JSON**, paste a fresh cookie export. The app encrypts it and loads it into the bundled gateway; it does the same automatically every time the gateway starts.
3. Check that **Cổng muse2api tích hợp** shows *Đang chạy* with 1 account, then make one trend/product video.

The gateway's self-upgrade is disabled. To upgrade, review the upstream changes and copy the new files by hand.

## Limits

- Muse clips are about 10 s and about 720p. The renderer scales them to 1080×1920, so they look slightly soft.
- On Muse's free plan, one clip uses about 2–3% of the weekly allowance (useapi.net's measurement).
- muse2api has no quota or cancel API that this app can read, so quota is enforced only through the local daily limit. Cancelling a clip only stops waiting for it; the remote job still finishes.

## Logging in with a cookie JSON

On **🎞 Video AI → Đăng nhập Muse bằng JSON**:

1. Paste a cookie export of your own muse.ai session. Two shapes are accepted:
   - a browser extension's JSON array, for example from Cookie-Editor;
   - muse2api's `{cookies, expires}` object.
2. The app keeps only `*.muse.ai` cookies and requires `hatch_sess`. It rejects an expired session.
3. The session is stored encrypted with Windows DPAPI in `state/secrets/muse_session.bin`. That file is git-ignored and readable only by the same Windows user on the same machine. Cookie values are never shown again or logged.
4. **Gửi sang muse2api** posts the session to your gateway with `POST /admin/accounts`, using the same payload as the gateway's own extension. Only one account is ever sent.

## Proxy / VPN

The app does not route Muse traffic through a proxy or VPN, and it has no per-account proxy settings. Muse is offered only in certain countries. Reaching it from an unsupported region through a proxy works around Muse's access rules and puts the account at risk. Whether and how the gateway's machine reaches Muse is the user's decision, outside this app.

Free "VPN proxy" browser extensions are a poor fit for this use. In December 2025, Koi Security reported that the Urban VPN Proxy extension captured users' AI chat conversations. The session cookie gives full control of the Muse account, so it should never pass through an untrusted extension or proxy.
