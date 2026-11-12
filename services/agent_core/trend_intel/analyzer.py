"""Time-series analysis of trend observations: growth, acceleration, spike z-score and
lifecycle. Pure functions over stored observations; nothing here invents numbers - a metric
without enough data is None and the reason is listed in `notes`."""
import math
from collections import defaultdict
from datetime import datetime, timezone

from .store import hours_since

GROWTH_CAP = 5.0  # +500%: beyond that the baseline is too small to mean anything


def _clip(g: float) -> float:
    return max(-1.0, min(GROWTH_CAP, g))


def _rel(new: float, old: float) -> float:
    return _clip((new - old) / max(abs(old), 1.0))


def series_by_source(obs: list[dict]) -> dict[str, list[dict]]:
    """Oldest -> newest per source, only observations with a numeric value."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for o in sorted(obs, key=lambda x: x["observed_at"]):
        grouped[o["source"]].append(o)
    return dict(grouped)


def source_metrics(source: str, series: list[dict]) -> dict:
    values = [o["value"] for o in series if o.get("value") is not None]
    out: dict = {"source": source, "observations": len(series), "growth": None,
                 "acceleration": None, "z_score": None, "method": None}
    if len(values) >= 2:
        out["growth"] = round(_rel(values[-1], values[-2]), 4)
        out["method"] = "series"
    if len(values) >= 3:
        previous = _rel(values[-2], values[-3])
        out["acceleration"] = round(_rel(values[-1], values[-2]) - previous, 4)
    if len(values) >= 4:
        base = [math.log1p(max(v, 0)) for v in values[-4:-1]]
        mean = sum(base) / len(base)
        std = math.sqrt(sum((b - mean) ** 2 for b in base) / len(base))
        out["z_score"] = round((math.log1p(max(values[-1], 0)) - mean) / (std or 1.0), 3)
    latest = series[-1]
    meta = latest.get("meta") or {}
    if out["growth"] is None and source == "google_news" and not meta.get("capped"):
        # One RSS read already holds two windows: last 7 days vs the 7 days before.
        last7, prev7, last24 = meta.get("last7d"), meta.get("prev7d"), meta.get("last24h")
        if last7 is not None and prev7 is not None and (last7 + prev7) >= 3:
            out["growth"] = round(_rel(last7, prev7), 4)
            out["method"] = "news_window"
            if last24 is not None and last7:
                # Is the last day running faster than the weekly average?
                out["acceleration"] = round(_clip((last24 * 7 - last7) / last7), 4)
    out["latest_value"] = values[-1] if values else None
    out["latest_meta"] = meta
    out["latest_rank"] = latest.get("rank")
    out["latest_at"] = latest["observed_at"]
    return out


def analyse(topic: dict, obs: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    per_source = {s: source_metrics(s, series) for s, series in series_by_source(obs).items()}
    growths = [m["growth"] for m in per_source.values() if m["growth"] is not None]
    accels = [m["acceleration"] for m in per_source.values() if m["acceleration"] is not None]
    zs = [m["z_score"] for m in per_source.values() if m["z_score"] is not None]
    recent_sources = {s for s, m in per_source.items()
                      if (hours_since(m["latest_at"], now) or 1e9) <= 72}
    notes = []
    if not growths:
        notes.append("Chưa đủ 2 lần ghi nhận để tính tốc độ tăng.")
    return {
        "per_source": per_source,
        "growth": round(sum(growths) / len(growths), 4) if growths else None,
        "growth_method": ("series" if any(m["method"] == "series" for m in per_source.values())
                          else "news_window" if growths else None),
        "acceleration": round(sum(accels) / len(accels), 4) if accels else None,
        "z_score": max(zs) if zs else None,
        "recent_sources": sorted(recent_sources),
        "observations": len(obs),
        "hours_since_first": hours_since(topic.get("first_seen"), now),
        "hours_since_last": hours_since(topic.get("last_seen"), now),
        "notes": notes,
    }


LIFECYCLE_LABELS = {
    "NEW": "Mới xuất hiện", "RISING": "Đang lên", "PEAKING": "Gần đỉnh",
    "SATURATED": "Bão hoà", "STABLE": "Ổn định", "DECLINING": "Đang giảm",
    "UNKNOWN": "Chưa đủ dữ liệu",
}


def lifecycle(a: dict, engagement: float | None = None) -> str:
    growth, accel = a["growth"], a["acceleration"]
    if (a["hours_since_last"] or 0) > 72:
        return "DECLINING"
    if growth is None:
        return "NEW" if (a["hours_since_first"] or 1e9) <= 24 else "UNKNOWN"
    if growth >= 0.25:
        return "PEAKING" if accel is not None and accel < -0.25 else "RISING"
    if growth <= -0.15:
        return "DECLINING"
    return "SATURATED" if (engagement or 0) >= 70 else "STABLE"
