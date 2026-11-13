"""Offline profile/proxy import contracts; all credentials here are synthetic."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agent_core.settings import Settings
from agent_core.videogen import gateway, muse_profiles as profiles
from agent_core.web.app import create_app


def settings_for(root):
    return Settings(_env_file=None, project_root=root, media_root=root / "media",
                    database_path=root / "state" / "test.sqlite3", videogen_provider="muse2api",
                    videogen_base_url="http://fake-gateway.invalid", videogen_api_key=SecretStr("fake-key"),
                    muse_embedded=False)


def cookie_export(value="FAKE-MUSE-COOKIE"):
    return [{"domain": ".muse.ai", "name": "hatch_sess", "value": value,
             "expirationDate": 4102444800.5, "httpOnly": True, "sameSite": "lax"},
            {"domain": "muse.ai", "name": "__Host-hatch_one_tap", "value": "FAKE-DEVICE"},
            {"domain": ".example.com", "name": "foreign", "value": "DROP-ME"}]


def entry(pid="muse-01", value="FAKE-MUSE-COOKIE", server="http://127.0.0.1:18888"):
    return {"profile_id": pid, "label": pid, "cookies": cookie_export(value),
            "proxy": {"server": server, "username": "fake-user", "password": "FAKE-PROXY-PASSWORD"}}


class ProfileParsingTests(unittest.TestCase):
    def test_owner_cookie_array_with_separate_proxy_fields(self):
        result = profiles.parse(json.dumps(cookie_export()), profile_id="muse-01", label="Muse 01",
                                proxy="socks5://fake-user:fake%40pass@127.0.0.1:1080")[0]
        self.assertEqual(result["proxy"], {"server": "socks5://127.0.0.1:1080",
                                           "username": "fake-user", "password": "fake@pass"})
        self.assertEqual(result["expires"]["hatch_sess"], 4102444800)
        self.assertNotIn("foreign", result["cookies"])
        self.assertEqual(result["label"], "Muse 01")

    def test_bulk_accepts_export_array_and_cookie_map(self):
        first, second = entry(), entry("muse-02", "FAKE-SECOND", "http://proxy2.invalid:8080")
        second["cookies"] = {"hatch_sess": "FAKE-SECOND"}
        second["expires"] = {"hatch_sess": 4102444800}
        parsed = profiles.parse(json.dumps({"profiles": [first, second]}))
        self.assertEqual([p["profile_id"] for p in parsed], ["muse-01", "muse-02"])
        self.assertEqual(profiles.parse(json.dumps([first, second])), parsed)

    def test_invalid_profile_and_proxy_never_echo_credentials(self):
        for changes in ({"profile_id": "../bad"}, {"profile_id": ""}, {"proxy": None},
                        {"proxy": "https://fake-user:FAKE-SECRET@127.0.0.1:1"},
                        {"proxy": "http://host:0"}, {"enabled": "false"},
                        {"proxy": {"server": "socks5://host:1080", "username": "u" * 256, "password": "p"}},
                        {"cookies": {"hatch_sess": None}},
                        {"cookies": {"hatch_sess": "fake"}, "expires": {"hatch_sess": 10**100}},
                        {"cookies": []}, {"expires": [], "cookies": {"hatch_sess": "fake"}}):
            with self.subTest(changes=changes):
                with self.assertRaises(profiles.ProfileError) as caught:
                    profiles.parse(json.dumps({**entry(), **changes}))
                self.assertNotIn("FAKE-SECRET", str(caught.exception))
                self.assertNotIn("FAKE-MUSE-COOKIE", str(caught.exception))

    def test_duplicate_profiles_and_account_sessions_refused(self):
        for rows in ([entry(), entry()],
                     [entry(), entry("muse-02", server="http://proxy2.invalid:8080")],
                     [entry(), entry("muse-02", "FAKE-SECOND")]):
            with self.assertRaises(profiles.ProfileError):
                profiles.parse(json.dumps({"profiles": rows}))


class StoredProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = settings_for(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, *items):
        return profiles.save(self.settings, json.dumps({"profiles": list(items or [entry()])}))

    def test_encrypted_upsert_and_safe_summaries(self):
        self.save()
        saved_path = self.root / "state" / "secrets" / "muse_profiles.bin"
        ciphertext = saved_path.read_bytes()
        self.assertNotIn(b"FAKE-MUSE-COOKIE", ciphertext)
        self.assertNotIn(b"FAKE-PROXY-PASSWORD", ciphertext)
        result = self.save(entry(value="FAKE-REFRESHED", server="socks5://proxy.invalid:1080"))
        self.assertEqual(result["total"], 1)
        self.assertEqual(profiles.load(self.settings)[0]["cookies"]["hatch_sess"], "FAKE-REFRESHED")
        safe = json.dumps(profiles.summaries(self.settings))
        self.assertNotIn("FAKE-REFRESHED", safe)
        self.assertNotIn("FAKE-PROXY-PASSWORD", safe)
        self.assertIn("socks5://proxy.invalid:1080", safe)

    def test_import_validation_is_atomic_and_preserves_existing_profiles(self):
        self.save()
        before = profiles.load(self.settings)
        with self.assertRaises(profiles.ProfileError):
            self.save(entry("muse-02"))  # Same account cannot move to a second profile.
        self.assertEqual(profiles.load(self.settings), before)
        with self.assertRaises(profiles.ProfileError):
            self.save(entry("muse-02", "FAKE-SECOND", "http://proxy2.invalid:8080"),
                      {**entry("muse-03"), "proxy": None})
        self.assertEqual(profiles.load(self.settings), before)

    def test_sync_sends_routes_but_discards_secret_response_fields(self):
        self.save(entry(), entry("muse-02", "FAKE-SECOND", "socks5h://proxy.invalid:1080"))
        seen = []

        def handler(request):
            seen.append(request)
            self.assertEqual(request.url.path, "/admin/accounts/profiles")
            self.assertEqual(request.headers["Authorization"], "Bearer fake-key")
            body = json.loads(request.content)
            return httpx.Response(200, json={"profiles": [
                {"profile_id": p["profile_id"], "account_id": "acc-" + p["profile_id"],
                 "cookies": "FAKE-LEAK", "password": "FAKE-LEAK"} for p in body["profiles"]]})

        result = profiles.push_to_gateway(self.settings, httpx.MockTransport(handler))
        self.assertEqual(result["synced"], 2)
        self.assertNotIn("FAKE-LEAK", json.dumps(result))
        body = json.loads(seen[0].content)
        self.assertEqual(body["profiles"][0]["proxy"]["password"], "FAKE-PROXY-PASSWORD")
        revision = body["profiles"][0]["revision"]
        profiles.push_to_gateway(self.settings, httpx.MockTransport(handler))
        self.assertEqual(json.loads(seen[-1].content)["profiles"][0]["revision"], revision)
        self.assertTrue(all(p["pushed_at"] for p in profiles.load(self.settings)))
        # Saving refreshed credentials invalidates the local sync receipt.
        self.save(entry(value="FAKE-REFRESHED"))
        self.assertIsNone(profiles.summaries(self.settings)[0]["pushed_at"])
        self.assertNotEqual(profiles.load(self.settings)[0]["revision"], revision)

    def test_sync_failure_masks_gateway_body_and_keeps_pending_profiles(self):
        self.save()
        transport = httpx.MockTransport(lambda _: httpx.Response(409, json={"detail": "FAKE-LEAK"}))
        with self.assertRaises(profiles.ProfileError) as caught:
            profiles.push_to_gateway(self.settings, transport)
        self.assertNotIn("FAKE-LEAK", str(caught.exception))
        self.assertIsNone(profiles.summaries(self.settings)[0]["pushed_at"])

    def test_startup_restores_profiles_even_when_legacy_accounts_exist(self):
        self.save()
        with patch.object(profiles, "push_to_gateway", return_value={"synced": 1}) as push, \
                patch("agent_core.videogen.muse_session.push_to_gateway") as legacy:
            self.assertEqual(gateway.sync_session(self.settings), {"synced": 1})
        push.assert_called_once_with(self.settings)
        legacy.assert_not_called()

    def test_account_summary_omits_proxy_credentials(self):
        response = httpx.Response(200, json={"accounts": [{"id": "a1", "profile_id": "muse-01",
            "proxy": {"server": "http://proxy.invalid:80", "username": "FAKE-USER-SECRET",
                      "password": "FAKE-PASSWORD", "has_password": True}}]})
        with patch.object(gateway.httpx, "get", return_value=response):
            result = gateway.account_status(self.settings)
        self.assertEqual(result["accounts"][0]["proxy_server"], "http://proxy.invalid:80")
        self.assertNotIn("FAKE-USER-SECRET", json.dumps(result))
        self.assertNotIn("FAKE-PASSWORD", json.dumps(result))


class ProfileWebTests(unittest.TestCase):
    def test_cookie_json_import_page_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = settings_for(Path(tmp))
            with patch("agent_core.web.app.get_settings", return_value=settings):
                app = create_app(start_worker=False)
            with patch("agent_core.web.app.setup_logging"), TestClient(app) as client:
                body = {"json": json.dumps(cookie_export()), "profile_id": "muse-01", "label": "Muse 01",
                        "proxy": entry()["proxy"]}
                self.assertEqual(client.post("/api/muse/profiles", json=body).status_code, 403)
                response = client.post("/api/muse/profiles", json=body,
                                       headers={"X-Requested-With": "affiliate-agent"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["imported"], 1)
                page = client.get("/generation")
                self.assertEqual(page.status_code, 200)
                self.assertIn('id="mp-json"', page.text)
                self.assertIn('id="mp-file"', page.text)
                self.assertIn("Muse 01", page.text)
                self.assertIn("http://127.0.0.1:18888", page.text)
                for secret in ("FAKE-MUSE-COOKIE", "FAKE-PROXY-PASSWORD"):
                    self.assertNotIn(secret, response.text)
                    self.assertNotIn(secret, page.text)
                bad = client.post("/api/muse/profiles", json={**body, "proxy": None},
                                  headers={"X-Requested-With": "affiliate-agent"})
                self.assertEqual(bad.status_code, 400)
                self.assertEqual(len(profiles.load(app.state.ctx["settings"])), 1)


if __name__ == "__main__":
    unittest.main()
