"""Public Chinese hot lists (no login, no signatures, no video download).

Support was measured live on 2026-11-14 (see SOURCES). Fetchers were written for this app,
using the public endpoints that open-source aggregators such as newsnow (MIT, ourongxing)
read. A source that needs a login, a signed request or a CAPTCHA is "manual" only.
"""
import logging
import urllib.parse

import httpx

from . import store

log = logging.getLogger("affiliate_agent.cn_trends")

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}

# platform -> (support, label, note). "auto" = collected; "manual" = paste URL / notes only.
SOURCES = {
    "bilibili": ("auto", "Bilibili", "Video phổ biến: tiêu đề, mục, thời lượng, lượt xem/thích/chia sẻ, ảnh bìa"),
    "douyin": ("auto", "Douyin (抖音)", "Bảng hot search: từ khoá, độ nóng, số video"),
    "weibo": ("auto", "Weibo (微博)", "Hot search thời gian thực: chủ đề, độ nóng"),
    "toutiao": ("auto", "Toutiao (头条)", "Bảng tin nóng: chủ đề, độ nóng"),
    "kuaishou": ("manual", "Kuaishou (快手)", "Bảng hot tải bằng API riêng sau khi mở trang: chỉ nhập tay"),
    "xiaohongshu": ("manual", "Xiaohongshu (小红书)", "Không có danh sách hot công khai (cần đăng nhập/chữ ký): nhập tay"),
    "wechat_channels": ("manual", "WeChat Channels (视频号)", "Không có API công khai: nhập tay"),
    "zhihu": ("off", "Zhihu (知乎)", "API hot list trả 401 (cần đăng nhập): không dùng"),
}
FAILS_BEFORE_PAUSE = 3
PAUSE_HOURS = 6


def _get(client: httpx.Client, url: str, referer: str = "") -> dict:
    resp = client.get(url, headers={**UA, **({"Referer": referer} if referer else {})})
    resp.raise_for_status()
    return resp.json()


def bilibili(client: httpx.Client, limit: int = 50) -> list[dict]:
    data = _get(client, f"https://api.bilibili.com/x/web-interface/popular?ps={min(50, limit)}&pn=1",
                "https://www.bilibili.com/")
    if data.get("code") != 0:
        raise RuntimeError(f"bilibili code {data.get('code')}: {data.get('message')}")
    out = []
    for rank, v in enumerate((data.get("data") or {}).get("list") or [], start=1):
        stat = v.get("stat") or {}
        out.append({
            "platform": "bilibili", "title": v.get("title") or "", "rank": rank,
            "heat": stat.get("view"), "views": stat.get("view"), "likes": stat.get("like"),
            "shares": stat.get("share"), "comments": stat.get("reply"),
            "url": f"https://www.bilibili.com/video/{v.get('bvid')}" if v.get("bvid") else "",
            "cover": v.get("pic") or "",
            "meta": {"category": v.get("tname"), "duration": v.get("duration"), "desc": (v.get("desc") or "")[:400],
                     "author": (v.get("owner") or {}).get("name"), "pubdate": v.get("pubdate"),
                     "favorite": stat.get("favorite"), "coin": stat.get("coin"),
                     "reason": (v.get("rcmd_reason") or {}).get("content")},
        })
    return out


def douyin(client: httpx.Client, limit: int = 50) -> list[dict]:
    data = _get(client, "https://www.douyin.com/aweme/v1/web/hot/search/list/?device_platform=webapp&aid=6383",
                "https://www.douyin.com/")
    words = ((data.get("data") or {}).get("word_list")) or []
    if not words:   # older public board
        words = _get(client, "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/").get("word_list") or []
    out = []
    for w in words[:limit]:
        word = w.get("word") or ""
        out.append({"platform": "douyin", "title": word, "rank": w.get("position"),
                    "heat": w.get("hot_value"), "creators": w.get("video_count"),
                    "url": "https://www.douyin.com/search/" + urllib.parse.quote(word),
                    "meta": {"event_time": w.get("event_time"), "tag": w.get("sentence_tag"),
                             "label": w.get("label")}})
    return out


