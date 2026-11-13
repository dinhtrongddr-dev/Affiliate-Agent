import argparse
from importlib.util import find_spec
import json
import shutil
import sys

from .affiliate_client import ShopeeAffiliateClient
from .db import (
    get_counts,
    get_job,
    init_db,
    list_jobs,
    list_jobs_by_status,
    list_platform_posts,
)
from .media import scan_inbox
from .settings import get_settings
from .watcher import watch_inbox
from .logging_setup import setup_logging
from .workflow import (
    PENDING_STATUSES,
    create_trend_job,
    process_job,
    publish_facebook_reel_job,
    publish_tiktok_job,
    regenerate_script,
    render_job,
    select_product_for_job,
    upload_shopee_draft,
)


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def doctor(settings) -> int:
    checks = {
        "python": sys.version.split()[0],
        "ffmpeg": shutil.which(settings.ffmpeg_path),
        "ffprobe": shutil.which(settings.ffprobe_path),
        "inbox": str(settings.inbox),
        "inbox_exists": settings.inbox.exists(),
        "database": str(settings.database_path),
        "ai_protocol": settings.ai_protocol,
        "ai_base_url": settings.ai_base_url,
        "ai_key_configured": bool(settings.ai_api_key.get_secret_value()),
        "shopee_affiliate_enabled": settings.shopee_affiliate_enabled,
        "shopee_skill_exists": (
            settings.project_root / "vendor" / "shopee-skill"
            / "scripts" / "shopee_affiliate.py"
        ).exists(),
        "luno_shopee_uploader_exists": (
            settings.project_root / "vendor" / "luno-automation"
            / "backend" / "shopee_uploader.py"
        ).exists(),
        "playwright_installed": find_spec("playwright") is not None,
        "meta_enabled": settings.meta_enabled,
        "auto_publish": settings.auto_publish,
    }
    init_db(settings.database_path)
    _print(checks)
    return 0 if checks["ffmpeg"] and checks["ffprobe"] else 2


def credentials(settings) -> int:
    result = {
        "ai": {
            "configured": bool(settings.ai_api_key.get_secret_value()),
            "protocol": settings.ai_protocol,
            "base_url": settings.ai_base_url,
            "model": settings.ai_model,
        },
        "shopee_affiliate": {
            "enabled": settings.shopee_affiliate_enabled,
            "configured": False,
        },
        "meta": {
            "enabled": settings.meta_enabled,
            "configured": bool(
                settings.meta_graph_version
                and settings.meta_page_id
                and settings.meta_page_access_token.get_secret_value()
            ),
        },
        "auto_publish": settings.auto_publish,
    }
    try:
        check = ShopeeAffiliateClient(settings).credentials_status()
        result["shopee_affiliate"]["configured"] = (
            check.get("sections", {})
            .get("shopee_affiliate", {})
            .get("configured", False)
        )
        result["shopee_affiliate"]["config_file_exists"] = check.get("exists", False)
    except Exception as exc:
        result["shopee_affiliate"]["check_error"] = str(exc)
    _print(result)
    return 0


