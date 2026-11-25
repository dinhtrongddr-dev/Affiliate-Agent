"""Automatic disk clean-up, so the owner never has to delete junk by hand.

Runs in the background every few hours (see worker.Worker._housekeeping). Only removes files
the app can always do without:
  - muse2api media copies: the gateway keeps a copy of every clip it sent us; the app has its
    own copy in cache/work/<job>/generated, so gateway copies older than MEDIA_DAYS go;
  - log files older than LOG_DAYS (a file still open is skipped);
  - cache/tmp files older than TMP_DAYS;
  - work folders of jobs that are finished (posted / draft saved) for WORK_DAYS: the final
    video lives in the outbox, cache/work only holds intermediates;
  - job folders whose job no longer exists (cleanup.orphans);
  - the database is compacted when much of it is free pages.
Never touches Muse/Chrome profiles, the outbox, the inbox or the owner's own files.
"""
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import cleanup, db

log = logging.getLogger("affiliate_agent.housekeeping")

MEDIA_DAYS = 3
LOG_DAYS = 14
TMP_DAYS = 1
WORK_DAYS = 14
VACUUM_FREE_RATIO = 0.2
FINISHED = {"PUBLISHED_CONFIRMED", "DRAFT_SAVED_CONFIRMED", "RECONCILED_POSTED", "TIKTOK_INBOX_CONFIRMED"}


def _old_files(folder: Path, days: float, pattern: str = "*") -> list[Path]:
    if not folder.is_dir():
        return []
    cutoff = time.time() - days * 86400
    out = []
    for p in folder.rglob(pattern):
        try:
            if p.is_file() and p.name != ".gitkeep" and p.stat().st_mtime < cutoff:
                out.append(p)
        except OSError:
            pass
    return out


def _unlink(files: list[Path]) -> int:
    freed = 0
    for p in files:
        try:
            size = p.stat().st_size
            p.unlink()
            freed += size
        except OSError:
            pass  # in use (open log, running gateway): try again next round
    return freed


def _finished_work(settings) -> list[Path]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WORK_DAYS)).isoformat()
    with db.connect(settings.database_path) as conn:
        rows = conn.execute(
            f"SELECT id FROM jobs WHERE status IN ({','.join('?' * len(FINISHED))}) AND updated_at < ?",
            (*FINISHED, cutoff)).fetchall()
    work = Path(settings.work_dir)
    return [work / r[0] for r in rows if cleanup.JOB_ID.fullmatch(r[0]) and (work / r[0]).is_dir()]


def _vacuum(path: Path) -> bool:
    conn = sqlite3.connect(path, timeout=30)
    try:
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        if not pages or free / pages < VACUUM_FREE_RATIO:
            return False
        conn.execute("VACUUM")
        return True
    except sqlite3.OperationalError:
        return False  # busy: next round
    finally:
        conn.close()


def run(settings) -> dict:
    root = Path(settings.project_root)
    freed = {
        "muse_media": _unlink(_old_files(root / "state" / "muse2api" / "data" / "media", MEDIA_DAYS)),
        "logs": _unlink(_old_files(root / "logs", LOG_DAYS)),
        "tmp": _unlink(_old_files(root / "cache" / "tmp", TMP_DAYS)),
        "work": 0,
    }
    try:
        for folder in _finished_work(settings) + cleanup.orphans(settings):
            freed["work"] += cleanup._safe_remove(settings, folder)
    except Exception:  # noqa: BLE001
        log.exception("housekeeping: job folders")
    freed["vacuum"] = _vacuum(Path(settings.database_path))
    total = sum(v for k, v in freed.items() if k != "vacuum")
    if total or freed["vacuum"]:
        log.info("housekeeping freed %.1f MB %s", total / 1048576, freed)
    return freed
