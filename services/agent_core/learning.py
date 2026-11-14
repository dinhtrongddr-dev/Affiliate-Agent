"""Channel learning: what each video was made of, how it did, and what to repeat.

- ``video_features``: recorded at render (branch, style, length, hook type/variant, music mood,
  talking shots, voices ...). Nothing is guessed later.
- ``video_performance`` (performance_learning): numbers the owner copies from Meta Business
  Suite / Shopee Affiliate after posting (no extra tokens or report files needed).
- ``insights``: per feature value, an engagement score; only offered to the storyboard prompt
  once MIN_VIDEOS videos have numbers, so a couple of lucky posts cannot steer the channel.
"""
import json
import sqlite3
from pathlib import Path

from .performance_learning import init_performance_db, record_video_performance

MIN_VIDEOS = 10
METRIC_FIELDS = {
    "views": ("Lượt xem", int), "retention_3s_rate": ("% giữ chân 3 giây", float),
    "completion_rate": ("% xem hết", float), "shares": ("Chia sẻ", int),
    "comments": ("Bình luận", int), "saves": ("Lưu", int), "clicks": ("Click link", int),
    "conversions": ("Đơn hàng", int), "commission_earned": ("Hoa hồng (đ)", float),
    "new_followers": ("Follower mới", int), "qualified_views": ("Lượt xem đủ ĐK kiếm tiền", int),
}
# KPI per goal: Facebook growth is judged on holding/sharing/followers, affiliate on buying.
GOAL_KPIS = {
    "fb_growth": ("retention_3s_rate", "completion_rate", "shares", "new_followers", "qualified_views"),
    "affiliate": ("clicks", "conversions", "commission_earned"),
}
MIN_PER_GROUP = 5          # a strategy suggestion needs this many videos with numbers per group
MAX_SHIFT = 0.20           # suggestions move a weight/share by at most 20 %
OUTLIER_X = 3.0            # a video above 3x the median engagement does not steer anything
FEATURE_LABELS = {
    "branch": "Nhánh", "style": "Phong cách quay", "length": "Độ dài", "hook_type": "Kiểu hook",
    "talking": "Cảnh nói trước camera", "music_mood": "Nhạc", "cast": "Số nhân vật",
    "niche": "Ngách", "format": "Định dạng", "goal": "Mục tiêu", "source_platform": "Nguồn trend",
    "trend_stage": "Giai đoạn trend lúc làm",
}


