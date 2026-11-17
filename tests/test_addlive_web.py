"""Local API/DPAPI contracts; no live provider or worker is started."""
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_core import catalog
from agent_core.settings import Settings
from agent_core.shopee_intelligence import addlive_config as config
from agent_core.shopee_intelligence.addlive_client import AddLiveClient
from agent_core.web import addlive as routes
from agent_core.web import app as web

HEADERS = {"X-Requested-With": "affiliate-agent"}
FAKE_KEY = "FAKE-ADDLIVE-KEY-ONLY-FOR-TESTS"


@pytest.fixture
def local(tmp_path, monkeypatch):
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "qa.sqlite3",
                 media_root=tmp_path / "media", muse_embedded=False, tts_enabled=False)
    monkeypatch.setattr(web, "get_settings", lambda: s)
    monkeypatch.setattr(routes.shopee_schedule, "install", lambda *a, **k: {
        "installed": True, "managed": True, "enabled": k.get("enabled", True)})
    monkeypatch.setattr(routes.shopee_schedule, "status", lambda *a, **k: {
        "installed": True, "managed": True, "enabled": True})
    app = web.create_app(start_worker=False)
    with patch.object(web, "setup_logging"), TestClient(app) as client:
        yield client, s


def configure(client, **values):
    return client.post("/api/intelligence/addlive/config", json={"api_key": FAKE_KEY, **values}, headers=HEADERS)


def test_encrypted_config_csrf_and_key_never_rendered(local):
    client, s = local
    assert client.post("/api/intelligence/addlive/config", json={"api_key": FAKE_KEY}).status_code == 403
    response = configure(client, base_rate=8, cap=20000)
    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert FAKE_KEY not in response.text
    assert FAKE_KEY.encode() not in (s.project_root / "state/secrets/addlive_api.bin").read_bytes()
    assert not (s.project_root / ".env").exists()
    page = client.get("/intelligence")
    assert page.status_code == 200
    assert "addliveLookup" in page.text and "autoSyncConfig" in page.text
    assert FAKE_KEY not in page.text
    configure(client, api_key="", base_rate=6)
    assert config.load(s)["api_key"] == FAKE_KEY


def test_invalid_key_validation_does_not_echo_key(local):
    client, _ = local
    value = FAKE_KEY * 40
    response = configure(client, api_key=value)
    assert response.status_code == 422
    assert value not in response.text and FAKE_KEY not in response.text
    assert configure(client, api_key="not\nallowed").status_code == 400


