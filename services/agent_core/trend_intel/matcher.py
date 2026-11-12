"""Trend-to-product matching.

1. ChatGPT2API judges each trend: commercial or not, purchase intent, sensitive, product
   categories and Vietnamese product keywords (batched, cached on the topic for 7 days).
2. Products from the local catalog (Shopee Affiliate bulk-link CSV / TikTok links) are matched
   by keyword overlap, then ranked by relevance, commission and sales momentum.
3. Optionally the AI re-ranks the shortlist for fit with the trend.
No Shopee API calls: price/commission/link come from the user's latest CSV import."""
import json
import logging
import math
import unicodedata
from datetime import datetime, timezone

from rapidfuzz import fuzz

from .. import catalog
from .store import hours_since, strip_accents

log = logging.getLogger("affiliate_agent.trend_intel")

MIN_RELEVANCE = 60.0
MIN_AI_FIT = 40.0  # AI re-rank veto: below this the product is dropped, not just demoted
STALE_HOURS = 7 * 24


def _plain(text: str, accents: bool = True) -> str:
    """Lowercase words. Accents are kept by default: stripped Vietnamese collides
    ("giá đỡ đứng" -> "gia do dung" == "đồ gia dụng")."""
    text = unicodedata.normalize("NFC", str(text or "")).lower()
    if not accents:
        text = strip_accents(text)
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def _has_accents(text: str) -> bool:
    return strip_accents(text) != text


def intent_prompt(topics: list[dict]) -> str:
    lines = []
    for t in topics:
        context = (t.get("notes") or "")[:160]
        lines.append(json.dumps({"key": t["key"], "trend": t["keyword"], "context": context},
                                ensure_ascii=False))
    return f"""
Bạn là chuyên gia affiliate marketing tại Việt Nam (Shopee, Facebook Reels, TikTok).
Dưới đây là các trend đang được tìm kiếm/nhắc đến ở Việt Nam. Với MỖI trend, đánh giá:
- commercial: có thể gắn tự nhiên một sản phẩm bán trên Shopee không (true/false).
- purchase_intent: 0-100, mức độ người quan tâm trend này muốn MUA một món đồ liên quan.
  Đây là nhận định của bạn, không phải số liệu; trend tin tức/thể thao/người nổi tiếng thường thấp.
- sensitive: true nếu trend liên quan tai nạn, thiên tai, người qua đời, tội phạm, chính trị,
  cờ bạc/xổ số, bệnh tật, nội dung người lớn, hoặc gắn sản phẩm vào sẽ phản cảm.
- categories: 1-3 ngành hàng Shopee phù hợp (tiếng Việt), rỗng nếu không commercial.
- product_keywords: 2-5 cụm từ tìm sản phẩm tiếng Việt ngắn (vd "nồi chiên không dầu").
- angle: một câu: cách nối trend với sản phẩm trong video (không bịa thông tin về trend).
- audience: nhóm khách hàng chính.
- reason: lý do ngắn.

Trend:
{chr(10).join(lines)}

Trả về DUY NHẤT JSON: {{"topics": [{{"key": "...", "commercial": true, "purchase_intent": 0,
"sensitive": false, "categories": [], "product_keywords": [], "angle": "", "audience": "",
"reason": ""}}]}}
"""


def analyse_intents(settings, topics: list[dict], ask=None) -> dict[str, dict]:
    """key -> intent. `ask(prompt) -> dict` defaults to ChatGPT2API via the workflow client."""
    if not topics:
        return {}
    if ask is None:
        from ..workflow import _ask_json, _client
        client = _client(settings)

        def ask(prompt):
            return _ask_json(client, settings, prompt, temperature=0.2, max_tokens=3000)

    results: dict[str, dict] = {}
    for start in range(0, len(topics), 15):
        chunk = topics[start:start + 15]
        reply = ask(intent_prompt(chunk)) or {}
        known = {t["key"] for t in chunk}
        for item in reply.get("topics") or []:
            key = str(item.get("key") or "")
            if key not in known:
                continue
            try:
                purchase = max(0.0, min(100.0, float(item.get("purchase_intent") or 0)))
            except (TypeError, ValueError):
                purchase = 0.0
            results[key] = {
                "commercial": bool(item.get("commercial")),
                "purchase_intent": purchase,
                "sensitive": bool(item.get("sensitive")),
                "categories": [str(c)[:60] for c in (item.get("categories") or [])][:3],
                "product_keywords": [str(k)[:60] for k in (item.get("product_keywords") or [])
                                     if str(k).strip()][:5],
                "angle": str(item.get("angle") or "")[:300],
                "audience": str(item.get("audience") or "")[:160],
                "reason": str(item.get("reason") or "")[:300],
                "by": "ai",
            }
    return results


def _term_matches(words: list[str], target: str, tokens: set[str]) -> bool:
    """The product must contain the term's head phrase (first two syllables, e.g. "may xay",
    "binh giu"), or the term's first word plus two thirds of its words. Sharing filler words
    ("mini", "sac", "giu nhiet") alone is not a match."""
    if len(words) == 1:
        return len(words[0]) >= 4 and words[0] in tokens
    if f" {words[0]} {words[1]} " in f" {target} ":
        return True
    coverage = sum(1 for w in words if w in tokens) / len(words)
    return words[0] in tokens and coverage >= 0.66


