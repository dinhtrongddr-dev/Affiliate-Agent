"""Trend signals. We collect *signals* (topics, hashtags, best-selling products),
never other creators' videos: videos are always produced fresh by the pipeline.

Sources:
  - Google Trends daily RSS for Vietnam (public feed)
  - Shopee Affiliate API best sellers (official API)
  - Manual entries: paste a TikTok/Facebook trend link + notes from Creative Center etc.
"""
import logging
import xml.etree.ElementTree as ET

import httpx

from .affiliate_client import ShopeeAffiliateClient
from .db import upsert_trend

log = logging.getLogger("affiliate_agent.trends")

GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=VN"
HT_NS = "https://trends.google.com/trending/rss"


def fetch_google_trends(settings, limit: int = 30) -> int:
    resp = httpx.get(GOOGLE_TRENDS_RSS, timeout=30, follow_redirects=True,
                     headers={"User-Agent": "Mozilla/5.0 (affiliate-agent trend reader)"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    count = 0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        traffic = item.findtext(f"{{{HT_NS}}}approx_traffic") or ""
        news = item.find(f"{{{HT_NS}}}news_item")
        url = news.findtext(f"{{{HT_NS}}}news_item_url") if news is not None else ""
        notes = news.findtext(f"{{{HT_NS}}}news_item_title") if news is not None else ""
        upsert_trend(settings.database_path, "google_trends", title, url or "", traffic,
                     notes or "")
        count += 1
        if count >= limit:
            break
    return count


def shopee_best_sellers(settings, keywords: list[str] | None = None, per_keyword: int = 8
                        ) -> list[dict]:
    """Best-selling offers for each seed keyword (deduplicated)."""
    client = ShopeeAffiliateClient(settings)
    seen, results = set(), []
    for keyword in keywords or settings.seed_keywords:
        try:
            offers = client.hot_offers(keyword, limit=per_keyword)
        except Exception as exc:  # noqa: BLE001
            log.warning("Shopee best sellers failed for %r: %s", keyword, exc)
            continue
        for offer in offers:
            item_id = str(offer.get("itemId") or "")
            if item_id and item_id not in seen:
                seen.add(item_id)
                results.append({**offer, "seed_keyword": keyword})
    return results


def add_manual_trend(settings, title: str, url: str = "", notes: str = "",
                     keywords: list[str] | None = None, source: str = "manual") -> None:
    if not title.strip():
        raise ValueError("Trend title is required")
    upsert_trend(settings.database_path, source, title, url, "", notes, keywords)
