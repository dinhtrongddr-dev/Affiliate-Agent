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
    "cn_trends": 3 * 3600,
    "autopilot": 3600,
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
    if kind == "long_outline":
        from . import long_video
        return long_video.outline(settings, job_id)
    if kind == "long_assemble":
        from . import long_video
        return long_video.assemble(settings, job_id)
    if kind == "affiliate_suggest":
        return workflow.suggest_affiliates(settings, job_id)
    if kind == "prepare_audio":
        return workflow.prepare_audio(settings, job_id)
    if kind == "render_generated":
        return workflow.resume_after_generation(settings, job_id)
    if kind == "cn_trends":
        from .cn_trends import pipeline as cn_pipeline
        return cn_pipeline.run(settings, collect=not payload.get("no_collect"))
    if kind == "cn_ideas":
        from .cn_trends import formula
        return {"ideas": formula.generate_ideas(settings, payload["key"])}
    if kind == "cn_idea_job":
        from .cn_trends import formula
        return {"job_id": formula.idea_to_job(settings, payload["idea_id"])}
    if kind == "autopilot":
        from .cn_trends import pipeline as cn_pipeline
        return cn_pipeline.autopilot(settings)
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


# Lanes run side by side: a long render never holds up another job's script or the
# background refreshes, and Muse clips keep moving while anything else runs.
LANES = {
    "ai": ("process", "regenerate", "select_product", "manual_product", "affiliate_suggest",
           "cn_ideas", "cn_idea_job", "long_outline"),
    "media": ("render", "render_generated", "prepare_audio", "publish", "scan", "long_assemble"),
    "background": ("fetch_trends", "trend_analyse", "sync_conversions", "sync_shopee", "cn_trends",
                   "autopilot"),
}
GEN_POLL_SECONDS = 10
LANE_IDLE_SECONDS = 15   # idle lane re-check for delayed retries (run_after in the future)


class Worker:
    def __init__(self, settings, watch: bool = True):
        self.settings = settings
        self.watch = watch
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.threads: list[threading.Thread] = []
        self.observer = None
        self.current: dict | None = None
        self.running: dict[str, dict] = {}
        self.last_run: dict[str, float] = {}

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        reset_stale_tasks(self.settings.database_path)
        if self.watch:
            self._start_watcher()
        self.thread = threading.Thread(target=self.run, name="affiliate-worker", daemon=True)
        self.thread.start()
        for lane in ("ai", "media", "background"):
            t = threading.Thread(target=self.run_lane, args=(lane,), name=f"worker-{lane}", daemon=True)
            t.start()
            self.threads.append(t)
        from .videogen import gateway
        if gateway.enabled(self.settings):
            # Embedded Muse gateway: start in the background so the UI comes up at once.
            threading.Thread(target=gateway.instance(self.settings).start,
                             name="muse-gateway", daemon=True).start()

    def stop(self) -> None:
        self.stop_event.set()
        from .db import notify_task_added
        notify_task_added()   # wake idle lanes so they see the stop
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
        self._housekeeping(now)
        if now - self.last_run.get("_long", -1e9) >= 30:
            self.last_run["_long"] = now
            self._advance_long_and_cast()
        for kind, interval in PERIODIC.items():
            # First run 10 minutes after start-up, so opening the app never queues a
            # several-minute AI refresh in front of the owner's videos.
            self.last_run.setdefault(kind, now - interval + 600)
            if now - self.last_run[kind] >= interval:
                self.last_run[kind] = now
                enqueue_task(self.settings.database_path, kind)

    def _advance_long_and_cast(self) -> None:
        """Long videos: join parts when all are done. Shared cast: wake jobs whose photos exist."""
        import json as _json

        from . import long_video, shared_cast
        try:
            long_video.check_parents(self.settings)
            for job in list_jobs_by_status(self.settings.database_path, [shared_cast.WAIT_STATUS], 50):
                source = _json.loads(job.get("brief_json") or "{}").get("shared_cast_from")
                if not source or shared_cast.ready(self.settings, source):
                    enqueue_task(self.settings.database_path, "process", job["id"])
        except Exception:  # noqa: BLE001
            log.exception("long video / shared cast check failed")

    HOUSEKEEPING_EVERY = 6 * 3600

    def _housekeeping(self, now: float) -> None:
        """Delete junk files on their own (first round 15 min after start, then every 6 h)."""
        self.last_run.setdefault("_housekeeping", now - self.HOUSEKEEPING_EVERY + 900)
        if now - self.last_run["_housekeeping"] < self.HOUSEKEEPING_EVERY:
            return
        self.last_run["_housekeeping"] = now
        import threading

        from . import housekeeping

        def job():
            try:
                housekeeping.run(self.settings)
            except Exception:  # noqa: BLE001
                log.exception("housekeeping failed")
        threading.Thread(target=job, name="housekeeping", daemon=True).start()

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
        """Advance AI clip tasks every ~10 s (only when some are active)."""
        now = time.monotonic()
        if now - self.last_run.get("_gen", -1e9) < GEN_POLL_SECONDS:
            return
        self.last_run["_gen"] = now
        from .videogen import engine, gateway
        gateway.instance(self.settings).ensure_alive()
        if engine.has_active(self.settings.database_path) or engine.has_waiting_job(self.settings.database_path):
            engine.poll(self.settings)   # poll also resumes a waiting job whose clips are all settled

    def run(self) -> None:
        log.info("Worker started")
        try:
            from .shopee_intelligence.unified_catalog import reconcile
            reconcile(self.settings)
        except Exception:  # noqa: BLE001
            log.exception("Shopee catalog reconciliation requires attention")
        # This thread only schedules and moves Muse clips along; tasks run in the lanes.
        while not self.stop_event.is_set():
            try:
                self._periodic()
                self._poll_generation()
            except Exception:  # noqa: BLE001
                log.exception("Scheduler error")
            self.stop_event.wait(2)
        log.info("Worker stopped")

    def run_lane(self, lane: str) -> None:
        from .db import task_generation, wait_for_task
        kinds = LANES[lane]
        while not self.stop_event.is_set():
            seen = task_generation()
            try:
                task = claim_task(self.settings.database_path, kinds)
            except Exception:  # noqa: BLE001
                log.exception("Queue error (%s)", lane)
                self.stop_event.wait(self.settings.worker_poll_seconds)
                continue
            if not task:
                # Woken at once by enqueue_task; the timeout only catches delayed retries.
                wait_for_task(seen, LANE_IDLE_SECONDS)
                continue
            self.run_task(task)

    def run_task(self, task: dict) -> None:
        db = self.settings.database_path
        self.current = task
        self.running[task["kind"]] = task
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
            self.running.pop(task["kind"], None)
            self.current = next(iter(self.running.values()), None)


def run_forever(settings) -> None:
    worker = Worker(settings)
    worker.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        worker.stop()
