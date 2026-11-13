"""Windows scheduled entry point: product data only; no UI/video/publishing worker."""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "services"))
    from agent_core.logging_setup import setup_logging
    from agent_core.settings import get_settings
    from agent_core.shopee_intelligence import auto_sync
    from agent_core.shopee_intelligence.unified_catalog import reconcile

    settings = get_settings()
    setup_logging(settings.logs_dir)
    logger = logging.getLogger("affiliate_agent.shopee_schedule")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(settings.logs_dir / "shopee-schedule.log", maxBytes=512000,
                                  backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    try:
        reconcile(settings)
        result = auto_sync.run(settings)
        logger.info("Product sync status=%s imported=%s", result.get("status"), result.get("imported"))
        return 1 if result.get("status") in {"failed", "partial", "blocked"} else 0
    except Exception as exc:  # noqa: BLE001
        # Unexpected bodies/configuration must not disclose credentials to scheduler logs.
        logger.error("Product sync failed (%s); inspect product sync status in the app", type(exc).__name__)
        return 1
    finally:
        logger.removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
