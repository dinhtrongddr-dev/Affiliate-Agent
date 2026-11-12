"""CSV import for Shopee product records copied/exported by the account owner."""

import csv
import hashlib
import io
import json
import re
import unicodedata

from ..db import connect, now_iso
from .catalog import save_product

ALIASES = {
    "product_name": ("product_name", "productname", "name", "title", "ten_san_pham"),
    "product_url": ("product_url", "product_link", "productlink", "url", "item_url",
                    "original_url", "offer_link", "offerlink", "link_san_pham"),
    "item_id": ("item_id", "itemid", "product_id", "productid", "ma_san_pham"),
    "shop_id": ("shop_id", "shopid"),
    "shop_name": ("shop_name", "shopname", "seller_name", "seller", "ten_cua_hang"),
    "category": ("category", "category_name", "nganh_hang", "danh_muc"),
    "current_price": ("current_price", "price", "price_min", "pricemin", "sale_price",
                      "price_current", "gia", "gia_ban"),
    "original_price": ("original_price", "originalprice", "price_original", "priceoriginal", "gia_goc"),
    "total_sold": ("total_sold", "totalsold", "sales", "sold", "sold_count", "da_ban", "luot_ban"),
    "rating_star": ("rating_star", "ratingstar", "rating", "rating_score", "stars"),
    "rating_count": ("rating_count", "ratingcount", "reviews", "review_count", "so_danh_gia"),
    "view_count": ("view_count", "viewcount", "views", "luot_xem"),
    "like_count": ("like_count", "likecount", "likes", "luot_thich"),
    "image_url": ("image_url", "imageurl", "image", "image_link", "product_image", "link_anh"),
    "video_url": ("video_url", "videourl", "video_link", "product_video"),
    "base_commission_rate": ("base_commission_rate", "base_rate", "hoa_hong_shopee"),
    "xtra_commission_rate": ("xtra_commission_rate", "xtra_rate", "xtracommissionrate", "hoa_hong_xtra"),
    "commission_total_rate": ("commission_total_rate", "commission_rate", "commissionrate",
                              "ti_le_hoa_hong", "ty_le_hoa_hong"),
    "commission_amount": ("commission_amount", "commission", "hoa_hong"),
    "affiliate_link": ("affiliate_link", "affiliate_url", "custom_link", "short_link", "shortlink", "link_uu_dai"),
    "offer_start_at": ("offer_start_at", "offer_start", "start_date", "ngay_bat_dau"),
    "offer_end_at": ("offer_end_at", "offer_end", "end_date", "ngay_ket_thuc"),
    "media_usage_rights": ("media_usage_rights", "rights_status", "media_rights"),
    "source_url": ("source_url",),
}


def _header(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _parse_number(value, integer=False):
    if value is None or str(value).strip() == "":
        return None
    raw = re.sub(r"[^0-9,.\-]", "", str(value).strip())
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        tail = raw.rsplit(",", 1)[-1]
        raw = raw.replace(",", "") if len(tail) == 3 else raw.replace(",", ".")
    elif "." in raw:
        tail = raw.rsplit(".", 1)[-1]
        raw = raw.replace(".", "") if len(tail) == 3 and integer else raw
    try:
        number = float(raw)
        return int(number) if integer else number
    except ValueError:
        return None


def _parse_rate(value):
    number = _parse_number(value)
    if number is None:
        return None
    if 0 < number <= 1 and "%" not in str(value):
        number *= 100
    return number


def _pick(row, field):
    for alias in ALIASES[field]:
        if alias in row and str(row[alias]).strip():
            return row[alias]
    return None


def template_csv() -> str:
    fields = [
        "product_name", "product_url", "item_id", "shop_id", "shop_name", "category",
        "current_price", "original_price", "total_sold", "rating_star", "rating_count",
        "view_count", "like_count", "base_commission_rate", "xtra_commission_rate",
        "commission_total_rate", "commission_amount", "affiliate_link", "offer_start_at",
        "offer_end_at", "image_url", "video_url",
        "media_usage_rights", "source_url",
    ]
    return ",".join(fields) + "\n"


def import_products_csv(db_path, content: bytes, source_name: str = "dashboard.csv") -> dict:
    digest = hashlib.sha256(content).hexdigest()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1258", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows_read = imported = skipped = 0
    errors = []
    for line_number, raw in enumerate(reader, start=2):
        rows_read += 1
        row = {_header(key): value for key, value in (raw or {}).items() if key}
        name, product_url = _pick(row, "product_name"), _pick(row, "product_url")
        if not name or not product_url:
            skipped += 1
            errors.append(f"Dòng {line_number}: thiếu product_name hoặc product_url.")
            continue
        data = {
            "product_name": name, "product_url": product_url,
            "item_id": _pick(row, "item_id"), "shop_id": _pick(row, "shop_id"),
            "shop_name": _pick(row, "shop_name"), "category": _pick(row, "category"),
            "current_price": _parse_number(_pick(row, "current_price"), integer=True),
            "original_price": _parse_number(_pick(row, "original_price"), integer=True),
            "total_sold": _parse_number(_pick(row, "total_sold"), integer=True),
            "rating_star": _parse_number(_pick(row, "rating_star")),
            "rating_count": _parse_number(_pick(row, "rating_count"), integer=True),
            "view_count": _parse_number(_pick(row, "view_count"), integer=True),
            "like_count": _parse_number(_pick(row, "like_count"), integer=True),
            "base_commission_rate": _parse_rate(_pick(row, "base_commission_rate")),
            "xtra_commission_rate": _parse_rate(_pick(row, "xtra_commission_rate")),
            "commission_total_rate": _parse_rate(_pick(row, "commission_total_rate")),
            "commission_amount": _parse_number(_pick(row, "commission_amount"), integer=True),
            "affiliate_link": _pick(row, "affiliate_link"),
            "offer_start_at": _pick(row, "offer_start_at"), "offer_end_at": _pick(row, "offer_end_at"),
            "image_url": _pick(row, "image_url"), "video_url": _pick(row, "video_url"),
            "media_usage_rights": _pick(row, "media_usage_rights") or "unknown",
            "source_url": _pick(row, "source_url") or product_url,
        }
        try:
            save_product(db_path, data, source_type="dashboard_csv", source_file=source_name)
            imported += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"Dòng {line_number}: {str(exc)[:240]}")
    with connect(db_path) as conn:
        conn.execute(
            """INSERT INTO shopee_intel_imports
            (source_file, sha256, rows_read, rows_imported, rows_skipped, errors_json, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (source_name[:255], digest, rows_read, imported, skipped,
             json.dumps(errors[:100], ensure_ascii=False), now_iso()),
        )
    return {"source_file": source_name, "sha256": digest, "rows_read": rows_read,
            "imported": imported, "skipped": skipped, "errors": errors[:100]}
