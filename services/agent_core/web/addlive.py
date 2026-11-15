"""Server-side AddLiveTag routes; secrets never enter the rendered page."""
from fastapi import File, HTTPException, Request, UploadFile
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import secret_store, shopee_intelligence, workflow, shopee_schedule
from ..shopee_intelligence import addlive_config as config
from ..shopee_intelligence import addlive_service as service
from ..shopee_intelligence.addlive_client import AddLiveClient, AddLiveError


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = Field(default="", max_length=512)
    base_rate: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    cap: int = Field(default=40000, ge=0, le=1_000_000_000, strict=True)
    affiliate_id: str = Field(default="", max_length=30)
    sub_id: str = Field(default="", max_length=200)


class Lookup(BaseModel):
    references: list[str] = Field(min_length=1, max_length=100)
    cache_only: bool = False
    max_api: int = Field(default=10, ge=0, le=20, strict=True)


class Search(BaseModel):
    kind: str = "products"
    keyword: str = Field(default="", max_length=200)
    page: int = Field(default=1, ge=1, le=1000, strict=True)
    limit: int = Field(default=20, ge=1, le=50, strict=True)
    sort_type: int = Field(default=1, ge=1, le=20, strict=True)
    shop_id: str = Field(default="", max_length=30)


class History(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=50)
    days: int = Field(default=90, ge=1, le=730, strict=True)


