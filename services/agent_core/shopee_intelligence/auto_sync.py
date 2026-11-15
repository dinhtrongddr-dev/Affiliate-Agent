"""Daily bounded Shopee data ingestion; no AI, rendering or publication.

The daily reservation is committed before network I/O. A failed/expired run is
not replayed that day because the source may already have charged its budget.
"""
import json
import math
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..db import connect, init_db
from . import addlive_config, addlive_service
from .addlive_client import AddLiveClient, AddLiveError, item_id_from_reference
from .unified_catalog import hidden_ids

DEFAULTS = {"local_time": "07:00", "max_api": 10, "discovery_limit": 200, "max_items": 300}
MAXIMA = {"max_api": 20, "discovery_limit": 1000, "max_items": 1000}
PAGE_SIZE = 50        # AddLiveTag offers return at most 50 rows per page (larger limits are clamped)
BATCH_SIZE = 100      # product-data-batch accepts at most 100 items per request
SHORT_WAIT = 60       # cooldowns up to this many seconds are waited out instead of ending the day
_sleep = time.sleep   # tests replace this
SCHEMA = """
CREATE TABLE IF NOT EXISTS shopee_sync_config (id INTEGER PRIMARY KEY CHECK(id=1), values_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shopee_sync_runs (
 day TEXT PRIMARY KEY, status TEXT NOT NULL, task_id INTEGER, owner TEXT,
 queued_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, lease_until TEXT,
 source_reserved INTEGER NOT NULL DEFAULT 0, discovery_reserved INTEGER NOT NULL DEFAULT 0,
 offers_requests INTEGER NOT NULL DEFAULT 0, batch_requests INTEGER NOT NULL DEFAULT 0,
 requested INTEGER NOT NULL DEFAULT 0, imported INTEGER NOT NULL DEFAULT 0,
 result_json TEXT, error TEXT, retry_after INTEGER NOT NULL DEFAULT 0
);
"""


def _init(settings):
    init_db(settings.database_path)
    with connect(settings.database_path) as conn:
        conn.executescript(SCHEMA)


