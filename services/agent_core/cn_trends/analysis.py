"""Label (AI), stage, normalise and score China trends for Vietnamese short-video production.

Scoring (0-100, weights from the owner's plan): growth 25, spread across platforms 20,
engagement 20, Vietnam-adaptable 15 (AI), producible with this app 10 (AI), commercial 10 (AI).
Components that cannot be measured are left out and the rest re-weighted, and the topic gets a
lower confidence; nothing is guessed. Multipliers: stage, Facebook/copyright risk, and how close
the topic is to videos this channel already made.
"""
import json
import logging
import re
from statistics import median

from rapidfuzz import fuzz

from . import store

log = logging.getLogger("affiliate_agent.cn_trends")

DEFAULT_NICHES = {
    "kien_thuc": ("Khám phá, kiến thức trực quan", "Khoa học thú vị, phát minh, lịch sử, hiện tượng lạ có thật"),
    "ky_nang": ("Kỹ năng đặc biệt, nghề lạ", "Chế tạo, thủ công, quy trình sản xuất, sửa chữa, phục chế"),
    "mini_drama": ("AI mini drama", "Chuyện ngắn có cao trào, gia đình, bí mật, twist, series nhân vật"),
    "meo_doi_song": ("Đời sống và mẹo hữu ích", "Mẹo nhà cửa, bếp, công nghệ đời sống, thử sản phẩm"),
    "nguoi_that": ("Chuyện người thật, cảm xúc", "Nghề đặc biệt, truyền cảm hứng, tử tế, lát cắt đời sống"),
    "trend_ngan": ("Trend ngắn hạn", "Meme, câu nói viral, hiệu ứng, thử thách, format mới"),
    "khac": ("Tin tức / không phù hợp", "Thời sự, chính trị, thể thao trực tiếp, giải trí người nổi tiếng"),
}
WEIGHTS = {"growth": 25, "spread": 20, "engagement": 20, "vn_adaptable": 15, "producible": 10,
           "commercial": 10}
STAGE_FACTOR = {"early_signal": 1.1, "rising": 1.0, "peak": 0.8, "saturated": 0.5}
STAGE_LABELS = {"early_signal": "Tín hiệu sớm", "rising": "Đang lên", "peak": "Đỉnh",
                "saturated": "Bão hoà"}
RISK_FACTOR = {"low": 1.0, "medium": 0.8, "high": 0.5}
LABEL_BATCH = 20
PARALLEL_AI = 3   # AI calls of one China-trend pass that run at the same time


# ------------------------------------------------------------------ AI labels

def label_prompt(topics: list[dict], niche_ids: list[str]) -> str:
    rows = []
    for t in topics:
        meta = t.get("meta") or {}
        rows.append(json.dumps({"key": t["key"], "platform": t["platform"], "title": t["title_zh"],
                                "category": meta.get("category"), "desc": (meta.get("desc") or "")[:200],
                                "notes": (meta.get("notes") or "")[:300]}, ensure_ascii=False))
    return f"""Bạn là biên tập viên nội dung video ngắn Việt Nam, đọc được tiếng Trung.
Dưới đây là các trend đang nóng trên mạng xã hội Trung Quốc. Với MỖI trend trả về:
- title_vi: tên tiếng Việt tự nhiên (không dịch máy từng chữ); summary_vi: 1-2 câu trend nói về gì.
- niche: một trong {json.dumps(niche_ids)} hoặc "new:<tên ngách mới tiếng Việt>" nếu không vừa nhóm nào.
- formats: các định dạng hợp lý trong ["explainer","story","mini_doc","process","product","animated"].
- vn_adaptable 0-100: chuyển thể sang khán giả Việt Nam có tự nhiên, có ý nghĩa không.
- producible 0-100: app làm được bằng video AI (Muse: người, con vật, đồ vật, quy trình, cảnh đời thường,
  hoạt hình 3D) + giọng đọc tiếng Việt. Thấp nếu cần cảnh thật của sự kiện, trận đấu, người nổi tiếng, tin tức.
- producible_reason: ngắn.
- commercial 0-100: gắn tự nhiên một sản phẩm bán trên Shopee không; product_keywords_vi: 0-4 cụm từ.
- sensitive: true nếu chính trị, quân sự, thiên tai, tai nạn, người chết, tội phạm, cờ bạc, người lớn,
  hoặc chủ đề nhạy cảm Trung Quốc - Việt Nam.
- facebook_risk: low|medium|high (vi phạm chính sách Meta, nội dung gây hiểu lầm, sốc).
- copyright_risk: low|medium|high (dựa trên IP phim, game, nhân vật, nhạc, người nổi tiếng).
- reason: vì sao đáng (hoặc không đáng) làm video tại Việt Nam.
Trend:
{chr(10).join(rows)}
Trả DUY NHẤT JSON: {{"topics":[{{"key":"","title_vi":"","summary_vi":"","niche":"","formats":[],
"vn_adaptable":0,"producible":0,"producible_reason":"","commercial":0,"product_keywords_vi":[],
"sensitive":false,"facebook_risk":"low","copyright_risk":"low","reason":""}}]}}"""