def test_product_lookup_mirrors_video_catalog_with_server_side_key(local, monkeypatch):
    client, s = local
    configure(client, base_rate=8, cap=20000)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "success", "productInfo": {
            "itemId": "456", "productName": "Synthetic product", "productLink": "https://shopee.vn/product/123/456",
            "price": 200000, "sales": 32, "sellerRate": 0.03, "shopeeRate": 0.08,
            "commission": 22000, "dataSource": "api"}})

    monkeypatch.setattr(routes, "AddLiveClient", lambda key: AddLiveClient(key, httpx.MockTransport(handler)))
    response = client.post("/api/intelligence/addlive/lookup", json={"references": ["456"], "cache_only": False}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1
    assert seen[0].headers["X-API-Key"] == FAKE_KEY
    assert seen[0].url.host == "data.addlivetag.com"
    assert seen[0].url.params["base_rate"] == "0.08"
    assert FAKE_KEY not in str(seen[0].url) and FAKE_KEY not in response.text
    record = catalog.get_product(s.database_path, "shopee:456")
    assert record["commission_rate"] == 11
    assert record["sold"] == 32
    assert "Synthetic product" in client.get("/catalog").text


def test_cache_and_history_routes_keep_status_and_tier(local, monkeypatch):
    client, _ = local
    configure(client, base_rate=8)
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("history.php"):
            return httpx.Response(200, json={"status": "success", "items": [{"itemId": "456", "status": "success",
                "commission": {"points": [{"date": "2026-11-12", "suspect": True, "recorded": {"shopeeRate": 0.02}}]}}],
                "limits": {"truncated": True}})
        return httpx.Response(200, json={"status": "success", "products": [{"input": "456", "status": "skipped", "reason": "not_cached"}],
                                        "summary": {"skipped": 1}, "limits": {}})

    monkeypatch.setattr(routes, "AddLiveClient", lambda key: AddLiveClient(key, httpx.MockTransport(handler)))
    response = client.post("/api/intelligence/addlive/lookup", json={"references": ["456"], "cache_only": True}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["imported"] == 0
    assert response.json()["results"][0]["reason"] == "not_cached"
    assert seen[0].url.params["max_api"] == "0"
    response = client.post("/api/intelligence/addlive/history", json={"item_ids": ["456"], "days": 7}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["items"][0]["commission"]["points"][0]["suspect"] is True
    assert response.json()["warnings"]
    assert seen[-1].url.params["base_rate"] == "0.08"


@pytest.mark.parametrize("kind,endpoint,field", [
    ("products", "product-offer.php", "products"), ("shops", "shop-offer.php", "shops"),
    ("campaigns", "shopee-offer.php", "offers"), ("shop-products", "shop-products.php", "products")])
def test_offer_routes(local, monkeypatch, kind, endpoint, field):
    client, _ = local
    configure(client)

    def handler(request):
        assert request.url.path == "/offers/" + endpoint
        return httpx.Response(200, json={"status": "success", field: [], "dataSource": "db", "hasNextPage": False})

    monkeypatch.setattr(routes, "AddLiveClient", lambda key: AddLiveClient(key, httpx.MockTransport(handler)))
    response = client.post("/api/intelligence/addlive/offers", json={"kind": kind, "shop_id": "123"}, headers=HEADERS)
    assert response.status_code == 200 and field in response.json()


def test_unconfigured_and_provider_failure_are_safe(local, monkeypatch):
    client, _ = local
    assert client.post("/api/intelligence/addlive/history", json={"item_ids": ["456"]}, headers=HEADERS).status_code == 400
    configure(client)
    monkeypatch.setattr(routes, "AddLiveClient", lambda key: AddLiveClient(key, httpx.MockTransport(
        lambda _: httpx.Response(403, text="PRIVATE BODY " + FAKE_KEY))))
    response = client.post("/api/intelligence/addlive/history", json={"item_ids": ["456"]}, headers=HEADERS)
    assert response.status_code == 400
    assert FAKE_KEY not in response.text and "PRIVATE BODY" not in response.text
    assert "403" in response.text


def test_config_validates_tier_and_preserves_previous_value(local):
    client, s = local
    configure(client, cap=20000)
    for values in ({"base_rate": -1}, {"cap": -1}, {"affiliate_id": "not-a-number"}, {"sub_id": "a-b-c-d-e-f"}):
        assert configure(client, **values).status_code in (400, 422)
    assert config.load(s)["cap"] == 20000
    assert json.dumps(config.summary(s)).find(FAKE_KEY) == -1


@pytest.mark.parametrize("percent,fraction", [(0.5, "0.005"), (1, "0.01"), (8, "0.08"), (100, "1.0")])
def test_ui_tier_always_means_percentage_points(local, monkeypatch, percent, fraction):
    client, _ = local
    configure(client, base_rate=percent)

    def handler(request):
        assert request.url.params["base_rate"] == fraction
        return httpx.Response(200, json={"status": "success", "requested": 1, "returned": 1,
                                        "items": [{"itemId": "456", "status": "no_data"}]})

    monkeypatch.setattr(routes, "AddLiveClient", lambda key: AddLiveClient(key, httpx.MockTransport(handler)))
    assert client.post("/api/intelligence/addlive/history", json={"item_ids": ["456"]}, headers=HEADERS).status_code == 200


def test_one_workspace_redirects_and_configures_daily_schedule(local):
    client, _ = local
    configure(client)
    for path in ('/intelligence?q=fixture', '/shopee?q=fixture'):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers['location'] == '/catalog?q=fixture'
    page = client.get('/catalog')
    assert 'Sản phẩm Shopee' in page.text
    assert 'href="/intelligence"' not in page.text
    assert 'href="/products"' not in page.text
    assert '07:00' in page.text
    response = client.post('/api/shopee/sync/config', json={'enabled': True, 'local_time': '08:15'}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()['schedule']['installed'] is True
    assert response.json()['config']['local_time'] == '08:15'
    first = client.post('/api/shopee/sync', json={}, headers=HEADERS).json()
    second = client.post('/api/shopee/sync', json={}, headers=HEADERS).json()
    assert first['queued'] is True and second['queued'] is False
    assert first['task_id']


def test_schedule_failure_is_visible_not_reported_enabled(local, monkeypatch):
    client, _ = local
    configure(client)
    def fail(*args, **kwargs):
        raise routes.shopee_schedule.ScheduleError('Windows chưa lưu được lịch')
    monkeypatch.setattr(routes.shopee_schedule, 'install', fail)
    response = client.post('/api/shopee/sync/config', json={'enabled': True}, headers=HEADERS)
    assert response.status_code == 400
    assert 'Windows' in response.text


def test_manual_canonical_save_mirrors_and_review_preselects(local, monkeypatch):
    client, s = local
    response = client.post('/api/intelligence/products', json={
        'product_id': 'custom456', 'item_id': '456', 'product_name': 'Owner selection',
        'product_url': 'https://shopee.vn/product/123/456'}, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert catalog.get_product(s.database_path, 'shopee:456')['name'] == 'Owner selection'
    page = client.get('/create?branch=product_review&catalog_id=shopee:456')
    assert 'value="shopee:456" selected' in page.text
    # Direct review links still work beyond the default dropdown's 300 rows.
    with monkeypatch.context() as scoped:
        scoped.setattr(catalog, 'list_products', lambda *a, **k: [])
        assert 'value="shopee:456" selected' in client.get('/create?catalog_id=shopee:456').text
    assert 'tra dữ liệu Sàn Cam' not in page.text
    assert client.post('/api/catalog/delete', json={'ids': ['shopee:456']}, headers=HEADERS).status_code == 200
    assert 'Owner selection' not in client.get('/catalog').text
