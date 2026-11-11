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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import db, workflow
from ..affiliate_client import SORT_COMMISSION, SORT_ITEM_SOLD, SORT_RELEVANCE, ShopeeAffiliateClient
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
BUSY = {"ANALYZING", "SCRIPTING", "RENDERING"}

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
    ("AA_TTS_VOICE", "Giọng đọc", "voice", "Dựng video"),
    ("AA_TTS_RATE", "Tốc độ đọc (vd +8%)", "text", "Dựng video"),
    ("AA_MUSIC_VOLUME", "Âm lượng nhạc nền (0-1)", "text", "Dựng video"),
    ("AA_KEEP_SOURCE_AUDIO", "Giữ âm thanh gốc video base", "bool", "Dựng video"),
    ("AA_CTA_TEXT", "Câu CTA mặc định", "text", "Dựng video"),
    ("AA_VIRAL_REVIEW", "AI tự chấm & viết lại kịch bản yếu", "bool", "Dựng video"),
    ("AA_VIRAL_MIN_SCORE", "Điểm viral tối thiểu (0-10)", "text", "Dựng video"),
    ("AA_PEXELS_API_KEY", "Pexels API key (video stock miễn phí)", "secret", "Nguồn hình"),
    ("AA_VIDEOGEN_PROVIDER", "Nhà cung cấp tạo video AI (none | http)", "text", "Nguồn hình"),
    ("AA_VIDEOGEN_BASE_URL", "Video AI base URL (vd Muse)", "text", "Nguồn hình"),
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
            {"name": f"Nhạc nền ({len(music)} bài)", "ok": bool(music),
             "hint": f"Chép nhạc bản quyền vào {s.music}"},
            {"name": "Giọng đọc AI", "ok": s.tts_enabled and find_spec("edge_tts") is not None,
             "hint": "Bật TTS trong Cài đặt"},
            {"name": "Video stock (Pexels)", "ok": bool(s.pexels_api_key.get_secret_value()),
             "hint": "Tuỳ chọn: thêm Pexels API key", "optional": True},
            {"name": "Video AI (Muse/khác)", "ok": s.videogen_provider == "http" and bool(s.videogen_base_url),
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
                      busy=job["status"] in BUSY, active="jobs")

    @app.get("/trends", response_class=HTMLResponse)
    def trends_page(request: Request):
        return render(request, "trends.html", trends=db.list_trends(S().database_path, 150))

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
                      tracks=[t.name for t in list_tracks(s.music)])

    @app.get("/logs", response_class=HTMLResponse)
    def logs_page(request: Request):
        return render(request, "logs.html", log=tail_log(S().logs_dir, 400))

    @app.get("/files/{job_id}/{name:path}")
    def job_file(job_id: str, name: str):
        base = (S().shopee_output / job_id).resolve()
        target = (base / name).resolve()
        if not target.is_relative_to(base) or not target.is_file():
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
        inserted, info = register_video(target, s)
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
        add_manual_trend(S(), str(body.get("title") or ""), str(body.get("url") or ""),
                         str(body.get("notes") or ""), keywords,
                         source=str(body.get("source") or "manual")[:30])
        return {"ok": True}

    @app.post("/api/trends/{trend_id}/create-job")
    def api_trend_job(trend_id: int):
        s = S()
        trend = db.get_trend(s.database_path, trend_id)
        if not trend:
            raise HTTPException(404)
        keywords = json.loads(trend.get("keywords_json") or "[]")
        job_id = workflow.create_trend_job(s, trend["title"], trend.get("notes") or "", keywords,
                                           trend_id=trend_id, url=trend.get("url") or "")
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
        job_id = workflow.create_trend_job(S(), str(body.get("topic") or offer.get("productName") or ""),
                                           str(body.get("notes") or ""), offer=offer)
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

    @app.post("/api/settings/shopee")
    async def api_shopee_credentials(request: Request):
        body = await request.json()
        ShopeeAffiliateClient(S()).save_credentials(str(body.get("app_id") or ""),
                                                    str(body.get("secret") or ""))
        return {"saved": True}

    @app.get("/review/{job_id}")
    def review_redirect(job_id: str):
        return RedirectResponse(f"/jobs/{job_id}")

    return app


def run(host: str | None = None, port: int | None = None, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    import uvicorn

    settings = get_settings()
    host = host or settings.web_host
    port = port or settings.web_port
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
