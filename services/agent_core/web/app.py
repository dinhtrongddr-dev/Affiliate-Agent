"""Local web UI (FastAPI). Runs the background worker in the same process.

Bind to 127.0.0.1 only. Every state-changing API call must carry the header
X-Requested-With: affiliate-agent, which cross-site pages cannot send without a
CORS preflight (we never allow one), so other websites cannot trigger actions.
"""
import json
import logging
import shutil
from contextlib import asynccontextmanager
from importlib.util import find_spec
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import catalog, db, shopee_intelligence, workflow
from ..videogen import engine as gen_engine
from ..videogen.providers import ProviderError, get_provider
from ..affiliate_client import SORT_COMMISSION, SORT_ITEM_SOLD, SORT_RELEVANCE, ShopeeAffiliateClient
from ..clips import ClipError, add_clip, clip_dir, delete_clip, list_clips, update_clip
from ..editor.music import list_tracks
from ..editor.tts import VOICES
from ..env_file import update_env
from ..logging_setup import setup_logging, tail_log
from ..media import VIDEO_EXTENSIONS, scan_inbox
from ..settings import get_settings
from ..worker import Worker, queue_new_jobs

log = logging.getLogger("affiliate_agent.web")
HERE = Path(__file__).resolve().parent

STATUS_LABELS = {
    "DISCOVERED": ("Mới", "info"), "ANALYZING": ("Đang phân tích", "run"),
    "ANALYZED": ("Đã phân tích", "info"), "SCRIPTING": ("Đang viết kịch bản", "run"),
    "RENDERING": ("Đang dựng video", "run"), "READY_FOR_REVIEW": ("Chờ duyệt", "ok"),
    "APPROVED": ("Đã duyệt - chờ đăng", "ok"),
    "NEEDS_PRODUCT_REVIEW": ("Cần chọn sản phẩm", "warn"),
    "NEEDS_MEDIA_REVIEW": ("Cần kiểm tra video", "warn"),
    "WAITING_FOR_GENERATION": ("Đang tạo clip AI", "run"),
    "GENERATION_DONE": ("Clip AI xong, chờ dựng", "run"),
    "WAITING_FOR_SHOPEE_CREDENTIALS": ("Thiếu key Shopee", "warn"),
    "WAITING_FOR_AI_CREDENTIALS": ("Thiếu key AI", "warn"),
    "WAITING_FOR_AI_SERVICE": ("AI chưa sẵn sàng", "warn"),
    "WAITING_FOR_SHOPEE_API": ("Shopee API lỗi", "warn"),
    "RENDER_FAILED": ("Lỗi dựng video", "err"),
    "CONTENT_GENERATION_FAILED": ("Lỗi viết kịch bản", "err"),
    "AI_ANALYSIS_FAILED": ("Lỗi phân tích AI", "err"),
    "KEYFRAME_EXTRACTION_FAILED": ("Lỗi đọc video", "err"),
    "SOURCE_MISSING": ("Mất file gốc", "err"),
    "DRAFT_SUBMITTED_UNCONFIRMED": ("Đã gửi nháp Shopee (chưa xác nhận)", "warn"),
    "DRAFT_SAVED_CONFIRMED": ("Đã lưu nháp Shopee", "ok"),
    "PUBLISHED_CONFIRMED": ("Đã đăng", "ok"),
    "SUBMITTED_UNCONFIRMED": ("Đã gửi (chưa xác nhận)", "warn"),
    "UPLOAD_NEEDS_RECONCILIATION": ("Cần đối soát upload", "err"),
    "QUEUED": ("Trong hàng đợi", "info"), "SCHEDULED": ("Đã lên lịch", "info"),
    "RUNNING": ("Đang chạy", "run"), "DONE": ("Xong", "ok"), "FAILED": ("Lỗi", "err"),
    "UPLOAD_FAILED_CONFIRMED": ("Upload thất bại", "err"),
    "RECONCILED_POSTED": ("Đã đăng (đối soát)", "ok"),
    "TIKTOK_INBOX_CONFIRMED": ("Đã gửi vào hộp thư TikTok", "ok"),
}
PLATFORM_LABELS = {"shopee": "Shopee Video", "facebook_reels": "Facebook Reels",
                   "tiktok": "TikTok"}
BUSY = {"ANALYZING", "SCRIPTING", "RENDERING", "WAITING_FOR_GENERATION", "GENERATION_DONE"}

