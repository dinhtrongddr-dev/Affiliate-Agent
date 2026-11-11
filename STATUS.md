# Deployment status — 2026-11-11 (v0.2.0)

## Working and verified locally

- **Web UI** (`scripts/start-app.cmd`): dashboard, video list/detail, script editor, caption editor, product picker (search or paste a Shopee link), trend list, hot products, approval and scheduling, revenue page, queue, settings, log viewer.
- **Background worker:** SQLite task queue with retry/backoff and inbox watcher; periodic Google Trends fetch and conversion sync. A publish interrupted by a crash is never retried automatically.
- **Render engine:** 9:16 1080x1920 output with blur-fill, Ken Burns/zoom, product cards, 10 xfade transition styles, Edge TTS Vietnamese voice with word timings, karaoke subtitles, hook title, CTA, ducked music and -14 LUFS loudness.
- **Base-video mode:** scene-cut detection → AI picks and reorders segments → render.
- **Trend/offer mode:** AI ideation → product → script → product images / Pexels / AI-clip adapter → render.
- **Script quality:** an AI self-review scores each script and rewrites it when the score is below the threshold.
- **Shopee Affiliate client:** rewritten as a native signed GraphQL client. The old subprocess parser could never read the upstream script's multi-line JSON, so live API calls always failed before.
- **Shopee uploader:** fixed the garbled button names (`Lưu bản nháp`, `Đăng`). Submissions now report CONFIRMED only when Shopee shows a success message or redirects.
- **TikTok Content Posting API** (inbox and direct), Facebook Reels (existing), approval gating per platform.
- **Tests:** 33 automated tests pass, plus ruff lint. The real ChatGPT2API vision analysis ran through the UI worker. The full render ran with real Edge TTS.

## Waiting for account setup

- Shopee Affiliate App ID and Secret (Cài đặt → Shopee), then enable the API.
- Manual Shopee Creator Center login (`scripts/open-shopee-login.cmd`).
- Licensed music tracks in `<media>\assets\music`.
- Optional: Pexels key, AI video generator endpoint (Muse or other), Meta Page token, TikTok token.

## Not yet verified live

- Live Shopee offer search, short links and conversion report (needs credentials).
- Creator Center draft upload with the new success detection (needs login; selectors come from upstream).
- Facebook and TikTok live uploads.
- The `http` video-generator contract against the actual Muse API (the adapter may need its field names adjusted).
