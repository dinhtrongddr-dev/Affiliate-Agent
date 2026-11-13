from types import SimpleNamespace

from agent_core import catalog
from agent_core.db import connect
from agent_core.shopee_intelligence.catalog import save_product
from agent_core.shopee_intelligence.unified_catalog import list_unified, mirror_product, reconcile


def legacy(path, platform='shopee', item='456'):
    catalog.import_products(path, [{'platform': platform, 'item_id': item, 'name': 'Owner legacy',
        'price': 250000, 'price_text': '250000', 'sold': 100, 'shop_name': 'Owner shop',
        'commission_rate': 8, 'commission': 20000, 'product_link': f'https://shopee.vn/product/123/{item}',
        'affiliate_link': 'https://s.shopee.vn/owner', 'image_url': '', 'tiktok_link': ''}], record_observations=False)


def test_backfill_both_directions_idempotent_no_observations(tmp_path):
    path = tmp_path / 'test.sqlite3'
    s = SimpleNamespace(database_path=path)
    legacy(path)
    first = reconcile(s)
    assert first['canonical_added'] == 1
    with connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM shopee_product_observations').fetchone()[0] == 0
    second = reconcile(s)
    assert all(second[k] == 0 for k in ('canonical_added', 'canonical_updated', 'legacy_added', 'legacy_updated'))
    save_product(path, {'product_id': 'custom789', 'item_id': '789', 'product_name': 'Canonical',
        'product_url': 'https://shopee.vn/product/123/789', 'current_price': 100000,
        'observed_at': '2026-01-01T00:00:00Z'})
    result = mirror_product(path, 'custom789')
    assert result['legacy_added'] == 1
    assert catalog.get_product(path, 'shopee:789')['price'] == 100000
    with connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM shopee_product_observations').fetchone()[0] == 1
    assert len(list_unified(s)) == 2


def test_owner_state_rights_and_tiktok_untouched(tmp_path):
    path = tmp_path / 'test.sqlite3'
    s = SimpleNamespace(database_path=path)
    legacy(path)
    legacy(path, platform='tiktok', item='888')
    tik_before = catalog.get_product(path, 'tiktok:888')
    catalog.update_product(path, 'shopee:456', notes='Keep me', status='USED', job_id='job-owner')
    save_product(path, {'product_id': 'custom456', 'item_id': '456', 'product_name': 'New facts',
        'product_url': 'https://shopee.vn/product/123/456', 'current_price': 300000,
        'affiliate_link': 'https://s.shopee.vn/canonical-owner', 'media_usage_rights': 'permission',
        'image_url': 'https://example.test/approved.png'})
    result = reconcile(s)
    assert result['conflicts'] == 1
    assert catalog.get_product(path, 'tiktok:888') == tik_before
    row = catalog.get_product(path, 'shopee:456')
    assert (row['notes'], row['status'], row['job_id']) == ('Keep me', 'USED', 'job-owner')
    assert row['affiliate_link'] == 'https://s.shopee.vn/owner'
    assert row['price'] == 300000
    unified = next(p for p in list_unified(s) if p['platform'] == 'shopee')
    assert unified['media_usage_rights'] == 'permission'
    assert unified['canonical_product_id'] == 'custom456'


def test_custom_duplicate_identity_one_display_row_no_deletion(tmp_path):
    path = tmp_path / 'test.sqlite3'
    s = SimpleNamespace(database_path=path)
    common = {'item_id': '456', 'product_name': 'Same identity', 'product_url': 'https://shopee.vn/product/123/456'}
    save_product(path, {**common, 'product_id': 'first'})
    save_product(path, {**common, 'product_id': 'second'})
    reconcile(s)
    assert len(list_unified(s)) == 1
    with connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM shopee_products').fetchone()[0] == 2


def test_canonical_only_visible_and_source_stamps_not_refreshed_on_read(tmp_path):
    path = tmp_path / 'test.sqlite3'
    s = SimpleNamespace(database_path=path)
    save_product(path, {'item_id': '456', 'product_name': 'Canonical only',
        'product_url': 'https://shopee.vn/product/123/456', 'current_price': 0})
    with connect(path) as conn:
        conn.execute("UPDATE shopee_products SET updated_at='2020-01-01T00:00:00Z',source_type='addlive_db'")
    row = list_unified(s)[0]
    assert row['price'] == 0
    assert row['freshness_status'] == 'cached'
    assert row['last_synced_at'] == '2020-01-01T00:00:00Z'
    assert row['owner_link_status'] == 'missing'
    reconcile(s)
    reconcile(s)
    assert list_unified(s)[0]['last_synced_at'] == '2020-01-01T00:00:00Z'
    with connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM shopee_product_observations').fetchone()[0] == 1


def test_hide_survives_reconcile_retains_evidence_and_explicit_readd(tmp_path):
    from agent_core.shopee_intelligence.unified_catalog import hide_product, unhide_product
    path = tmp_path / 'test.sqlite3'
    s = SimpleNamespace(database_path=path)
    save_product(path, {'item_id': '456', 'product_name': 'Hide me', 'product_url': 'https://shopee.vn/product/123/456'})
    reconcile(s)
    hide_product(path, 'shopee:456')
    reconcile(s)
    assert list_unified(s) == []
    assert catalog.get_product(path, 'shopee:456') is None
    with connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM shopee_products').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM shopee_product_observations').fetchone()[0] == 1
    unhide_product(path, '456')
    reconcile(s)
    assert len(list_unified(s)) == 1


def test_fill_owner_links_preserves_custom_links_timestamps_observations(tmp_path):
    from agent_core.shopee_intelligence.unified_catalog import populate_owner_links
    path = tmp_path / 'test.sqlite3'
    for item, link in [('456', ''), ('789', 'https://s.shopee.vn/custom-owner')]:
        save_product(path, {'item_id': item, 'product_name': 'Fixture',
            'product_url': f'https://shopee.vn/product/123/{item}', 'affiliate_link': link})
    with connect(path) as conn:
        before = [tuple(r) for r in conn.execute('SELECT product_id,updated_at FROM shopee_products ORDER BY product_id')]
    assert populate_owner_links(path, '111222333') == 1
    assert populate_owner_links(path, '111222333') == 0
    assert 'affiliate_id=111222333' in catalog.get_product(path, 'shopee:456')['affiliate_link']
    assert catalog.get_product(path, 'shopee:789')['affiliate_link'] == 'https://s.shopee.vn/custom-owner'
    with connect(path) as conn:
        assert [tuple(r) for r in conn.execute('SELECT product_id,updated_at FROM shopee_products ORDER BY product_id')] == before
        assert conn.execute('SELECT count(*) FROM shopee_product_observations').fetchone()[0] == 2