def _num(v, default=0.0) -> float:
    try:
        return max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return default


def label_topics(settings, topics: list[dict], ask=None) -> int:
    """AI labels, LABEL_BATCH per call; each topic is labelled once (re-labelled after 7 days)."""
    if not topics:
        return 0
    if ask is None:
        from ..workflow import _ask_json, _client

        def ask(prompt):   # one client per call: batches run in parallel
            return _ask_json(_client(settings), settings, prompt, temperature=0.2, max_tokens=4000)
    db = settings.database_path
    known = [n["id"] for n in store.niches(db) if n["status"] == "active"] or list(DEFAULT_NICHES)
    chunks = [topics[i:i + LABEL_BATCH] for i in range(0, len(topics), LABEL_BATCH)]
    for chunk in chunks:
        for t in chunk:
            obs = store.observations(db, t["key"], 1)
            t["meta"] = json.loads(obs[-1]["meta_json"] or "{}") if obs else {}

    def call(chunk):
        try:
            return chunk, ask(label_prompt(chunk, known)) or {}
        except Exception as exc:  # noqa: BLE001 - unlabelled topics simply wait for the next run
            log.warning("cn label batch failed: %s", exc)
            return chunk, None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max(1, min(PARALLEL_AI, len(chunks)))) as ex:   # batches at once, not in turn
        replies = list(ex.map(call, chunks))
    done = 0
    for chunk, reply in replies:
        if reply is None:
            continue
        keys = {t["key"] for t in chunk}
        for item in reply.get("topics") or []:
            key = str(item.get("key") or "")
            if key not in keys:
                continue
            niche = str(item.get("niche") or "khac")[:60]
            labels = {
                "formats": [f for f in (item.get("formats") or []) if f in
                            {"explainer", "story", "mini_doc", "process", "product", "animated"}][:4],
                "vn_adaptable": _num(item.get("vn_adaptable")), "producible": _num(item.get("producible")),
                "producible_reason": str(item.get("producible_reason") or "")[:200],
                "commercial": _num(item.get("commercial")),
                "product_keywords_vi": [str(k)[:60] for k in (item.get("product_keywords_vi") or [])][:4],
                "sensitive": bool(item.get("sensitive")),
                "facebook_risk": item.get("facebook_risk") if item.get("facebook_risk") in RISK_FACTOR else "medium",
                "copyright_risk": item.get("copyright_risk") if item.get("copyright_risk") in RISK_FACTOR else "medium",
                "reason": str(item.get("reason") or "")[:300], "niche_raw": niche,
            }
            fields = {"title_vi": str(item.get("title_vi") or "")[:200],
                      "summary_vi": str(item.get("summary_vi") or "")[:500],
                      "niche": niche if niche in known else ("khac" if not niche.startswith("new:") else niche),
                      "labels_json": labels, "labeled_at": store.now_iso()}
            if labels["sensitive"]:
                fields["status"] = "BLOCKED"
            store.update_topic(db, key, **fields)
            done += 1
    return done


# ------------------------------------------------------------------ stage, normalisation

def percentile(value: float | None, population: list[float]) -> float | None:
    if value is None or not population:
        return None
    below = sum(1 for v in population if v is not None and v < value)
    same = sum(1 for v in population if v == value)
    return round(100.0 * (below + 0.5 * same) / len(population), 1)


