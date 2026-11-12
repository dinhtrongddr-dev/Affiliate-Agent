"""Trend collectors. Only public feeds, official APIs, or keys the user configured:

- google_trends : Google Trends daily RSS, geo=VN (public feed, no key)
- google_news   : Google News RSS search, hl=vi gl=VN (public feed, no key)
- youtube       : YouTube Data API v3 search + statistics, regionCode=VN (user's free key)
- trendsmcp     : trendsmcp.ai REST, TikTok Trending Hashtags VN (user's key, monthly budget)
- tiktok_manual : hashtags the user copies from TikTok Creative Center

No scraping of TikTok/Shopee/X/Instagram and no undocumented endpoints.
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from . import store

log = logging.getLogger("affiliate_agent.trend_intel")

UA = {"User-Agent": "Mozilla/5.0 (affiliate-agent trend reader)"}
GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo=VN"
HT_NS = "https://trends.google.com/trending/rss"
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
TRENDSMCP_API = "https://api.trendsmcp.ai/api"


class CollectorError(RuntimeError):
    pass


def parse_count(text) -> float | None:
    """'200000+', '2K+', '1,2tr', '3.5M', '12.400' -> number."""
    if text is None:
        return None
    raw = str(text).strip().lower().replace("+", "").replace(" ", "")
    if not raw:
        return None
    mult = 1.0
    for suffix, factor in (("tỷ", 1e9), ("ty", 1e9), ("tr", 1e6), ("m", 1e6), ("k", 1e3),
                           ("n", 1e3), ("b", 1e9)):
        if raw.endswith(suffix):
            mult, raw = factor, raw[: -len(suffix)]
            break
    if mult > 1:
        raw = raw.replace(",", ".")
    else:
        raw = raw.replace(",", "").replace(".", "") if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw) \
            else raw.replace(",", ".")
    try:
        return float(raw) * mult
    except ValueError:
        return None


# ------------------------------------------------------------------ Google Trends

def google_trends(settings, transport=None) -> list[dict]:
    with httpx.Client(transport=transport, timeout=30, follow_redirects=True, headers=UA) as c:
        resp = c.get(GOOGLE_TRENDS_RSS)
        resp.raise_for_status()
    root = ET.fromstring(resp.content)
    signals = []
    for rank, item in enumerate(root.iter("item"), start=1):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        news = [{"title": n.findtext(f"{{{HT_NS}}}news_item_title") or "",
                 "url": n.findtext(f"{{{HT_NS}}}news_item_url") or "",
                 "source": n.findtext(f"{{{HT_NS}}}news_item_source") or ""}
                for n in item.findall(f"{{{HT_NS}}}news_item")]
        traffic = item.findtext(f"{{{HT_NS}}}approx_traffic") or ""
        signals.append({
            "source": "google_trends", "keyword": title, "rank": rank,
            "value": parse_count(traffic), "url": news[0]["url"] if news else "",
            "notes": news[0]["title"] if news else "",
            "meta": {"traffic": traffic, "geo": "VN", "news": news[:3],
                     "pub_date": item.findtext("pubDate") or ""},
        })
    return signals


# ------------------------------------------------------------------ Google News

def _pub(item) -> datetime | None:
    try:
        value = parsedate_to_datetime(item.findtext("pubDate") or "")
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def google_news(settings, keyword: str, transport=None, now: datetime | None = None) -> dict:
    """Article counts for a keyword in the last 24h / 7d / previous 7d (Vietnamese edition).
    The feed returns at most ~100 items ranked by relevance, so counts are lower bounds."""
    params = {"q": keyword, "hl": "vi", "gl": "VN", "ceid": "VN:vi"}
    with httpx.Client(transport=transport, timeout=30, follow_redirects=True, headers=UA) as c:
        resp = c.get(GOOGLE_NEWS_RSS, params=params)
        resp.raise_for_status()
    root = ET.fromstring(resp.content)
    now = now or datetime.now(timezone.utc)
    last24 = last7 = prev7 = 0
    headlines = []
    items = list(root.iter("item"))
    for item in items:
        published = _pub(item)
        if not published:
            continue
        age = now - published
        if age <= timedelta(days=1):
            last24 += 1
        if age <= timedelta(days=7):
            last7 += 1
            if len(headlines) < 3:
                headlines.append((item.findtext("title") or "")[:160])
        elif age <= timedelta(days=14):
            prev7 += 1
    return {
        "source": "google_news", "keyword": keyword, "value": float(last7),
        "meta": {"last24h": last24, "last7d": last7, "prev7d": prev7, "feed_items": len(items),
                 "capped": len(items) >= 95, "headlines": headlines, "geo": "VN"},
    }


# ------------------------------------------------------------------ YouTube Data API

def youtube(settings, keyword: str, transport=None, now: datetime | None = None) -> dict:
    """Recent (7 day) Vietnamese videos for a keyword, with real view/like/comment counts.
    Costs 101 quota units (search 100 + videos 1)."""
    key = settings.youtube_api_key.get_secret_value()
    if not key:
        raise CollectorError("Chưa có AA_YOUTUBE_API_KEY")
    now = now or datetime.now(timezone.utc)
    after = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with httpx.Client(transport=transport, timeout=30) as c:
        found = c.get(f"{YOUTUBE_API}/search", params={
            "part": "snippet", "q": keyword, "type": "video", "regionCode": "VN",
            "relevanceLanguage": "vi", "publishedAfter": after, "order": "viewCount",
            "maxResults": 10, "key": key})
        _youtube_raise(found)
        ids = [i["id"]["videoId"] for i in found.json().get("items", []) if i.get("id", {}).get("videoId")]
        stats = []
        if ids:
            detail = c.get(f"{YOUTUBE_API}/videos", params={
                "part": "statistics,snippet", "id": ",".join(ids), "key": key})
            _youtube_raise(detail)
            stats = detail.json().get("items", [])
    views = likes = comments = 0
    top = []
    for v in stats:
        s = v.get("statistics", {})
        views += int(s.get("viewCount") or 0)
        likes += int(s.get("likeCount") or 0)
        comments += int(s.get("commentCount") or 0)
        top.append({"title": v.get("snippet", {}).get("title", "")[:120],
                    "views": int(s.get("viewCount") or 0), "id": v.get("id")})
    top.sort(key=lambda x: x["views"], reverse=True)
    return {
        "source": "youtube", "keyword": keyword, "value": float(views),
        "url": f"https://www.youtube.com/watch?v={top[0]['id']}" if top else "",
        "meta": {"videos": len(stats), "views": views, "likes": likes, "comments": comments,
                 "engagement_rate": round((likes + comments) / views, 5) if views else None,
                 "top": top[:3], "window_days": 7, "geo": "VN"},
    }


def _youtube_raise(resp) -> None:
    if resp.status_code >= 400:
        try:
            message = resp.json()["error"]["message"]
        except Exception:  # noqa: BLE001
            message = resp.text[:200]
        raise CollectorError(f"YouTube API {resp.status_code}: {message}")


# ------------------------------------------------------------------ Trends MCP (REST)

def _rows(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for name in ("data", "results", "items", "trends", "rows", "board"):
            value = payload.get(name)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _rows(value)
                if nested:
                    return nested
    return []


def trendsmcp_board(settings, board: str = "TikTok Trending Hashtags", country: str = "VN",
                    limit: int = 10, transport=None) -> list[dict]:
    key = settings.trendsmcp_api_key.get_secret_value()
    if not key:
        raise CollectorError("Chưa có AA_TRENDSMCP_API_KEY")
    with httpx.Client(transport=transport, timeout=60) as c:
        resp = c.post(TRENDSMCP_API, headers={"Authorization": f"Bearer {key}"},
                      json={"mode": "get_top_trends", "type": board, "country": country,
                            "limit": limit})
    if resp.status_code >= 400:
        raise CollectorError(f"Trends MCP {resp.status_code}: {resp.text[:200]}")
    signals = []
    for position, row in enumerate(_rows(resp.json()), start=1):
        if isinstance(row, str):
            row = {"name": row}
        if not isinstance(row, dict):
            continue
        name = next((str(row[k]) for k in ("keyword", "name", "hashtag", "title", "term", "query")
                     if row.get(k)), "")
        if not name:
            continue
        value = next((row[k] for k in ("value", "volume", "views", "score", "posts", "count")
                      if isinstance(row.get(k), (int, float))), None)
        signals.append({
            "source": "trendsmcp_tiktok" if "TikTok" in board else "trendsmcp",
            "keyword": name.lstrip("#"), "rank": int(row.get("rank") or position),
            "value": float(value) if value is not None else None,
            "url": str(row.get("url") or ""),
            "meta": {"board": board, "country": country,
                     "raw": {k: row[k] for k in list(row)[:12]}},
        })
    return signals


# ------------------------------------------------------------------ manual paste

def parse_manual(text: str, source: str = "tiktok_manual") -> list[dict]:
    """One trend per line: `#hashtag | 1,2M | https://...` (views and link optional)."""
    signals = []
    for rank, line in enumerate((text or "").splitlines(), start=1):
        parts = [p.strip() for p in re.split(r"[|\t;]", line) if p.strip()]
        if not parts:
            continue
        keyword = parts[0].lstrip("#").strip()
        if not keyword or len(keyword) > 120:
            continue
        value, url = None, ""
        for part in parts[1:]:
            if part.startswith("https://"):
                url = part
            elif value is None:
                value = parse_count(re.sub(r"(lượt xem|views|bài đăng|posts|video)", "", part,
                                           flags=re.I))
        signals.append({"source": source, "keyword": keyword, "rank": rank, "value": value,
                        "url": url, "meta": {"entered_by": "user", "geo": "VN"}})
    return signals[:100]


# ------------------------------------------------------------------ orchestration

def keywords_to_probe(settings, limit: int, source: str, min_hours: float = 6.0) -> list[str]:
    """Most promising, non-blocked topics not probed by `source` in the last `min_hours`,
    plus the user's seed keywords."""
    picked, seen = [], set()
    candidates = [t["keyword"] for t in store.list_topics(settings.database_path, 300,
                                                          include_blocked=False,
                                                          since_hours=72)]
    candidates += list(settings.seed_keywords)
    for keyword in candidates:
        key = store.topic_key(keyword)
        if not key or key in seen:
            continue
        seen.add(key)
        latest = store.observations(settings.database_path, key, source, 1)
        if latest and (store.hours_since(latest[0]["observed_at"]) or 0) < min_hours:
            continue
        picked.append(keyword)
        if len(picked) >= limit:
            break
    return picked


