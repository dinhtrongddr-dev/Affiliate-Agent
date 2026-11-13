import httpx
import pytest

from agent_core.shopee_intelligence.addlive_client import (
    AddLiveClient, AddLiveError, item_id_from_reference,
)


@pytest.fixture(autouse=True)
def reset_cooldowns():
    AddLiveClient._source_until = AddLiveClient._all_until = 0
    yield
    AddLiveClient._source_until = AddLiveClient._all_until = 0


def client_for(payload, status=200, headers=None):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=payload, headers=headers)

    return AddLiveClient("fake-key-never-real", httpx.MockTransport(handle)), requests


@pytest.mark.parametrize("reference", ["123", 123, "https://shopee.vn/product/456/123?x=y",
                                      "https://shopee.vn/ten-san-pham-i.456.123"])
def test_id_and_canonical_urls(reference):
    assert item_id_from_reference(reference) == "123"


@pytest.mark.parametrize("reference", [True, 0, -1, "1.5", "１２３", "https://evil.test/product/1/2",
                                      "https://shopee.vn@evil.test/product/1/2",
                                      "http://shopee.vn/product/1/2", "https://s.shopee.vn/abc"])
def test_invalid_references(reference):
    with pytest.raises(AddLiveError):
        item_id_from_reference(reference)


def test_product_header_fixed_origin_rate_and_masking():
    client, requests = client_for({"status": "success", "productInfo": {"sellerRate": .03}})
    assert client.product("https://shopee.vn/product/4/123", base_rate=8)["productInfo"]["sellerRate"] == .03
    request = requests[0]
    assert request.headers["X-API-Key"] == "fake-key-never-real"
    assert str(request.url).startswith("https://data.addlivetag.com/product-data/product-data.php?")
    assert request.url.params["item_id"] == "123"
    assert float(request.url.params["base_rate"]) == .08
    assert request.url.params["cap"] == "40000"
    assert "fake-key" not in str(request.url)
    assert "fake-key" not in repr(client)


def test_single_short_url_only():
    client, requests = client_for({"status": "success", "productInfo": {}})
    client.product("https://vn.shp.ee/example")
    assert requests[0].url.params["url"] == "https://vn.shp.ee/example"
    with pytest.raises(AddLiveError):
        client.batch(["https://vn.shp.ee/example"])
    assert len(requests) == 1


def test_partial_batch_preserves_order_metadata_and_duplicates():
    payload = {"status": "success", "products": [
        {"status": "success", "productInfo": {"itemId": "1"}},
        {"status": "skipped", "reason": "api_budget_exhausted"},
        {"status": "stale", "productInfo": {"itemId": "1", "dataSource": "db"}}],
        "summary": {"skipped": 1}, "limits": {"sourceCooldownSeconds": 0}}
    client, requests = client_for(payload)
    assert client.batch(["1", "2", "1"], max_api=10) == payload
    assert requests[0].url.params["item_ids"] == "1,2,1"
    assert requests[0].url.params["max_api"] == "10"
    assert len(requests) == 1


def test_batch_limits_and_history_parameters():
    client, requests = client_for({"status": "success", "items": [{"status": "no_data"}]})
    client.history(["123"], days=730, base_rate=.08, cap=20000)
    params = requests[0].url.params
    assert requests[0].url.path == "/price-tracking/history.php"
    assert dict(params) == {"item_ids": "123", "days": "730", "type": "both",
                            "changes_only": "1", "base_rate": "0.08", "cap": "20000"}
    for operation in (lambda: client.batch(["1"] * 101), lambda: client.history(["1"] * 51),
                      lambda: client.history(["1"], days=731),
                      lambda: client.batch(["1"], max_api=51),
                      lambda: client.product("1", base_rate=float("nan"))):
        with pytest.raises(AddLiveError):
            operation()
    assert len(requests) == 1


@pytest.mark.parametrize("kind,path,field", [("products", "product-offer.php", "products"),
    ("shops", "shop-offer.php", "shops"), ("campaigns", "shopee-offer.php", "offers"),
    ("shop-products", "shop-products.php", "products")])
def test_offer_endpoints(kind, path, field):
    client, requests = client_for({"status": "success", field: []})
    client.offers(kind, keyword="áo", page=2, limit=50, sort_type=2, shop_id="123")
    assert requests[0].url.path == "/offers/" + path
    assert requests[0].url.params["page"] == "2"
    assert requests[0].url.params["limit"] == "50"
    assert requests[0].url.params["sortType"] == "2"
    if kind == "shop-products":
        assert requests[0].url.params["shopId"] == "123"
        assert "keyword" not in requests[0].url.params
    else:
        assert requests[0].url.params["keyword"] == "áo"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 302])
def test_status_errors_never_echo_body_or_follow_redirects(status):
    client, requests = client_for({"status": "error", "message": "fake-key-never-real SECRET"},
                                  status, {"Location": "https://evil.test", "Retry-After": "15"})
    with pytest.raises(AddLiveError) as caught:
        client.product("1")
    assert str(status) in str(caught.value)
    assert "SECRET" not in str(caught.value)
    assert "fake-key" not in str(caught.value)
    assert len(requests) == 1
    if status == 429:
        assert caught.value.retry_after == 15
        with pytest.raises(AddLiveError):
            client.history(["1"])
        assert len(requests) == 1


