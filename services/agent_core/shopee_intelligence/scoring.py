"""Transparent heuristic scoring for manually observed Shopee affiliate products."""

from datetime import datetime, timezone

RIGHTS_ALLOWED = {"owned", "licensed", "permission", "official"}


def _number(value, default=0.0):
    try:
        return float(value) if value is not None and str(value).strip() else default
    except (TypeError, ValueError):
        return default


def _date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def score_product(product, observations):
    """100-point heuristic: momentum 35, commission 25, quality 20, price 10, media 5, link 5."""
    notes = []
    velocity = None
    trend_score = 0.0
    if len(observations) >= 2:
        latest, previous = observations[0], observations[1]
        latest_at, previous_at = _date(latest.get("observed_at")), _date(previous.get("observed_at"))
        latest_sold, previous_sold = latest.get("total_sold"), previous.get("total_sold")
        if latest_at and previous_at and latest_sold is not None and previous_sold is not None:
            elapsed_hours = (latest_at - previous_at).total_seconds() / 3600
            if elapsed_hours >= 1:
                velocity = max(0.0, (_number(latest_sold) - _number(previous_sold)) * 24 / elapsed_hours)
                trend_score = (35 if velocity >= 100 else 30 if velocity >= 50 else
                               25 if velocity >= 20 else 20 if velocity >= 10 else
                               15 if velocity >= 5 else 10 if velocity > 0 else 2)
            else:
                notes.append("Hai lần ghi nhận cách nhau dưới 1 giờ.")
        else:
            notes.append("Thiếu mốc thời gian hoặc tổng lượt bán để tính tốc độ.")
    else:
        notes.append("Chưa đủ hai lần ghi nhận để xác định sản phẩm đang tăng tốc.")

    base_rate = _number(product.get("base_commission_rate"))
    xtra_rate = _number(product.get("xtra_commission_rate"))
    total_rate = (_number(product.get("commission_total_rate"))
                  if product.get("commission_total_rate") is not None else base_rate + xtra_rate)
    commission_score = (25 if total_rate >= 15 else 22 if total_rate >= 12 else
                        20 if total_rate >= 10 else 16 if total_rate >= 7 else
                        12 if total_rate >= 5 else 8 if total_rate >= 3 else
                        4 if total_rate > 0 else 0)
    if total_rate <= 0:
        notes.append("Chưa có tỷ lệ hoa hồng được xác nhận.")

    rating = _number(product.get("rating_star"))
    rating_count = int(_number(product.get("rating_count")))
    quality_score = (20 if rating >= 4.9 and rating_count >= 100 else
                     17 if rating >= 4.8 and rating_count >= 50 else
                     14 if rating >= 4.7 and rating_count >= 20 else
                     11 if rating >= 4.5 and rating_count >= 10 else
                     7 if rating >= 4.0 else 3 if rating > 0 else 0)
    if rating <= 0:
        notes.append("Chưa có dữ liệu đánh giá.")

    price = _number(product.get("current_price"))
    price_score = 10 if 50_000 <= price <= 500_000 else 7 if 20_000 <= price <= 1_000_000 else 4 if price > 0 else 0
    if price <= 0:
        notes.append("Chưa có giá bán để đánh giá.")

    rights = str(product.get("media_usage_rights") or "unknown").lower()
    has_media = bool(product.get("image_url") or product.get("video_url"))
    if rights in RIGHTS_ALLOWED and has_media:
        media_score = 5
    elif has_media:
        media_score = 2
        notes.append("Media chưa được xác nhận quyền sử dụng.")
    else:
        media_score = 0
        notes.append("Chưa có media sản phẩm.")

    has_link = bool(product.get("affiliate_link"))
    has_rate = total_rate > 0
    affiliate_score = 5 if has_link and has_rate else 3 if has_link else 2 if has_rate else 0
    if has_rate and not has_link:
        notes.append("Có hoa hồng nhưng chưa có Custom Link.")

    total = round(trend_score + commission_score + quality_score + price_score
                  + media_score + affiliate_score, 1)
    return {
        "win_score": total,
        "score_status": "OBSERVED" if velocity is not None else "PROVISIONAL",
        "daily_sales_velocity": round(velocity, 2) if velocity is not None else None,
        "commission_total_rate": round(total_rate, 3),
        "trend_score": trend_score,
        "commission_score": commission_score,
        "quality_score": quality_score,
        "price_score": price_score,
        "media_score": media_score,
        "affiliate_score": affiliate_score,
        "score_notes": notes,
    }
