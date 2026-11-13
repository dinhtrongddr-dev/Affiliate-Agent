import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_core import catalog, db
from agent_core.settings import Settings
from agent_core.web import app as web

HEADERS = {'X-Requested-With': 'affiliate-agent'}


@pytest.fixture
def local(tmp_path, monkeypatch):
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / 'test.sqlite3',
                 media_root=tmp_path / 'media', muse_embedded=False)
    monkeypatch.setattr(web, 'get_settings', lambda: s)
    app = web.create_app(start_worker=False)
    with patch.object(web, 'setup_logging'), TestClient(app) as client:
        yield client, s


def product(s):
    catalog.import_products(s.database_path, [{
        'platform': 'shopee', 'item_id': '456', 'name': 'Fixture', 'price': 100000,
        'price_text': '100000', 'sold': 10, 'shop_name': 'Shop', 'commission_rate': 5,
        'commission': 5000, 'product_link': 'https://shopee.vn/product/123/456',
        'affiliate_link': '', 'image_url': 'https://example.test/unapproved.png', 'tiktok_link': '',
    }], record_observations=False)


def test_workspace_and_no_product_viral_job(local):
    c, s = local
    page = c.get('/create')
    assert page.status_code == 200
    assert 'Review sản phẩm' in page.text and 'Series nhân vật' in page.text
    response = c.post('/api/create', json={'creation_branch': 'viral_creator', 'topic': 'Một chuyện vui'}, headers=HEADERS)
    assert response.status_code == 200, response.text
    job = db.get_job(s.database_path, response.json()['job_id'])
    assert json.loads(job['brief_json'])['creation_branch'] == 'viral_creator'
    assert not job['affiliate_link']
    assert c.get('/jobs/' + job['id']).status_code == 200
    assert c.post('/api/create', json={'creation_branch': 'viral_creator', 'topic': 'x'}).status_code == 403


def test_review_without_link_preserves_image_boundary(local):
    c, s = local
    product(s)
    response = c.post('/api/create', json={'creation_branch': 'product_review', 'catalog_id': 'shopee:456',
        'experience': {'basis': 'owner_experience', 'notes': 'Tôi dùng thử'}}, headers=HEADERS)
    assert response.status_code == 200, response.text
    job = db.get_job(s.database_path, response.json()['job_id'])
    brief = json.loads(job['brief_json'])
    assert brief['offer']['imageUrl'] == ''
    assert brief['experience']['notes'] == 'Tôi dùng thử'
    assert brief['product_context']['identity']['item_id'] == '456'
    state = s.work_dir / job['id'] / 'plan_state.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({'plan': {'scenes': [{'shot': {'camera': 'Human camera fixture',
                     'action': 'Human action fixture'}}]}}), encoding='utf-8')
    assert 'data-catalog-id=' not in c.get('/jobs/' + job['id']).text
    db.update_job(s.database_path, job['id'], status='READY_FOR_REVIEW')
    page = c.get('/jobs/' + job['id'])
    assert '<b>Máy quay:</b> Human camera fixture' in page.text
    assert '<b>Hành động:</b> Human action fixture' in page.text
    assert 'data-catalog-id="shopee:456"' in page.text
    brief['offer']['catalogId'] = 'tiktok:456'
    db.update_job(s.database_path, job['id'], brief_json=json.dumps(brief))
    page = c.get('/jobs/' + job['id'])
    assert 'data-catalog-id="tiktok:456"' in page.text
    assert 'data-catalog-id="shopee:456"' not in page.text


def test_series_snapshot_and_template_edit(local):
    c, s = local
    response = c.post('/api/create', json={'creation_branch': 'template_series', 'template_id': 'suri-family',
                                          'topic': 'Suri giúp bố rửa bát'}, headers=HEADERS)
    assert response.status_code == 200, response.text
    brief = json.loads(db.get_job(s.database_path, response.json()['job_id'])['brief_json'])
    assert brief['template']['id'] == 'suri-family'
    assert brief['template']['version'] == 1
    assert brief['template_references'] == []
    assert c.post('/api/creative-templates', json={'id': '../bad'}, headers=HEADERS).status_code == 400
    assert c.post('/api/creative-templates/suri-family/references',
        data={'character_id': 'suri'}, files={'file': ('bad.png', b'bad', 'image/png')}, headers=HEADERS).status_code == 400


def test_affiliate_route_protects_non_api_post(local, monkeypatch):
    c, s = local
    called = []
    monkeypatch.setattr(web.workflow, 'attach_affiliate', lambda settings, jid, pid: called.append((jid, pid)) or {'ok': True}, raising=False)
    assert c.post('/jobs/test/affiliate', json={'catalog_id': 'shopee:456'}).status_code == 403
    assert c.post('/jobs/test/affiliate', json={'catalog_id': 'shopee:456'},
                  headers={**HEADERS, 'Origin': 'https://evil.example'}).status_code == 403
    assert c.post('/jobs/test/affiliate', json={'catalog_id': 'shopee:456'}, headers=HEADERS).status_code == 200
    assert called == [('test', 'shopee:456')]


def test_safe_noncommercial_trend_visible_and_creates_creator_without_product(local):
    from agent_core.trend_intel import store
    c, s = local
    key = store.record_signal(s.database_path, {'source': 'manual', 'keyword': 'Chuyện gia đình fixture'})
    store.update_topic(s.database_path, key, safety='OK', status='NOT_COMMERCIAL',
                       intent_json={'commercial': False, 'reason': 'Không có nhu cầu mua'})
    page = c.get('/trends')
    assert 'Chuyện gia đình fixture' in page.text
    assert 'Chưa có affiliate tự nhiên' in page.text
    assert 'Viết video viral' in page.text
    assert 'Chuyện gia đình fixture' not in c.get('/trends?view=skipped').text
    assert 'Chuyện gia đình fixture' not in c.get('/trends?view=products').text
    response = c.post('/api/intel/create-job', json={'key': key}, headers=HEADERS)
    assert response.status_code == 200, response.text
    job = db.get_job(s.database_path, response.json()['job_id'])
    assert json.loads(job['brief_json'])['creation_branch'] == 'viral_creator'
    assert not job['product_item_id'] and not job['affiliate_link']
