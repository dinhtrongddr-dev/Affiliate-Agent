"""Product catalog: hot / high-commission products with ready affiliate links.

Data comes from official exports the user downloads themselves:
  * Shopee Affiliate -> Hoa hồng Sản phẩm -> "Lấy link hàng loạt" CSV
    (Mã sản phẩm, Tên sản phẩm, Giá, Doanh thu, Tên cửa hàng, Tỉ lệ hoa hồng,
     Hoa hồng, Link sản phẩm, Link ưu đãi)
  * any CSV with similar columns (TikTok Shop showcase exports, spreadsheets)
No scraping: the app never logs into or crawls Shopee/TikTok.
"""
import csv
import io
import re
import unicodedata
from pathlib import Path
from typing import Any

from .db import connect, init_db, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'shopee',
    item_id TEXT NOT NULL,
    name TEXT NOT NULL,
    price REAL,
    price_text TEXT,
    sold INTEGER,
    revenue REAL,
    shop_name TEXT,
    commission_rate REAL,
    commission REAL,
    product_link TEXT,
    affiliate_link TEXT,
    image_url TEXT,
    tiktok_link TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'NEW',
    job_id TEXT,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
"""

# normalised header -> field
HEADER_MAP = {
    "ma san pham": "item_id", "item id": "item_id", "itemid": "item_id", "product id": "item_id",
    "id san pham": "item_id",
    "ten san pham": "name", "product name": "name", "productname": "name", "ten": "name",
    "gia": "price", "price": "price", "gia ban": "price",
    "doanh thu": "revenue", "so luong da ban": "sold", "luot ban": "sold",
    "da ban": "sold", "sold": "sold", "sales": "sold",
    "ten cua hang": "shop_name", "shop name": "shop_name", "cua hang": "shop_name",
    "ti le hoa hong": "commission_rate", "ty le hoa hong": "commission_rate",
    "commission rate": "commission_rate",
    "hoa hong": "commission", "commission": "commission",
    "link san pham": "product_link", "product link": "product_link", "productlink": "product_link",
    "link uu dai": "affiliate_link", "affiliate link": "affiliate_link", "offer link": "affiliate_link",
    "link affiliate": "affiliate_link", "short link": "affiliate_link",
    "link anh": "image_url", "image": "image_url", "image url": "image_url", "anh": "image_url",
    "link tiktok": "tiktok_link", "tiktok link": "tiktok_link",
}


class CatalogError(RuntimeError):
    pass


_ready: set[str] = set()


def ensure_schema(db_path) -> None:
    key = str(Path(db_path).resolve())
    if key in _ready and Path(db_path).exists():
        return      # once per process: pages read many products and must not re-run the DDL
    init_db(db_path)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        if "revenue" not in columns:
            conn.execute("ALTER TABLE products ADD COLUMN revenue REAL")
    _ready.add(key)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or "").strip().lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"[^a-z0-9 ]+", " ", text).strip()


def _number(text: Any) -> float | None:
    """'49,0k' -> 49000, '₫3.430' -> 3430, '7%' -> 7, '1,2tr' -> 1200000."""
    raw = str(text or "").strip().lower().replace("₫", "").replace("đ", "").replace(" ", "")
    if not raw:
        return None
    mult = 1.0
    if raw.endswith("k"):
        mult, raw = 1_000, raw[:-1]
    elif raw.endswith(("tr", "m")):
        mult, raw = 1_000_000, raw.rstrip("trm")
    raw = raw.rstrip("%+")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".") if mult > 1 or len(raw.split(",")[-1]) != 3 else raw.replace(",", "")
    elif raw.count(".") == 1 and len(raw.split(".")[-1]) == 3 and mult == 1:
        raw = raw.replace(".", "")
    else:
        raw = raw.replace(".", "") if raw.count(".") > 1 else raw
    try:
        return float(raw) * mult
    except ValueError:
        return None


def _platform_for(link: str) -> str:
    return "tiktok" if "tiktok" in (link or "").lower() else "shopee"


def parse_csv(data: bytes) -> list[dict]:
    text = None
    for encoding in ("utf-8-sig", "utf-16", "cp1258", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise CatalogError("Không đọc được file CSV")
    sample = text[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        raise CatalogError("File trống")
    header = [HEADER_MAP.get(_norm(h)) for h in rows[0]]
    if "name" not in header or not ({"affiliate_link", "product_link"} & set(header)):
        raise CatalogError("Không nhận ra cột: cần ít nhất 'Tên sản phẩm' và 'Link sản phẩm'/'Link ưu đãi'")
    products = []
    for row in rows[1:]:
        record = {field: (row[i].strip() if i < len(row) else "")
                  for i, field in enumerate(header) if field}
        if not record.get("name"):
            continue
        link = record.get("product_link") or record.get("affiliate_link") or ""
        item_id = record.get("item_id") or ""
        if not item_id:
            match = re.search(r"/product/\d+/(\d+)|i\.\d+\.(\d+)|/product/(\d+)", link)
            item_id = next((g for g in (match.groups() if match else []) if g), "") or \
                str(abs(hash(record["name"])) % 10**12)
        products.append({
            "platform": _platform_for(link),
            "item_id": item_id,
            "name": record["name"][:300],
            "price": _number(record.get("price")),
            "price_text": record.get("price", "")[:40],
            "sold": int(_number(record.get("sold")) or 0) or None,
            "revenue": _number(record.get("revenue")),
            "shop_name": record.get("shop_name", "")[:120],
            "commission_rate": _number(record.get("commission_rate")),
            "commission": _number(record.get("commission")),
            "product_link": record.get("product_link", ""),
            "affiliate_link": record.get("affiliate_link", ""),
            "image_url": record.get("image_url", ""),
            "tiktok_link": record.get("tiktok_link", ""),
        })
    _fix_sales_column(products)
    return products


def _fix_sales_column(products: list[dict]) -> None:
    """Shopee's bulk-link export labels the units-sold count "Doanh thu". Real exports
    (2026-11-12, 300 rows) hold 0-982 for products priced 14,500-1,290,000 VND, so the
    values cannot be money. If no row's value reaches its price, treat the column as units sold."""
    rows = [p for p in products if p.get("revenue") is not None]
    if not rows or any(p.get("price") and p["revenue"] >= p["price"] for p in rows):
        return
    for p in rows:
        if not p.get("sold"):
            p["sold"] = int(p["revenue"]) or None
        p["revenue"] = None


