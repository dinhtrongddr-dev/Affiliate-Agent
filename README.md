# AI Affiliate Agent (Windows)

Local-first app that turns videos into affiliate shorts for Shopee Video, Facebook Reels and TikTok, A to Z:

- **Có video base:** thả video vào inbox (hoặc kéo thả trên web). AI xem video, tìm sản phẩm Shopee, lấy link affiliate, viết kịch bản viral, tự cắt và sắp lại cảnh, thêm hook, phụ đề karaoke, giọng đọc, nhạc nền, chuyển cảnh và CTA, rồi xuất video 9:16.
- **Không có video base:** chọn một trend (Google Trends VN, hoặc trend TikTok/Facebook bạn dán vào) hoặc một sản phẩm bán chạy. AI chọn sản phẩm, viết kịch bản bắt trend và tự dựng video từ ảnh sản phẩm, video stock (Pexels) hoặc clip AI (Muse/khác, qua adapter).
- **Một click duyệt:** xem video, sửa kịch bản/caption nếu muốn, rồi bấm *Duyệt & đăng* (có thể lên lịch).
- **Đo lường:** báo cáo hoa hồng Shopee được gắn về từng video (sub_id = mã job).

## Start

```bash
scripts\start-app.cmd
```

This opens http://127.0.0.1:8765. The UI runs the background worker and the inbox watcher in the same process.
Without the UI, run `scripts\start-media-watcher.cmd` (worker only).

First-time setup is done in the **Cài đặt** page:

1. **AI key:** use the existing ChatGPT2API key (`scripts\configure-local.ps1` imports it).
2. **Shopee Affiliate App ID/Secret:** then turn on *Bật Shopee Affiliate API*.
3. **Background music:** copy licensed tracks into `<media>\assets\music` and name them by mood (`upbeat_*.mp3`, `chill_*.mp3`, …).
4. **Shopee Creator Center login:** run `scripts\open-shopee-login.cmd` and log in yourself (OTP/CAPTCHA).
5. *Optional:* Pexels API key (stock footage), an AI video generator, a Facebook Page, a TikTok token.

## Pipeline

```
video base ─┐                         ┌─ product match (fuzzy + AI visual tie-break)
            ├─ AI analysis ───────────┤
trend/offer ┘   (frames / ideation)   └─ Shopee affiliate short link (sub_id = job)
        → AI creative plan (hook formulas, 5-9 scenes, CTA) → AI self-review (score, rewrite)
        → assets (segments of base video | product image/card | Pexels | AI clips)
        → render: 9:16 blur-fill, Ken Burns/zoom, xfade transitions, Edge TTS voice,
          karaoke ASS subtitles, hook title, CTA card, ducked music, -14 LUFS
        → QA → review package (mp4, cover, caption, manifest, review.html)
        → human approval in UI → queued/scheduled upload per platform
        → conversion sync → revenue per video
```

Job states: `DISCOVERED → ANALYZING → ANALYZED → SCRIPTING → RENDERING → READY_FOR_REVIEW → APPROVED → (platform statuses)`.
States that need you are listed on the dashboard: `NEEDS_PRODUCT_REVIEW`, `WAITING_FOR_*`, `*_FAILED`, `UPLOAD_NEEDS_RECONCILIATION`.

## Repository layout

    services/agent_core/          Python package
      workflow.py                 pipeline orchestration, approval, publishing
      creative.py                 viral script prompts + validation
      editor/                     render engine (ffmpeg), TTS, subtitles, music, scene detection
      assets.py                   product images, Pexels, AI video generator adapter
      trends.py, analytics.py     trend signals, Shopee conversion sync
      affiliate_client.py         Shopee Affiliate Open API (signed GraphQL)
      shopee_video.py, meta_reels.py, tiktok.py   platform uploaders
      worker.py, db.py            SQLite task queue + background worker
      web/                        FastAPI UI (templates, static)
    tests/                        unittest/pytest suite
    scripts/                      start-app.cmd, start-media-watcher.cmd, open-shopee-login.cmd, configure-local.ps1
    config/                       .env.example, Shopee credentials template
    vendor/                       pinned sparse upstream clones (see vendor/UPSTREAMS.md)

## CLI

```bash
.venv\Scripts\python.exe -m pip install -e .[dev]
```

```bash
.venv\Scripts\affiliate-agent.exe doctor
```

Other commands: `ui`, `worker`, `scan`, `process JOB`, `process-all`, `show JOB`,
`trend-job "topic" --keywords "a,b" --run`, `render JOB`, `regenerate JOB`,
`select-product JOB ITEM --confirm`, `shopee-draft JOB --confirm`,
`facebook-publish JOB --confirm`, `tiktok-upload JOB --confirm`, `trends-fetch`, `sync-conversions`.

Tests:

```bash
.venv\Scripts\python.exe -m pytest -q
```

## Safety and policy

- Nothing is posted without your approval in the UI (or an explicit `--confirm`).
- Shopee uploads default to **draft**. Public posting (Facebook, Shopee publish, TikTok direct) also requires `AA_AUTO_PUBLISH=true`.
- TikTok defaults to **inbox** mode: the video lands in your TikTok drafts, where you add a trending sound and post.
- Trends are used as *signals* (topic, format, hook). The app never downloads or reposts other creators' videos.
- Music must be tracks you own or license. Use platform sounds inside the TikTok/Facebook apps for trending audio.
- Videos with AI-generated clips are flagged `is_aigc` on TikTok. Every caption carries an affiliate disclosure.
- Content prompts forbid invented prices, specs, claims or fake first-hand experience.
- An upload with an unknown outcome is marked for reconciliation, and blind retries are refused.
- The web UI binds to 127.0.0.1, and API calls require an anti-CSRF header.
- Secrets live only in `.env` and `config/shopee-skill/credentials.json`, are never shown back in the UI, and are git-ignored.
