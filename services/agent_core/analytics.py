"""Shopee conversion report -> per-video revenue (sub_id = job id)."""
import time

from .affiliate_client import ShopeeAffiliateClient
from .db import connect, init_db, upsert_conversions


def _known_job_ids(db_path) -> list[str]:
    init_db(db_path)
    with connect(db_path) as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()]


def sync_conversions(settings, days: int = 30) -> dict:
    end = int(time.time())
    start = end - days * 86400
    nodes = ShopeeAffiliateClient(settings).conversion_report(start, end)
    job_ids = _known_job_ids(settings.database_path)
    rows = []
    for node in nodes:
        blob = str(node)
        job_id = next((j for j in job_ids if j in blob), None)
        orders = node.get("orders") or []
        order_id = node.get("orderId") or (orders[0].get("orderId") if orders else None)
        rows.append({
            "conversion_id": node.get("conversionId"),
            "order_id": order_id,
            "job_id": job_id,
            "total_commission": node.get("totalCommission"),
            "purchase_time": node.get("purchaseTime"),
            "raw": node,
        })
    saved = upsert_conversions(settings.database_path, rows)
    return {"fetched": len(nodes), "saved": saved,
            "attributed": sum(1 for r in rows if r["job_id"])}