EDITABLE_SETTINGS = [
    # key, label, kind, group
    ("AA_MEDIA_ROOT", "Thư mục media", "text", "Chung"),
    ("AA_AUTO_PROCESS", "Tự xử lý video mới trong inbox", "bool", "Chung"),
    ("AA_AI_BASE_URL", "AI base URL", "text", "AI"),
    ("AA_AI_PROTOCOL", "AI protocol (openai | anthropic-messages)", "text", "AI"),
    ("AA_AI_MODEL", "AI model", "text", "AI"),
    ("AA_AI_THINKING_EFFORT", "Thinking effort", "text", "AI"),
    ("AA_AI_API_KEY", "AI API key", "secret", "AI"),
    ("AA_SHOPEE_AFFILIATE_ENABLED", "Bật Shopee Affiliate API", "bool", "Shopee"),
    ("AA_SHOPEE_SEED_KEYWORDS", "Từ khoá ngách (phẩy ngăn cách)", "text", "Shopee"),
    ("AA_TTS_ENABLED", "Bật giọng đọc AI", "bool", "Dựng video"),
    ("AA_TTS_MODE", "Chế độ đọc VieNeu", "tts_mode", "Dựng video"),
    ("AA_TTS_VOICE", "Giọng đọc VieNeu", "voice", "Dựng video"),
    ("AA_TTS_RATE", "Tốc độ đọc (vd +0% hoặc +8%)", "text", "Dựng video"),
    ("AA_MUSIC_VOLUME", "Âm lượng nhạc nền (0-1)", "text", "Dựng video"),
    ("AA_KEEP_SOURCE_AUDIO", "Giữ âm thanh gốc video base", "bool", "Dựng video"),
    ("AA_CTA_TEXT", "Câu CTA mặc định", "text", "Dựng video"),
    ("AA_VIRAL_REVIEW", "AI tự chấm & viết lại kịch bản yếu", "bool", "Dựng video"),
    ("AA_VIRAL_MIN_SCORE", "Điểm viral tối thiểu (0-10)", "text", "Dựng video"),
    ("AA_PEXELS_API_KEY", "Pexels API key (video stock miễn phí)", "secret", "Nguồn hình"),
    ("AA_VIDEOGEN_PROVIDER", "Nhà cung cấp video AI (none | muse2api | http)", "text", "Nguồn hình"),
    ("AA_VIDEOGEN_BASE_URL", "Video AI base URL (muse2api: http://127.0.0.1:18610)", "text", "Nguồn hình"),
    ("AA_MUSE_EMBEDDED", "Tự chạy cổng muse2api tích hợp trong app", "bool", "Nguồn hình"),
    ("AA_VIDEOGEN_MAX_CLIPS", "Số clip AI tối đa mỗi video", "text", "Nguồn hình"),
    ("AA_VIDEOGEN_DAILY_LIMIT", "Số clip + ảnh AI tối đa mỗi ngày", "text", "Nguồn hình"),
    ("AA_VIDEOGEN_CHARACTER", "Tạo ảnh nhân vật trước để các cảnh giống nhau", "bool", "Nguồn hình"),
    ("AA_VIDEOGEN_MODEL", "Video AI model", "text", "Nguồn hình"),
    ("AA_VIDEOGEN_API_KEY", "Video AI API key", "secret", "Nguồn hình"),
    ("AA_META_ENABLED", "Bật Facebook Page", "bool", "Facebook"),
    ("AA_META_GRAPH_VERSION", "Graph API version (vd v23.0)", "text", "Facebook"),
    ("AA_META_PAGE_ID", "Page ID", "text", "Facebook"),
    ("AA_META_PAGE_ACCESS_TOKEN", "Page Access Token", "secret", "Facebook"),
    ("AA_TIKTOK_ENABLED", "Bật TikTok", "bool", "TikTok"),
    ("AA_TIKTOK_MODE", "Chế độ (inbox = nháp | direct = đăng)", "text", "TikTok"),
    ("AA_TIKTOK_PRIVACY_LEVEL", "Quyền riêng tư khi direct", "text", "TikTok"),
    ("AA_TIKTOK_ACCESS_TOKEN", "TikTok access token", "secret", "TikTok"),
    ("AA_YOUTUBE_API_KEY", "YouTube Data API key (miễn phí, Google Cloud)", "secret", "Trend"),
    ("AA_YOUTUBE_DAILY_UNITS", "Quota YouTube dùng tối đa / ngày (mỗi lần tìm = 101)", "text", "Trend"),
    ("AA_TRENDSMCP_API_KEY", "Trends MCP API key (trendsmcp.ai, gói Free 100 request/tháng)", "secret", "Trend"),
    ("AA_TRENDSMCP_MONTHLY_BUDGET", "Trends MCP: số request tối đa / tháng", "text", "Trend"),
    ("AA_TREND_MIN_PURCHASE_INTENT", "Bỏ trend có ý định mua dưới (0-100)", "text", "Trend"),
    ("AA_TREND_WEIGHTS", "Trọng số điểm trend (JSON, để trống = mặc định)", "text", "Trend"),
    ("AA_AUTO_PUBLISH", "Cho phép đăng công khai (FB, Shopee publish, TikTok direct)",
     "bool", "An toàn"),
]


def label(status: str) -> tuple[str, str]:
    return STATUS_LABELS.get(status or "", (status or "-", "info"))


