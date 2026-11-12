# Deployment status — 2026-11-12 (v0.3.0)

## Upgrade tracker (P0–P7)

States: **Có sẵn** (existed before), **Đã test thật** (ran against live services),
**Đã code, chưa test thật** (unit/contract tests only), **Chỉ thiết kế** (not built).

| Part | State | Evidence / blocker |
|---|---|---|
| P0 restore point | Đã test thật | git tag `pre-upgrade`, DB copy in `state/backups/`, 67 tests pass |
| Shopee Product Intelligence | Có sẵn (committed in P0) | Real export: "Doanh thu" holds units sold (0–982, always below price); the importer detects this and stores it as sold |
| Google Trends VN collector | Đã test thật | 10 topics on 2026-11-12 |
| Google News VN collector | Đã test thật | 15 keyword probes on 2026-11-12 |
| YouTube Data API collector | Đã code, chưa test thật | needs `AA_YOUTUBE_API_KEY` |
| Trends MCP (TikTok hashtags VN) | Đã code, chưa test thật | needs `AA_TRENDSMCP_API_KEY`; response parser is defensive because the README does not document the JSON shape |
| TikTok hashtags pasted by hand | Đã test thật (UI + API) | |
| Analyzer, scoring, lifecycle | Đã test thật | real topics ranked; growth needs ≥2 observations or a news window |
| Safety filter (rules + AI) | Đã test thật | lottery, crime, politics blocked on live data |
| AI intent via ChatGPT2API | Đã test thật | 14 topics judged in one live run |
| Trend → product matching | Đã test thật | 300-product CSV: son môi → 3CE lipsticks, ốp lưng → iPhone cases; job created from a match (stops at NEEDS_MEDIA_REVIEW: CSV has no images) |
| Muse Video Engine (P4): provider interface, muse2api client, gen_tasks queue, character reference, clip QC, retry, daily limit, restart resume, async render | Đã code, chưa test thật | 11 contract/integration tests with a fake gateway (incl. trend job -> wait -> render with AI clips). muse2api v1.6.10 is bundled and started by the app (child process, 127.0.0.1:18620, auto-upgrade disabled); lifecycle tested for real. Live generation needs the user's Muse session imported and network access to Muse |
| Autopilot, performance loop (P5–P6) | Chỉ thiết kế | |


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
