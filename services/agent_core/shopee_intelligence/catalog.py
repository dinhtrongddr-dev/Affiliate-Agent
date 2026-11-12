"""SQLite catalog for Shopee product intelligence."""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from ..db import connect, init_db, now_iso
from .scoring import score_product

SCHEMA = """
CREATE TABLE IF NOT EXISTS shopee_products (
    product_id TEXT PRIMARY KEY, item_id TEXT, shop_id TEXT,
    product_name TEXT NOT NULL, product_url TEXT NOT NULL, category TEXT, shop_name TEXT,
    current_price REAL, original_price REAL, currency TEXT NOT NULL DEFAULT 'VND',
    total_sold INTEGER, rating_star REAL, rating_count INTEGER, view_count INTEGER, like_count INTEGER,
    image_url TEXT, video_url TEXT, base_commission_rate REAL, xtra_commission_rate REAL,
    commission_total_rate REAL, commission_amount REAL,
    affiliate_link TEXT, offer_start_at TEXT, offer_end_at TEXT,
    media_usage_rights TEXT NOT NULL DEFAULT 'unknown',
    source_type TEXT NOT NULL DEFAULT 'manual', source_url TEXT,
    observed_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shopee_product_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id TEXT NOT NULL REFERENCES shopee_products(product_id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL, current_price REAL, total_sold INTEGER, view_count INTEGER,
    like_count INTEGER, rating_star REAL, rating_count INTEGER,
    base_commission_rate REAL, xtra_commission_rate REAL, commission_total_rate REAL,
    commission_amount REAL, source_type TEXT NOT NULL,
    source_file TEXT, raw_json TEXT, UNIQUE(product_id, observed_at)
);
CREATE TABLE IF NOT EXISTS shopee_intel_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source_file TEXT NOT NULL, sha256 TEXT NOT NULL,
    rows_read INTEGER NOT NULL DEFAULT 0, rows_imported INTEGER NOT NULL DEFAULT 0,
    rows_skipped INTEGER NOT NULL DEFAULT 0, errors_json TEXT, imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shopee_products_updated ON shopee_products(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_shopee_products_sold ON shopee_products(total_sold DESC);
CREATE INDEX IF NOT EXISTS idx_shopee_products_commission ON shopee_products(base_commission_rate, xtra_commission_rate);
CREATE INDEX IF NOT EXISTS idx_shopee_observations_product_time ON shopee_product_observations(product_id, observed_at DESC);
"""


def init_intelligence_schema(db_path: Path) -> None:
    init_db(db_path)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        for table in ("shopee_products", "shopee_product_observations"):
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name in ("commission_total_rate", "commission_amount"):
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} REAL")


def _valid_product_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "shopee.vn" or host.endswith(".shopee.vn")) and bool(parsed.path and parsed.path != "/")


def _valid_affiliate_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(str(value).strip())
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "shopee.vn" or host.endswith(".shopee.vn") or host == "shope.ee")


def _valid_media_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(str(value).strip())
    return parsed.scheme == "https" and bool(parsed.hostname)


def _item_id_from_url(url: str) -> tuple[str, str]:
    match = re.search(r"i\.(\d+)\.(\d+)", url)
    if match:
        return match.group(2), match.group(1)
    match = re.search(r"/product/(\d+)/(\d+)", url)
    return (match.group(2), match.group(1)) if match else ("", "")


