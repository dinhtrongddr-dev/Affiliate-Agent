"""Trend Intelligence storage: topics, timestamped observations, matched products,
and per-source request budgets. Additive tables only; the legacy `trends` table is untouched."""
import json
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..db import connect, init_db, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS trend_topics (
    key TEXT PRIMARY KEY,
    keyword TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    url TEXT,
    notes TEXT,
    safety TEXT NOT NULL DEFAULT 'UNCHECKED',
    safety_reason TEXT,
    intent_json TEXT,
    intent_at TEXT,
    scores_json TEXT,
    total_score REAL,
    score_status TEXT,
    lifecycle TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    job_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trend_topics_score ON trend_topics(total_score DESC);

CREATE TABLE IF NOT EXISTS trend_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_key TEXT NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    value REAL,
    rank INTEGER,
    meta_json TEXT,
    UNIQUE(topic_key, source, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_trend_obs_topic ON trend_observations(topic_key, source, observed_at);

CREATE TABLE IF NOT EXISTS trend_products (
    topic_key TEXT NOT NULL,
    product_id TEXT NOT NULL,
    score REAL,
    relevance REAL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (topic_key, product_id)
);

CREATE TABLE IF NOT EXISTS trend_pair_fit (
    topic_key TEXT NOT NULL,
    product_id TEXT NOT NULL,
    name_hash TEXT NOT NULL,
    fit REAL NOT NULL,
    reason TEXT,
    checked_at TEXT NOT NULL,
    PRIMARY KEY (topic_key, product_id, name_hash)
);

CREATE TABLE IF NOT EXISTS trend_source_usage (
    source TEXT NOT NULL,
    period TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, period)
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


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def topic_key(keyword: str) -> str:
    """Stable dedupe key: accent-free, lowercase, single spaces, no leading '#'."""
    text = strip_accents(keyword).lower().lstrip("#").strip()
    text = "".join(c if c.isalnum() else " " for c in text)
    return " ".join(text.split())[:120]


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        value = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def hours_since(ts: str | None, now: datetime | None = None) -> float | None:
    parsed = _parse(ts)
    if parsed is None:
        return None
    return ((now or datetime.now(timezone.utc)) - parsed).total_seconds() / 3600


def record_signal(db_path, signal: dict, observed_at: str | None = None) -> str | None:
    """Upsert the topic and append one observation. Returns the topic key."""
    ensure_schema(db_path)
    keyword = " ".join(str(signal.get("keyword") or "").split())[:200]
    key = topic_key(keyword)
    if not key:
        return None
    stamp = observed_at or now_iso()
    source = str(signal["source"])
    with connect(db_path) as conn:
        row = conn.execute("SELECT sources_json, url, notes FROM trend_topics WHERE key=?",
                           (key,)).fetchone()
        if row:
            sources = sorted(set(json.loads(row["sources_json"] or "[]")) | {source})
            conn.execute(
                "UPDATE trend_topics SET last_seen=?, sources_json=?, url=COALESCE(NULLIF(url,''), ?),"
                " notes=COALESCE(NULLIF(notes,''), ?), updated_at=? WHERE key=?",
                (stamp, json.dumps(sources), signal.get("url") or "", signal.get("notes") or "",
                 stamp, key))
        else:
            conn.execute(
                "INSERT INTO trend_topics (key, keyword, first_seen, last_seen, sources_json, url,"
                " notes, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (key, keyword, stamp, stamp, json.dumps([source]), signal.get("url") or "",
                 signal.get("notes") or "", stamp))
        conn.execute(
            "INSERT OR REPLACE INTO trend_observations (topic_key, source, observed_at, value, rank,"
            " meta_json) VALUES (?,?,?,?,?,?)",
            (key, source, stamp, signal.get("value"), signal.get("rank"),
             json.dumps(signal.get("meta") or {}, ensure_ascii=False)))
    return key


def observations(db_path, key: str, source: str | None = None, limit: int = 30) -> list[dict]:
    """Newest first."""
    ensure_schema(db_path)
    sql = "SELECT * FROM trend_observations WHERE topic_key=?"
    args: list[Any] = [key]
    if source:
        sql += " AND source=?"
        args.append(source)
    sql += " ORDER BY observed_at DESC LIMIT ?"
    args.append(int(limit))
    with connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        r["meta"] = json.loads(r.pop("meta_json") or "{}")
    return rows


def _topic(row) -> dict:
    data = dict(row)
    data["sources"] = json.loads(data.pop("sources_json") or "[]")
    data["intent"] = json.loads(data.pop("intent_json") or "null")
    data["scores"] = json.loads(data.pop("scores_json") or "null")
    return data


def get_topic(db_path, key: str) -> dict | None:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM trend_topics WHERE key=?", (key,)).fetchone()
    return _topic(row) if row else None


def list_topics(db_path, limit: int = 200, include_blocked: bool = True,
                since_hours: float | None = None, status: str = "") -> list[dict]:
    ensure_schema(db_path)
    where, args = [], []
    if not include_blocked:
        where.append("safety != 'BLOCKED'")
    if since_hours is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat(
            timespec="seconds")
        where.append("last_seen >= ?")
        args.append(cutoff)
    if status:
        where.append("status=?")
        args.append(status)
    sql = "SELECT * FROM trend_topics"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(total_score, -1) DESC, last_seen DESC LIMIT ?"
    with connect(db_path) as conn:
        return [_topic(r) for r in conn.execute(sql, [*args, int(limit)]).fetchall()]


TOPIC_FIELDS = {"safety", "safety_reason", "intent_json", "intent_at", "scores_json",
                "total_score", "score_status", "lifecycle", "status", "job_id", "notes"}


def update_topic(db_path, key: str, **fields) -> None:
    updates = {k: v for k, v in fields.items() if k in TOPIC_FIELDS}
    if not updates:
        return
    for name in ("intent_json", "scores_json"):
        if name in updates and not isinstance(updates[name], str) and updates[name] is not None:
            updates[name] = json.dumps(updates[name], ensure_ascii=False)
    updates["updated_at"] = now_iso()
    clause = ", ".join(f"{k}=?" for k in updates)
    with connect(db_path) as conn:
        conn.execute(f"UPDATE trend_topics SET {clause} WHERE key=?", [*updates.values(), key])


def delete_topic(db_path, key: str) -> None:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        conn.execute("DELETE FROM trend_topics WHERE key=?", (key,))
        conn.execute("DELETE FROM trend_observations WHERE topic_key=?", (key,))
        conn.execute("DELETE FROM trend_products WHERE topic_key=?", (key,))


def save_matches(db_path, key: str, matches: list[dict]) -> None:
    ensure_schema(db_path)
    stamp = now_iso()
    with connect(db_path) as conn:
        conn.execute("DELETE FROM trend_products WHERE topic_key=?", (key,))
        for m in matches:
            conn.execute(
                "INSERT OR REPLACE INTO trend_products (topic_key, product_id, score, relevance,"
                " reason, created_at) VALUES (?,?,?,?,?,?)",
                (key, m["product_id"], m.get("score"), m.get("relevance"), m.get("reason") or "",
                 stamp))


def matches_for(db_path, key: str) -> list[dict]:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM trend_products WHERE topic_key=? ORDER BY score DESC", (key,)).fetchall()]


def matches_for_many(db_path, keys: list[str], per_topic: int = 5) -> dict[str, list[dict]]:
    """Best product matches of many topics in one query (topic key -> rows, best first)."""
    keys = list(dict.fromkeys(keys))
    out: dict[str, list[dict]] = {k: [] for k in keys}
    if not keys:
        return out
    ensure_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM trend_products WHERE topic_key IN ({','.join('?' * len(keys))}) "
                            "ORDER BY topic_key, score DESC", keys).fetchall()
    for r in rows:
        bucket = out.setdefault(r["topic_key"], [])
        if len(bucket) < per_topic:
            bucket.append(dict(r))
    return out