def import_products(db_path, products: list[dict], *, record_observations: bool = True) -> dict:
    ensure_schema(db_path)
    stamp = now_iso()
    inserted = updated = 0
    with connect(db_path) as conn:
        for p in products:
            pid = f"{p['platform']}:{p['item_id']}"
            exists = conn.execute("SELECT 1 FROM products WHERE id=?", (pid,)).fetchone()
            conn.execute(
                """
                INSERT INTO products (id, platform, item_id, name, price, price_text, sold, revenue,
                    shop_name, commission_rate, commission, product_link, affiliate_link,
                    image_url, tiktok_link, imported_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, price=excluded.price, price_text=excluded.price_text,
                    sold=COALESCE(excluded.sold, products.sold),
                    revenue=COALESCE(excluded.revenue, products.revenue),
                    shop_name=excluded.shop_name,
                    commission_rate=COALESCE(excluded.commission_rate, products.commission_rate),
                    commission=COALESCE(excluded.commission, products.commission),
                    product_link=COALESCE(NULLIF(excluded.product_link,''), products.product_link),
                    affiliate_link=COALESCE(NULLIF(excluded.affiliate_link,''), products.affiliate_link),
                    image_url=COALESCE(NULLIF(excluded.image_url,''), products.image_url),
                    tiktok_link=COALESCE(NULLIF(excluded.tiktok_link,''), products.tiktok_link),
                    updated_at=excluded.updated_at
                """,
                (pid, p["platform"], p["item_id"], p["name"], p["price"], p["price_text"],
                 p["sold"], p.get("revenue"), p["shop_name"], p["commission_rate"], p["commission"],
                 p["product_link"], p["affiliate_link"], p["image_url"], p["tiktok_link"],
                 stamp, stamp),
            )
            if exists:
                updated += 1
            else:
                inserted += 1
    if not record_observations:
        return {"inserted": inserted, "updated": updated, "total": len(products),
                "intelligence_snapshots": 0}
    # Keep a timestamped intelligence observation alongside the existing video catalog.
    from .shopee_intelligence.catalog import save_product as save_intel_product
    intel_saved = 0
    for p in products:
        if p.get("platform") != "shopee" or not p.get("product_link"):
            continue
        try:
            save_intel_product(db_path, {
                "product_name": p["name"],
                "product_url": p["product_link"],
                "item_id": p["item_id"],
                "shop_name": p.get("shop_name"),
                "current_price": p.get("price"),
                "total_sold": p.get("sold"),
                "commission_total_rate": p.get("commission_rate"),
                "commission_amount": p.get("commission"),
                "affiliate_link": p.get("affiliate_link"),
                "image_url": p.get("image_url"),
                "media_usage_rights": "unknown",
                "source_url": p["product_link"],
            }, source_type="affiliate_bulk_csv")
            intel_saved += 1
        except ValueError:
            continue
    return {"inserted": inserted, "updated": updated, "total": len(products),
            "intelligence_snapshots": intel_saved}