def _relevance(terms: list[tuple[str, float]], name: str) -> float:
    accents = _has_accents(name)  # unaccented product names fall back to accent-free matching
    target = _plain(name, accents)
    tokens = set(target.split())
    best = 0.0
    for term, weight in terms:
        plain = _plain(term, accents)
        if not plain:
            continue
        words = plain.split()
        if not _term_matches(words, target, tokens):
            continue
        coverage = sum(1 for w in words if w in tokens) / len(words)
        score = (0.5 * fuzz.token_set_ratio(plain, target) + 50 * coverage) * weight
        best = max(best, score)
    return round(best, 1)


def _units(p: dict) -> float | None:
    if p.get("sold"):
        return float(p["sold"])
    if p.get("revenue") and p.get("price"):
        return float(p["revenue"]) / float(p["price"])
    return None


def product_score(p: dict, relevance: float, win_score: float | None = None) -> tuple[float, list]:
    notes = []
    rate = p.get("commission_rate")
    if rate:
        commission = min(100.0, float(rate) / 15 * 100)
    elif p.get("commission"):
        commission = min(100.0, float(p["commission"]) / 30000 * 100)
    else:
        commission = 0.0
        notes.append("chưa có hoa hồng")
    if win_score is not None:
        momentum = float(win_score)
    else:
        units = _units(p)
        momentum = min(100.0, math.log10(1 + units) / 4 * 100) if units else 0.0
        if not units:
            notes.append("chưa có dữ liệu bán")
    score = 0.55 * relevance + 0.25 * commission + 0.20 * momentum
    age = hours_since(p.get("updated_at"))
    if age is not None and age > STALE_HOURS:
        score *= 0.9
        notes.append(f"dữ liệu CSV {int(age // 24)} ngày tuổi, nên nhập lại")
    return round(score, 1), notes


def match_products(settings, topic: dict, limit: int = 10, products: list[dict] | None = None,
                   win_scores: dict | None = None) -> list[dict]:
    intent = topic.get("intent") or {}
    # Categories ("Đồ gia dụng") are too broad to create a match; only product keywords and
    # the trend itself do.
    terms = [(k, 1.0) for k in intent.get("product_keywords") or []]
    terms.append((topic["keyword"], 0.9))
    if products is None:
        products = catalog.list_products(settings.database_path, "score", limit=2000)
    win_scores = win_scores or {}
    matches = []
    for p in products:
        if not (p.get("affiliate_link") or p.get("tiktok_link")):
            continue
        relevance = _relevance(terms, p.get("name") or "")
        if relevance < MIN_RELEVANCE:
            continue
        score, notes = product_score(p, relevance, win_scores.get(str(p.get("item_id"))))
        matches.append({"product_id": p["id"], "name": p.get("name"), "score": score,
                        "relevance": relevance, "reason": "; ".join(notes)})
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches[:limit]


def rerank_prompt(topic: dict, matches: list[dict]) -> str:
    rows = "\n".join(json.dumps({"product_id": m["product_id"], "name": m["name"]},
                                ensure_ascii=False) for m in matches)
    intent = topic.get("intent") or {}
    return f"""
Trend Việt Nam: "{topic['keyword']}". Góc nối sản phẩm dự kiến: {intent.get('angle', '')}
Chấm độ phù hợp (fit 0-100) của từng sản phẩm với trend này cho video affiliate ngắn.
Chỉ dựa vào tên sản phẩm; không bịa tính năng. Sản phẩm không liên quan cho fit thấp.
Sản phẩm:
{rows}
Trả về DUY NHẤT JSON: {{"ranking": [{{"product_id": "...", "fit": 0, "reason": "ngắn"}}]}}
"""


def rerank(settings, topic: dict, matches: list[dict], ask=None) -> list[dict]:
    if not matches:
        return matches
    if ask is None:
        from ..workflow import _ask_json, _client
        client = _client(settings)

        def ask(prompt):
            return _ask_json(client, settings, prompt, temperature=0.2, max_tokens=1500)

    reply = ask(rerank_prompt(topic, matches)) or {}
    fits = {}
    for item in reply.get("ranking") or []:
        try:
            fits[str(item.get("product_id"))] = (max(0.0, min(100.0, float(item.get("fit")))),
                                                 str(item.get("reason") or "")[:200])
        except (TypeError, ValueError):
            continue
    out = []
    for m in matches:
        if m["product_id"] in fits:
            fit, why = fits[m["product_id"]]
            if fit < MIN_AI_FIT:
                continue
            m = {**m, "score": round(0.6 * m["score"] + 0.4 * fit, 1),
                 "reason": "; ".join(x for x in (f"AI: {why}" if why else "", m["reason"]) if x)}
        out.append(m)
    out.sort(key=lambda m: m["score"], reverse=True)
    return out


def intent_is_fresh(topic: dict) -> bool:
    at = topic.get("intent_at")
    return bool(topic.get("intent")) and (hours_since(at) or 1e9) < 7 * 24


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