def _now(settings, value=None):
    value = value or datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise ValueError("Thời điểm đồng bộ không hợp lệ")
    tz = ZoneInfo(settings.timezone)
    return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def local_display(settings, value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return _now(settings, dt).strftime('%d/%m/%Y %H:%M')
    except (TypeError, ValueError):
        return None


def config(settings) -> dict:
    _init(settings)
    try:
        configured = bool(addlive_config.summary(settings)["configured"])
        config_error = False
    except Exception:
        configured, config_error = False, True
    with connect(settings.database_path) as conn:
        row = conn.execute("SELECT values_json FROM shopee_sync_config WHERE id=1").fetchone()
    saved = json.loads(row[0]) if row else {}
    # 20 / 100 were the old hard caps, not a choice: let them follow the new defaults
    if saved.get("discovery_limit") == 20:
        saved.pop("discovery_limit")
    if saved.get("max_items") == 100:
        saved.pop("max_items")
    return {**DEFAULTS, "enabled": configured, **saved, "configured": configured,
            "config_error": config_error, "timezone": settings.timezone}


def save_config(settings, values) -> dict:
    if not isinstance(values, dict):
        raise ValueError("Cấu hình đồng bộ không hợp lệ")
    unknown = set(values) - {*DEFAULTS, "enabled"}
    if unknown:
        raise ValueError("Trường cấu hình đồng bộ không hỗ trợ")
    clean = {}
    for key, value in values.items():
        if key == "enabled":
            if not isinstance(value, bool):
                raise ValueError("Bật đồng bộ phải là đúng/sai")
        elif key == "local_time":
            if not isinstance(value, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", value):
                raise ValueError("Giờ đồng bộ cần định dạng HH:MM")
        else:
            maximum = MAXIMA[key]
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{key} phải từ 1 đến {maximum}")
        clean[key] = value
    _init(settings)
    with connect(settings.database_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT values_json FROM shopee_sync_config WHERE id=1").fetchone()
        saved = json.loads(row[0]) if row else {}
        saved.update(clean)
        conn.execute("INSERT INTO shopee_sync_config VALUES (1,?) ON CONFLICT(id) "
                     "DO UPDATE SET values_json=excluded.values_json", (json.dumps(saved),))
    return config(settings)


def _public(row):
    if row is None:
        return None
    result = dict(row)
    result.pop("owner", None)
    result["result"] = json.loads(result.pop("result_json") or "null")
    return result


def status(settings) -> dict:
    cfg = config(settings)
    now = _now(settings)
    with connect(settings.database_path) as conn:
        latest = conn.execute("SELECT * FROM shopee_sync_runs ORDER BY day DESC LIMIT 1").fetchone()
        success = conn.execute("SELECT * FROM shopee_sync_runs WHERE status='completed' "
                               "ORDER BY day DESC LIMIT 1").fetchone()
        today = conn.execute("SELECT * FROM shopee_sync_runs WHERE day=?", (now.date().isoformat(),)).fetchone()
    hour, minute = map(int, cfg["local_time"].split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if today:
        due += timedelta(days=1)
    state = "config_error" if cfg["config_error"] else "no_key" if not cfg["configured"] else (
        "disabled" if not cfg["enabled"] else "ready")
    last = _public(latest)
    if last:
        last['local_time'] = local_display(settings, last.get('finished_at') or last.get('started_at') or last.get('queued_at'))
    return {"config": cfg, "state": state, "last_run": last, "last_success": _public(success),
            "next_due_at": _stamp(due) if cfg["configured"] and cfg["enabled"] else None,
            "next_due_local": due.strftime('%d/%m/%Y %H:%M') if cfg["configured"] and cfg["enabled"] else None,
            "source_reserved_today": today["source_reserved"] if today else 0,
            "retry_after": latest["retry_after"] if latest else 0}


def enqueue_due(settings, now=None, force=False) -> int | None:
    cfg = config(settings)
    if not cfg["configured"] or (not cfg["enabled"] and not force):
        return None
    local = _now(settings, now)
    day, stamp = local.date().isoformat(), _stamp(local)
    hour, minute = map(int, cfg["local_time"].split(":"))
    with connect(settings.database_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM shopee_sync_runs WHERE day=?", (day,)).fetchone():
            return None
        last = conn.execute("SELECT day FROM shopee_sync_runs ORDER BY day DESC LIMIT 1").fetchone()
        catchup = bool(last and last["day"] < (local.date() - timedelta(days=1)).isoformat())
        if not force and (local.hour, local.minute) < (hour, minute) and not catchup:
            return None
        cur = conn.execute("INSERT INTO tasks(kind,payload_json,status,max_attempts,run_after,created_at,updated_at) "
                           "VALUES ('sync_shopee',?,'QUEUED',1,?,?,?)",
                           (json.dumps({"sync_day": day}), stamp, stamp, stamp))
        task_id = cur.lastrowid
        conn.execute("INSERT INTO shopee_sync_runs(day,status,task_id,queued_at) VALUES (?,'queued',?,?)",
                     (day, task_id, stamp))
        return task_id


def _tracked_ids(settings, limit):
    result = []
    hidden = hidden_ids(settings.database_path)
    with connect(settings.database_path) as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("shopee_products", "products"):
            if table not in tables:
                continue
            where = " WHERE platform='shopee'" if table == "products" else ""
            for row in conn.execute(f"SELECT item_id FROM {table}{where} ORDER BY updated_at ASC LIMIT ?", (limit,)):
                try:
                    item_id = item_id_from_reference(row["item_id"])
                except AddLiveError:
                    continue
                if item_id not in result and item_id not in hidden:
                    result.append(item_id)
    return result[:limit]


def _finish(settings, day, owner, state, result, error=None, retry_after=0, now=None):
    with connect(settings.database_path) as conn:
        conn.execute("UPDATE shopee_sync_runs SET status=?,finished_at=?,lease_until=NULL,result_json=?,"
                     "error=?,retry_after=? WHERE day=? AND owner=?",
                     (state, _stamp(_now(settings, now)), json.dumps(result), error, retry_after, day, owner))
    return {"status": state, **result, "error": error, "retry_after": retry_after}


def run(settings, now=None, client=None, sync_day=None) -> dict:
    cfg = config(settings)
    if not cfg["configured"]:
        return {"status": "blocked", "reason": "no_key", "error": "Chưa cấu hình API Key AddLiveTag."}
    local = _now(settings, now)
    day, stamp, owner = local.date().isoformat(), _stamp(local), uuid.uuid4().hex
    if sync_day is not None and sync_day != day:
        # Old queued tasks must not consume today's reservation or replay a missed day.
        return {"status": "skipped", "reason": "expired_queue_day", "day": sync_day}
    with connect(settings.database_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM shopee_sync_runs WHERE day=?", (day,)).fetchone()
        if row and row["status"] != "queued":
            if row["status"] == "running" and row["lease_until"] <= stamp:
                conn.execute("UPDATE shopee_sync_runs SET status='unknown',error=?,finished_at=? WHERE day=?",
                             ("Phiên đồng bộ dừng giữa chừng; không phát lại ngân sách nguồn trong ngày.", stamp, day))
            return {"status": "skipped", "reason": "daily_run_already_reserved", "day": day}
        if not cfg["enabled"] and not row:
            return {"status": "skipped", "reason": "disabled", "day": day}
        if not row:
            conn.execute("INSERT INTO shopee_sync_runs(day,status,queued_at) VALUES (?,'queued',?)", (day, stamp))
        conn.execute("UPDATE shopee_sync_runs SET status='running',owner=?,started_at=?,lease_until=?,"
                     "source_reserved=?,discovery_reserved=1 WHERE day=?",
                     (owner, stamp, _stamp(local + timedelta(minutes=30)), cfg["max_api"], day))
    result = {"day": day, "requested": 0, "imported": 0, "discovered": 0,
              "source_reserved": cfg["max_api"], "offers_requests": 0, "batch_requests": 0}
    try:
        credentials = addlive_config.load(settings)
        provider = client or AddLiveClient(credentials["api_key"])
        def bump(column, value):
            with connect(settings.database_path) as conn:
                conn.execute(f"UPDATE shopee_sync_runs SET {column}=? WHERE day=? AND owner=?", (value, day, owner))

        def pause(limits):
            """Wait out a short source cooldown; return the wait if it is too long (stop for today)."""
            limits = limits if isinstance(limits, dict) else {}
            wait = max(0, min(86400, math.ceil(float(limits.get("sourceCooldownSeconds") or 0))))
            if limits.get("sourceRateLimited"):
                return max(120, wait)
            if wait > SHORT_WAIT:
                return wait
            if wait:
                _sleep(wait + 1)
            return 0

        # 1) discovery: page through every seed keyword (50 per page), starting with today's
        keywords = settings.seed_keywords or [""]
        first = local.date().toordinal() % len(keywords)
        queue = [[kw, 1] for kw in keywords[first:] + keywords[:first]]
        target = cfg["discovery_limit"]
        discovered, hidden, blocked_wait = [], hidden_ids(settings.database_path), 0
        max_calls = math.ceil(target / PAGE_SIZE) + len(keywords) + 2
        while queue and len(discovered) < target and result["offers_requests"] < max_calls:
            entry = queue.pop(0)
            result["offers_requests"] += 1
            bump("offers_requests", result["offers_requests"])
            offers = provider.offers("products", keyword=entry[0], page=entry[1],
                                     limit=min(PAGE_SIZE, max(1, target - len(discovered))), sort_type=1)
            if not isinstance(offers, dict) or offers.get("status") != "success" or not isinstance(offers.get("products"), list):
                raise ValueError("Invalid offers envelope")
            fresh = 0
            for offer in offers["products"]:
                if not isinstance(offer, dict):
                    raise ValueError("Invalid offer row")
                try:
                    item_id = item_id_from_reference(offer.get("itemId"))
                except AddLiveError:
                    continue
                if item_id not in discovered and item_id not in hidden and len(discovered) < target:
                    discovered.append(item_id)
                    fresh += 1
            if offers.get("hasNextPage") and fresh:
                queue.append([entry[0], entry[1] + 1])   # round-robin: the other keywords go first
            blocked_wait = pause(offers.get("limits"))
            if blocked_wait:
                break
        result["discovered"] = len(discovered)
        # Alternate new discoveries and oldest tracked records so a source budget
        # consumed in request order cannot indefinitely bury newly found products.
        tracked = _tracked_ids(settings, cfg["max_items"])
        new_ids = [item for item in discovered if item not in tracked]
        references = []
        for index in range(max(len(new_ids), len(tracked))):
            if index < len(new_ids):
                references.append(new_ids[index])
            if index < len(tracked):
                references.append(tracked[index])
        references = references[:cfg["max_items"]]
        result["requested"] = len(references)
        if blocked_wait:
            return _finish(settings, day, owner, "blocked", result,
                           "Nguồn AddLiveTag đang nghỉ theo hạn mức; không gọi lô tiếp.", blocked_wait, now)
        if not references:
            return _finish(settings, day, owner, "completed", result, now=now)
        bump("requested", len(references))

        # 2) details in batches of 100; rows refused only because of a short source cooldown
        #    are retried once after waiting it out
        summary, retry = {}, []
        base_rate = credentials["base_rate"] / 100 if credentials.get("base_rate") is not None else None

        def run_batch(chunk):
            result["batch_requests"] += 1
            bump("batch_requests", result["batch_requests"])
            imported = addlive_service.import_batch(settings, provider, chunk, base_rate=base_rate,
                cap=credentials.get("cap", 40000), max_api=cfg["max_api"],
                affiliate_id=credentials.get("affiliate_id", ""), sub_id=credentials.get("sub_id", ""))
            result["imported"] += imported["imported"]
            bump("imported", result["imported"])
            for key, value in (imported.get("summary") or {}).items():
                if isinstance(value, int) and not isinstance(value, bool):
                    summary[key] = summary.get(key, 0) + value
            for row in imported.get("results") or []:
                if not row.get("imported") and row.get("reason") == "source_cooldown":
                    try:
                        retry.append(item_id_from_reference(row.get("itemId") or row.get("input")))
                    except AddLiveError:
                        pass
            return pause(imported.get("limits"))

        wait = 0
        for begin in range(0, len(references), BATCH_SIZE):
            wait = run_batch(references[begin:begin + BATCH_SIZE])
            if wait:
                break
        if retry and not wait:
            again = list(retry)
            retry.clear()
            for begin in range(0, len(again), BATCH_SIZE):
                wait = run_batch(again[begin:begin + BATCH_SIZE])
                if wait:
                    break
        result["summary"] = summary
        result["source_rate_limited"] = bool(wait)
        partial = result["imported"] != len(references) or bool(wait)
        state = "partial" if partial else "completed"
        return _finish(settings, day, owner, state, result,
                       "Một số sản phẩm chưa được nhập; giữ các dữ liệu đã nhập, không phát lại trong ngày."
                       if partial else None, wait, now)
    except Exception as exc:
        if result["batch_requests"]:
            # Import persists rows individually; a crash can follow committed rows.
            result["imported"] = None
            result["imports_may_be_partial"] = True
        wait = exc.retry_after if isinstance(exc, AddLiveError) else 0
        result['error_code'] = exc.code if isinstance(exc, AddLiveError) else 'sync_error'
        message = ("AddLiveTag chưa hoàn tất do kết nối, quyền truy cập hoặc hạn mức; không tự phát lại trong ngày."
                   if isinstance(exc, AddLiveError) else
                   "Đồng bộ dữ liệu chưa hoàn tất; giữ dữ liệu đã nhập và kiểm tra nhật ký trạng thái.")
        if result['error_code'] in {'http_401', 'http_403'}:
            message = 'AddLiveTag từ chối key hoặc quyền truy cập (' + result['error_code'][5:] + '); cập nhật key trong Cài đặt nâng cao.'
        return _finish(settings, day, owner, "failed", result, message, wait, now)