SORTS = {
    # "Doanh thu" is revenue; revenue / price approximates units when no sold column exists.
    "score": "(COALESCE(commission,0) * (1 + ln(1 + COALESCE(sold, revenue / NULLIF(price, 0), 0)))) DESC",
    "commission": "COALESCE(commission,0) DESC",
    "rate": "COALESCE(commission_rate,0) DESC",
    "sold": "COALESCE(sold,0) DESC",
    "new": "imported_at DESC",
}


def list_products(db_path, sort: str = "score", q: str = "", status: str = "",
                  limit: int = 300) -> list[dict]:
    ensure_schema(db_path)
    where, args = [], []
    if q:
        where.append("name LIKE ?")
        args.append(f"%{q}%")
    if status:
        where.append("status=?")
        args.append(status)
    sql = "SELECT * FROM products"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {SORTS.get(sort, SORTS['score'])} LIMIT ?"
    with connect(db_path) as conn:
        conn.create_function("ln", 1, _ln)
        return [dict(r) for r in conn.execute(sql, [*args, int(limit)]).fetchall()]


def _ln(x):
    import math
    return math.log(x) if x and x > 0 else 0.0


def get_product(db_path, product_id: str) -> dict | None:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return dict(row) if row else None


def get_products(db_path, ids: list[str]) -> dict[str, dict]:
    """Many products in one query (id -> row)."""
    ids = list(dict.fromkeys(str(i) for i in ids if i))
    if not ids:
        return {}
    ensure_schema(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM products WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {str(r["id"]): dict(r) for r in rows}


def update_product(db_path, product_id: str, **fields) -> None:
    allowed = {"image_url", "tiktok_link", "notes", "status", "job_id", "affiliate_link"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    updates["updated_at"] = now_iso()
    clause = ", ".join(f"{k}=?" for k in updates)
    with connect(db_path) as conn:
        conn.execute(f"UPDATE products SET {clause} WHERE id=?", [*updates.values(), product_id])


def delete_product(db_path, product_id: str) -> None:
    with connect(db_path) as conn:
        conn.execute("DELETE FROM products WHERE id=?", (product_id,))


def as_offer(product: dict) -> dict:
    """Catalog row -> the offer dict the pipeline understands."""
    return {
        "itemId": product["item_id"], "productName": product["name"],
        "priceMin": product.get("price_text") or product.get("price"),
        "commissionRate": (f"{product['commission_rate']:g}%"
                           if product.get("commission_rate") is not None else None),
        "sales": product.get("sold"), "shopName": product.get("shop_name"),
        "offerLink": product.get("product_link") or "",
        "imageUrl": product.get("image_url") or "",
        "affiliateLink": product.get("affiliate_link") or "",
        "tiktokLink": product.get("tiktok_link") or "",
        "catalogId": product["id"],
    }
