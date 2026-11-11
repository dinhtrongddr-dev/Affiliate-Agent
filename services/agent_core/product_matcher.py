from rapidfuzz import fuzz


def rank_products(video_context: str, offers: list[dict], min_score: int = 58, min_margin: int = 8) -> dict:
    """Rank Shopee offers against transcript/visual description; refuse ambiguous matches."""
    context = " ".join((video_context or "").lower().split())
    if not context:
        return {"status": "NO_CONTEXT", "candidates": []}

    ranked = []
    for offer in offers:
        name = str(offer.get("productName") or "").strip()
        if not name:
            continue
        score = max(
            fuzz.token_set_ratio(context, name.lower()),
            fuzz.partial_ratio(context, name.lower()),
        )
        ranked.append({
            "item_id": str(offer.get("itemId") or ""),
            "name": name,
            "score": round(float(score), 1),
            "commission_rate": offer.get("commissionRate"),
            "price_min": offer.get("priceMin"),
            "offer_link": offer.get("offerLink"),
            "image_url": offer.get("imageUrl"),
        })

    ranked.sort(key=lambda item: item["score"], reverse=True)
    if not ranked or ranked[0]["score"] < min_score:
        return {"status": "NO_CONFIDENT_MATCH", "candidates": ranked[:5]}
    if len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < min_margin:
        return {"status": "AMBIGUOUS", "candidates": ranked[:5]}
    if not ranked[0]["item_id"]:
        return {"status": "MISSING_PRODUCT_ID", "candidates": ranked[:5]}
    return {"status": "MATCHED", "product": ranked[0], "candidates": ranked[:5]}