def stage_of(points: list[dict], age_hours: float, last_seen_hours: float) -> tuple[str, float, dict]:
    """(stage, confidence 0-1, facts). points: one platform's observations, oldest first."""
    facts = {"points": len(points), "age_h": round(age_hours, 1)}
    if last_seen_hours > 12:
        return "saturated", 0.6, {**facts, "why": "rời bảng hot hơn 12 giờ"}
    heats = [p["heat"] for p in points if p.get("heat")]
    ranks = [p["rank"] for p in points if p.get("rank")]
    if len(points) < 3 or len(heats) < 2:
        rank = ranks[-1] if ranks else None
        stage = "early_signal" if age_hours <= 12 and (rank is None or rank > 10) else \
            "peak" if rank and rank <= 5 else "rising"
        return stage, 0.3, {**facts, "why": "ít hơn 3 mốc dữ liệu: độ tin cậy thấp", "rank": rank}
    recent = heats[-3:]
    growth = (recent[-1] - recent[0]) / max(1.0, recent[0])
    rank_delta = (ranks[-3] - ranks[-1]) if len(ranks) >= 3 else 0     # positive = climbing
    facts.update(growth=round(growth, 3), rank_delta=rank_delta, rank=ranks[-1] if ranks else None)
    if growth < -0.15 or rank_delta <= -5:
        stage = "saturated"
    elif age_hours <= 12 and (growth > 0.2 or rank_delta >= 3) and (not ranks or ranks[-1] > 10):
        stage = "early_signal"
    elif growth > 0.08 or rank_delta >= 2:
        stage = "rising"
    else:
        stage = "peak"
    confidence = min(1.0, len(points) / 6) * (1.0 if any(p.get("views") for p in points) else 0.85)
    return stage, round(confidence, 2), facts


def _growth_score(facts: dict) -> float | None:
    if "growth" not in facts:
        return None
    g = max(-0.5, min(2.0, facts["growth"]))
    score = 50 + g * 50 + 6 * max(-5, min(5, facts.get("rank_delta") or 0))
    return max(0.0, min(100.0, score))


def own_similarity(db, text: str, own: list[str] | None = None) -> float:
    """0-100: how close a topic is to what this channel already made (hooks/titles)."""
    if not text:
        return 0.0
    if own is None:
        own = own_titles(db)
    return max((fuzz.token_set_ratio(text.lower(), o.lower()) for o in own if o), default=0.0)


def own_titles(db) -> list[str]:
    from ..db import connect
    out = []
    with connect(db) as conn:
        for row in conn.execute("SELECT file_name, content_json FROM jobs ORDER BY updated_at DESC LIMIT 200"):
            out.append(row["file_name"] or "")
            try:
                c = json.loads(row["content_json"] or "{}")
                out += [c.get("title") or "", c.get("hook") or ""]
            except ValueError:
                pass
    return [o for o in out if o]