class Automation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    local_time: str = Field(default="07:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    max_api: int = Field(default=10, ge=1, le=20, strict=True)
    discovery_limit: int = Field(default=200, ge=1, le=1000, strict=True)
    max_items: int = Field(default=300, ge=1, le=1000, strict=True)


def install_routes(app, settings, render):
    def connection():
        values = config.load(settings())
        if not values["api_key"]:
            raise HTTPException(400, "Lưu API key dữ liệu Sàn Cam trước khi tra cứu")
        # The UI always uses percentage points; the upstream accepts fractions too.
        if values["base_rate"] is not None:
            values["base_rate"] /= 100
        return AddLiveClient(values["api_key"]), values

    @app.exception_handler(AddLiveError)
    async def addlive_error(_request, exc):
        retry = getattr(exc, "retry_after", 0)
        return JSONResponse({"error": str(exc), "retry_after": retry}, 429 if retry else 400)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path.startswith("/api/intelligence/addlive/"):
            # FastAPI's default validation response includes the rejected input.
            return JSONResponse({"error": "Thông tin tra cứu hoặc kết nối không hợp lệ"}, 422)
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(secret_store.SecretStoreError)
    async def secret_error(_request, _exc):
        return JSONResponse({"error": "Không đọc hoặc lưu được kho bí mật Windows"}, 400)

    @app.get("/intelligence")
    def page(request: Request):
        query = request.url.query
        return RedirectResponse("/catalog" + ("?" + query if query else ""), status_code=307)

    @app.get("/shopee")
    def shopee_alias(request: Request):
        query = request.url.query
        return RedirectResponse("/catalog" + ("?" + query if query else ""), status_code=307)

    @app.post("/api/intelligence/addlive/config")
    def save_connection(body: Connection):
        try:
            s = settings()
            result = config.save(s, body.model_dump())
            from ..shopee_intelligence.unified_catalog import populate_owner_links
            result['links_added'] = populate_owner_links(s.database_path, result['affiliate_id'], result['sub_id'])
            from ..shopee_intelligence import auto_sync
            cfg = auto_sync.config(s)
            try:
                result["schedule"] = shopee_schedule.install(s, enabled=cfg["enabled"], local_time=cfg["local_time"])
            except shopee_schedule.ScheduleError as sched_err:
                result["schedule"] = {"installed": False, "managed": False, "enabled": False, "error": str(sched_err)}
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/shopee/sync/status")
    def sync_status():
        from ..shopee_intelligence import auto_sync
        s = settings()
        return {**auto_sync.status(s), "schedule": shopee_schedule.status(s)}

    @app.post("/api/shopee/sync/config")
    def save_automation(body: Automation):
        from ..shopee_intelligence import auto_sync
        s = settings()
        try:
            result = auto_sync.save_config(s, body.model_dump())
            scheduled = shopee_schedule.install(s, enabled=result["enabled"], local_time=result["local_time"])
            return {"config": result, "schedule": scheduled}
        except (ValueError, shopee_schedule.ScheduleError) as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/shopee/sync")
    def sync_now():
        from ..shopee_intelligence import auto_sync
        s = settings()
        if not auto_sync.config(s)["configured"]:
            raise HTTPException(400, "Lưu API key trong Cài đặt nâng cao trước khi đồng bộ")
        task_id = auto_sync.enqueue_due(s, force=True)
        return {"task_id": task_id, "queued": task_id is not None,
                "message": "Đã đưa vào hàng đợi" if task_id is not None else "Hôm nay đã có lượt đồng bộ; xem trạng thái bên dưới"}

    @app.post("/api/intelligence/addlive/lookup")
    def lookup(body: Lookup):
        client, values = connection()
        options = {k: values[k] for k in ("base_rate", "cap", "affiliate_id", "sub_id")}
        # A short URL is supported by the single-product endpoint only.
        if len(body.references) == 1 and not body.cache_only and body.max_api:
            result = service.import_product(settings(), client, body.references[0], **options)
        else:
            result = service.import_batch(settings(), client, body.references, cache_only=body.cache_only,
                                          max_api=body.max_api, **options)
        from ..shopee_intelligence.unified_catalog import reconcile, unhide_product
        for row in result.get('results', []):
            if row.get('imported'):
                unhide_product(settings().database_path, row['itemId'])
        reconcile(settings())
        return result

    @app.post("/api/intelligence/addlive/offers")
    def offers(body: Search):
        client, _ = connection()
        return client.offers(**body.model_dump())

    @app.post("/api/intelligence/addlive/history")
    def history(body: History):
        client, values = connection()
        return service.retrieve_history(client, body.item_ids, days=body.days,
                                        base_rate=values["base_rate"], cap=values["cap"])

    # Activate the existing manual/CSV controls alongside the new API source.
    @app.get("/intelligence/template")
    def csv_template():
        return Response(shopee_intelligence.template_csv(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="shopee-products.csv"'})

    @app.post("/api/intelligence/import-csv")
    async def import_csv(file: UploadFile = File(...)):
        from fastapi.concurrency import run_in_threadpool
        content = await file.read(20 * 1024 * 1024 + 1)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(400, "File quá lớn")
        if not content.strip():
            raise HTTPException(400, "File CSV trống")
        def ingest():
            from ..shopee_intelligence.unified_catalog import reconcile
            s = settings()
            result = shopee_intelligence.import_products_csv(s.database_path, content, file.filename or "products.csv")
            reconcile(s)
            return result
        try:
            result = await run_in_threadpool(ingest)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if not (result or {}).get("imported") and not (result or {}).get("updated")                 and (result or {}).get("errors"):
            raise HTTPException(400, "Không nhập được dòng nào: " + "; ".join(
                str(e) for e in result["errors"][:3]))
        return result

    @app.post("/api/intelligence/products")
    def save_product(body: dict):
        try:
            from ..shopee_intelligence.unified_catalog import mirror_product, unhide_product
            s = settings()
            result = shopee_intelligence.save_product(s.database_path, body)
            unhide_product(s.database_path, result['item_id'])
            mirror_product(s.database_path, result["product_id"])
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/intelligence/products/{product_id}/create-job")
    def create_job(product_id: str, body: dict):
        from .. import db
        s = settings()
        product = shopee_intelligence.get_product(s.database_path, product_id)
        if not product:
            raise HTTPException(404, "Không có sản phẩm")
        offer = shopee_intelligence.product_to_offer(product)
        offer["affiliateLink"] = product["affiliate_link"]
        from ..creation_context import build_product_context
        job_id = workflow.create_trend_job(s, product["product_name"], str(body.get("notes") or ""), offer=offer,
                                           creation_branch="product_review", experience=body.get("experience") or {},
                                           product_context=build_product_context(s, product["item_id"]))
        db.enqueue_task(s.database_path, "process", job_id)
        return {"job_id": job_id}
