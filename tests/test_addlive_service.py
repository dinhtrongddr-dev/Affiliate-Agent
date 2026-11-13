"""Synthetic provider fixtures; no configured credentials or network."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from agent_core import catalog as video_catalog
from agent_core.db import connect
from agent_core.shopee_intelligence.catalog import get_product, save_product
from agent_core.shopee_intelligence.addlive_service import (
    import_batch, import_product, normalize_product, retrieve_history,
)


def info(**changes):
    return {'itemId': '456', 'productName': 'Fixture product',
            'productLink': 'https://shopee.vn/product/123/456?utm_source=old',
            'price': 100000, 'sales': 100, 'sellerRate': .03, 'shopeeRate': .08,
            'commission': 11000, 'dataSource': 'api', **changes}


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def batch(self, references, **kwargs):
        self.calls.append((references, kwargs))
        return self.response

    product = batch
    history = batch


def settings(tmp_path):
    return SimpleNamespace(database_path=tmp_path / 'isolated.sqlite3')


def observations(path):
    with connect(path) as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM shopee_product_observations')]


def test_normalize_rates_zero_and_missing():
    p = normalize_product(info(shopeeRatePercent=0, shopeeRate=.08, commission=0))
    assert p['base_commission_rate'] == 0
    assert p['xtra_commission_rate'] == 3
    assert p['commission_total_rate'] == 3
    assert p['commission_amount'] == 0
    assert p['product_url'] == 'https://shopee.vn/product/123/456'
    p = normalize_product(info(sales=None, sellerRate=None, shopeeRate=None, commission=None))
    assert p['total_sold'] is None
    assert p['commission_total_rate'] is None
    assert p['commission_amount'] is None


@pytest.mark.parametrize('changes', [
    {'productName': ''}, {'productLink': 'https://evil.example/product/123/456'},
    {'productLink': 'https://s.shopee.vn/abc'}, {'itemId': '999'},
])
def test_bad_product_not_normalized(changes):
    with pytest.raises(ValueError):
        normalize_product(info(**changes))


def test_fresh_import_mirrors_catalog_and_creates_one_observation(tmp_path):
    s = settings(tmp_path)
    c = Client({'status': 'success', 'productInfo': info()})
    result = import_product(s, c, '456', base_rate=8, cap=0)
    assert result['imported'] == 1
    assert len(observations(s.database_path)) == 1
    assert get_product(s.database_path, '456')['affiliate_link'] is None
    assert video_catalog.get_product(s.database_path, 'shopee:456')['name'] == 'Fixture product'
    assert c.calls[0][1] == {'base_rate': 8, 'cap': 0}


def test_cache_does_not_create_sales_observation(tmp_path):
    s = settings(tmp_path)
    result = import_product(s, Client({'status': 'success', 'productInfo': info(dataSource='db')}), '456')
    assert result['imported'] == 1
    assert get_product(s.database_path, '456')['score_status'] == 'PROVISIONAL'
    assert observations(s.database_path) == []


def test_cache_uses_provider_timestamp_not_new_retrieval_as_freshness(tmp_path):
    s = settings(tmp_path)
    response = {'status': 'success', 'productInfo': info(dataSource='db', lastUpdate='2026-01-01 12:00:00')}
    import_product(s, Client(response), '456')
    assert get_product(s.database_path, '456')['updated_at'] == '2026-01-01T05:00:00+00:00'
    import_product(s, Client(response), '456')
    assert get_product(s.database_path, '456')['updated_at'] == '2026-01-01T05:00:00+00:00'
    assert observations(s.database_path) == []


def test_batch_mixed_results_retains_provider_order_and_limits(tmp_path):
    s = settings(tmp_path)
    rows = [
        {'status': 'success', 'productInfo': info()},
        {'status': 'stale', 'productInfo': info(sales=999)},
        {'status': 'success', 'dataSource': 'fallback', 'productInfo': info()},
        {'status': 'skipped', 'itemId': '999', 'reason': 'source_cooldown'},
        {'status': 'success', 'warning': 'Old cache', 'productInfo': info(sales=888)},
    ]
    limits = {'sourceRateLimited': True, 'sourceCooldownSeconds': 120}
    c = Client({'status': 'success', 'products': rows, 'summary': {'stale': 1}, 'limits': limits})
    result = import_batch(s, c, ['456'], cache_only=True, max_api=0)
    assert result['imported'] == 1
    assert [r['status'] for r in result['results']] == [r['status'] for r in rows]
    assert result['limits'] == limits
    assert len(c.calls) == 1
    assert len(observations(s.database_path)) == 1
    assert get_product(s.database_path, '456')['total_sold'] == 100


def test_preserves_owner_link_rights_asset_and_manual_catalog_fields(tmp_path):
    s = settings(tmp_path)
    save_product(s.database_path, {'product_name': 'Old',
        'product_url': 'https://shopee.vn/product/123/456', 'item_id': '456',
        'affiliate_link': 'https://s.shopee.vn/owner', 'media_usage_rights': 'permission',
        'image_url': 'https://assets.example/approved.jpg'})
    video_catalog.import_products(s.database_path, [{
        'platform': 'shopee', 'item_id': '456', 'name': 'Old', 'price': None,
        'price_text': '', 'sold': None, 'shop_name': '', 'commission_rate': None,
        'commission': None, 'product_link': 'https://shopee.vn/product/123/456',
        'affiliate_link': 'https://s.shopee.vn/owner', 'image_url': '', 'tiktok_link': '',
    }], record_observations=False)
    video_catalog.update_product(s.database_path, 'shopee:456', notes='owner note', status='USED', job_id='ownerjob')
    import_product(s, Client({'status': 'success', 'productInfo': info(imageUrl='https://assets.example/new.jpg')}),
                   '456', affiliate_id='other')
    p = get_product(s.database_path, '456')
    assert p['affiliate_link'] == 'https://s.shopee.vn/owner'
    assert p['media_usage_rights'] == 'permission'
    assert p['image_url'] == 'https://assets.example/approved.jpg'
    legacy = video_catalog.get_product(s.database_path, 'shopee:456')
    assert (legacy['notes'], legacy['status'], legacy['job_id']) == ('owner note', 'USED', 'ownerjob')


def test_affiliate_generated_only_for_explicit_owner_id(tmp_path):
    s = settings(tmp_path)
    import_product(s, Client({'status': 'success', 'productInfo': info()}), '456',
                   affiliate_id='owner123', sub_id='campaign-post')
    link = get_product(s.database_path, '456')['affiliate_link']
    assert parse_qs(urlparse(link).query) == {'origin_link': ['https://shopee.vn/product/123/456'],
        'affiliate_id': ['owner123'], 'sub_id': ['campaign-post']}


@pytest.mark.parametrize('has_intelligence', [False, True])
def test_missing_api_details_preserve_merged_and_legacy_values(tmp_path, has_intelligence):
    s = settings(tmp_path)
    video_catalog.import_products(s.database_path, [{
        'platform': 'shopee', 'item_id': '456', 'name': 'Owner product', 'price': 250000,
        'price_text': '250000', 'sold': 80, 'shop_name': 'Owner shop',
        'commission_rate': 12, 'commission': 30000,
        'product_link': 'https://shopee.vn/product/123/456',
        'affiliate_link': 'https://s.shopee.vn/owner', 'image_url': '', 'tiktok_link': '',
    }], record_observations=False)
    if has_intelligence:
        save_product(s.database_path, {'product_name': 'Owner product',
            'product_url': 'https://shopee.vn/product/123/456', 'item_id': '456',
            'current_price': 300000, 'shop_name': 'Intelligence shop',
            'observed_at': '2026-01-01T00:00:00+00:00'})
    before = len(observations(s.database_path)) if has_intelligence else 0
    result = import_product(s, Client({'status': 'success', 'productInfo': info(
        price=None, shopName=None, sales=None, sellerRate=None, shopeeRate=None,
        commission=None)}), '456')
    legacy = video_catalog.get_product(s.database_path, 'shopee:456')
    assert legacy['price'] == (300000 if has_intelligence else 250000)
    assert legacy['shop_name'] == ('Intelligence shop' if has_intelligence else 'Owner shop')
    assert legacy['sold'] == 80
    assert legacy['commission_rate'] == 12
    assert legacy['commission'] == 30000
    assert len(observations(s.database_path)) == before + 1
    assert observations(s.database_path)[-1]['total_sold'] is None
    assert result['imported'] == 1


def test_fresh_imports_in_same_second_remain_separate_observations(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from agent_core.shopee_intelligence import addlive_service

    stamps = iter([datetime(2026, 11, 12, 12, 0, 0, 100, tzinfo=timezone.utc),
                   datetime(2026, 11, 12, 12, 0, 0, 200, tzinfo=timezone.utc)])

    class Clock:
        @staticmethod
        def now(tz):
            return next(stamps)

    monkeypatch.setattr(addlive_service, 'datetime', Clock)
    s = settings(tmp_path)
    for sales in (100, 101):
        import_product(s, Client({'status': 'success', 'productInfo': info(sales=sales)}), '456')
    rows = observations(s.database_path)
    assert len(rows) == 2
    assert [row['total_sold'] for row in rows] == [100, 101]
    assert rows[0]['observed_at'] != rows[1]['observed_at']


def test_history_preserves_recorded_and_suspect_and_no_db(tmp_path):
    response = {'status': 'success', 'normalized': True, 'limits': {'truncated': True},
        'items': [{'price': {'stats': {'min': 1, 'last': 2, 'isLowest': False},
                             'allTime': {'priceChange7d': 1}},
                   'commission': {'warning': 'tier', 'points': [{'suspect': True, 'recorded': {'shopeeRate': .02}}]}}]}
    c = Client(response)
    result = retrieve_history(c, ['456'])
    assert result['items'] == response['items']
    assert result['normalized'] is True
    assert len(result['warnings']) == 3
    assert not list(tmp_path.iterdir())
