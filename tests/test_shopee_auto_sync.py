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


@pytest.mark.parametrize('values', [{'max_api': 21}, {'max_items': 101}, {'discovery_limit': 21},
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
    assert [call[0] for call in client.calls] == ['offers', 'batch']
    assert client.calls[1][2]['max_api'] == 10
    assert get_product(settings.database_path, '123')['affiliate_link'] is None
    assert sync.run(settings, at(), client)['status'] == 'skipped'
    assert len(client.calls) == 2


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
    assert [c[0] for c in client.calls] == ['offers', 'batch']


def test_hidden_product_not_rediscovered_or_refreshed(settings):
    from agent_core.shopee_intelligence.unified_catalog import hide_product
    sync.run(settings, at(), Client())
    hide_product(settings.database_path, 'shopee:123')
    client = Client()
    result = sync.run(settings, at(14), client)
    assert result['requested'] == 0
    assert len(client.calls) == 1


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
    assert client.calls[1][1] == ['123', '998', '997', '996']