def _connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS video_features (
        job_id TEXT PRIMARY KEY, features_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    return conn


def length_bucket(seconds: float) -> str:
    return "<15s" if seconds < 15 else "15-25s" if seconds < 25 else "25-35s" if seconds < 35 else ">35s"


def hook_type(hook: str) -> str:
    text = (hook or "").strip().lower()
    if not text:
        return "không hook"
    if text.endswith("?"):
        return "câu hỏi"
    if any(ch.isdigit() for ch in text):
        return "con số"
    if text.startswith(("khi ", "lúc ", "pov", "góc nhìn")):
        return "pov"
    return "khẳng định"


def features_from_timeline(timeline: dict, duration: float, brief: dict | None = None) -> dict:
    scenes = timeline.get("scenes") or []
    brief = brief or {}
    source = brief.get("trend_source") or {}
    return {
        "niche": timeline.get("niche") or brief.get("niche") or "",
        "format": timeline.get("format") or brief.get("format") or "",
        "goal": timeline.get("goal") or brief.get("goal")
        or ("affiliate" if timeline.get("creation_branch") == "product_review" else "fb_growth"),
        "source_platform": source.get("platform") or "",
        "trend_stage": source.get("stage") or "",
        "predicted": source.get("opportunity"),
        "branch": timeline.get("creation_branch") or "base",
        "style": timeline.get("style_profile") or "",
        "length": length_bucket(float(duration or 0)),
        "hook_type": hook_type(timeline.get("hook") or ""),
        "hook": timeline.get("hook") or "",
        "talking": str(sum(1 for s in scenes if s.get("talking"))),
        "music_mood": str(timeline.get("music_mood") or "")[:30],
        "cast": str(len(timeline.get("characters") or [])),
        "voices": sorted({v.get("voice") for v in (timeline.get("voice_cast") or {}).values()
                          if isinstance(v, dict) and v.get("voice")}),
    }


def save_features(db: Path, job_id: str, features: dict) -> None:
    from .db import now_iso
    conn = _connect(db)
    try:
        conn.execute("INSERT OR REPLACE INTO video_features VALUES (?,?,?)",
                     (job_id, json.dumps(features, ensure_ascii=False), now_iso()))
        conn.commit()
    finally:
        conn.close()


def get_features(db: Path, job_id: str) -> dict:
    conn = _connect(db)
    try:
        row = conn.execute("SELECT features_json FROM video_features WHERE job_id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else {}


def backfill(db: Path) -> int:
    """Videos rendered before features were recorded get them from their stored timeline."""
    conn = _connect(db)
    try:
        missing = conn.execute("""SELECT j.id, j.script_json, j.duration_sec FROM jobs j
                                  LEFT JOIN video_features f ON f.job_id = j.id
                                  WHERE f.job_id IS NULL AND j.output_path IS NOT NULL
                                  AND j.script_json IS NOT NULL""").fetchall()
    finally:
        conn.close()
    count = 0
    for row in missing:
        try:
            timeline = json.loads(row["script_json"] or "{}")
        except ValueError:
            continue
        if timeline.get("scenes"):
            save_features(db, row["id"], features_from_timeline(timeline, float(row["duration_sec"] or 0)))
            count += 1
    return count


def clean_metrics(raw: dict) -> dict:
    """Owner input -> numbers. Percentages may be typed as 45 or 0.45."""
    out = {}
    for key, (_, kind) in METRIC_FIELDS.items():
        value = raw.get(key)
        if value in (None, ""):
            continue
        try:
            number = kind(str(value).replace(",", ".").replace("%", "").strip())
        except ValueError as exc:
            raise ValueError(f"{METRIC_FIELDS[key][0]}: không phải số") from exc
        if number < 0:
            raise ValueError(f"{METRIC_FIELDS[key][0]}: không được âm")
        if key.endswith("_rate"):
            number = number / 100 if number > 1 else number
            number = min(1.0, number)
        out[key] = number
    if not out:
        raise ValueError("Nhập ít nhất một số liệu")
    return out


def record(db: Path, job_id: str, platform: str, metrics: dict) -> None:
    features = get_features(db, job_id)
    if not features:
        backfill(db)
        features = get_features(db, job_id)
    record_video_performance(db, job_id, platform, clean_metrics(metrics),
                             content_mode=features.get("branch", ""),
                             hook_style=features.get("hook_type", ""),
                             duration_category=features.get("length", ""))


def engagement(m: dict) -> float | None:
    """0-100 blend of what the platforms reward: holding viewers, finishing, sharing, buying."""
    parts, weights = [], []
    if m.get("retention_3s_rate"):
        parts.append(float(m["retention_3s_rate"]) * 100)
        weights.append(0.35)
    if m.get("completion_rate"):
        parts.append(float(m["completion_rate"]) * 100)
        weights.append(0.35)
    views = float(m.get("views") or 0)
    if views:
        share = (float(m.get("shares") or 0) + float(m.get("saves") or 0)) / views
        parts.append(min(100.0, share * 2000))     # 5 % share+save rate = 100
        weights.append(0.15)
        if m.get("clicks") or m.get("conversions"):
            buy = (float(m.get("clicks") or 0) + 10 * float(m.get("conversions") or 0)) / views
            parts.append(min(100.0, buy * 1000))
            weights.append(0.15)
    if not parts:
        return None
    return round(sum(p * w for p, w in zip(parts, weights, strict=True)) / sum(weights), 1)


def rows(db: Path) -> list[dict]:
    init_performance_db(db)
    conn = _connect(db)
    try:
        data = conn.execute("""SELECT p.job_id, p.platform, p.metrics_json, p.created_at,
                                      f.features_json, j.file_name, j.product_name
                               FROM video_performance p
                               LEFT JOIN video_features f ON f.job_id = p.job_id
                               LEFT JOIN jobs j ON j.id = p.job_id
                               ORDER BY p.created_at DESC""").fetchall()
    finally:
        conn.close()
    out = []
    for r in data:
        metrics = json.loads(r["metrics_json"] or "{}")
        out.append({"job_id": r["job_id"], "platform": r["platform"], "metrics": metrics,
                    "features": json.loads(r["features_json"] or "{}"), "title": r["file_name"],
                    "product": r["product_name"], "score": engagement(metrics),
                    "recorded_at": r["created_at"]})
    return out


def insights(db: Path, min_videos: int = MIN_VIDEOS) -> dict:
    data = [r for r in rows(db) if r["score"] is not None and r["features"]]
    videos = len({r["job_id"] for r in data})
    result = {"videos": videos, "needed": min_videos, "ready": videos >= min_videos, "by_feature": {},
              "lessons": []}
    if not data:
        return result
    overall = sum(r["score"] for r in data) / len(data)
    for key in FEATURE_LABELS:
        groups: dict[str, list[float]] = {}
        for r in data:
            value = str(r["features"].get(key) or "")
            if value:
                groups.setdefault(value, []).append(r["score"])
        stats = sorted(((v, round(sum(s) / len(s), 1), len(s)) for v, s in groups.items()),
                       key=lambda x: -x[1])
        result["by_feature"][key] = stats
        best = [s for s in stats if s[2] >= 2]
        if len(best) >= 2 and best[0][1] - best[-1][1] >= 8:
            result["lessons"].append(
                f"{FEATURE_LABELS[key]}: \"{best[0][0]}\" đạt {best[0][1]} điểm tương tác "
                f"({best[0][2]} video), \"{best[-1][0]}\" chỉ {best[-1][1]} ({best[-1][2]} video).")
    top = sorted(data, key=lambda r: -r["score"])[:3]
    hooks = [r["features"].get("hook") for r in top if r["features"].get("hook")]
    if hooks:
        result["lessons"].append("Hook của các video tốt nhất: " + "; ".join(f"\"{h}\"" for h in hooks))
    result["overall"] = round(overall, 1)
    return result


def _robust(data: list[dict]) -> list[dict]:
    """Drop freak hits (> OUTLIER_X x median) so one lucky video cannot steer strategy."""
    scores = sorted(r["score"] for r in data)
    if len(scores) < 4:
        return data
    med = scores[len(scores) // 2] or 1.0
    return [r for r in data if r["score"] <= OUTLIER_X * med]


def prediction_check(db: Path) -> dict:
    """Trend opportunity predicted at production time vs the engagement measured afterwards."""
    pairs = [(float(r["features"]["predicted"]), r["score"]) for r in rows(db)
             if r["score"] is not None and r["features"].get("predicted") is not None]
    if len(pairs) < 3:
        return {"pairs": len(pairs), "correlation": None}
    n = len(pairs)
    mx, my = sum(p for p, _ in pairs) / n, sum(a for _, a in pairs) / n
    cov = sum((p - mx) * (a - my) for p, a in pairs)
    vx = sum((p - mx) ** 2 for p, _ in pairs) ** 0.5
    vy = sum((a - my) ** 2 for _, a in pairs) ** 0.5
    corr = round(cov / (vx * vy), 2) if vx and vy else None
    return {"pairs": n, "correlation": corr, "mean_predicted": round(mx, 1), "mean_actual": round(my, 1)}


def goal_kpis(db: Path) -> dict:
    out = {}
    for goal, fields in GOAL_KPIS.items():
        mine = [r for r in rows(db) if (r["features"].get("goal") or "fb_growth") == goal]
        out[goal] = {"videos": len(mine)}
        for f in fields:
            values = [float(r["metrics"][f]) for r in mine if r["metrics"].get(f) is not None]
            out[goal][f] = round(sum(values) / len(values), 3) if values else None   # None = not entered
    return out


def suggestions(db: Path) -> list[dict]:
    """Shift production toward niches/formats that do better, with guard rails:
    >= MIN_PER_GROUP videos per group, outliers removed, at most MAX_SHIFT per suggestion.
    These are proposals; nothing changes until the owner applies them."""
    data = _robust([r for r in rows(db) if r["score"] is not None and r["features"]])
    out = []
    for key in ("niche", "format", "goal"):
        groups: dict[str, list[float]] = {}
        for r in data:
            value = str(r["features"].get(key) or "")
            if value:
                groups.setdefault(value, []).append(r["score"])
        ready = {v: sum(s) / len(s) for v, s in groups.items() if len(s) >= MIN_PER_GROUP}
        if len(ready) < 2:
            continue
        best, worst = max(ready, key=ready.get), min(ready, key=ready.get)
        gap = ready[best] - ready[worst]
        if gap < 8:
            continue
        shift = round(min(MAX_SHIFT, gap / 100), 2)
        out.append({"feature": FEATURE_LABELS.get(key, key), "increase": best, "decrease": worst,
                    "shift": shift, "evidence": f"{ready[best]:.1f} vs {ready[worst]:.1f} điểm "
                    f"({len(groups[best])} và {len(groups[worst])} video)"})
    return out


def prompt_block(db: Path) -> str:
    """Text for the storyboard prompt; empty until enough videos have numbers."""
    info = insights(db)
    if not info["ready"] or not info["lessons"]:
        return ""
    return ("Bài học từ số liệu thật của kênh (" + str(info["videos"]) + " video, dùng làm định hướng, "
            "không sao chép nguyên văn):\n- " + "\n- ".join(info["lessons"]))