def test_network_exception_is_sanitized_and_not_retried():
    calls = []

    def fail(request):
        calls.append(request)
        raise httpx.ConnectError("fake-key-never-real SECRET", request=request)

    with pytest.raises(AddLiveError) as caught:
        AddLiveClient("fake", httpx.MockTransport(fail)).product("1")
    assert "SECRET" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert len(calls) == 1


def test_source_cooldown_shared_but_cache_history_remain_available():
    payload = {"status": "success", "products": [{"status": "skipped"}],
               "limits": {"sourceRateLimited": True, "sourceCooldownSeconds": 120}}
    client, requests = client_for(payload)
    assert client.batch(["1"])["limits"]["sourceRateLimited"]
    with pytest.raises(AddLiveError) as caught:
        AddLiveClient("another-fake", client._transport).product("2")
    assert 1 <= caught.value.retry_after <= 120
    assert len(requests) == 1
    client.batch(["2"], cache_only=True)
    assert requests[-1].url.params["cache_only"] == "1"
    assert requests[-1].url.params["max_api"] == "0"
    history, history_requests = client_for({"status": "success", "items": [{"status": "no_data"}]})
    history.history(["1"])
    assert len(history_requests) == 1


@pytest.mark.parametrize("payload", [[], {"status": "error", "message": "SECRET"},
    {"status": "success", "productInfo": []},
    {"status": "success", "products": ["bad"]}])
def test_invalid_envelopes(payload):
    client, _ = client_for(payload)
    with pytest.raises(AddLiveError) as caught:
        client.product("1")
    assert "SECRET" not in str(caught.value)


def test_invalid_json():
    client = AddLiveClient("fake", httpx.MockTransport(lambda request: httpx.Response(200, text="SECRET")))
    with pytest.raises(AddLiveError, match="JSON"):
        client.product("1")


@pytest.mark.parametrize("row", [{"status": "unknown"}, {"status": "success", "productInfo": []},
                                {"status": None}])
def test_invalid_batch_rows(row):
    client, _ = client_for({"status": "success", "products": [row]})
    with pytest.raises(AddLiveError):
        client.batch(["1"])


def test_timeout_and_redirect_configuration_is_bounded():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "success", "productInfo": {}})

    AddLiveClient("fake", httpx.MockTransport(handle)).product("1")
    assert requests[0].extensions["timeout"] == {"connect": 10, "read": 45, "write": 45, "pool": 45}


@pytest.mark.parametrize("field,method", [("products", "batch"), ("items", "history")])
@pytest.mark.parametrize("override", [{"requested": 1}, {"returned": 1}, {"requested": True},
                                      {"returned": "2"}, {"requested": 3}])
def test_response_counts_must_match_requested_occurrences(field, method, override):
    rows = ([{"status": "skipped"}] * 2 if field == "products"
            else [{"status": "no_data"}] * 2)
    client, requests = client_for({"status": "success", field: rows, **override})
    with pytest.raises(AddLiveError, match="Số lượng"):
        getattr(client, method)(["1", "1"])
    assert len(requests) == 1


@pytest.mark.parametrize("field,method", [("products", "batch"), ("items", "history")])
@pytest.mark.parametrize("count", [0, 1, 3])
def test_missing_or_extra_rows_rejected_even_without_counts(field, method, count):
    status = "skipped" if field == "products" else "no_data"
    client, _ = client_for({"status": "success", field: [{"status": status}] * count})
    with pytest.raises(AddLiveError, match="thiếu hoặc thừa"):
        getattr(client, method)(["1", "1"])


@pytest.mark.parametrize("field,method", [("products", "batch"), ("items", "history")])
def test_row_identity_and_order_checked(field, method):
    rows = [{"status": "success", "itemId": 2, "productInfo": {}},
            {"status": "success", "itemId": 1, "productInfo": {}}]
    client, _ = client_for({"status": "success", field: rows, "requested": 2, "returned": 2})
    with pytest.raises(AddLiveError, match="Mã sản phẩm"):
        getattr(client, method)(["1", "2"])


def test_duplicate_success_rows_and_normalized_urls_are_preserved():
    rows = [{"status": "success", "itemId": 123, "productInfo": {"itemId": "123"}}] * 2
    payload = {"status": "success", "products": rows, "requested": 2, "returned": 2}
    client, _ = client_for(payload)
    assert client.batch(["https://shopee.vn/product/4/123", "123"]) == payload


def test_nested_identity_mismatch_rejected():
    client, _ = client_for({"status": "success", "products": [
        {"status": "success", "itemId": "1", "productInfo": {"itemId": "2"}}]})
    with pytest.raises(AddLiveError, match="Mã sản phẩm"):
        client.batch(["1"])


def test_truncated_history_points_preserve_valid_item_and_warning():
    payload = {"status": "success", "requested": 1, "returned": 1,
               "items": [{"status": "success", "itemId": "123", "price": {"points": []}}],
               "limits": {"truncated": True}, "warning": "Point limit reached"}
    client, _ = client_for(payload)
    assert client.history(["123"]) == payload