def collect_all(settings) -> dict:
    """Run every enabled collector once and store observations. Never raises."""
    db_path = settings.database_path
    summary: dict = {}
    errors: list[str] = []

    def save(signals: list[dict]) -> int:
        return sum(1 for s in signals if store.record_signal(db_path, s))

    try:
        summary["google_trends"] = save(google_trends(settings))
    except Exception as exc:  # noqa: BLE001
        errors.append(f"google_trends: {exc}")

    if settings.trendsmcp_api_key.get_secret_value():
        boards = [b.strip() for b in settings.trendsmcp_boards.split(",") if b.strip()]
        count = 0
        for board in boards[: settings.trendsmcp_calls_per_run]:
            if not store.consume(db_path, "trendsmcp", settings.trendsmcp_monthly_budget):
                errors.append("trendsmcp: hết ngân sách request tháng này")
                break
            try:
                count += save(trendsmcp_board(settings, board))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"trendsmcp {board}: {exc}")
        summary["trendsmcp"] = count

    news = 0
    for keyword in keywords_to_probe(settings, settings.trend_news_keywords_per_run, "google_news"):
        try:
            news += save([google_news(settings, keyword)])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"google_news {keyword!r}: {exc}")
    summary["google_news"] = news

    if settings.youtube_api_key.get_secret_value():
        yt = 0
        for keyword in keywords_to_probe(settings, settings.trend_youtube_searches_per_run,
                                         "youtube", min_hours=12):
            if not store.consume(db_path, "youtube_units", settings.youtube_daily_units,
                                 kind="day", calls=101):
                errors.append("youtube: hết quota ngày đã đặt")
                break
            try:
                yt += save([youtube(settings, keyword)])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"youtube {keyword!r}: {exc}")
                if "quota" in str(exc).lower() or "403" in str(exc):
                    break
        summary["youtube"] = yt

    for message in errors:
        log.warning("trend collector: %s", message)
    summary["errors"] = errors
    return summary