def show_job(settings, job_id: str) -> int:
    job = get_job(settings.database_path, job_id)
    if not job:
        _print({"error": "job_not_found", "job_id": job_id})
        return 2
    for field in (
        "analysis_json", "keyframes_json", "product_candidates_json", "content_json"
    ):
        raw = job.get(field)
        try:
            job[field.removesuffix("_json")] = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            job[field.removesuffix("_json")] = raw
        job.pop(field, None)
    _print({
        "job": job,
        "platform_posts": list_platform_posts(settings.database_path, job_id),
    })
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="affiliate-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    ui = sub.add_parser("ui", help="open the web interface (also runs the background worker)")
    ui.add_argument("--no-browser", action="store_true")
    ui.add_argument("--port", type=int)
    sub.add_parser("worker", help="run the background worker + inbox watcher without UI")

    trend = sub.add_parser("trend-job", help="create a video from a trend topic (no base video)")
    trend.add_argument("topic")
    trend.add_argument("--notes", default="")
    trend.add_argument("--keywords", default="", help="comma separated Shopee keywords")
    trend.add_argument("--run", action="store_true", help="process immediately")

    render = sub.add_parser("render", help="re-render a job from its stored script")
    render.add_argument("job_id")
    regen = sub.add_parser("regenerate", help="ask AI for a new script and render")
    regen.add_argument("job_id")
    sub.add_parser("trends-fetch", help="fetch Google Trends Vietnam")
    sub.add_parser("sync-conversions", help="pull Shopee conversion report (30 days)")
    sub.add_parser("sync-shopee", help="sync Shopee product data only, without starting video workers")

    tiktok = sub.add_parser("tiktok-upload", help="send a reviewed video to TikTok")
    tiktok.add_argument("job_id")
    tiktok.add_argument("--confirm", action="store_true")

    sub.add_parser("doctor")
    sub.add_parser("credentials")
    sub.add_parser("scan")
    sub.add_parser("watch")
    sub.add_parser("shopee-check")

    status = sub.add_parser("status")
    status.add_argument("--limit", type=int, default=20)

    process = sub.add_parser("process", help="analyze one registered video and prepare review package")
    process.add_argument("job_id")

    process_all = sub.add_parser("process-all", help="process pending video jobs")
    process_all.add_argument("--limit", type=int, default=10)

    show = sub.add_parser("show", help="show a job, analysis, content and upload history")
    show.add_argument("job_id")

    select_product = sub.add_parser("select-product", help="choose one candidate after manual review")
    select_product.add_argument("job_id")
    select_product.add_argument("item_id")
    select_product.add_argument("--confirm", action="store_true", help="confirm the selected affiliate product")

    draft = sub.add_parser("shopee-draft", help="upload a reviewed job to Shopee Creator Center as a draft")
    draft.add_argument("job_id")
    draft.add_argument("--confirm", action="store_true", help="confirm the non-public draft upload")

    facebook = sub.add_parser("facebook-publish", help="publish a reviewed Reel to the configured Facebook Page")
    facebook.add_argument("job_id")
    facebook.add_argument("--confirm", action="store_true", help="confirm the public Facebook Reel upload")

    args = parser.parse_args()
    settings = get_settings()
    setup_logging(settings.logs_dir)

    if args.command == "ui":
        from .web.app import run
        run(port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "worker":
        from .worker import run_forever
        run_forever(settings)
        return 0
    if args.command == "trend-job":
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
        job_id = create_trend_job(settings, args.topic, args.notes, keywords)
        _print(process_job(settings, job_id) if args.run else {"job_id": job_id})
        return 0
    if args.command == "render":
        _print(render_job(settings, args.job_id))
        return 0
    if args.command == "regenerate":
        _print(regenerate_script(settings, args.job_id))
        return 0
    if args.command == "trends-fetch":
        from .trends import fetch_google_trends
        _print({"google_trends": fetch_google_trends(settings)})
        return 0
    if args.command == "sync-conversions":
        from .analytics import sync_conversions
        _print(sync_conversions(settings))
        return 0
    if args.command == "sync-shopee":
        from .shopee_intelligence import auto_sync
        from .shopee_intelligence.unified_catalog import reconcile
        reconcile(settings)
        result = auto_sync.run(settings)
        _print(result)
        return 1 if result.get("status") in {"failed", "partial", "blocked"} else 0
    if args.command == "tiktok-upload":
        if not args.confirm:
            _print({"status": "NOT_STARTED", "message": "Repeat with --confirm to upload."})
            return 2
        _print(publish_tiktok_job(settings, args.job_id, confirmed=True))
        return 0

    if args.command == "doctor":
        return doctor(settings)
    if args.command == "credentials":
        return credentials(settings)
    if args.command == "scan":
        _print(scan_inbox(settings))
        return 0
    if args.command == "watch":
        _print({"startup_scan": scan_inbox(settings)})
        watch_inbox(settings)
        return 0
    if args.command == "shopee-check":
        from .shopee_video import check_shopee_integration
        result = check_shopee_integration(settings)
        _print(result)
        return 0 if result["ready"] else 2
    if args.command == "status":
        _print({
            "counts": get_counts(settings.database_path),
            "jobs": list_jobs(settings.database_path, args.limit),
        })
        return 0
    if args.command == "show":
        return show_job(settings, args.job_id)
    if args.command == "process":
        _print(process_job(settings, args.job_id))
        return 0
    if args.command == "process-all":
        pending = list_jobs_by_status(settings.database_path, PENDING_STATUSES, args.limit)
        results = [process_job(settings, job["id"]) for job in pending]
        _print({"processed": len(results), "results": results})
        return 0
    if args.command == "select-product":
        if not args.confirm:
            _print({
                "status": "NOT_STARTED",
                "message": "No product was selected. Review the candidate, then repeat with --confirm.",
                "job_id": args.job_id,
                "item_id": args.item_id,
            })
            return 2
        _print(select_product_for_job(
            settings, args.job_id, args.item_id, confirmed=True
        ))
        return 0
    if args.command == "facebook-publish":
        if not args.confirm:
            _print({
                "status": "NOT_STARTED",
                "message": "No Facebook upload was performed. Review the job, then repeat with --confirm.",
                "job_id": args.job_id,
            })
            return 2
        _print(publish_facebook_reel_job(
            settings, args.job_id, confirmed=True
        ))
        return 0
    if args.command == "shopee-draft":
        if not args.confirm:
            _print({
                "status": "NOT_STARTED",
                "message": "No upload was performed. Review the job, then repeat with --confirm.",
                "job_id": args.job_id,
            })
            return 2
        _print(upload_shopee_draft(settings, args.job_id, confirmed=True))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
