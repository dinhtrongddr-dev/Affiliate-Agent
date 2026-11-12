"""Trend Intelligence run: collect -> safety -> score -> AI intent -> product match -> rescore."""
import logging

from . import collectors, matcher, safety, scoring, store

log = logging.getLogger("affiliate_agent.trend_intel")

RECENT_HOURS = 72


def _win_scores(db_path) -> dict:
    try:
        from ..shopee_intelligence import list_products
        return {str(p.get("item_id")): p.get("win_score")
                for p in list_products(db_path, limit=2000) if p.get("item_id")}
    except Exception:  # noqa: BLE001 - intelligence data is optional
        return {}


def rescore(settings, key: str, best_product: float | None = None) -> dict | None:
    db_path = settings.database_path
    topic = store.get_topic(db_path, key)
    if not topic:
        return None
    if best_product is None:
        found = store.matches_for(db_path, key)
        best_product = found[0]["score"] if found else None
    result = scoring.score_topic(topic, store.observations(db_path, key, limit=60), settings,
                                 best_product)
    total = None if topic["safety"] == "BLOCKED" else result["total"]
    store.update_topic(db_path, key, scores_json=result, total_score=total,
                       score_status=result["status"], lifecycle=result["lifecycle"])
    return result


def run(settings, collect: bool = True, ask=None) -> dict:
    """One full pass. `ask` overrides the AI call (tests). Never raises on a source error."""
    db_path = settings.database_path
    store.ensure_schema(db_path)
    summary: dict = {"collected": collectors.collect_all(settings) if collect else None}

    recent = store.list_topics(db_path, 500, since_hours=RECENT_HOURS)
    blocked = 0
    for t in recent:
        if t["safety"] == "UNCHECKED":
            is_blocked, reason = safety.check(t["keyword"], t.get("notes") or "")
            store.update_topic(db_path, t["key"], safety="BLOCKED" if is_blocked else "OK",
                               safety_reason=reason)
            blocked += is_blocked
    summary["blocked_by_rules"] = blocked

    for t in recent:
        rescore(settings, t["key"])

    # AI intent for the best-ranked open topics that have no fresh verdict yet.
    candidates = [t for t in store.list_topics(db_path, 500, include_blocked=False,
                                               since_hours=RECENT_HOURS)
                  if t["status"] in ("NEW", "MATCHED", "NO_MATCH") and not matcher.intent_is_fresh(t)]
    candidates = candidates[: settings.trend_ai_topics_per_run]
    intents: dict = {}
    if candidates:
        try:
            intents = matcher.analyse_intents(settings, candidates, ask=ask)
        except Exception as exc:  # noqa: BLE001 - AI down: keep provisional scores
            log.warning("trend intent analysis failed: %s", exc)
            summary["ai_error"] = str(exc)[:300]
    for key, intent in intents.items():
        fields = {"intent_json": intent, "intent_at": matcher.utcnow()}
        if intent["sensitive"]:
            fields.update(safety="BLOCKED", safety_reason=f"AI: {intent['reason']}"[:300])
        elif not intent["commercial"] or intent["purchase_intent"] < settings.trend_min_purchase_intent:
            fields["status"] = "NOT_COMMERCIAL"
        store.update_topic(db_path, key, **fields)
    summary["ai_analysed"] = len(intents)

    # Match products for commercial topics.
    products = None
    win = _win_scores(db_path)
    matched = 0
    reranked = 0
    open_topics = [t for t in store.list_topics(db_path, 500, include_blocked=False,
                                                since_hours=RECENT_HOURS)
                   if t.get("intent") and t["status"] in ("NEW", "MATCHED", "NO_MATCH")]
    for t in open_topics:
        if products is None:
            from .. import catalog
            products = catalog.list_products(db_path, "score", limit=2000)
        found = matcher.match_products(settings, t, products=products, win_scores=win)
        if found and reranked < settings.trend_rerank_topics:
            try:
                found = matcher.rerank(settings, t, found, ask=ask)
                reranked += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("rerank failed for %s: %s", t["key"], exc)
        store.save_matches(db_path, t["key"], found)
        store.update_topic(db_path, t["key"], status="MATCHED" if found else "NO_MATCH")
        rescore(settings, t["key"], found[0]["score"] if found else None)
        matched += bool(found)
    summary["matched_topics"] = matched
    summary["catalog_products"] = len(products or [])
    return summary


def add_manual(settings, text: str, source: str = "tiktok_manual") -> int:
    count = 0
    for signal in collectors.parse_manual(text, source):
        if store.record_signal(settings.database_path, signal):
            count += 1
    return count
