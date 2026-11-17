"""Delete jobs for good: the database rows and every file the app made for them.

A job owns one folder in each of: cache/work/<job>, <assets>/clips/<job>,
<outbox>/shopee/<job>, <outbox>/facebook/<job>. Those folders are removed whole. The owner's own
source video (base mode, in the inbox) is never touched. Paths are checked to sit inside those
roots before anything is deleted.
"""
import logging
import re
import shutil
from pathlib import Path

from . import db

log = logging.getLogger("affiliate_agent.cleanup")

JOB_ID = re.compile(r"[A-Za-z0-9_-]{4,64}")
BUSY = {"ANALYZING", "SCRIPTING", "RENDERING", "WAITING_FOR_GENERATION", "GENERATION_DONE",
        "WAITING_FOR_CAST", "WAITING_FOR_SEGMENTS", "APPROVED"}
DONE = {"READY_FOR_REVIEW", "PUBLISHED_CONFIRMED", "DRAFT_SAVED_CONFIRMED", "RECONCILED_POSTED",
        "TIKTOK_INBOX_CONFIRMED", "DRAFT_SUBMITTED_UNCONFIRMED", "SUBMITTED_UNCONFIRMED"}
GROUPS = {
    "failed": "Lỗi / cần xử lý tay",
    "done": "Đã xong (chờ duyệt, đã đăng)",
    "unfinished": "Chưa xong (dừng giữa chừng)",
    "orphans": "Rác mồ côi (file không còn job nào dùng)",
}


def roots(settings) -> list[Path]:
    return [Path(settings.work_dir), Path(settings.assets) / "clips",
            Path(settings.shopee_output), Path(settings.facebook_output)]


def group_of(status: str) -> str | None:
    if status in BUSY:
        return None                 # never while it runs or waits to be posted
    if status in DONE:
        return "done"
    if status.endswith("FAILED") or status in {"SOURCE_MISSING", "UPLOAD_NEEDS_RECONCILIATION"}:
        return "failed"
    if status.startswith(("NEEDS_", "WAITING_FOR_")):
        return "failed"
    return "unfinished"


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def job_folders(settings, job_id: str) -> list[Path]:
    if not JOB_ID.fullmatch(job_id or ""):
        return []
    return [r / job_id for r in roots(settings) if (r / job_id).exists()]


def _safe_remove(settings, path: Path) -> int:
    path = Path(path)
    resolved = path.resolve()
    if not any(resolved.is_relative_to(r.resolve()) and resolved != r.resolve() for r in roots(settings)):
        log.warning("cleanup refused a path outside the app folders: %s", path)
        return 0
    size = _size(path)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink(missing_ok=True)
    return size


def purge_job(settings, job_id: str, force: bool = False) -> dict:
    """Remove one job, its tasks, its AI clip tasks and all its folders. Returns bytes freed."""
    job = db.get_job(settings.database_path, job_id)
    if job and not force and group_of(job["status"]) is None:
        raise ValueError("Job đang chạy hoặc chờ đăng, chưa xoá được")
    freed = sum(_safe_remove(settings, p) for p in job_folders(settings, job_id))
    if job:
        db.delete_job(settings.database_path, job_id)
    with db.connect(settings.database_path) as conn:
        conn.execute("DELETE FROM tasks WHERE job_id=? AND status!='RUNNING'", (job_id,))
        try:
            conn.execute("DELETE FROM gen_tasks WHERE job_id=?", (job_id,))
        except Exception:  # noqa: BLE001 - table not created yet
            pass
    log.info("purged job %s (%.1f MB)", job_id, freed / 1048576)
    return {"job_id": job_id, "bytes": freed}


def orphans(settings) -> list[Path]:
    """Job folders whose job no longer exists."""
    with db.connect(settings.database_path) as conn:
        known = {r[0] for r in conn.execute("SELECT id FROM jobs")}
    out = []
    for r in roots(settings):
        if r.is_dir():
            out += [p for p in r.iterdir() if p.is_dir() and JOB_ID.fullmatch(p.name) and p.name not in known]
    return out


def preview(settings) -> dict:
    """How many jobs and how much disk each group would free (shown before deleting)."""
    db.init_db(settings.database_path)
    out = {g: {"label": label, "jobs": 0, "bytes": 0} for g, label in GROUPS.items()}
    with db.connect(settings.database_path) as conn:
        jobs = [dict(r) for r in conn.execute("SELECT id, status FROM jobs")]
    for job in jobs:
        g = group_of(job["status"])
        if g:
            out[g]["jobs"] += 1
            out[g]["bytes"] += sum(_size(p) for p in job_folders(settings, job["id"]))
    lost = orphans(settings)
    out["orphans"]["jobs"] = len(lost)
    out["orphans"]["bytes"] = sum(_size(p) for p in lost)
    return out


def purge_groups(settings, groups: list[str]) -> dict:
    groups = [g for g in groups if g in GROUPS]
    freed, removed = 0, 0
    if any(g != "orphans" for g in groups):
        with db.connect(settings.database_path) as conn:
            jobs = [dict(r) for r in conn.execute("SELECT id, status FROM jobs")]
        for job in jobs:
            if group_of(job["status"]) in groups:
                freed += purge_job(settings, job["id"])["bytes"]
                removed += 1
    if "orphans" in groups:
        for p in orphans(settings):
            freed += _safe_remove(settings, p)
    return {"jobs": removed, "bytes": freed}
