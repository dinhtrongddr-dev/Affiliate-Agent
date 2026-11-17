"""SQLite tables for China trends, ideas and niches."""
import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import connect, init_db, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS cn_topics (
    key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    title_zh TEXT NOT NULL,
    title_vi TEXT,
    summary_vi TEXT,
    niche TEXT,
    url TEXT,
    cover TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    platforms_json TEXT NOT NULL DEFAULT '[]',
    labels_json TEXT,
    labeled_at TEXT,
    stage TEXT,
    confidence REAL,
    scores_json TEXT,
    opportunity REAL,
    goal TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    formula_json TEXT,
    source_kind TEXT NOT NULL DEFAULT 'auto',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cn_topics_opp ON cn_topics(opportunity DESC);

CREATE TABLE IF NOT EXISTS cn_observations (
    key TEXT NOT NULL,
    platform TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    rank INTEGER,
    heat REAL,
    views REAL,
    likes REAL,
    shares REAL,
    comments REAL,
    creators REAL,
    meta_json TEXT,
    PRIMARY KEY (key, platform, observed_at)
);

CREATE TABLE IF NOT EXISTS cn_sources (
    platform TEXT PRIMARY KEY,
    supported TEXT NOT NULL,
    last_ok TEXT,
    last_error TEXT,
    fail_streak INTEGER NOT NULL DEFAULT 0,
    paused_until TEXT,
    items_last INTEGER
);

CREATE TABLE IF NOT EXISTS cn_ideas (
    id TEXT PRIMARY KEY,
    topic_key TEXT NOT NULL,
    title TEXT NOT NULL,
    premise TEXT,
    hook TEXT,
    format TEXT,
    duration INTEGER,
    niche TEXT,
    goal TEXT,
    branch TEXT,
    est_score REAL,
    data_json TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    job_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cn_ideas_status ON cn_ideas(status, est_score DESC);

CREATE TABLE IF NOT EXISTS cn_niches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    evidence_json TEXT,
    created_at TEXT NOT NULL
);
"""

_ready: set[str] = set()


def ensure_schema(db_path) -> None:
    key = str(Path(db_path).resolve())
    if key in _ready and Path(db_path).exists():
        return
    init_db(db_path)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
    _ready.add(key)


def topic_key(platform: str, title: str) -> str:
    text = unicodedata.normalize("NFKC", " ".join(str(title or "").split())).lower()
    return "cn_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16] if text else ""


def hours_since(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - at).total_seconds() / 3600


def record(db_path, signal: dict, observed_at: str | None = None) -> str | None:
    """Upsert a topic and add one observation. The same title on two platforms is one topic."""
    ensure_schema(db_path)
    title = " ".join(str(signal.get("title") or "").split())[:300]
    key = topic_key(signal["platform"], title)
    if not key:
        return None
    stamp = observed_at or now_iso()
    platform = str(signal["platform"])
    with connect(db_path) as conn:
        row = conn.execute("SELECT platforms_json FROM cn_topics WHERE key=?", (key,)).fetchone()
        if row:
            platforms = sorted(set(json.loads(row["platforms_json"] or "[]")) | {platform})
            conn.execute("UPDATE cn_topics SET last_seen=?, platforms_json=?, url=COALESCE(NULLIF(url,''),?),"
                         " cover=COALESCE(NULLIF(cover,''),?), updated_at=? WHERE key=?",
                         (stamp, json.dumps(platforms), signal.get("url") or "",
                          signal.get("cover") or "", stamp, key))
        else:
            conn.execute("INSERT INTO cn_topics (key, platform, title_zh, url, cover, first_seen, last_seen,"
                         " platforms_json, source_kind, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (key, platform, title, signal.get("url") or "", signal.get("cover") or "",
                          stamp, stamp, json.dumps([platform]), signal.get("source_kind") or "auto", stamp))
        conn.execute("INSERT OR REPLACE INTO cn_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (key, platform, stamp, signal.get("rank"), signal.get("heat"), signal.get("views"),
                      signal.get("likes"), signal.get("shares"), signal.get("comments"),
                      signal.get("creators"), json.dumps(signal.get("meta") or {}, ensure_ascii=False)))
    return key


def update_topic(db_path, key: str, **fields) -> None:
    ensure_schema(db_path)
    for name in ("labels_json", "scores_json", "formula_json"):
        if name in fields and not isinstance(fields[name], str) and fields[name] is not None:
            fields[name] = json.dumps(fields[name], ensure_ascii=False)
    fields["updated_at"] = now_iso()
    with connect(db_path) as conn:
        conn.execute(f"UPDATE cn_topics SET {', '.join(f'{k}=?' for k in fields)} WHERE key=?",
                     [*fields.values(), key])


def _topic(row) -> dict:
    d = dict(row)
    for name in ("labels_json", "scores_json", "formula_json", "platforms_json"):
        d[name[:-5]] = json.loads(d.get(name) or ("[]" if name == "platforms_json" else "null"))
    return d


def get_topic(db_path, key: str) -> dict | None:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM cn_topics WHERE key=?", (key,)).fetchone()
    return _topic(row) if row else None


def list_topics(db_path, limit: int = 200, since_hours: float | None = 96,
                status: list[str] | None = None) -> list[dict]:
    ensure_schema(db_path)
    sql, args = "SELECT * FROM cn_topics WHERE 1=1", []
    if since_hours:
        # last_seen is ISO with a "T"; compare against the same format (datetime() uses a space,
        # which made this a text comparison that let every row through)
        sql += " AND last_seen >= strftime('%Y-%m-%dT%H:%M:%S', 'now', ?)"
        args.append(f"-{int(since_hours)} hours")
    if status:
        sql += f" AND status IN ({','.join('?' * len(status))})"
        args += status
    sql += " ORDER BY COALESCE(opportunity, -1) DESC, last_seen DESC LIMIT ?"
    args.append(int(limit))
    with connect(db_path) as conn:
        return [_topic(r) for r in conn.execute(sql, args).fetchall()]


def topic_names(db_path, keys: list[str]) -> dict[str, str]:
    """Display name of the listed topics only (key -> Vietnamese title, else Chinese)."""
    keys = list(dict.fromkeys(k for k in keys if k))
    if not keys:
        return {}
    ensure_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(f"SELECT key, title_vi, title_zh FROM cn_topics WHERE key IN ({','.join('?' * len(keys))})",
                            keys).fetchall()
    return {r["key"]: r["title_vi"] or r["title_zh"] for r in rows}


def observations(db_path, key: str, limit: int = 60) -> list[dict]:
    """Oldest first."""
    ensure_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM cn_observations WHERE key=? ORDER BY observed_at DESC LIMIT ?",
                            (key, int(limit))).fetchall()
    return [dict(r) for r in reversed(rows)]


def snapshot(db_path, platform: str, observed_at: str) -> list[dict]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM cn_observations WHERE platform=? AND observed_at=?", (platform, observed_at))]


# ------------------------------------------------------------------ source health

def source_state(db_path) -> dict[str, dict]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        return {r["platform"]: dict(r) for r in conn.execute("SELECT * FROM cn_sources")}


def mark_source(db_path, platform: str, supported: str, ok: bool, error: str = "",
                items: int | None = None, pause_hours: float = 0) -> None:
    ensure_schema(db_path)
    stamp = now_iso()
    with connect(db_path) as conn:
        row = conn.execute("SELECT fail_streak FROM cn_sources WHERE platform=?", (platform,)).fetchone()
        streak = 0 if ok else (row["fail_streak"] if row else 0) + 1
        paused = None
        if pause_hours:
            from datetime import timedelta
            paused = (datetime.now(timezone.utc) + timedelta(hours=pause_hours)).isoformat(timespec="seconds")
        conn.execute("INSERT INTO cn_sources (platform, supported, last_ok, last_error, fail_streak, paused_until,"
                     " items_last) VALUES (?,?,?,?,?,?,?) ON CONFLICT(platform) DO UPDATE SET"
                     " supported=excluded.supported, last_ok=COALESCE(excluded.last_ok, cn_sources.last_ok),"
                     " last_error=excluded.last_error, fail_streak=excluded.fail_streak,"
                     " paused_until=excluded.paused_until, items_last=COALESCE(excluded.items_last, cn_sources.items_last)",
                     (platform, supported, stamp if ok else None, None if ok else error[:300], streak, paused, items))


# ------------------------------------------------------------------ ideas & niches

def save_idea(db_path, idea: dict) -> str:
    ensure_schema(db_path)
    stamp = now_iso()
    iid = idea.get("id") or "i" + hashlib.sha256(
        f"{idea['topic_key']}|{idea['title']}".encode()).hexdigest()[:15]
    with connect(db_path) as conn:
        conn.execute("INSERT OR REPLACE INTO cn_ideas (id, topic_key, title, premise, hook, format, duration, niche,"
                     " goal, branch, est_score, data_json, status, job_id, created_at, updated_at)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,COALESCE((SELECT status FROM cn_ideas WHERE id=?),'NEW'),"
                     " (SELECT job_id FROM cn_ideas WHERE id=?),"
                     " COALESCE((SELECT created_at FROM cn_ideas WHERE id=?),?),?)",
                     (iid, idea["topic_key"], idea["title"], idea.get("premise"), idea.get("hook"),
                      idea.get("format"), idea.get("duration"), idea.get("niche"), idea.get("goal"),
                      idea.get("branch"), idea.get("est_score"),
                      json.dumps(idea.get("data") or {}, ensure_ascii=False), iid, iid, iid, stamp, stamp))
    return iid


def list_ideas(db_path, status: list[str] | None = None, topic_key: str | None = None,
               limit: int = 200) -> list[dict]:
    ensure_schema(db_path)
    sql, args = "SELECT * FROM cn_ideas WHERE 1=1", []
    if status:
        sql += f" AND status IN ({','.join('?' * len(status))})"
        args += status
    if topic_key:
        sql += " AND topic_key=?"
        args.append(topic_key)
    sql += " ORDER BY COALESCE(est_score,0) DESC, created_at DESC LIMIT ?"
    args.append(int(limit))
    with connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        r["data"] = json.loads(r.pop("data_json") or "{}")
    return rows


def get_idea(db_path, iid: str) -> dict | None:
    rows = [r for r in list_ideas(db_path, limit=10000) if r["id"] == iid]
    return rows[0] if rows else None


def update_idea(db_path, iid: str, **fields) -> None:
    ensure_schema(db_path)
    fields["updated_at"] = now_iso()
    with connect(db_path) as conn:
        conn.execute(f"UPDATE cn_ideas SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     [*fields.values(), iid])


def niches(db_path) -> list[dict]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM cn_niches ORDER BY status, created_at")]
    for r in rows:
        r["evidence"] = json.loads(r.pop("evidence_json") or "[]")
    return rows


def save_niche(db_path, nid: str, name: str, description: str, status: str = "active",
               evidence: list | None = None) -> None:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        conn.execute("INSERT INTO cn_niches (id, name, description, status, evidence_json, created_at)"
                     " VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
                     " description=excluded.description, status=excluded.status,"
                     " evidence_json=excluded.evidence_json",
                     (nid, name, description, status, json.dumps(evidence or [], ensure_ascii=False), now_iso()))


def count(db_path, sql: str, args: Any = ()) -> int:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        return int(conn.execute(sql, args).fetchone()[0])
