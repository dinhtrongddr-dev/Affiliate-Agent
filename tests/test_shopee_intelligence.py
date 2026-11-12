from pathlib import Path

import pytest

from agent_core import catalog
from agent_core.shopee_intelligence import (
    get_product,
    import_products_csv,
    product_to_offer,
    save_product,
)


def test_dashboard_csv_import_records_offer_and_snapshot(tmp_path: Path):
    db_path = tmp_path / "agent.sqlite3"
    payload = (
        "product_name,product_url,item_id,current_price,total_sold,commission_rate,affiliate_link\n"
        "Test product,https://shopee.vn/test-i.123.456,456,339.000,120,12%,https://s.shopee.vn/abc\n"
    ).encode("utf-8")

    result = import_products_csv(db_path, payload, "affiliate-offers.csv")
    product = get_product(db_path, "456")

    assert result["imported"] == 1
    assert product is not None
    assert product["current_price"] == 339000
    assert product["total_sold"] == 120
    assert product["commission_total_rate"] == 12
    assert product["affiliate_link"] == "https://s.shopee.vn/abc"
    assert product["score_status"] == "PROVISIONAL"


def test_two_snapshots_calculate_sales_velocity(tmp_path: Path):
    db_path = tmp_path / "agent.sqlite3"
    common = {
        "product_name": "Test product",
        "product_url": "https://shopee.vn/test-i.123.456",
        "item_id": "456",
        "current_price": 200000,
        "rating_star": 4.9,
        "rating_count": 120,
        "commission_total_rate": 12,
        "affiliate_link": "https://s.shopee.vn/abc",
        "media_usage_rights": "permission",
        "image_url": "https://cdn.example.test/product.jpg",
    }
    save_product(db_path, {**common, "total_sold": 1000, "observed_at": "2026-11-10T12:00:00+00:00"})
    latest = save_product(
        db_path,
        {**common, "total_sold": 1100, "observed_at": "2026-11-11T12:00:00+00:00"},
    )

    assert latest["score_status"] == "OBSERVED"
    assert latest["daily_sales_velocity"] == 100
    assert latest["trend_score"] == 35
    assert product_to_offer(latest)["imageUrl"] == common["image_url"]


def test_media_is_not_passed_to_video_pipeline_without_rights(tmp_path: Path):
    db_path = tmp_path / "agent.sqlite3"
    product = save_product(
        db_path,
        {
            "product_name": "Test product",
            "product_url": "https://shopee.vn/test-i.123.456",
            "item_id": "456",
            "image_url": "https://cdn.example.test/product.jpg",
            "media_usage_rights": "unknown",
        },
    )
    assert product_to_offer(product)["imageUrl"] == ""


def test_non_shopee_product_url_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="Shopee Việt Nam"):
        save_product(
            tmp_path / "agent.sqlite3",
            {"product_name": "Not Shopee", "product_url": "https://example.com/item"},
        )


def test_official_bulk_link_csv_keeps_revenue_separate_from_units_sold(tmp_path: Path):
    db_path = tmp_path / "agent.sqlite3"
    payload = (
        "Mã sản phẩm,Tên sản phẩm,Giá,Doanh thu,Lượt bán,Tỉ lệ hoa hồng,Hoa hồng,"
        "Link sản phẩm,Link ưu đãi\n"
        "456,Test product,₫199.000,₫2.500.000,300,8.5%,₫16.915,"
        "https://shopee.vn/test-i.123.456,https://s.shopee.vn/abc\n"
    ).encode("utf-8")

    products = catalog.parse_csv(payload)
    result = catalog.import_products(db_path, products)
    intel = get_product(db_path, "456")

    assert products[0]["sold"] == 300
    assert products[0]["revenue"] == 2500000
    assert result["intelligence_snapshots"] == 1
    assert intel is not None
    assert intel["total_sold"] == 300
    assert intel["commission_total_rate"] == 8.5
