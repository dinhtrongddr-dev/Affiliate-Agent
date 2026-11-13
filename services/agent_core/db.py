import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    job_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'QUEUED',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    run_after TEXT NOT NULL,
    last_error TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    traffic TEXT,
    notes TEXT,
    keywords_json TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, title)
);

CREATE TABLE IF NOT EXISTS conversions (
    conversion_id TEXT PRIMARY KEY,
    order_id TEXT,
    job_id TEXT,
    total_commission REAL,
    purchase_time INTEGER,
    raw_json TEXT,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_posts_status ON platform_posts(status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, run_after);
CREATE INDEX IF NOT EXISTS idx_conversions_job ON conversions(job_id);
"""

MIGRATIONS = {
    "jobs": {
        "analysis_json": "TEXT",
        "facebook_output_path": "TEXT",
        "keyframes_json": "TEXT",
        "product_candidates_json": "TEXT",
        "content_json": "TEXT",
        "shopee_ready": "INTEGER NOT NULL DEFAULT 0",
        "facebook_ready": "INTEGER NOT NULL DEFAULT 0",
        "review_status": "TEXT NOT NULL DEFAULT 'PENDING'",
        "mode": "TEXT NOT NULL DEFAULT 'base'",
        "brief_json": "TEXT",
        "script_json": "TEXT",
        "viral_score": "REAL",  # AI editorial review, 0-10
        "qa_score": "REAL",     # rendered-video QA, 0-100
        "approved_at": "TEXT",
    },
    "platform_posts": {
        "scheduled_at": "TEXT",
    },
}

JOB_FIELDS = {
    "status", "ai_generated", "product_item_id", "product_name",
    "affiliate_link", "output_path", "facebook_output_path", "analysis_json",
    "keyframes_json", "product_candidates_json", "content_json", "shopee_ready",
    "facebook_ready", "review_status", "last_error", "mode", "brief_json",
    "script_json", "viral_score", "qa_score", "approved_at", "duration_sec", "width",
    "height", "fps",
}

_initialised: set[str] = set()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_in(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


@contextmanager
def connect(db_path: Path):
    db_path = Path(db_path)
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
    key = str(Path(db_path).resolve())
    if key in _initialised and Path(db_path).exists():
        return
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for table, columns in MIGRATIONS.items():
            existing = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, declaration in columns.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        # viral_score used to receive the /100 QA total; keep the two scales apart.
        conn.execute("UPDATE jobs SET qa_score=viral_score, viral_score=NULL "
                     "WHERE viral_score > 10 AND qa_score IS NULL")
    _initialised.add(key)


# ---------------------------------------------------------------- jobs

def register_job(db_path: Path, job: dict[str, Any]) -> bool:
    init_db(db_path)
    stamp = now_iso()
    with connect(db_path) as conn:
        conn.execute(_DELETED_SOURCES)
        if job.get("sha256") and not job.get("allow_deleted") and conn.execute(
                "SELECT 1 FROM deleted_sources WHERE sha256=?", (job["sha256"],)).fetchone():
            return False  # the user deleted this video; a rescan must not resurrect it
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO jobs
            (id, source_path, source_sha256, file_name, status,
             duration_sec, width, height, fps, mode, brief_json,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, 'DISCOVERED', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job["id"], job["source_path"], job["sha256"], job["file_name"],
                job.get("duration_sec"), job.get("width"), job.get("height"),
                job.get("fps"), job.get("mode", "base"),
                json.dumps(job["brief"], ensure_ascii=False) if job.get("brief") else None,
                stamp, stamp,
            ),
        )
        return cur.rowcount == 1


def update_job(db_path: Path, job_id: str, **fields: Any) -> None:
    updates = {key: value for key, value in fields.items() if key in JOB_FIELDS}
    if not updates:
        return
    init_db(db_path)
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


_DELETED_SOURCES = ("CREATE TABLE IF NOT EXISTS deleted_sources "
                    "(sha256 TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)")


def forget_deleted_source(db_path: Path, sha256: str) -> None:
    """A deliberate re-upload of a deleted video is allowed again."""
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(_DELETED_SOURCES)
        conn.execute("DELETE FROM deleted_sources WHERE sha256=?", (sha256,))


def delete_job(db_path: Path, job_id: str) -> None:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(_DELETED_SOURCES)
        row = conn.execute("SELECT source_sha256, mode FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row and row["source_sha256"] and (row["mode"] or "base") == "base":
            conn.execute("INSERT OR REPLACE INTO deleted_sources VALUES (?, ?)",
                         (row["source_sha256"], now_iso()))
        conn.execute("DELETE FROM platform_posts WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM tasks WHERE job_id=? AND status IN ('QUEUED','FAILED')", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))


def get_counts(db_path: Path) -> dict[str, int]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}


def list_jobs(db_path: Path, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    limit = max(1, min(int(limit), 500))
    with connect(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
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


# ---------------------------------------------------------------- platform posts

def upsert_platform_post(
    db_path: Path,
    job_id: str,
    platform: str,
    status: str,
    external_id: str | None = None,
    permalink: str | None = None,
    last_error: str | None = None,
    scheduled_at: str | None = None,
    count_attempt: bool = True,
) -> None:
    init_db(db_path)
    stamp = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            f"""
            INSERT INTO platform_posts
                (job_id, platform, status, external_id, permalink,
                 attempts, last_error, scheduled_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, {1 if count_attempt else 0}, ?, ?, ?, ?)
            ON CONFLICT(job_id, platform) DO UPDATE SET
                status=excluded.status,
                external_id=COALESCE(excluded.external_id, platform_posts.external_id),
                permalink=COALESCE(excluded.permalink, platform_posts.permalink),
                attempts=platform_posts.attempts + {1 if count_attempt else 0},
                last_error=excluded.last_error,
                scheduled_at=COALESCE(excluded.scheduled_at, platform_posts.scheduled_at),
                updated_at=excluded.updated_at
            """,
            (job_id, platform, status, external_id, permalink, last_error,
             scheduled_at, stamp, stamp),
        )


def list_platform_posts(db_path: Path, job_id: str | None = None) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        if job_id is None:
            rows = conn.execute(
                "SELECT * FROM platform_posts ORDER BY updated_at DESC LIMIT 500"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM platform_posts WHERE job_id=? ORDER BY platform",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]


# ---------------------------------------------------------------- tasks

def enqueue_task(
    db_path: Path,
    kind: str,
    job_id: str | None = None,
    payload: dict | None = None,
    run_after: str | None = None,
    max_attempts: int = 3,
    dedupe: bool = True,
) -> int | None:
    """Queue background work. With dedupe, an identical queued/running task is reused."""
    init_db(db_path)
    stamp = now_iso()
    payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
    with connect(db_path) as conn:
        if dedupe:
            row = conn.execute(
                "SELECT id FROM tasks WHERE kind=? AND IFNULL(job_id,'')=IFNULL(?, '') "
                "AND payload_json=? AND status IN ('QUEUED','RUNNING')",
                (kind, job_id, payload_json),
            ).fetchone()
            if row:
                return None
        cur = conn.execute(
            "INSERT INTO tasks (kind, job_id, payload_json, status, max_attempts, "
            "run_after, created_at, updated_at) VALUES (?, ?, ?, 'QUEUED', ?, ?, ?, ?)",
            (kind, job_id, payload_json, max_attempts, run_after or stamp, stamp, stamp),
        )
        return cur.lastrowid


def claim_task(db_path: Path) -> dict[str, Any] | None:
    init_db(db_path)
    stamp = now_iso()
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM tasks WHERE status='QUEUED' AND run_after<=? "
            # Video work the owner is waiting for runs before background refreshes.
            "ORDER BY CASE WHEN kind IN ('fetch_trends','trend_analyse','sync_conversions',"
            "'sync_shopee') THEN 1 ELSE 0 END, run_after ASC, id ASC LIMIT 1",
            (stamp,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE tasks SET status='RUNNING', attempts=attempts+1, updated_at=? WHERE id=?",
            (stamp, row["id"]),
        )
        task = dict(row)
        task["attempts"] += 1
        task["payload"] = json.loads(task.get("payload_json") or "{}")
        return task


def finish_task(db_path: Path, task_id: int, result: Any = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET status='DONE', last_error=NULL, result_json=?, updated_at=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False, default=str)[:20000], now_iso(), task_id),
        )


def fail_task(db_path: Path, task: dict, error: str, retry_in: float | None) -> None:
    with connect(db_path) as conn:
        if retry_in is not None and task["attempts"] < task["max_attempts"]:
            conn.execute(
                "UPDATE tasks SET status='QUEUED', last_error=?, run_after=?, updated_at=? WHERE id=?",
                (error[:2000], iso_in(retry_in), now_iso(), task["id"]),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status='FAILED', last_error=?, updated_at=? WHERE id=?",
                (error[:2000], now_iso(), task["id"]),
            )


def reset_stale_tasks(db_path: Path) -> int:
    """Tasks left RUNNING by a crashed worker are requeued on startup."""
    init_db(db_path)
    with connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE tasks SET status='QUEUED', updated_at=? WHERE status='RUNNING' "
            "AND kind NOT IN ('publish')",
            (now_iso(),),
        )
        conn.execute(
            "UPDATE tasks SET status='FAILED', last_error='Worker stopped during publish; "
            "reconcile on the platform before retrying.', updated_at=? "
            "WHERE status='RUNNING' AND kind='publish'",
            (now_iso(),),
        )
        return cur.rowcount


def list_tasks(db_path: Path, limit: int = 100) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def retry_task(db_path: Path, task_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE tasks SET status='QUEUED', attempts=0, run_after=?, updated_at=? "
            "WHERE id=? AND status='FAILED' AND kind!='publish'",
            (now_iso(), now_iso(), task_id),
        )


# ---------------------------------------------------------------- trends

def upsert_trend(db_path: Path, source: str, title: str, url: str = "",
                 traffic: str = "", notes: str = "", keywords: list | None = None) -> None:
    init_db(db_path)
    stamp = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO trends (source, title, url, traffic, notes, keywords_json,
                                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, title) DO UPDATE SET
                url=COALESCE(NULLIF(excluded.url,''), trends.url),
                traffic=COALESCE(NULLIF(excluded.traffic,''), trends.traffic),
                notes=COALESCE(NULLIF(excluded.notes,''), trends.notes),
                keywords_json=COALESCE(excluded.keywords_json, trends.keywords_json),
                updated_at=excluded.updated_at
            """,
            (source, title.strip()[:300], url, traffic, notes,
             json.dumps(keywords, ensure_ascii=False) if keywords else None, stamp, stamp),
        )