def _optional_float(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def save_product(db_path: Path, data: dict, source_type: str = "manual", source_file: str = "") -> dict:
    """Upsert one user-supplied or authorized product record and save an observation."""
    init_intelligence_schema(db_path)
    name = str(data.get("product_name") or data.get("name") or "").strip()
    product_url = str(data.get("product_url") or data.get("url") or "").strip()
    if not name:
        raise ValueError("Tên sản phẩm không được để trống.")
    if not _valid_product_url(product_url):
        raise ValueError("URL sản phẩm phải là HTTPS thuộc Shopee Việt Nam.")
    affiliate_link = str(data.get("affiliate_link") or "").strip()
    if affiliate_link and not _valid_affiliate_url(affiliate_link):
        raise ValueError("Custom Link phải là link HTTPS thuộc Shopee.")
    for field in ("image_url", "video_url"):
        if data.get(field) and not _valid_media_url(str(data[field]).strip()):
            raise ValueError(f"{field} phải là URL HTTPS hợp lệ.")
    item_from_url, shop_from_url = _item_id_from_url(product_url)
    item_id = str(data.get("item_id") or item_from_url).strip()
    shop_id = str(data.get("shop_id") or shop_from_url).strip()
    product_id = str(data.get("product_id") or item_id).strip()
    if not product_id:
        product_id = hashlib.sha256(product_url.encode("utf-8")).hexdigest()[:24]
    rights = str(data.get("media_usage_rights") or "unknown").strip().lower()
    if rights not in {"unknown", "owned", "licensed", "permission", "official"}:
        raise ValueError("media_usage_rights phải là unknown, owned, licensed, permission hoặc official.")

    observed_at = str(data.get("observed_at") or now_iso())
    stamp = now_iso()
    values = {
        "product_id": product_id, "item_id": item_id or None, "shop_id": shop_id or None,
        "product_name": name[:500], "product_url": product_url,
        "category": str(data.get("category") or "").strip() or None,
        "shop_name": str(data.get("shop_name") or "").strip() or None,
        "current_price": _optional_float(data.get("current_price")),
        "original_price": _optional_float(data.get("original_price")),
        "currency": str(data.get("currency") or "VND").strip().upper(),
        "total_sold": _optional_int(data.get("total_sold")),
        "rating_star": _optional_float(data.get("rating_star")),
        "rating_count": _optional_int(data.get("rating_count")),
        "view_count": _optional_int(data.get("view_count")),
        "like_count": _optional_int(data.get("like_count")),
        "image_url": str(data.get("image_url") or "").strip() or None,
        "video_url": str(data.get("video_url") or "").strip() or None,
        "base_commission_rate": _optional_float(data.get("base_commission_rate")),
        "xtra_commission_rate": _optional_float(data.get("xtra_commission_rate")),
        "commission_total_rate": _optional_float(data.get("commission_total_rate")),
        "commission_amount": _optional_float(data.get("commission_amount")),
        "affiliate_link": affiliate_link or None,
        "offer_start_at": str(data.get("offer_start_at") or "").strip() or None,
        "offer_end_at": str(data.get("offer_end_at") or "").strip() or None,
        "media_usage_rights": rights, "source_type": str(source_type or "manual")[:40],
        "source_url": str(data.get("source_url") or product_url).strip()[:1000],
        "observed_at": observed_at, "created_at": stamp, "updated_at": stamp,
    }
    columns = list(values)
    placeholders = ",".join("?" for _ in columns)
    updates = []
    for column in columns:
        if column in {"product_id", "created_at"}:
            continue
        if column in {"product_name", "product_url", "currency", "media_usage_rights", "source_type", "observed_at"}:
            updates.append(f"{column}=excluded.{column}")
        else:
            updates.append(f"{column}=COALESCE(excluded.{column}, shopee_products.{column})")
    sql = (f"INSERT INTO shopee_products ({','.join(columns)}) VALUES ({placeholders}) "
           f"ON CONFLICT(product_id) DO UPDATE SET {','.join(updates)}")
    with connect(db_path) as conn:
        conn.execute(sql, list(values.values()))
        conn.execute(
            """INSERT OR IGNORE INTO shopee_product_observations
            (product_id, observed_at, current_price, total_sold, view_count, like_count,
             rating_star, rating_count, base_commission_rate, xtra_commission_rate,
             commission_total_rate, commission_amount, source_type, source_file, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (product_id, observed_at, values["current_price"], values["total_sold"],
             values["view_count"], values["like_count"], values["rating_star"],
             values["rating_count"], values["base_commission_rate"], values["xtra_commission_rate"],
             values["commission_total_rate"], values["commission_amount"], values["source_type"],
             source_file or None, json.dumps(data, ensure_ascii=False, default=str)),
        )
    return get_product(db_path, product_id)


def _observations(db_path: Path, product_id: str, limit: int = 10) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM shopee_product_observations WHERE product_id=? ORDER BY observed_at DESC, id DESC LIMIT ?",
            (product_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_product(db_path: Path, product_id: str) -> dict | None:
    init_intelligence_schema(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM shopee_products WHERE product_id=? OR item_id=? LIMIT 1",
                           (str(product_id), str(product_id))).fetchone()
    if not row:
        return None
    product = dict(row)
    product.update(score_product(product, _observations(db_path, product["product_id"])))
    return product


def list_products(db_path: Path, query: str = "", sort: str = "score", limit: int = 100) -> list[dict]:
    init_intelligence_schema(db_path)
    limit = max(1, min(int(limit), 500))
    sql = "SELECT * FROM shopee_products"
    params = []
    if query.strip():
        sql += " WHERE product_name LIKE ? OR shop_name LIKE ? OR category LIKE ? OR item_id LIKE ?"
        term = f"%{query.strip()}%"
        params.extend([term, term, term, term])
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with connect(db_path) as conn:
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    products = []
    for product in rows:
        product.update(score_product(product, _observations(db_path, product["product_id"])))
        products.append(product)
    if sort == "commission":
        products.sort(key=lambda p: (p["commission_total_rate"], p["win_score"]), reverse=True)
    elif sort == "sales":
        products.sort(key=lambda p: (p.get("daily_sales_velocity") or -1, p.get("total_sold") or 0), reverse=True)
    elif sort == "recent":
        products.sort(key=lambda p: p.get("updated_at") or "", reverse=True)
    else:
        products.sort(key=lambda p: (p["win_score"], p["commission_total_rate"]), reverse=True)
    return products


def summary(db_path: Path) -> dict:
    init_intelligence_schema(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS products,
            SUM(CASE WHEN affiliate_link IS NOT NULL AND affiliate_link!='' THEN 1 ELSE 0 END) AS linked,
            SUM(CASE WHEN COALESCE(commission_total_rate,COALESCE(base_commission_rate,0)+COALESCE(xtra_commission_rate,0))>=10 THEN 1 ELSE 0 END) AS high_commission,
            SUM(CASE WHEN media_usage_rights IN ('owned','licensed','permission','official') THEN 1 ELSE 0 END) AS rights_cleared
            FROM shopee_products"""
        ).fetchone()
    return {key: int(row[key] or 0) for key in ("products", "linked", "high_commission", "rights_cleared")}


def product_to_offer(product: dict) -> dict:
    """Convert catalog record to the existing workflow's product-offer shape."""
    total_rate = (float(product["commission_total_rate"]) if product.get("commission_total_rate") is not None
                  else float(product.get("base_commission_rate") or 0) + float(product.get("xtra_commission_rate") or 0))
    rights = str(product.get("media_usage_rights") or "unknown").lower()
    approved_media = rights in {"owned", "licensed", "permission", "official"}
    return {
        "itemId": str(product.get("item_id") or product.get("product_id") or ""),
        "productName": str(product.get("product_name") or ""),
        "offerLink": str(product.get("product_url") or ""),
        "productLink": str(product.get("product_url") or ""),
        "imageUrl": str(product.get("image_url") or "") if approved_media else "",
        "priceMin": str(product.get("current_price") or ""),
        "priceMax": str(product.get("current_price") or ""),
        "commissionRate": f"{total_rate:g}%" if total_rate else "",
        "sales": product.get("total_sold"),
        "ratingStar": product.get("rating_star"),
        "shopName": product.get("shop_name") or "",
        "category": product.get("category") or "",
    }