def weibo(client: httpx.Client, limit: int = 50) -> list[dict]:
    data = _get(client, "https://weibo.com/ajax/side/hotSearch", "https://weibo.com/")
    out = []
    for i, w in enumerate(((data.get("data") or {}).get("realtime")) or [], start=1):
        word = w.get("word") or w.get("note") or ""
        if not word or w.get("is_ad"):
            continue
        out.append({"platform": "weibo", "title": word, "rank": i, "heat": w.get("num"),
                    "url": "https://s.weibo.com/weibo?q=" + urllib.parse.quote(f"#{word}#"),
                    "meta": {"label": w.get("label_name"), "category": w.get("category")}})
        if len(out) >= limit:
            break
    return out


def toutiao(client: httpx.Client, limit: int = 50) -> list[dict]:
    data = _get(client, "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc")
    out = []
    for i, t in enumerate((data.get("data") or [])[:limit], start=1):
        try:
            heat = float(t.get("HotValue") or 0)
        except ValueError:
            heat = None
        out.append({"platform": "toutiao", "title": t.get("Title") or "", "rank": i, "heat": heat,
                    "url": t.get("Url") or "", "meta": {"label": t.get("Label")}})
    return out


FETCHERS = {"bilibili": bilibili, "douyin": douyin, "weibo": weibo, "toutiao": toutiao}


def collect_all(settings, transport=None, fetchers: dict | None = None) -> dict:
    """One pass over every auto source. Never raises; a failing source pauses itself."""
    db = settings.database_path
    store.ensure_schema(db)
    for platform, (support, _, _) in SOURCES.items():
        if support != "auto":
            store.mark_source(db, platform, support, ok=False, error="chưa hỗ trợ tự động")
    state = store.source_state(db)
    summary, enabled = {}, set(str(getattr(settings, "cn_trend_sources", "") or "").split(",")) - {""}
    stamp = store.now_iso()
    with httpx.Client(transport=transport, timeout=20, follow_redirects=True) as client:
        for platform, fetch in (fetchers or FETCHERS).items():
            if enabled and platform not in enabled:
                continue
            paused = (state.get(platform) or {}).get("paused_until")
            if paused and (store.hours_since(paused) or 0) < 0:
                summary[platform] = "tạm dừng do lỗi liên tục"
                continue
            try:
                signals = [s for s in fetch(client) if s.get("title")]
                for s in signals:
                    store.record(db, s, observed_at=stamp)
                store.mark_source(db, platform, "auto", ok=True, items=len(signals))
                summary[platform] = len(signals)
            except Exception as exc:  # noqa: BLE001 - one bad source never stops the others
                streak = ((state.get(platform) or {}).get("fail_streak") or 0) + 1
                store.mark_source(db, platform, "auto", ok=False, error=str(exc),
                                  pause_hours=PAUSE_HOURS if streak >= FAILS_BEFORE_PAUSE else 0)
                summary[platform] = f"lỗi: {str(exc)[:120]}"
                log.warning("cn trend source %s failed: %s", platform, exc)
    return summary


def add_manual(settings, platform: str, title: str, url: str = "", notes: str = "",
               reference: str = "") -> str:
    """Owner-entered trend (Kuaishou, Xiaohongshu, WeChat ... or anything seen by hand)."""
    if platform not in SOURCES:
        raise ValueError("Nền tảng không hợp lệ")
    title = " ".join(str(title or "").split())
    if not title:
        raise ValueError("Nhập tên/chủ đề của trend")
    if url and not url.startswith(("http://", "https://")):
        raise ValueError("Link phải bắt đầu bằng http(s)://")
    return store.record(settings.database_path, {
        "platform": platform, "title": title, "url": url, "source_kind": "manual",
        "meta": {"notes": notes[:2000], "reference": reference, "entered_by": "owner"}})