def list_trends(db_path: Path, limit: int = 100) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM trends ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def get_trend(db_path: Path, trend_id: int) -> dict[str, Any] | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM trends WHERE id=?", (trend_id,)).fetchone()
        return dict(row) if row else None


def update_trend(db_path: Path, trend_id: int, **fields) -> None:
    allowed = {"status", "job_id", "keywords_json", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = now_iso()
    clause = ", ".join(f"{k}=?" for k in updates)
    with connect(db_path) as conn:
        conn.execute(f"UPDATE trends SET {clause} WHERE id=?", [*updates.values(), trend_id])


def delete_trend(db_path: Path, trend_id: int) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM trends WHERE id=?", (trend_id,))


# ---------------------------------------------------------------- conversions

def upsert_conversions(db_path: Path, rows: list[dict]) -> int:
    init_db(db_path)
    stamp = now_iso()
    count = 0
    with connect(db_path) as conn:
        for row in rows:
            conversion_id = str(row.get("conversion_id") or "")
            if not conversion_id:
                continue
            conn.execute(
                """
                INSERT INTO conversions (conversion_id, order_id, job_id, total_commission,
                                         purchase_time, raw_json, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversion_id) DO UPDATE SET
                    total_commission=excluded.total_commission,
                    job_id=COALESCE(excluded.job_id, conversions.job_id),
                    raw_json=excluded.raw_json, synced_at=excluded.synced_at
                """,
                (conversion_id, row.get("order_id"), row.get("job_id"),
                 float(row.get("total_commission") or 0), row.get("purchase_time"),
                 json.dumps(row.get("raw") or {}, ensure_ascii=False), stamp),
            )
            count += 1
    return count


def conversion_summary(db_path: Path) -> list[dict[str, Any]]:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT c.job_id, j.product_name, j.status, COUNT(*) AS orders,
                   ROUND(SUM(c.total_commission), 2) AS commission
            FROM conversions c LEFT JOIN jobs j ON j.id = c.job_id
            GROUP BY c.job_id ORDER BY commission DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]
