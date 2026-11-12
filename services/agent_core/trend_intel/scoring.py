"""Trend scoring: eight 0-100 component scores.

- Trend score  = weighted average of the seven signal components (growth, engagement,
  acceleration, cross-platform, VN relevance, freshness, data confidence).
- Opportunity  = Trend score x (0.3 + 0.7 x Affiliate potential / 100); while the affiliate
  potential is still unknown the factor is 0.65 (neutral). Topics are ranked by Opportunity,
  so a huge news story nobody buys anything for cannot outrank a smaller shopping trend.

Every component is derived from stored evidence; a component without data is None and is
left out of the average (and the topic is marked PROVISIONAL). Scores rank topics for the
user - they are not forecasts of views or revenue."""
import json
import math
import re

from . import analyzer

DEFAULT_WEIGHTS = {
    "growth": 0.30, "engagement": 0.15, "acceleration": 0.15, "cross_platform": 0.10,
    "vn_relevance": 0.10, "freshness": 0.12, "confidence": 0.08,
}
UNKNOWN_AFFILIATE_FACTOR = 0.65
LABELS = {
    "growth": "Tăng trưởng", "engagement": "Tương tác", "acceleration": "Gia tốc",
    "cross_platform": "Đa nền tảng", "vn_relevance": "Hợp thị trường VN",
    "affiliate": "Tiềm năng affiliate", "freshness": "Độ mới", "confidence": "Độ tin cậy dữ liệu",
}
VN_SOURCES = {"google_trends", "google_news", "youtube", "trendsmcp_tiktok", "tiktok_manual",
              "manual"}
_VI_CHARS = re.compile(r"[ăâđêôơưàáạảãằắặẳẵầấậẩẫèéẹẻẽềếệểễìíịỉĩòóọỏõồốộổỗờớợởỡùúụủũừứựửữỳýỵỷỹ]",
                       re.I)


def weights(settings=None) -> dict:
    merged = dict(DEFAULT_WEIGHTS)
    raw = getattr(settings, "trend_weights", "") if settings else ""
    if raw:
        try:
            custom = json.loads(raw)
            merged.update({k: float(v) for k, v in custom.items() if k in merged and float(v) >= 0})
        except (ValueError, TypeError, AttributeError):
            pass
    return merged


def _clamp(x: float) -> float:
    return round(max(0.0, min(100.0, x)), 1)


def _log_score(value, full_at: float) -> float | None:
    if value is None or value <= 0:
        return None
    return _clamp(math.log10(value + 1) / math.log10(full_at) * 100)


def growth_score(g):
    if g is None:
        return None
    if g >= 0:
        return _clamp(50 + 50 * min(1.0, math.log2(1 + g) / 2))  # +300% -> 100
    return _clamp(50 * (1 + g))


def acceleration_score(a):
    return None if a is None else _clamp(50 + 50 * math.tanh(a))


def engagement_score(per_source: dict) -> float | None:
    parts = []
    for source, m in per_source.items():
        meta, value = m.get("latest_meta") or {}, m.get("latest_value")
        if source == "google_trends":
            parts.append(_log_score(value, 1e6))
        elif source == "youtube":
            views = _log_score(meta.get("views"), 1e7)
            er = meta.get("engagement_rate")
            er_score = _clamp(er / 0.06 * 100) if er is not None else None
            if views is not None:
                parts.append(views if er_score is None else round(0.6 * views + 0.4 * er_score, 1))
        elif source == "google_news":
            last7 = meta.get("last7d")
            if last7:
                parts.append(_clamp(last7 / 40 * 100))
        elif value is not None:
            parts.append(_log_score(value, 1e9))
        elif m.get("latest_rank"):
            parts.append(_clamp(100 - (m["latest_rank"] - 1) * 5))
    parts = [p for p in parts if p is not None]
    return round(sum(parts) / len(parts), 1) if parts else None


def cross_platform_score(sources: list[str]) -> float | None:
    n = len(sources)
    return None if n == 0 else {1: 25.0, 2: 60.0, 3: 85.0}.get(n, 100.0)


def vn_relevance_score(keyword: str, sources: list[str]) -> float:
    base = 70.0 if set(sources) & VN_SOURCES else 40.0
    return _clamp(base + (30 if _VI_CHARS.search(keyword or "") else 0))


def freshness_score(hours_first, hours_last) -> float | None:
    if hours_first is None:
        return None
    score = 100 * max(0.0, 1 - hours_first / 336)  # fades out over 14 days
    if (hours_last or 0) > 72:
        score *= 0.5
    return _clamp(score)


def confidence_score(a: dict) -> float:
    n_sources = len(a["per_source"])
    hours_last = a["hours_since_last"] or 1e9
    recency = 1.0 if hours_last <= 24 else 0.5 if hours_last <= 72 else 0.0
    return _clamp(40 * min(n_sources, 3) / 3 + 40 * min(a["observations"], 4) / 4 + 20 * recency)


def affiliate_score(intent: dict | None, best_product: float | None) -> float | None:
    if not intent:
        return None
    if intent.get("sensitive") or not intent.get("commercial", True):
        return 0.0
    purchase = float(intent.get("purchase_intent") or 0)
    if best_product is None:
        return _clamp(0.6 * purchase)
    return _clamp(0.6 * purchase + 0.4 * best_product)


def score_topic(topic: dict, obs: list[dict], settings=None, best_product: float | None = None,
                now=None) -> dict:
    a = analyzer.analyse(topic, obs, now)
    components = {
        "growth": growth_score(a["growth"]),
        "engagement": engagement_score(a["per_source"]),
        "acceleration": acceleration_score(a["acceleration"]),
        "cross_platform": cross_platform_score(a["recent_sources"]),
        "vn_relevance": vn_relevance_score(topic.get("keyword", ""), list(a["per_source"])),
        "affiliate": affiliate_score(topic.get("intent"), best_product),
        "freshness": freshness_score(a["hours_since_first"], a["hours_since_last"]),
        "confidence": confidence_score(a),
    }
    w = weights(settings)
    used = {k: v for k, v in components.items() if v is not None and w.get(k, 0) > 0}
    signal = (round(sum(v * w[k] for k, v in used.items()) / sum(w[k] for k in used), 1)
              if used else None)
    aff = components["affiliate"]
    factor = UNKNOWN_AFFILIATE_FACTOR if aff is None else 0.3 + 0.7 * aff / 100
    total = None if signal is None else round(signal * factor, 1)
    notes = list(a["notes"])
    if components["affiliate"] is None:
        notes.append("Chưa phân tích ý định mua / sản phẩm phù hợp.")
    status = ("OBSERVED" if a["growth_method"] == "series"
              else "WINDOW" if a["growth_method"] == "news_window" else "PROVISIONAL")
    return {
        "total": total,
        "trend_score": signal,
        "affiliate_factor": round(factor, 3),
        "status": status,
        "lifecycle": analyzer.lifecycle(a, components["engagement"]),
        "components": components,
        "weights": w,
        "metrics": {
            "growth": a["growth"], "acceleration": a["acceleration"], "z_score": a["z_score"],
            "growth_method": a["growth_method"], "observations": a["observations"],
            "sources": list(a["per_source"]), "recent_sources": a["recent_sources"],
            "per_source": {s: {k: m[k] for k in ("observations", "growth", "acceleration",
                                                 "z_score", "latest_value", "latest_rank",
                                                 "method")}
                           for s, m in a["per_source"].items()},
        },
        "notes": notes,
    }
