"""Background worker: task queue, inbox watcher and periodic jobs in one process."""
import logging
import threading
import time
import traceback

from .db import (
    claim_task,
    enqueue_task,
    fail_task,
    finish_task,
    get_job,
    list_jobs_by_status,
    reset_stale_tasks,
    upsert_platform_post,
)

log = logging.getLogger("affiliate_agent.worker")

PERIODIC = {
    "fetch_trends": 6 * 3600,
    "sync_conversions": 12 * 3600,
}


class RetryLater(RuntimeError):
    pass


def handle_task(settings, task: dict):
    from . import workflow

    kind, job_id, payload = task["kind"], task.get("job_id"), task.get("payload") or {}
    if kind == "process":
        result = workflow.process_job(settings, job_id, force=bool(payload.get("force")))
        if result.get("status") in workflow.RETRYABLE_STATUSES:
            raise RetryLater(f"{result['status']}: {result.get('error', '')}")
        return result
    if kind == "render":
        return workflow.render_job(settings, job_id)
    if kind == "regenerate":
        return workflow.regenerate_script(settings, job_id)
    if kind == "select_product":
        return workflow.select_product_for_job(settings, job_id, payload["item_id"], confirmed=True)
    if kind == "manual_product":
        return workflow.set_manual_product(settings, job_id, payload["url"], payload.get("name", ""),
                                           payload.get("affiliate_link", ""),
                                           payload.get("image_url", ""))
    if kind == "publish":
        return workflow.publish_platform(settings, job_id, payload["platform"],
                                         payload.get("action", "draft"))
    if kind == "render_generated":
        return workflow.resume_after_generation(settings, job_id)
    if kind == "fetch_trends":
        from .trend_intel import pipeline
        return pipeline.run(settings, collect=True)
    if kind == "trend_analyse":
        from .trend_intel import pipeline
        return pipeline.run(settings, collect=False)
    if kind == "sync_conversions":
        if not settings.shopee_affiliate_enabled:
            return {"skipped": "Shopee Affiliate API disabled"}
        from .analytics import sync_conversions
        return sync_conversions(settings)
    if kind == "sync_shopee":
        from .shopee_intelligence import auto_sync
        return auto_sync.run(settings, sync_day=payload.get("sync_day"))
    if kind == "scan":
        from .media import scan_inbox
        results = scan_inbox(settings)
        queue_new_jobs(settings)
        return results
    raise ValueError(f"Unknown task kind: {kind}")


def queue_new_jobs(settings) -> int:
    if not settings.auto_process:
        return 0
    count = 0
    for job in list_jobs_by_status(settings.database_path, ["DISCOVERED"], 100):
        if enqueue_task(settings.database_path, "process", job["id"]):
            count += 1
    return count


