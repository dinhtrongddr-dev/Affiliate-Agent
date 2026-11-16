"""Profile expiry, live-probe contracts and cookie replacement with synthetic secrets."""
import copy
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_core.videogen import gateway, muse_health, muse_profiles
from agent_core.web.app import create_app
from test_muse_profiles import cookie_export, entry, settings_for


@pytest.fixture
def saved():
    with tempfile.TemporaryDirectory(prefix="muse-health-") as tmp:
        settings = settings_for(Path(tmp))
        muse_profiles.save(settings, json.dumps(entry()))
        profiles = muse_profiles.load(settings)
        profiles[0].update(account_id="account-one", pushed_at="2026-11-16T00:00:00Z")
        muse_profiles.secret_store.save(settings, muse_profiles.NAME, {"profiles": profiles})
        yield settings, profiles[0]


def account_for(profile, **extra):
    return {"id": profile["account_id"], "profile_id": profile["profile_id"],
            "profile_revision": profile["revision"], "proxy_server": profile["proxy"]["server"], **extra}


def test_future_expiry_does_not_hide_rejected_session(saved):
    _, profile = saved
    account = account_for(profile, ok=False, checked_at=time.time(), note="Session authentication failed (/api/session HTTP 401)")
    health = muse_health.describe(profile, account)
    assert health["cookie_state"] == "invalid"
    assert health["cookie_tone"] == "err"
    assert health["session_state"] == "error"
    assert health["remaining"]  # Cookie metadata can still claim a future expiry.


def test_expired_and_unknown_timestamps_are_not_healthy(saved):
    _, profile = saved
    profile["expires"] = {"hatch_sess": time.time() - 1}
    assert muse_health.describe(profile)["cookie_state"] == "expired"
    profile["expires"] = {"hatch_vml": time.time() - 1}
    assert muse_health.describe(profile)["cookie_state"] == "unknown"  # Optional workspace token.
    profile["expires"] = {"hatch_sess": time.time() + 1800}
    assert muse_health.describe(profile)["cookie_state"] == "expiring"


def test_proxy_failure_and_unconfirmed_probe_never_kill_cookie(saved):
    _, profile = saved
    for ok, note in [(False, "Proxy authentication failed HTTP 407"), (True, "测试未确认（保留账号状态）")]:
        account = account_for(profile, ok=ok, checked_at=time.time(), note=note,
                              proxy_status={"proxy_reachable": False, "checked_at": time.time()})
        result = muse_health.describe(profile, account)
        assert result["cookie_state"] == "valid"
        assert result["session_state"] == "unknown"
        assert result["proxy_state"] == "error"


def test_status_uses_gateway_renewed_expiry_and_separate_proxy_proof(saved):
    _, profile = saved
    stamp = time.time()
    account = account_for(profile, ok=False, checked_at=stamp - 2,
                          note="Session authentication failed", cookies_exp={"hatch_sess": stamp + 4000},
                          proxy_status={"proxy_reachable": True, "session_ok": True, "checked_at": stamp,
                                        "exit_ip": "203.0.113.1", "message": "DO-NOT-RETURN"})
    result = muse_health.describe(profile, account)
    assert result["session_state"] == "ok"
    assert result["cookie_state"] == "expiring"
    assert result["proxy_state"] == "ok"
    assert result["exit_ip"] == "203.0.113.1"
    assert "DO-NOT-RETURN" not in json.dumps(result)
    account["profile_revision"] = "changed"
    assert not muse_health.describe(profile, account)["can_test"]


def test_inconclusive_network_error_preserves_known_dead_cookie(saved):
    _, profile = saved
    stamp = time.time()
    profile['health_checks'] = {'session': {'session_ok': False, 'failure_type': 'auth', 'checked_at': stamp - 3}}
    account = account_for(profile, ok=False, note='Session not confirmed; network failed', checked_at=stamp)
    assert muse_health.describe(profile, account)['cookie_state'] == 'invalid'
    account.update(ok=True, note='Session OK', checked_at=stamp + 1)
    assert muse_health.describe(profile, account)['session_state'] == 'ok'


def test_cookie_replacement_keeps_identity_route_and_resets_proof(saved):
    settings, profile = saved
    original = copy.deepcopy(profile)
    result = muse_profiles.update_cookies(settings, profile["profile_id"], json.dumps(cookie_export("FAKE-NEW-SESSION")))
    changed = muse_profiles.load(settings)[0]
    for field in ("profile_id", "label", "enabled", "proxy"):
        assert changed[field] == original[field]
    assert changed["cookies"]["hatch_sess"] == "FAKE-NEW-SESSION"
    assert changed["revision"] != original["revision"]
    assert not changed.get("pushed_at") and not changed.get("health_checks")
    assert "FAKE-NEW-SESSION" not in json.dumps(result)
    before = muse_profiles.load(settings)
    for text in ('{}', '[]', '{"cookies":{"hatch_sess":"x"},"expires":{"hatch_sess":12}}', '{"profiles":[]}'):
        with pytest.raises(muse_profiles.ProfileError):
            muse_profiles.update_cookies(settings, profile["profile_id"], text)
        assert muse_profiles.load(settings) == before


