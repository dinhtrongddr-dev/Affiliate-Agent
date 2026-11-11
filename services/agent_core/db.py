import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_sec REAL,
    width INTEGER,
    height INTEGER,
    fps TEXT,
    ai_generated INTEGER NOT NULL DEFAULT 0,
    product_item_id TEXT,
    product_name TEXT,
    affiliate_link TEXT,
    output_path TEXT,
    facebook_output_path TEXT,
    analysis_json TEXT,
    keyframes_json TEXT,
    product_candidates_json TEXT,
    content_json TEXT,
    shopee_ready INTEGER NOT NULL DEFAULT 0,
    facebook_ready INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'PENDING',
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    external_id TEXT,
    permalink TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, platform)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_posts_status ON platform_posts(status);
"""

MIGRATIONS = {
    "analysis_json": "TEXT",
    "facebook_output_path": "TEXT",
    "keyframes_json": "TEXT",
    "product_candidates_json": "TEXT",
    "content_json": "TEXT",
    "shopee_ready": "INTEGER NOT NULL DEFAULT 0",
    "facebook_ready": "INTEGER NOT NULL DEFAULT 0",
    "review_status": "TEXT NOT NULL DEFAULT 'PENDING'",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        for name, declaration in MIGRATIONS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")


def register_job(db_path: Path, job: dict[str, Any]) -> bool:
    init_db(db_path)
    stamp = now_iso()
    with connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO jobs
            (id, source_path, source_sha256, file_name, status,
             duration_sec, width, height, fps, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'DISCOVERED', ?, ?, ?, ?, ?, ?)
            """,
            (
                job["id"], job["source_path"], job["sha256"], job["file_name"],
                job.get("duration_sec"), job.get("width"), job.get("height"),
                job.get("fps"), stamp, stamp,
            ),
        )
        return cur.rowcount == 1


def update_job(db_path: Path, job_id: str, **fields: Any) -> None:
    allowed = {
        "status", "ai_generated", "product_item_id", "product_name",
        "affiliate_link", "output_path", "facebook_output_path", "analysis_json", "keyframes_json",
        "product_candidates_json", "content_json", "shopee_ready",
        "facebook_ready", "review_status", "last_error",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = now_iso()
    clause = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [job_id]
    with connect(db_path) as conn:
        conn.execute(f"UPDATE jobs SET {clause} WHERE id=?", values)


def get_job(db_path: Path, job_id: str) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def get_counts(db_path: Path) -> dict[str, int]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}


def list_jobs(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    init_db(db_path)
    limit = max(1, min(int(limit), 500))
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def list_jobs_by_status(
    db_path: Path, statuses: list[str], limit: int = 50
) -> list[dict[str, Any]]:
    if not statuses:
        return []
    init_db(db_path)
    limit = max(1, min(int(limit), 500))
    placeholders = ",".join("?" for _ in statuses)
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({placeholders}) "
            "ORDER BY created_at ASC LIMIT ?",
            [*statuses, limit],
        ).fetchall()
        return [dict(row) for row in rows]


def upsert_platform_post(
    db_path: Path,
    job_id: str,
    platform: str,
    status: str,
    external_id: str | None = None,
    permalink: str | None = None,
    last_error: str | None = None,
) -> None:
    stamp = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO platform_posts
                (job_id, platform, status, external_id, permalink,
                 attempts, last_error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(job_id, platform) DO UPDATE SET
                status=excluded.status,
                external_id=COALESCE(excluded.external_id, platform_posts.external_id),
                permalink=COALESCE(excluded.permalink, platform_posts.permalink),
                attempts=platform_posts.attempts + 1,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
            """,
            (job_id, platform, status, external_id, permalink, last_error, stamp, stamp),
        )


def list_platform_posts(db_path: Path, job_id: str) -> list[dict[str, Any]]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM platform_posts WHERE job_id=? ORDER BY platform",
            (job_id,),
        ).fetchall()
        return [dict(row) for row in rows]
