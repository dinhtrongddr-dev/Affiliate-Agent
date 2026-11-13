import json
import sqlite3
from types import SimpleNamespace

from agent_core.creation_context import build_product_context


def test_missing_database_is_read_only(tmp_path):
    path = tmp_path / 'absent.sqlite3'
    result = build_product_context(SimpleNamespace(database_path=path), '123')
    assert not path.exists()
    assert result['identity']['item_id'] == '123'
    assert 'description' in result['missing_info']
    assert result['image_references'] == []


def test_context_exact_id_provenance_rights_and_no_provider_affiliate(tmp_path):
    path = tmp_path / 'fixture.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE shopee_products (item_id TEXT,product_name TEXT,image_url TEXT,'
                           'media_usage_rights TEXT,current_price REAL,source_type TEXT);'
                           'CREATE TABLE addlive_import_evidence (id INTEGER PRIMARY KEY,retrieved_at TEXT,response_json TEXT);')
        conn.execute('INSERT INTO shopee_products VALUES (?,?,?,?,?,?)',
                     ('123', 'Owner item', 'https://example.test/approved.png', 'permission', 100, 'addlive_db'))
        for item_id in ('123', '999'):
            response = {'status': 'success', 'dataSource': 'db', 'productInfo': {
                'itemId': item_id, 'imageUrl': 'https://example.test/provider.png',
                'catIds': [1, 2], 'catName': 'Inferred', 'lastUpdate': '2026-01-01 12:00:00',
                'priceStats': {'min': 50, 'last': 100, 'isLowest': False},
                'affLink': 'https://example.test/provider-affiliate'}}
            conn.execute('INSERT INTO addlive_import_evidence (retrieved_at,response_json) VALUES (?,?)',
                         ('2026-01-02T00:00:00Z', json.dumps(response)))
    before = path.read_bytes()
    result = build_product_context(SimpleNamespace(database_path=path), '123', experience='Owner says useful')
    assert path.read_bytes() == before
    assert result['provenance']['evidence_record_id'] == 1
    assert result['provenance']['provider_timezone'] == 'Asia/Ho_Chi_Minh'
    assert result['facts']['category_names_inferred'] is True
    assert result['facts']['price_statistics']['min'] == 50
    assert [x['approvedForRender'] for x in result['image_references']] == [True, False]
    assert 'provider-affiliate' not in json.dumps(result)
    assert result['experience']['verified_by_system'] is False


def test_malformed_evidence_is_ignored(tmp_path):
    path = tmp_path / 'fixture.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE addlive_import_evidence (id INTEGER PRIMARY KEY,retrieved_at TEXT,response_json TEXT)')
        for payload in ('bad json', '[]', '{"products":[1]}'):
            conn.execute('INSERT INTO addlive_import_evidence (retrieved_at,response_json) VALUES (?,?)', ('now', payload))
    assert build_product_context(SimpleNamespace(database_path=path), '123')['provenance']['evidence_record_id'] is None