def score_all(settings, topics: list[dict] | None = None) -> int:
    db = settings.database_path
    topics = topics if topics is not None else store.list_topics(db, 500, since_hours=7 * 24)
    if not topics:
        return 0
    # per-platform latest snapshot -> percentile of heat (platform scales differ by 1000x)
    latest_heat: dict[str, list[float]] = {}
    series: dict[str, dict[str, list[dict]]] = {}
    for t in topics:
        for o in store.observations(db, t["key"], 60):
            series.setdefault(t["key"], {}).setdefault(o["platform"], []).append(o)
    for per in series.values():
        for platform, pts in per.items():
            if pts[-1].get("heat") is not None:
                latest_heat.setdefault(platform, []).append(pts[-1]["heat"])
    own = own_titles(db)
    titles = [(t["key"], t.get("title_vi") or t["title_zh"]) for t in topics]
    done = 0
    for t in topics:
        per = series.get(t["key"]) or {}
        if not per:
            continue
        age = store.hours_since(t["first_seen"]) or 0
        seen = store.hours_since(t["last_seen"]) or 0
        best_platform = max(per, key=lambda p: len(per[p]))
        stage, confidence, facts = stage_of(per[best_platform], age, seen)
        labels = t.get("labels") or {}
        # spread: platforms carrying this topic (own key + similar titles elsewhere)
        name = t.get("title_vi") or t["title_zh"]
        similar = {k for k, n in titles if k != t["key"] and n and fuzz.token_set_ratio(name, n) >= 85}
        spread_n = len(set(t.get("platforms") or [])) + len(similar)
        heat_pct = [percentile(pts[-1].get("heat"), latest_heat.get(p, [])) for p, pts in per.items()]
        heat_pct = [h for h in heat_pct if h is not None]
        last = per[best_platform][-1]
        engagement = None
        if last.get("views"):
            rate = ((last.get("likes") or 0) + 3 * (last.get("shares") or 0) + (last.get("comments") or 0)) \
                / max(1.0, last["views"])
            engagement = min(100.0, rate * 1000)          # 10 % weighted interaction = 100
        elif heat_pct:
            engagement = max(heat_pct)
        if last.get("creators"):
            engagement = min(100.0, (engagement or 0) * 0.7 + min(30.0, last["creators"] * 3))
        parts = {
            "growth": _growth_score(facts),
            "spread": min(100.0, 35.0 * spread_n) if spread_n else None,
            "engagement": engagement,
            "vn_adaptable": labels.get("vn_adaptable"),
            "producible": labels.get("producible"),
            "commercial": labels.get("commercial"),
        }
        got = {k: v for k, v in parts.items() if v is not None}
        base = sum(v * WEIGHTS[k] for k, v in got.items()) / max(1, sum(WEIGHTS[k] for k in got))
        novelty = own_similarity(db, name, own)
        factor = STAGE_FACTOR[stage] * RISK_FACTOR.get(labels.get("facebook_risk", "medium"), 0.8) \
            * RISK_FACTOR.get(labels.get("copyright_risk", "medium"), 0.8) * (1 - max(0.0, novelty - 60) / 100)
        coverage = sum(WEIGHTS[k] for k in got) / 100
        conf = round(confidence * (0.5 + 0.5 * coverage) * (1.0 if labels else 0.6), 2)
        opportunity = None if t.get("status") == "BLOCKED" or not labels else round(base * factor, 1)
        goal = "affiliate" if labels.get("commercial", 0) >= 60 and labels.get("product_keywords_vi") else "fb_growth"
        store.update_topic(db, t["key"], stage=stage, confidence=conf, opportunity=opportunity, goal=goal,
                           scores_json={"parts": {k: (round(v, 1) if v is not None else None) for k, v in parts.items()},
                                        "facts": facts, "novelty_overlap": round(novelty, 1),
                                        "factor": round(factor, 2), "spread_platforms": spread_n,
                                        "heat_percentile": max(heat_pct) if heat_pct else None})
        done += 1
    return done


def propose_niches(settings, min_topics: int = 5) -> list[str]:
    """Labels like "new:Ẩm thực đường phố" seen on >= min_topics topics in 7 days become a
    proposed niche the owner can approve on the dashboard."""
    db = settings.database_path
    existing = {n["id"] for n in store.niches(db)}
    counts: dict[str, list[str]] = {}
    for t in store.list_topics(db, 1000, since_hours=7 * 24):
        raw = ((t.get("labels") or {}).get("niche_raw") or "")
        if raw.startswith("new:"):
            name = raw[4:].strip()
            counts.setdefault(name.lower(), []).append(t.get("title_vi") or t["title_zh"])
    proposed = []
    for name, examples in counts.items():
        nid = "n_" + re.sub(r"[^a-z0-9]+", "_", store.topic_key("x", name))[3:15]
        if len(examples) >= min_topics and nid not in existing:
            store.save_niche(db, nid, name.capitalize(), f"Đề xuất từ {len(examples)} trend",
                             status="proposed", evidence=examples[:8])
            proposed.append(name)
    return proposed


def ensure_niches(settings) -> None:
    db = settings.database_path
    if not store.niches(db):
        for nid, (name, desc) in DEFAULT_NICHES.items():
            store.save_niche(db, nid, name, desc, status="active")


def niche_stats(settings) -> list[dict]:
    db = settings.database_path
    topics = store.list_topics(db, 1000, since_hours=7 * 24)
    rows = []
    for n in store.niches(db):
        mine = [t for t in topics if t.get("niche") == n["id"]]
        opps = [t["opportunity"] for t in mine if t.get("opportunity") is not None]
        rising = sum(1 for t in mine if t.get("stage") in ("early_signal", "rising"))
        rows.append({**n, "topics": len(mine), "rising": rising,
                     "median_opportunity": round(median(opps), 1) if opps else None,
                     "best": max(mine, key=lambda t: t.get("opportunity") or 0, default=None)})
    return sorted(rows, key=lambda r: (r["status"] != "active", -(r["median_opportunity"] or 0)))