class Worker:
    def __init__(self, settings, watch: bool = True):
        self.settings = settings
        self.watch = watch
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.observer = None
        self.current: dict | None = None
        self.last_run: dict[str, float] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        reset_stale_tasks(self.settings.database_path)
        if self.watch:
            self._start_watcher()
        self.thread = threading.Thread(target=self.run, name="affiliate-worker", daemon=True)
        self.thread.start()
        from .videogen import gateway
        if gateway.enabled(self.settings):
            # Embedded Muse gateway: start in the background so the UI comes up at once.
            threading.Thread(target=gateway.instance(self.settings).start,
                             name="muse-gateway", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        from .videogen import gateway
        gateway.instance(self.settings).stop()
        if self.observer:
            self.observer.stop()
        if self.thread:
            self.thread.join(timeout=10)

    def _start_watcher(self) -> None:
        try:
            from watchdog.observers import Observer

            from .watcher import VideoInboxHandler

            self.settings.inbox.mkdir(parents=True, exist_ok=True)
            self.observer = Observer()
            self.observer.schedule(VideoInboxHandler(self.settings, on_registered=self._registered),
                                   str(self.settings.inbox), recursive=True)
            self.observer.daemon = True
            self.observer.start()
            enqueue_task(self.settings.database_path, "scan")
            log.info("Watching inbox %s", self.settings.inbox)
        except Exception:  # noqa: BLE001
            log.exception("Inbox watcher could not start")

    def _registered(self, job_id: str, inserted: bool) -> None:
        if inserted and self.settings.auto_process:
            enqueue_task(self.settings.database_path, "process", job_id)

    # ------------------------------------------------------------ loop
    def _periodic(self) -> None:
        now = time.monotonic()
        if now - self.last_run.get("_shopee_due", -1e9) >= 60:
            self.last_run["_shopee_due"] = now
            from .shopee_intelligence import auto_sync
            auto_sync.enqueue_due(self.settings)
        self._requeue_ai_waits()
        for kind, interval in PERIODIC.items():
            # First run 10 minutes after start-up, so opening the app never queues a
            # several-minute AI refresh in front of the owner's videos.
            self.last_run.setdefault(kind, now - interval + 600)
            if now - self.last_run[kind] >= interval:
                self.last_run[kind] = now
                enqueue_task(self.settings.database_path, kind)

    AI_RETRY_EVERY = 15 * 60
    AI_RETRY_MAX = 6

    def _requeue_ai_waits(self) -> None:
        """Jobs parked on WAITING_FOR_AI_SERVICE retry on their own (15 min apart, max 6)."""
        now = time.monotonic()
        if now - self.last_run.get("_ai_retry", -1e9) < self.AI_RETRY_EVERY:
            return
        self.last_run["_ai_retry"] = now
        counts = self.last_run.setdefault("_ai_retry_counts", {})
        for job in list_jobs_by_status(self.settings.database_path,
                                       ["WAITING_FOR_AI_SERVICE"], 50):
            if counts.get(job["id"], 0) >= self.AI_RETRY_MAX:
                continue
            if enqueue_task(self.settings.database_path, "process", job["id"]):
                counts[job["id"]] = counts.get(job["id"], 0) + 1
                log.info("Auto-retry AI for job %s (%s/%s)", job["id"],
                         counts[job["id"]], self.AI_RETRY_MAX)

    def _poll_generation(self) -> None:
        """Advance AI clip tasks every ~20 s (only when some are active)."""
        now = time.monotonic()
        if now - self.last_run.get("_gen", -1e9) < 20:
            return
        self.last_run["_gen"] = now
        from .videogen import engine, gateway
        gateway.instance(self.settings).ensure_alive()
        if engine.has_active(self.settings.database_path):
            engine.poll(self.settings)

    def run(self) -> None:
        log.info("Worker started")
        try:
            from .shopee_intelligence.unified_catalog import reconcile
            reconcile(self.settings)
        except Exception:  # noqa: BLE001
            log.exception("Shopee catalog reconciliation requires attention")
        while not self.stop_event.is_set():
            try:
                self._periodic()
                self._poll_generation()
                task = claim_task(self.settings.database_path)
            except Exception:  # noqa: BLE001
                log.exception("Queue error")
                self.stop_event.wait(self.settings.worker_poll_seconds)
                continue
            if not task:
                self.stop_event.wait(self.settings.worker_poll_seconds)
                continue
            self.run_task(task)
        log.info("Worker stopped")

    def run_task(self, task: dict) -> None:
        db = self.settings.database_path
        self.current = task
        log.info("Task %s %s job=%s attempt=%s", task["id"], task["kind"], task.get("job_id"),
                 task["attempts"])
        try:
            result = handle_task(self.settings, task)
            finish_task(db, task["id"], result)
        except RetryLater as exc:
            fail_task(db, task, str(exc), retry_in=300 * task["attempts"])
        except Exception as exc:  # noqa: BLE001
            log.error("Task %s failed: %s\n%s", task["id"], exc, traceback.format_exc())
            if task["kind"] == "publish":
                payload = task.get("payload") or {}
                job = get_job(db, task.get("job_id") or "")
                if job:
                    from .db import list_platform_posts
                    post = next((p for p in list_platform_posts(db, job["id"])
                                 if p["platform"] == payload.get("platform")), None)
                    # Still QUEUED/SCHEDULED means nothing reached the platform.
                    if post and post["status"] in {"QUEUED", "SCHEDULED"}:
                        upsert_platform_post(db, job["id"], payload["platform"],
                                             "UPLOAD_FAILED_CONFIRMED",
                                             last_error=str(exc)[:1000], count_attempt=False)
                fail_task(db, task, str(exc), retry_in=None)
            else:
                fail_task(db, task, str(exc), retry_in=60 * task["attempts"])
        finally:
            self.current = None


def run_forever(settings) -> None:
    worker = Worker(settings)
    worker.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()