def create_app(start_worker: bool = True) -> FastAPI:
    state = {"settings": get_settings(), "worker": None}

    @asynccontextmanager
    async def lifespan(_app):
        settings = state["settings"]
        setup_logging(settings.logs_dir)
        db.init_db(settings.database_path)
        if start_worker:
            worker = Worker(settings)
            worker.start()
            state["worker"] = worker
        if settings.tts_enabled:
            import threading
            from ..editor.tts import warmup_engine
            threading.Thread(target=warmup_engine, args=(getattr(settings, "tts_mode", "fast"),),
                             daemon=True, name="tts-warmup").start()
        yield
        if state["worker"]:
            state["worker"].stop()

    app = FastAPI(title="Affiliate Agent", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.ctx = state
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.globals.update(label=label, platform_labels=PLATFORM_LABELS)
    templates.env.filters["fromjson"] = lambda v: json.loads(v) if v else None

    def S():
        return state["settings"]

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in {"GET", "HEAD"} and request.url.path.startswith("/api/"):
            if request.headers.get("x-requested-with") != "affiliate-agent":
                return JSONResponse({"error": "missing X-Requested-With header"}, 403)
            origin = request.headers.get("origin")
            if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
                return JSONResponse({"error": "cross-origin request refused"}, 403)
        return await call_next(request)

    @app.exception_handler(workflow.WorkflowError)
    async def workflow_error(_request, exc):
        return JSONResponse({"error": str(exc)}, 400)

    def render(request: Request, name: str, **ctx):
        ctx.setdefault("active", name.split(".")[0])
        worker = state["worker"]
        ctx["worker_task"] = worker.current if worker else None
        return templates.TemplateResponse(request, name, ctx)

    def job_or_404(job_id: str) -> dict:
        job = db.get_job(S().database_path, job_id)
        if not job:
            raise HTTPException(404, "Không tìm thấy job")
        return job

    from .addlive import install_routes as install_addlive_routes
    install_addlive_routes(app, S, render)
    from .creation import install_routes as install_creation_routes
    install_creation_routes(app, S, render)

    def health() -> list[dict]:
        s = S()
        aff = ShopeeAffiliateClient(s).credentials_status()["sections"]["shopee_affiliate"]
        music = list_tracks(s.music)
        return [
            {"name": "FFmpeg", "ok": bool(shutil.which(s.ffmpeg_path)), "hint": "Cài FFmpeg vào PATH"},
            {"name": "AI gateway", "ok": bool(s.ai_api_key.get_secret_value()),
             "hint": "Nhập AI API key trong Cài đặt"},
            {"name": "Shopee Open API", "ok": s.shopee_affiliate_enabled and aff["configured"],
             "hint": "Chưa có thì dán link affiliate tạo tay (Custom Link) ở từng video",
             "optional": True},
            {"name": "Đăng nhập Shopee Creator", "ok": (s.project_root / "profiles" / "shopee").exists(),
             "hint": "Chạy scripts/open-shopee-login.cmd"},
            {"name": (f"Nhạc nền: {len(music)} bài trong thư viện + Mixkit tự chọn theo nội dung"
                      if music else "Nhạc nền: Mixkit tự chọn theo nội dung (thư viện riêng trống)"),
             "ok": True,
             "hint": f"Tuỳ chọn: chép nhạc có bản quyền vào {s.music}"},
            {"name": "Giọng đọc AI (VieNeu)", "ok": s.tts_enabled and find_spec("vieneu") is not None,
             "hint": "Bật TTS trong Cài đặt"},
            {"name": "Video stock (Pexels)", "ok": bool(s.pexels_api_key.get_secret_value()),
             "hint": "Tuỳ chọn: thêm Pexels API key", "optional": True},
            {"name": "Video AI (Muse/khác)", "ok": s.videogen_provider in ("muse2api", "http") and bool(s.videogen_base_url),
             "hint": "Tuỳ chọn: cấu hình nhà cung cấp video AI", "optional": True},
            {"name": "Facebook Page", "ok": s.meta_enabled, "hint": "Tuỳ chọn", "optional": True},
            {"name": "TikTok", "ok": s.tiktok_enabled, "hint": "Tuỳ chọn", "optional": True},
        ]

    # ------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        s = S()
        counts = db.get_counts(s.database_path)
        groups = {
            "Đang xử lý": sum(v for k, v in counts.items() if k in BUSY | {"DISCOVERED", "ANALYZED"}),
            "Chờ duyệt": counts.get("READY_FOR_REVIEW", 0),
            "Cần xử lý tay": sum(v for k, v in counts.items() if label(k)[1] in {"warn", "err"}),
            "Đã duyệt/đăng": sum(v for k, v in counts.items() if k in {
                "APPROVED", "DRAFT_SAVED_CONFIRMED", "PUBLISHED_CONFIRMED",
                "DRAFT_SUBMITTED_UNCONFIRMED", "SUBMITTED_UNCONFIRMED"}),
        }
        return render(request, "dashboard.html", jobs=db.list_jobs(s.database_path, 12),
                      groups=groups, health=health(), inbox=str(s.inbox),
                      summary=db.conversion_summary(s.database_path)[:5])

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, status: str = ""):
        s = S()
        return render(request, "jobs.html", jobs=db.list_jobs(s.database_path, 300, status or None),
                      counts=db.get_counts(s.database_path), status=status)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str):
        from .creation import storyboard
        s = S()
        job = job_or_404(job_id)
        posts = db.list_platform_posts(s.database_path, job_id)
        blockers = {p: workflow.publish_blockers(s, job, p) for p in PLATFORM_LABELS}
        return render(request, "job.html", job=job, posts=posts, blockers=blockers,
                      analysis=json.loads(job.get("analysis_json") or "{}"),
                      script=json.loads(job.get("script_json") or "{}"),
                      content=json.loads(job.get("content_json") or "{}"),
                      candidates=json.loads(job.get("product_candidates_json") or "[]"),
                      brief=json.loads(job.get("brief_json") or "{}"),
                      busy=job["status"] in BUSY, active="jobs",
                      clips=list_clips(s, job_id), voices=VOICES,
                      link_info=workflow.link_check(s, job),
                      hook_variants=_hook_variants(s, job),
                      storyboard=storyboard(s, job_id),
                      gen_tasks=gen_engine.list_tasks(s.database_path, job_id, 50))

    @app.get("/trends", response_class=HTMLResponse)
    def trends_page(request: Request, view: str = "all"):
        from ..trend_intel import analyzer, scoring, store as tstore
        s = S()
        topics = tstore.list_topics(s.database_path, 300)
        counts = {"products": 0, "all": 0, "skipped": 0}
        shown = []
        for t in topics:
            skipped = t["safety"] == "BLOCKED" or t["status"] == "IGNORED"
            counts["skipped" if skipped else "all"] += 1
            if t["status"] == "MATCHED" and not skipped:
                counts["products"] += 1
            if (view == "products" and (skipped or t["status"] != "MATCHED")) or                (view == "all" and skipped) or (view == "skipped" and not skipped):
                continue
            t["matches"] = []
            for m in tstore.matches_for(s.database_path, t["key"])[:5]:
                product = catalog.get_product(s.database_path, m["product_id"])
                if product:
                    t["matches"].append({**m, "product": product})
            t["observations"] = tstore.observations(s.database_path, t["key"], limit=8)
            t["lifecycle_label"] = analyzer.LIFECYCLE_LABELS.get(t.get("lifecycle") or "", "")
            shown.append(t)
        usage = {"trendsmcp": tstore.usage(s.database_path, "trendsmcp"),
                 "youtube_units": tstore.usage(s.database_path, "youtube_units", "day")}
        return render(request, "trends.html", topics=shown, view=view, counts=counts,
                      labels=scoring.LABELS, usage=usage,
                      has_youtube=bool(s.youtube_api_key.get_secret_value()),
                      has_trendsmcp=bool(s.trendsmcp_api_key.get_secret_value()),
                      trends=db.list_trends(s.database_path, 150))

    @app.get("/generation", response_class=HTMLResponse)
    def generation_page(request: Request, job: str = ""):
        s = S()
        provider = get_provider(s)
        return render(request, "generation.html", tasks=gen_engine.list_tasks(s.database_path, job or None, 300),
                      provider=provider.name if provider else "", base_url=s.videogen_base_url,
                      remaining=gen_engine.remaining_today(s), daily=s.videogen_daily_limit,
                      job_filter=job, active="generation", session=_muse_summary(s),
                      gateway=_gateway_info(s), muse_profiles=_muse_profile_summaries(s))

    def _gateway_info(s):
        from ..videogen import gateway
        if not gateway.enabled(s):
            return None
        proc = gateway.instance(s)
        info = {"url": gateway.base_url(s), "running": proc.running(), "error": proc.error,
                "healthy": gateway.healthy(s, timeout=1.5)}
        if info["healthy"]:
            info.update(gateway.account_status(s))
        return info

    def _muse_summary(s):
        from ..videogen import muse_session
        try:
            return muse_session.summary(muse_session.load(s))
        except Exception:  # noqa: BLE001 - unreadable store: show as not imported
            return None

    def _muse_profile_summaries(s):
        from ..videogen import muse_profiles
        from ..secret_store import SecretStoreError
        try:
            return muse_profiles.summaries(s)
        except (SecretStoreError, ValueError):
            return []

    @app.post("/api/muse/profiles")
    async def api_muse_profiles(request: Request):
        from ..videogen import gateway, muse_profiles
        from ..secret_store import SecretStoreError
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "Không đọc được JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "Cần nội dung JSON và thông tin profile")
        text = body.get("json") or ""
        if not isinstance(text, str) or len(text) > 2_000_000:
            raise HTTPException(400, "JSON profile không hợp lệ hoặc quá lớn (tối đa 2 MB)")
        try:
            info = await run_in_threadpool(muse_profiles.save, S(), text,
                                          profile_id=body.get("profile_id") or "",
                                          label=body.get("label") or "", proxy=body.get("proxy"))
        except muse_profiles.ProfileError as exc:
            raise HTTPException(400, str(exc)) from exc
        except SecretStoreError as exc:
            raise HTTPException(400, "Không lưu được kho profile mã hoá Windows") from exc
        if gateway.enabled(S()) and await run_in_threadpool(gateway.healthy, S()):
            try:
                info["pushed"] = await run_in_threadpool(muse_profiles.push_to_gateway, S())
            except muse_profiles.ProfileError as exc:
                info["push_error"] = str(exc)
        return info

    @app.post("/api/muse/profiles/push")
    def api_muse_profiles_push():
        from ..videogen import muse_profiles
        from ..secret_store import SecretStoreError
        try:
            return muse_profiles.push_to_gateway(S())
        except muse_profiles.ProfileError as exc:
            raise HTTPException(400, str(exc)) from exc
        except SecretStoreError as exc:
            raise HTTPException(400, "Không đọc được kho profile mã hoá Windows") from exc

    @app.post("/api/muse/session")
    async def api_muse_session(request: Request):
        """Import the user's own muse.ai cookie export (JSON). Values are never returned."""
        from ..videogen import muse_session
        body = await request.json()
        text = str(body.get("json") or "")
        if len(text) > 200_000:
            raise HTTPException(400, "Nội dung quá lớn")
        try:
            info = muse_session.save(S(), text, str(body.get("label") or ""))
        except muse_session.SessionError as exc:
            raise HTTPException(400, str(exc)) from exc
        from ..videogen import gateway
        if gateway.enabled(S()) and gateway.healthy(S()):
            try:  # embedded gateway: hand the session over right away
                info["pushed"] = muse_session.push_to_gateway(S())
            except (muse_session.SessionError, httpx.HTTPError) as exc:
                info["push_error"] = str(exc)[:300]
        return info

    @app.post("/api/muse/gateway/restart")
    def api_muse_gateway_restart():
        from ..videogen import gateway
        s = S()
        if not gateway.enabled(s):
            raise HTTPException(400, "Chưa bật muse2api (Cài đặt → Nguồn hình)")
        proc = gateway.instance(s)
        proc.stop()
        if not proc.start():
            raise HTTPException(502, proc.error or "Không khởi động được gateway")
        return {"ok": True}

    @app.post("/api/muse/session/push")
    def api_muse_push():
        from ..videogen import muse_session
        try:
            return muse_session.push_to_gateway(S())
        except muse_session.SessionError as exc:
            raise HTTPException(400, str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Không kết nối được gateway: {exc}") from exc

    @app.post("/api/muse/session/delete")
    def api_muse_delete():
        from ..videogen import muse_session
        return {"deleted": muse_session.delete(S())}

    @app.post("/api/videogen/check")
    def api_videogen_check():
        provider = get_provider(S())
        if not provider:
            raise HTTPException(400, "Chưa cấu hình: đặt AA_VIDEOGEN_PROVIDER=muse2api và base URL")
        try:
            return provider.check_connection()
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/videogen/{task_id}/retry")
    def api_videogen_retry(task_id: str):
        new = gen_engine.retry_task(S(), task_id)
        if not new:
            raise HTTPException(400, "Chỉ thử lại được task lỗi/đã huỷ")
        return {"task_id": new}

    @app.post("/api/videogen/{task_id}/cancel")
    def api_videogen_cancel(task_id: str):
        if not gen_engine.cancel_task(S(), task_id):
            raise HTTPException(400, "Task không còn chạy")
        return {"ok": True}

    @app.post("/api/intel/analyse")
    def api_intel_analyse():
        return queue("trend_analyse")

    @app.post("/api/intel/manual")
    async def api_intel_manual(request: Request):
        from ..trend_intel import pipeline
        body = await request.json()
        source = "tiktok_manual" if body.get("source", "tiktok") == "tiktok" else "manual"
        count = pipeline.add_manual(S(), str(body.get("text") or ""), source)
        if not count:
            raise HTTPException(400, "Không đọc được trend nào (mỗi dòng 1 trend)")
        queue("trend_analyse")
        return {"added": count}

    @app.post("/api/intel/topic")
    async def api_intel_topic(request: Request):
        """status change / delete for one topic: {key, action: ignore|restore|delete}"""
        from ..trend_intel import store as tstore
        body = await request.json()
        s = S()
        key = str(body.get("key") or "")
        if not tstore.get_topic(s.database_path, key):
            raise HTTPException(404, "Không có trend")
        action = body.get("action")
        if action == "delete":
            tstore.delete_topic(s.database_path, key)
        elif action == "ignore":
            tstore.update_topic(s.database_path, key, status="IGNORED")
        elif action == "restore":
            tstore.update_topic(s.database_path, key, status="NEW", safety="OK",
                                safety_reason="Người dùng mở lại")
        else:
            raise HTTPException(400, "Hành động không hợp lệ")
        return {"ok": True}

    @app.post("/api/intel/create-job")
    async def api_intel_job(request: Request):
        from ..trend_intel import store as tstore
        body = await request.json()
        s = S()
        topic = tstore.get_topic(s.database_path, str(body.get("key") or ""))
        if not topic:
            raise HTTPException(404, "Không có trend")
        if topic["safety"] == "BLOCKED":
            raise HTTPException(400, "Trend nhạy cảm: không gắn sản phẩm")
        intent = topic.get("intent") or {}
        notes = " ".join(x for x in (
            f"Trend đang hot ở VN: {topic['keyword']}.",
            f"Góc nối: {intent['angle']}" if intent.get("angle") else "",
            f"Khán giả: {intent['audience']}" if intent.get("audience") else "",
            f"Bối cảnh: {topic['notes']}" if topic.get("notes") else "") if x)
        try:
            job_id = workflow.create_trend_job(s, topic["keyword"], notes,
                                               intent.get("product_keywords") or [],
                                               url=topic.get("url") or "",
                                               creation_branch="viral_creator")
        except workflow.WorkflowError as exc:
            raise HTTPException(400, str(exc)) from exc
        tstore.update_topic(s.database_path, topic["key"], status="USED", job_id=job_id)
        db.enqueue_task(s.database_path, "process", job_id)
        return {"job_id": job_id}

    @app.get("/products", response_class=HTMLResponse)
    def products_page(request: Request, q: str = "", sort: str = "sold"):
        s = S()
        offers, error = [], ""
        if q or sort != "relevance":
            sort_type = {"sold": SORT_ITEM_SOLD, "commission": SORT_COMMISSION}.get(sort, SORT_RELEVANCE)
            try:
                offers = ShopeeAffiliateClient(s).search_offers(q, limit=30, sort_type=sort_type)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
        return render(request, "products.html", offers=offers, q=q, sort=sort, error=error,
                      seeds=s.seed_keywords)

    @app.get("/catalog", response_class=HTMLResponse)
    def catalog_page(request: Request, sort: str = "score", q: str = "", status: str = "", platform: str = ""):
        s = S()
        from ..shopee_intelligence import auto_sync, unified_catalog, addlive_config
        from .. import shopee_schedule
        unified_catalog.reconcile(s)
        rows = unified_catalog.list_unified(s, q=q, sort=sort, status=status)
        if platform in {"shopee", "tiktok"}:
            rows = [p for p in rows if p.get("platform") == platform]
        return render(request, "catalog.html", products=rows, sort=sort, q=q, status=status,
                      platform=platform, sync=auto_sync.status(s), addlive=addlive_config.summary(s),
                      schedule=shopee_schedule.status(s))

    @app.post("/api/catalog/import")
    async def api_catalog_import(file: UploadFile = File(...), tiktok: str = Form("false")):
        data = await file.read()
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(400, "File quá lớn")
        try:
            products = catalog.parse_csv(data)
        except catalog.CatalogError as exc:
            raise HTTPException(400, str(exc)) from exc
        if tiktok in ("true", "1", "on"):
            for p in products:
                p["platform"] = "tiktok"
                p["tiktok_link"] = p["tiktok_link"] or p["affiliate_link"] or p["product_link"]
        result = catalog.import_products(S().database_path, products)
        from ..shopee_intelligence.unified_catalog import reconcile, unhide_product
        for p in products:
            if p['platform'] == 'shopee':
                unhide_product(S().database_path, p['item_id'])
        reconcile(S())
        queue("trend_analyse")  # re-match open trends against the new catalog
        return result

    @app.post("/api/catalog/update")
    async def api_catalog_update(request: Request):
        body = await request.json()
        s = S()
        pid = str(body.get("id") or "")
        product = catalog.get_product(s.database_path, pid)
        if not product:
            raise HTTPException(404, "Không có sản phẩm")
        fields = {k: str(body[k]).strip() for k in
                  ("image_url", "tiktok_link", "notes", "status", "affiliate_link") if k in body}
        for key in ("image_url", "tiktok_link", "affiliate_link"):
            if fields.get(key) and not fields[key].startswith("https://"):
                raise HTTPException(400, "Link phải bắt đầu bằng https://")
        catalog.update_product(s.database_path, pid, **fields)

        intel_keys = {
            "base_commission_rate", "xtra_commission_rate", "commission_total_rate",
            "rating_star", "rating_count", "total_sold", "current_price",
            "view_count", "like_count", "video_url", "media_usage_rights",
        }
        if intel_keys.intersection(body) or "image_url" in body or "affiliate_link" in body:
            if not product.get("product_link"):
                raise HTTPException(400, "Sản phẩm chưa có link sản phẩm Shopee hợp lệ để ghi snapshot.")
            existing = shopee_intelligence.get_product(s.database_path, product["item_id"]) or {}
            data = {
                "product_name": product["name"], "product_url": product["product_link"],
                "item_id": product["item_id"], "shop_name": product.get("shop_name"),
                "current_price": existing.get("current_price", product.get("price")),
                "total_sold": existing.get("total_sold", product.get("sold")),
                "rating_star": existing.get("rating_star"), "rating_count": existing.get("rating_count"),
                "view_count": existing.get("view_count"), "like_count": existing.get("like_count"),
                "base_commission_rate": existing.get("base_commission_rate"),
                "xtra_commission_rate": existing.get("xtra_commission_rate"),
                "commission_total_rate": existing.get("commission_total_rate", product.get("commission_rate")),
                "commission_amount": product.get("commission"),
                "affiliate_link": fields.get("affiliate_link", product.get("affiliate_link") or ""),
                "image_url": fields.get("image_url", product.get("image_url") or ""),
                "video_url": existing.get("video_url"),
                "media_usage_rights": existing.get("media_usage_rights") or "unknown",
            }
            for key in intel_keys:
                if key in body:
                    data[key] = body[key]
            if any(body.get(key) not in (None, "") for key in
                   ("base_commission_rate", "xtra_commission_rate")):
                base = float(data.get("base_commission_rate") or 0)
                xtra = float(data.get("xtra_commission_rate") or 0)
                data["commission_total_rate"] = base + xtra
            try:
                saved = shopee_intelligence.save_product(
                    s.database_path, data, source_type="manual_snapshot"
                )
                from ..shopee_intelligence.unified_catalog import mirror_product
                mirror_product(s.database_path, saved['product_id'])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        return {"ok": True}

    @app.post("/api/catalog/delete")
    async def api_catalog_delete(request: Request):
        body = await request.json()
        from ..shopee_intelligence.unified_catalog import hide_product
        for pid in body.get("ids") or []:
            hide_product(S().database_path, str(pid))
        return {"ok": True}

    @app.post("/api/catalog/make-videos")
    async def api_catalog_make(request: Request):
        """Create one trend job per selected product (link already known -> no Open API)."""
        body = await request.json()
        s = S()
        ids = [str(i) for i in body.get("ids") or []][:30]
        if not ids:
            raise HTTPException(400, "Chọn ít nhất 1 sản phẩm")
        created, errors = [], []
        for pid in ids:
            product = catalog.get_product(s.database_path, pid)
            if not product:
                continue
            offer = catalog.as_offer(product)
            if not offer["affiliateLink"]:
                offer["affiliateLink"] = product.get("tiktok_link") or ""
            if product.get("platform") == "shopee":
                intel = shopee_intelligence.get_product(s.database_path, product["item_id"])
                if not intel or intel.get("media_usage_rights") not in {"owned", "licensed", "permission", "official"}:
                    offer["imageUrl"] = ""
                elif intel.get("image_url"):
                    offer["imageUrl"] = intel["image_url"]
            try:
                from ..creation_context import build_product_context
                job_id = workflow.create_trend_job(s, str(body.get("topic") or product["name"]),
                                                   str(body.get("notes") or product.get("notes") or ""),
                                                   offer=offer, creation_branch="product_review",
                                                   experience=body.get("experience") or {},
                                                   product_context=build_product_context(s, product["item_id"]))
            except workflow.WorkflowError as exc:
                errors.append(f"{product['name'][:40]}: {exc}")
                continue
            catalog.update_product(s.database_path, pid, status="VIDEO_QUEUED", job_id=job_id)
            db.enqueue_task(s.database_path, "process", job_id)
            created.append(job_id)
        return {"created": created, "errors": errors}

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics_page(request: Request):
        s = S()
        return render(request, "analytics.html", summary=db.conversion_summary(s.database_path),
                      posts=db.list_platform_posts(s.database_path))

    @app.get("/tasks", response_class=HTMLResponse)
    def tasks_page(request: Request):
        return render(request, "tasks.html", tasks=db.list_tasks(S().database_path, 200))

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        from ..env_file import read_env
        s = S()
        env = read_env(s.project_root / ".env")
        fields = []
        for key, text, kind, group in EDITABLE_SETTINGS:
            attr = key[3:].lower()
            current = getattr(s, attr, "")
            if kind == "secret":
                value = bool(current.get_secret_value()) if current else False
            else:
                value = env.get(key, current if not isinstance(current, Path) else str(current))
            fields.append({"key": key, "label": text, "kind": kind, "group": group, "value": value})
        aff = ShopeeAffiliateClient(s).credentials_status()["sections"]["shopee_affiliate"]
        return render(request, "settings.html", fields=fields, voices=VOICES,
                      shopee_configured=aff["configured"], music_dir=str(s.music),
                      tracks=[t.name for t in list_tracks(s.music)], meta_app_id=s.meta_app_id)

    @app.get("/logs", response_class=HTMLResponse)
    def logs_page(request: Request):
        return render(request, "logs.html", log=tail_log(S().logs_dir, 400))

    def _hook_variants(s, job) -> list[dict]:
        out = Path(job.get("output_path") or "")
        manifest = out.parent / "manifest.json"
        if not manifest.is_file():
            return []
        try:
            rows = json.loads(manifest.read_text(encoding="utf-8")).get("hook_variants") or []
        except ValueError:
            return []
        return [{**r, "file": Path(r["path"]).name} for r in rows if Path(r.get("path", "")).is_file()]

    @app.post("/api/jobs/{job_id}/hook-variant")
    async def api_hook_variant(job_id: str, request: Request):
        job_or_404(job_id)
        body = await request.json()
        try:
            return workflow.use_hook_variant(S(), job_id, int(body.get("index") or 0))
        except (workflow.WorkflowError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/files/{job_id}/{name:path}")
    def job_file(job_id: str, name: str):
        base = (S().shopee_output / job_id).resolve()
        target = (base / name).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise HTTPException(404)
        return FileResponse(target)

    @app.get("/clips/{job_id}/{name}")
    def clip_file(job_id: str, name: str):
        try:
            base = clip_dir(S(), job_id).resolve()
        except ClipError as exc:
            raise HTTPException(404) from exc
        target = (base / name).resolve()
        if not target.is_relative_to(base) or not target.is_file() or target.suffix == ".json":
            raise HTTPException(404)
        return FileResponse(target)

    @app.get("/keyframe/{job_id}/{index}")
    def keyframe(job_id: str, index: int):
        job = job_or_404(job_id)
        frames = json.loads(job.get("keyframes_json") or "[]")
        if not 0 <= index < len(frames) or not Path(frames[index]).is_file():
            raise HTTPException(404)
        return FileResponse(frames[index])

    # ------------------------------------------------------------ API
    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        job = job_or_404(job_id)
        return {"id": job_id, "status": job["status"], "label": label(job["status"])[0],
                "busy": job["status"] in BUSY, "error": job.get("last_error"),
                "updated_at": job["updated_at"]}

    @app.get("/api/status")
    def api_status():
        worker = state["worker"]
        cur = worker.current if worker else None
        return {"worker": bool(worker), "task": cur and {"kind": cur["kind"], "job_id": cur.get("job_id")}}

    @app.post("/api/scan")
    def api_scan():
        s = S()
        results = scan_inbox(s)
        queued = queue_new_jobs(s)
        return {"found": len(results), "queued": queued}

    @app.post("/api/upload")
    async def api_upload(file: UploadFile = File(...)):
        s = S()
        name = Path(file.filename or "video.mp4").name
        if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
            raise HTTPException(400, "Định dạng video không hỗ trợ")
        s.inbox.mkdir(parents=True, exist_ok=True)
        target = s.inbox / name
        stem, n = target.stem, 1
        while target.exists():
            target = s.inbox / f"{stem}_{n}{target.suffix}"
            n += 1
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        from ..media import register_video
        inserted, info = register_video(target, s, allow_deleted=True)
        if info.get("job_id"):
            db.enqueue_task(s.database_path, "process", info["job_id"])
        return {"inserted": inserted, **info}

    def queue(kind: str, job_id: str | None = None, payload: dict | None = None):
        task_id = db.enqueue_task(S().database_path, kind, job_id, payload)
        return {"queued": bool(task_id), "task_id": task_id}

    @app.post("/api/jobs/{job_id}/process")
    def api_process(job_id: str, force: bool = False):
        job_or_404(job_id)
        return queue("process", job_id, {"force": True} if force else None)

    @app.post("/api/jobs/{job_id}/regenerate")
    def api_regenerate(job_id: str):
        job_or_404(job_id)
        return queue("regenerate", job_id)

    @app.post("/api/jobs/{job_id}/render")
    async def api_render(job_id: str, request: Request):
        job_or_404(job_id)
        body = await request.json()
        workflow.update_script(S(), job_id, body.get("script"), body.get("content"))
        return queue("render", job_id)

    @app.post("/api/jobs/{job_id}/content")
    async def api_content(job_id: str, request: Request):
        job_or_404(job_id)
        body = await request.json()
        workflow.update_script(S(), job_id, None, body.get("content"))
        return {"saved": True}

    @app.post("/api/jobs/{job_id}/select-product")
    async def api_select(job_id: str, request: Request):
        job_or_404(job_id)
        body = await request.json()
        return queue("select_product", job_id, {"item_id": str(body["item_id"])})

    @app.post("/api/jobs/{job_id}/search-products")
    async def api_search_products(job_id: str, request: Request):
        body = await request.json()
        try:
            found = workflow.search_products_for_job(S(), job_id, str(body.get("keyword") or ""))
        except workflow.WorkflowError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise workflow.WorkflowError(f"Không tìm được sản phẩm: {exc}") from exc
        return {"count": len(found)}

    @app.post("/api/jobs/{job_id}/manual-product")
    async def api_manual_product(job_id: str, request: Request):
        body = await request.json()
        url = str(body.get("url") or "").strip()
        if not url.startswith("https://") or "shopee.vn" not in url:
            raise HTTPException(400, "Dán link sản phẩm Shopee Việt Nam (https://shopee.vn/...)")
        return queue("manual_product", job_id, {
            "url": url, "name": str(body.get("name") or ""),
            "affiliate_link": str(body.get("affiliate_link") or "").strip(),
            "image_url": str(body.get("image_url") or "").strip()})

    @app.post("/api/jobs/{job_id}/approve")
    async def api_approve(job_id: str, request: Request):
        body = await request.json()
        return workflow.approve_job(S(), job_id, body.get("platforms") or {},
                                    body.get("schedule_at") or "")

    @app.post("/api/jobs/{job_id}/reconcile")
    async def api_reconcile(job_id: str, request: Request):
        body = await request.json()
        workflow.mark_post_reconciled(S(), job_id, body["platform"], bool(body.get("success")))
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/clips")
    async def api_clip_upload(job_id: str, files: list[UploadFile] = File(...),
                              ai_generated: str = Form("false"), note: str = Form("")):
        job_or_404(job_id)
        added, errors = [], []
        for upload in files:
            try:
                clip = add_clip(S(), job_id, upload.filename or "clip", upload.file,
                                ai_generated=ai_generated in ("true", "1", "on"), note=note)
                added.append(clip["id"])
            except ClipError as exc:
                errors.append(f"{upload.filename}: {exc}")
        if errors and not added:
            raise HTTPException(400, "; ".join(errors))
        return {"added": added, "errors": errors}

    @app.post("/api/jobs/{job_id}/clips/{clip_id}")
    async def api_clip_update(job_id: str, clip_id: str, request: Request):
        job_or_404(job_id)
        body = await request.json()
        if not update_clip(S(), job_id, clip_id, note=body.get("note"),
                           ai_generated=body.get("ai_generated")):
            raise HTTPException(404, "Không có clip này")
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/clips/{clip_id}/delete")
    def api_clip_delete(job_id: str, clip_id: str):
        job = job_or_404(job_id)
        script = json.loads(job.get("script_json") or "{}")
        if any(sc.get("clip_id") == clip_id for sc in script.get("scenes") or []):
            raise HTTPException(400, "Clip đang được dùng trong kịch bản: đổi cảnh đó trước")
        if not any(c["id"] == clip_id for c in list_clips(S(), job_id)):
            raise HTTPException(404, "Không có clip này")
        delete_clip(S(), job_id, clip_id)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/delete")
    def api_delete(job_id: str):
        job = job_or_404(job_id)
        if job["status"] in BUSY:
            raise HTTPException(400, "Job đang chạy, thử lại sau")
        db.delete_job(S().database_path, job_id)
        return {"deleted": True}

    @app.post("/api/trends/fetch")
    def api_trends_fetch():
        return queue("fetch_trends")

    @app.post("/api/trends")
    async def api_trend_add(request: Request):
        from ..trends import add_manual_trend
        body = await request.json()
        keywords = [k.strip() for k in str(body.get("keywords") or "").split(",") if k.strip()]
        title = str(body.get("title") or "").strip()
        if not title:
            raise HTTPException(400, "Nhập tiêu đề trend")
        try:
            add_manual_trend(S(), title, str(body.get("url") or ""),
                             str(body.get("notes") or ""), keywords,
                             source=str(body.get("source") or "manual")[:30])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

    @app.post("/api/trends/{trend_id}/create-job")
    def api_trend_job(trend_id: int):
        s = S()
        trend = db.get_trend(s.database_path, trend_id)
        if not trend:
            raise HTTPException(404)
        keywords = json.loads(trend.get("keywords_json") or "[]")
        job_id = workflow.create_trend_job(s, trend["title"], trend.get("notes") or "", keywords,
                                           trend_id=trend_id, url=trend.get("url") or "",
                                           creation_branch="viral_creator")
        db.update_trend(s.database_path, trend_id, status="USED", job_id=job_id)
        queue("process", job_id)
        return {"job_id": job_id}

    @app.post("/api/trends/{trend_id}/delete")
    def api_trend_delete(trend_id: int):
        db.delete_trend(S().database_path, trend_id)
        return {"ok": True}

    @app.post("/api/products/create-job")
    async def api_product_job(request: Request):
        body = await request.json()
        offer = body.get("offer") or {}
        if not offer.get("itemId"):
            raise HTTPException(400, "Thiếu sản phẩm")
        from ..creation_context import build_product_context
        context = build_product_context(S(), str(offer["itemId"]))
        offer["imageUrl"] = next((r["url"] for r in context["image_references"] if r["approvedForRender"]), "")
        job_id = workflow.create_trend_job(S(), str(body.get("topic") or offer.get("productName") or ""),
                                           str(body.get("notes") or ""), offer=offer,
                                           creation_branch="product_review", experience=body.get("experience") or {},
                                           product_context=context)
        queue("process", job_id)
        return {"job_id": job_id}

    @app.post("/api/analytics/sync")
    def api_sync():
        return queue("sync_conversions")

    @app.post("/api/tasks/{task_id}/retry")
    def api_retry(task_id: int):
        db.retry_task(S().database_path, task_id)
        return {"ok": True}

    @app.post("/api/settings")
    async def api_settings(request: Request):
        body = await request.json()
        allowed = {key: kind for key, _l, kind, _g in EDITABLE_SETTINGS}
        updates = {}
        for key, value in (body or {}).items():
            if key not in allowed:
                continue
            kind = allowed[key]
            if kind == "secret" and not str(value).strip():
                continue  # empty = keep existing secret
            if kind == "bool":
                value = "true" if value in (True, "true", "on", "1") else "false"
            updates[key] = str(value)
        s = S()
        update_env(s.project_root / ".env", updates)
        new_settings = get_settings()
        state["settings"] = new_settings
        if state["worker"]:
            state["worker"].settings = new_settings
        return {"saved": sorted(updates)}

    @app.post("/api/settings/facebook-token")
    async def api_facebook_token(request: Request):
        """Exchange a short-lived Explorer token for a non-expiring Page token and save it."""
        from ..meta_reels import exchange_for_page_token
        body = await request.json()
        s = S()
        app_id = str(body.get("app_id") or "").strip()
        app_secret = str(body.get("app_secret") or "").strip()
        token = str(body.get("token") or "").strip()
        if not (app_id and app_secret and token and s.meta_page_id and s.meta_graph_version):
            raise HTTPException(400, "Cần App ID, App Secret, token và Page ID/Graph version")
        try:
            page_token = exchange_for_page_token(s.meta_graph_version, app_id, app_secret,
                                                 token, s.meta_page_id)
        except Exception as exc:  # noqa: BLE001
            raise workflow.WorkflowError(f"Không đổi được token: {exc}") from exc
        update_env(s.project_root / ".env", {"AA_META_PAGE_ACCESS_TOKEN": page_token,
                                             "AA_META_APP_ID": app_id,
                                             "AA_META_ENABLED": "true"})
        state["settings"] = get_settings()
        if state["worker"]:
            state["worker"].settings = state["settings"]
        return {"saved": True}

    @app.post("/api/settings/facebook-check")
    def api_facebook_check():
        from ..meta_reels import MetaReelsPublisher
        s = S()
        try:
            page = MetaReelsPublisher(s.meta_graph_version, s.meta_page_id,
                                      s.meta_page_access_token.get_secret_value()).check_page()
        except Exception as exc:  # noqa: BLE001
            raise workflow.WorkflowError(f"Facebook chưa kết nối: {exc}") from exc
        return {"ok": True, "page": page.get("name"), "id": page.get("id")}

    @app.post("/api/settings/shopee")
    async def api_shopee_credentials(request: Request):
        body = await request.json()
        ShopeeAffiliateClient(S()).save_credentials(str(body.get("app_id") or ""),
                                                    str(body.get("secret") or ""))
        return {"saved": True}

    @app.post("/api/tts/preview")
    async def api_tts_preview(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        voice = str(body.get("voice") or "Mai Anh")
        mode = str(body.get("mode") or "fast")
        rate = str(body.get("rate") or "+0%")
        custom_text = str(body.get("text") or "").strip()
        text = custom_text or f"Chào bạn, tôi là {voice}."
        try:
            from ..editor.tts import synthesize_bytes
            data, media_type = synthesize_bytes(text, voice=voice, mode=mode, rate=rate)
            from fastapi import Response
            return Response(content=data, media_type=media_type)
        except Exception as exc:
            return JSONResponse({"error": f"Lỗi tạo giọng đọc: {exc}"}, 500)

    @app.get("/review/{job_id}")
    def review_redirect(job_id: str):
        return RedirectResponse(f"/jobs/{job_id}")

    return app


def run(host: str | None = None, port: int | None = None, open_browser: bool = True) -> None:
    from .launcher import serve

    settings = get_settings()
    host = host or settings.web_host
    port = port or settings.web_port
    serve(create_app, host, port, open_browser=open_browser)