@pytest.mark.parametrize("kind,body,session_ok", [
    ("session", {"ok": True, "quota_error": "FAKE-SECRET"}, True),
    ("session", {"ok": False, "failure_type": "auth", "message": "FAKE-SECRET"}, False),
    ("session", {"ok": False, "failure_type": "proxy", "message": "FAKE-SECRET"}, None),
    ("session", {"ok": False, "failure_type": "network", "message": "FAKE-SECRET"}, None),
    ("proxy", {"ok": False, "proxy_reachable": True, "session_ok": False, "failure_type": "auth", "exit_ip": "203.0.113.1"}, False),
    ("proxy", {"ok": False, "proxy_reachable": True, "session_ok": False, "message": "upstream temporary failure"}, None),
])
def test_check_routes_once_and_returns_only_safe_results(saved, kind, body, session_ok):
    settings, profile = saved
    seen = []
    def handler(request):
        seen.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer fake-key"
        if request.method == "GET":
            account = account_for(profile) | {"proxy": {"server": profile["proxy"]["server"]}}
            return httpx.Response(200, json={"accounts": [account]})
        assert request.url.path == "/admin/accounts/account-one/" + ("proxy/test" if kind == "proxy" else "test")
        return httpx.Response(200, json=body)
    result = muse_health.check(settings, profile["profile_id"], kind, httpx.MockTransport(handler))
    assert len(seen) == 2
    assert result["session_ok"] is session_ok
    assert "FAKE-SECRET" not in json.dumps(result)
    assert muse_profiles.load(settings)[0]["health_checks"][kind] == result


def test_changed_remote_binding_prevents_probe(saved):
    settings, profile = saved
    seen = []
    def handler(request):
        seen.append(request.method)
        return httpx.Response(200, json={"accounts": [account_for(profile, profile_revision="different")]})
    with pytest.raises(muse_profiles.ProfileError, match="đồng bộ lại"):
        muse_health.check(settings, profile["profile_id"], "session", httpx.MockTransport(handler))
    assert seen == ["GET"]


def test_busy_and_timeout_are_reported_without_retry_or_cookie_death(saved):
    settings, profile = saved
    for error in (409, "timeout"):
        seen = []
        def handler(request, seen=seen, error=error):
            seen.append(request.method)
            if request.method == "GET":
                return httpx.Response(200, json={"accounts": [account_for(profile) | {"proxy": profile["proxy"]}]})
            if error == "timeout":
                raise httpx.ReadTimeout("FAKE-SECRET", request=request)
            return httpx.Response(error, json={"detail": "FAKE-SECRET"})
        with pytest.raises(muse_profiles.ProfileError) as caught:
            muse_health.check(settings, profile["profile_id"], "session", httpx.MockTransport(handler))
        assert 'FAKE-SECRET' not in str(caught.value)
        assert seen == ['GET', 'POST']
        assert not muse_profiles.load(settings)[0].get('health_checks')


def test_concurrent_cookie_update_discards_old_probe(saved):
    settings, profile = saved
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"accounts": [account_for(profile) | {"proxy": profile["proxy"]}]})
        muse_profiles.update_cookies(settings, profile["profile_id"], json.dumps(cookie_export("FAKE-CONCURRENT")))
        return httpx.Response(200, json={"ok": False, "failure_type": "auth"})
    with pytest.raises(muse_profiles.ProfileError, match="vừa được cập nhật"):
        muse_health.check(settings, profile["profile_id"], "session", httpx.MockTransport(handler))
    assert not muse_profiles.load(settings)[0].get("health_checks")


def test_web_status_cookie_update_and_csrf(saved):
    settings, profile = saved
    info = {"accounts": [account_for(profile, ok=False, checked_at=time.time(), note="/api/session HTTP 401")]}
    with patch("agent_core.web.app.get_settings", return_value=settings), \
            patch("agent_core.web.app.setup_logging"), patch.object(gateway, "account_status", return_value=info):
        app = create_app(start_worker=False)
        with TestClient(app) as client:
            page = client.get("/generation")
            assert page.status_code == 200
            assert 'cookie-dead' in page.text and 'Cookie die' in page.text
            assert 'data-cookie-json' in page.text and 'Test phiên Muse' in page.text and 'Test proxy' in page.text
            assert 'FAKE-MUSE-COOKIE' not in page.text and 'FAKE-PROXY-PASSWORD' not in page.text
            status = client.get('/api/muse/profiles').json()
            assert status['profiles'][0]['health']['cookie_tone'] == 'err'
            path = '/api/muse/profiles/muse-01/cookies'
            body = {'json': json.dumps(cookie_export('FAKE-REPLACEMENT'))}
            assert client.post(path, json=body).status_code == 403
            assert client.post('/api/muse/profiles/muse-01/check/session').status_code == 403
            with patch.object(muse_profiles, 'push_to_gateway', return_value={'synced': 1}):
                result = client.post(path, json=body, headers={'X-Requested-With': 'affiliate-agent'})
            assert result.status_code == 200
            assert 'FAKE-REPLACEMENT' not in result.text
            assert muse_profiles.load(settings)[0]['proxy'] == profile['proxy']
