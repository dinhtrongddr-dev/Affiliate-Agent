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

Muse has no official video API. The bundled gateway automates the muse.ai website, so site changes can affect it. Account sessions and their proxies are configured through the profile import described below.

## Setup

The muse2api gateway is **bundled in the app** (`services/muse_gateway`, MIT, see its `UPSTREAM.md`); there is nothing to install separately.
- When `AA_VIDEOGEN_PROVIDER=muse2api` and `AA_MUSE_EMBEDDED=true` (the default), the app's worker starts the gateway as a hidden child process on `127.0.0.1:18620`.
- The worker restarts the gateway if it crashes and stops it when the app exits.
- On first start the app creates the gateway's API key and stores it in `.env`.
- The gateway runs its own headless Chrome profile in `state/muse2api/profiles`.
- The gateway keeps its account file, tasks and media in `state/muse2api`, which is git-ignored. That account file holds the session in plain text.

Steps:
1. Use your own muse.ai account. Do not use the Meta account that administers your Facebook Page.
2. On **🎞 Video AI → Profile · proxy · tài khoản Muse**, enter a profile ID and proxy, then paste a fresh cookie export or choose its JSON file. The app encrypts the configuration and synchronizes it with the gateway.
3. Check that the profile is synchronized and **Cổng muse2api tích hợp** shows *Đang chạy*, then create a video. Importing a profile does not itself generate a video.

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
4. This older single-session import remains under **Phiên Muse cũ (cookie đơn)**. For an account with its own proxy, use the profile import instead.

## One profile / proxy / account

On **Video AI**, fill in a stable **Mã profile** (for example `muse-01`), a label, proxy server and optional proxy credentials. Paste the browser's original Cookie-Editor JSON array unchanged. Both `http://host:port` and `socks5://host:port` / `socks5h://host:port` are supported; a URL containing credentials is also accepted.

Use the same profile ID to refresh that account's cookie or change its proxy. Each profile owns one Muse session and one proxy. A duplicate session in two profiles, conflicting bindings, or changing an account in use is rejected. Importing several profiles validates the whole batch before the gateway applies it.

For bulk import, the form also accepts this shape (all values below are examples):

```json
{"profiles":[{
  "profile_id":"muse-01",
  "label":"Muse 01",
  "proxy":{"server":"http://proxy.example:8080","username":"user","password":"pass"},
  "cookies":[{"domain":".muse.ai","name":"hatch_sess","value":"YOUR_COOKIE","expirationDate":4102444800}]
}]}
```

`cookies` may also be an object mapping cookie names to values, with an optional `expires` object. An optional `enabled` boolean controls whether a profile participates. Profile IDs use 1–64 ASCII letters/digits, `_` or `-`; import supports up to 100 profiles.

The app stores profiles encrypted with Windows DPAPI in `state/secrets/muse_profiles.bin`. It restores their bindings when starting the gateway and exposes a **Đồng bộ profile sang Muse** button for retrying synchronization. A saved profile marked **Chờ đồng bộ** has not yet been applied to the gateway. Synchronization preserves cookies renewed by the gateway; a new explicit import gets a new revision and can replace that profile's credentials. The gateway needs its own operational account/proxy configuration in its existing account store. Responses and the main UI omit cookies and proxy passwords.

The profile is a stable account/routing binding. Chromium creates isolated browser contexts for account sessions; these are not separate interactive Windows Chrome user profiles. Browser traffic, session renewal and Muse generation use the selected account's proxy. A proxy failure does not fall back to direct access. Once managed profiles exist, generation uses those profiles and does not select older proxy-less accounts; their stored records are preserved.

No live generation or proxy-credential verification is implied by importing JSON. Use the configured proxy service and a valid Muse session for an owner-authorized live check.
