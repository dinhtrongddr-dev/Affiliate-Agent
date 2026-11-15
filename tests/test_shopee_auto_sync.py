"""Daily ingestion tests use isolated SQLite databases and synthetic providers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest

from agent_core.db import connect
from agent_core.shopee_intelligence import auto_sync as sync
from agent_core.shopee_intelligence.addlive_client import AddLiveError
from agent_core.shopee_intelligence.catalog import get_product


@pytest.fixture
def settings(tmp_path, monkeypatch):
    value = SimpleNamespace(database_path=tmp_path / 'sync.sqlite3',
                            timezone='Asia/Ho_Chi_Minh', seed_keywords=['home', 'tech'])
    monkeypatch.setattr(sync, '_sleep', lambda seconds: None)
    monkeypatch.setattr(sync.addlive_config, 'summary', lambda _: {'configured': True})
    monkeypatch.setattr(sync.addlive_config, 'load', lambda _: {
        'api_key': 'synthetic', 'base_rate': None, 'cap': 40000,
        'affiliate_id': '', 'sub_id': ''})
    return value


class Client:
    def __init__(self, failure=None, cooldown=0, partial=False):
        self.calls = []
        self.failure, self.cooldown, self.partial = failure, cooldown, partial

    def offers(self, kind, **kwargs):
        self.calls.append(('offers', kind, kwargs))
        if self.failure:
            raise self.failure
        return {'status': 'success', 'products': [
            {'itemId': '123', 'affLink': 'https://provider.invalid/owner'}],
            'limits': {'sourceCooldownSeconds': self.cooldown}}

    def batch(self, references, **kwargs):
        self.calls.append(('batch', references, kwargs))
        return {'status': 'success', 'products': [
            {'status': 'error'} if self.partial else {
                'status': 'success', 'itemId': item, 'dataSource': 'api',
                'productInfo': {'itemId': item, 'productName': 'Fixture',
                                'productLink': f'https://shopee.vn/product/4/{item}',
                                'price': 100000, 'sales': 100}}
            for item in references], 'summary': {'fromApi': len(references)}}


def at(day=13, hour=7):
    return datetime(2026, 11, day, hour)


def test_config_and_validation(settings):
    assert sync.config(settings)['enabled'] is True
    assert sync.save_config(settings, {'max_api': 20, 'enabled': False})['max_api'] == 20
    assert sync.config(settings)['enabled'] is False
    with connect(settings.database_path) as conn:
        assert 'synthetic' not in conn.execute('SELECT values_json FROM shopee_sync_config').fetchone()[0]


@pytest.mark.parametrize('values', [{'max_api': 21}, {'max_items': 1001}, {'discovery_limit': 1001},
                                    {'local_time': '24:00'}, {'enabled': 1}, {'api_key': 'secret'}])
def test_invalid_config(settings, values):
    with pytest.raises(ValueError):
        sync.save_config(settings, values)


def test_schedule_daily_and_missed_catchup(settings):
    assert sync.enqueue_due(settings, at(hour=6)) is None
    assert sync.enqueue_due(settings, at())
    assert sync.enqueue_due(settings, at()) is None
    assert sync.enqueue_due(settings, at(14, 6)) is None
    assert sync.enqueue_due(settings, at(15, 6))
    with connect(settings.database_path) as conn:
        assert {r[0] for r in conn.execute('SELECT kind FROM tasks')} == {'sync_shopee'}


def test_concurrent_enqueue(settings):
    sync.config(settings)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: sync.enqueue_due(settings, at()), range(4)))
    assert sum(value is not None for value in results) == 1


def test_real_import_and_daily_reservation(settings):
    client = Client()
    result = sync.run(settings, at(), client)
    assert result['status'] == 'completed'
    assert result['imported'] == 1
    assert [call[0] for call in client.calls] == ['offers', 'offers', 'batch']
    assert client.calls[2][2]['max_api'] == 10
    assert get_product(settings.database_path, '123')['affiliate_link'] is None
    assert sync.run(settings, at(), client)['status'] == 'skipped'
    assert len(client.calls) == 3


@pytest.mark.parametrize('partial,expected', [(True, 'partial'), (False, 'completed')])
def test_import_status(settings, partial, expected):
    assert sync.run(settings, at(), Client(partial=partial))['status'] == expected


def test_cooldown_stops_batch(settings):
    client = Client(cooldown=300)
    result = sync.run(settings, at(), client)
    assert result['status'] == 'blocked'
    assert result['retry_after'] == 300
    assert len(client.calls) == 1


def test_error_persisted_without_secret_or_replay(settings):
    client = Client(failure=AddLiveError('private synthetic secret', retry_after=180))
    result = sync.run(settings, at(), client)
    assert result['status'] == 'failed'
    assert result['retry_after'] == 180
    assert 'private' not in str(result)
    assert sync.run(settings, at(), client)['status'] == 'skipped'
    assert len(client.calls) == 1


def test_expired_lease_never_replays(settings):
    sync.enqueue_due(settings, at())
    with connect(settings.database_path) as conn:
        conn.execute("UPDATE shopee_sync_runs SET status='running',lease_until='2026-01-01T00:00:00+00:00'")
    client = Client()
    assert sync.run(settings, at(), client)['status'] == 'skipped'
    with connect(settings.database_path) as conn:
        assert conn.execute('SELECT status FROM shopee_sync_runs').fetchone()[0] == 'unknown'
    assert client.calls == []


def test_disabled_manual_and_missing_key(settings, monkeypatch):
    sync.save_config(settings, {'enabled': False})
    assert sync.run(settings, at(), Client())['reason'] == 'disabled'
    assert sync.enqueue_due(settings, at()) is None
    assert sync.enqueue_due(settings, at(), force=True)
    assert sync.run(settings, at(), Client())['status'] == 'completed'
    monkeypatch.setattr(sync.addlive_config, 'summary', lambda _: {'configured': False})
    assert sync.run(settings, at(14), Client())['status'] == 'blocked'


def test_import_crash_reports_uncertain_count(settings, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('potentially committed rows')
    monkeypatch.setattr(sync.addlive_service, 'import_batch', fail)
    result = sync.run(settings, at(), Client())
    assert result['status'] == 'failed'
    assert result['imported'] is None
    assert result['imports_may_be_partial'] is True


def test_old_queue_day_never_uses_today_budget(settings):
    client = Client()
    assert sync.run(settings, at(14), client, sync_day='2026-11-13')['reason'] == 'expired_queue_day'
    assert client.calls == []
    assert sync.run(settings, at(14), client)['status'] == 'completed'


def test_standalone_and_worker_claim_same_day_once(settings):
    sync.enqueue_due(settings, at())
    client = Client()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sync.run(settings, at(), client), range(2)))
    assert sorted(r['status'] for r in results) == ['completed', 'skipped']
    assert [c[0] for c in client.calls] == ['offers', 'offers', 'batch']


def test_hidden_product_not_rediscovered_or_refreshed(settings):
    from agent_core.shopee_intelligence.unified_catalog import hide_product
    sync.run(settings, at(), Client())
    hide_product(settings.database_path, 'shopee:123')
    client = Client()
    result = sync.run(settings, at(14), client)
    assert result['requested'] == 0
    assert [c[0] for c in client.calls] == ['offers', 'offers']


def test_worker_dispatch_passes_day_and_stays_data_only(settings, monkeypatch):
    from agent_core.worker import handle_task
    seen = []
    monkeypatch.setattr(sync, 'run', lambda s, **kw: seen.append(kw) or {'status': 'completed'})
    assert handle_task(settings, {'kind': 'sync_shopee', 'payload': {'sync_day': '2026-11-13'}})['status'] == 'completed'
    assert seen == [{'sync_day': '2026-11-13'}]


def test_authentication_error_actionable_without_private_body(settings):
    result = sync.run(settings, at(), Client(failure=AddLiveError('PRIVATE', code='http_401')))
    assert result['error_code'] == 'http_401'
    assert '401' in result['error'] and 'PRIVATE' not in str(result)


def test_discovery_alternates_with_refresh_before_source_budget(settings, monkeypatch):
    monkeypatch.setattr(sync, '_tracked_ids', lambda *a: ['998', '997', '996'])
    client = Client()
    sync.run(settings, at(), client)
    assert client.calls[2][1] == ['123', '998', '997', '996']


class PagedClient:
    """Synthetic source: 50 rows per page, 3 pages per keyword, batch of at most 100."""
    def __init__(self, cooldown_rows=0, cooldown=7, stale_info=False):
        self.calls, self.cooldown_rows, self.cooldown = [], cooldown_rows, cooldown
        self.stale_info = stale_info

    def offers(self, kind, keyword='', page=1, limit=20, **kwargs):
        self.calls.append(('offers', keyword, page, limit))
        base = {'home': 1000, 'tech': 5000}[keyword] + (page - 1) * 50
        return {'status': 'success', 'hasNextPage': page < 3,
                'products': [{'itemId': str(base + i)} for i in range(min(limit, 50))], 'limits': {}}

    def batch(self, references, **kwargs):
        assert len(references) <= 100
        self.calls.append(('batch', list(references)))
        rows, cooled = [], self.cooldown_rows
        for item in references:
            if cooled:
                cooled -= 1
                rows.append({'status': 'stale', 'itemId': int(item), 'input': item, 'dataSource': 'db_stale',
                             'reason': 'source_cooldown', 'productInfo': {} if not self.stale_info else {
                                 'itemId': item, 'productName': 'Old', 'dataSource': 'db_stale',
                                 'productLink': f'https://shopee.vn/product/4/{item}', 'price': 90000,
                                 'lastUpdate': '2026-11-01 08:00:00'}})
            else:
                rows.append({'status': 'success', 'itemId': item, 'dataSource': 'api', 'productInfo': {
                    'itemId': item, 'productName': 'Fixture', 'productLink': f'https://shopee.vn/product/4/{item}',
                    'price': 100000, 'sales': 100}})
        self.cooldown_rows = 0
        return {'status': 'success', 'products': rows, 'summary': {'fromApi': len(rows)},
                'limits': {'sourceCooldownSeconds': self.cooldown if any(r['status'] == 'stale' for r in rows) else 0}}


def test_discovery_pages_through_keywords_and_batches_by_100(settings):
    sync.save_config(settings, {'discovery_limit': 250, 'max_items': 250})
    client = PagedClient()
    result = sync.run(settings, at(), client)
    offers = [c for c in client.calls if c[0] == 'offers']
    batches = [c for c in client.calls if c[0] == 'batch']
    assert result['discovered'] == 250 and result['imported'] == 250 and result['status'] == 'completed'
    assert all(c[3] <= 50 for c in offers)
    # round-robin over both keywords, page by page
    assert [(c[1], c[2]) for c in offers][:4] == [('tech', 1), ('home', 1), ('tech', 2), ('home', 2)]
    assert [len(b[1]) for b in batches] == [100, 100, 50]


def test_short_cooldown_is_waited_out_and_rows_retried(settings, monkeypatch):
    waits = []
    monkeypatch.setattr(sync, '_sleep', waits.append)
    sync.save_config(settings, {'discovery_limit': 30, 'max_items': 30})
    client = PagedClient(cooldown_rows=30, cooldown=7)
    result = sync.run(settings, at(), client)
    batches = [c for c in client.calls if c[0] == 'batch']
    assert len(batches) == 2 and len(batches[1][1]) == 30      # all 30 retried once
    assert waits == [8]
    assert result['imported'] == 30 and result['status'] == 'completed'


def test_old_hard_caps_follow_new_defaults(settings):
    with connect(settings.database_path) as conn:
        sync.config(settings)
        conn.execute("INSERT INTO shopee_sync_config VALUES (1, ?)", ('{"discovery_limit": 20, "max_items": 100}',))
    cfg = sync.config(settings)
    assert cfg['discovery_limit'] == sync.DEFAULTS['discovery_limit'] and cfg['max_items'] == sync.DEFAULTS['max_items']


def test_stale_cache_counts_once_and_is_refreshed_after_cooldown(settings):
    sync.save_config(settings, {'discovery_limit': 30, 'max_items': 30})
    client = PagedClient(cooldown_rows=30, cooldown=7, stale_info=True)
    result = sync.run(settings, at(), client)
    batches = [c for c in client.calls if c[0] == 'batch']
    assert len(batches) == 2                       # cache now, then one try for fresh data
    assert result['imported'] == 30 and result['status'] == 'completed'
    assert get_product(settings.database_path, batches[0][1][0])['source_type'] == 'addlive_api'