def observations_many(db_path, keys: list[str], limit: int = 8) -> dict[str, list[dict]]:
    """Newest observations of many topics in one query (topic key -> rows, newest first)."""
    keys = list(dict.fromkeys(keys))
    out: dict[str, list[dict]] = {k: [] for k in keys}
    if not keys:
        return out
    ensure_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY topic_key ORDER BY observed_at DESC) AS rn "
            f"FROM trend_observations WHERE topic_key IN ({','.join('?' * len(keys))})) WHERE rn <= ? "
            "ORDER BY topic_key, observed_at DESC", [*keys, int(limit)]).fetchall()
    for r in rows:
        row = dict(r)
        row.pop("rn", None)
        row["meta"] = json.loads(row.pop("meta_json") or "{}")
        out.setdefault(row["topic_key"], []).append(row)
    return out


# ------------------------------------------------------------------ budgets

def _period(kind: str) -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m") if kind == "month" else now.strftime("%Y-%m-%d")


def usage(db_path, source: str, kind: str = "month") -> int:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT calls FROM trend_source_usage WHERE source=? AND period=?",
                           (source, _period(kind))).fetchone()
    return int(row["calls"]) if row else 0


def consume(db_path, source: str, budget: int, kind: str = "month", calls: int = 1) -> bool:
    """Reserve `calls` requests; False (and nothing reserved) when the budget is spent."""
    ensure_schema(db_path)
    period = _period(kind)
    with connect(db_path) as conn:
        row = conn.execute("SELECT calls FROM trend_source_usage WHERE source=? AND period=?",
                           (source, period)).fetchone()
        used = int(row["calls"]) if row else 0
        if used + calls > budget:
            return False
        conn.execute(
            "INSERT INTO trend_source_usage (source, period, calls) VALUES (?,?,?)"
            " ON CONFLICT(source, period) DO UPDATE SET calls=calls+excluded.calls",
            (source, period, calls))
    return True
