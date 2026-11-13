"""Offline gateway routing/profile contracts; synthetic sessions only."""
import importlib.util
import os
import subprocess
import socketserver
import threading
import requests
import textwrap
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest


GATEWAY = Path(__file__).resolve().parents[1] / "services" / "muse_gateway"


def load_store():
    # Store has no app lifespan/browser side effects.
    spec = importlib.util.spec_from_file_location("profile_test_proxy", GATEWAY / "proxy.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    previous = sys.modules.get("proxy")
    sys.modules["proxy"] = proxy
    try:
        spec = importlib.util.spec_from_file_location("profile_test_store", GATEWAY / "store.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.Store
    finally:
        if previous is None:
            sys.modules.pop("proxy", None)
        else:
            sys.modules["proxy"] = previous


Store = load_store()


def profile(pid="one", session="FAKE-ONE", port=8081):
    return {"profile_id": pid, "cookies": {"hatch_sess": session},
            "proxy": {"server": f"http://127.0.0.1:{port}", "username": "", "password": ""},
            "expires": {}, "label": pid, "enabled": True}


class GatewayProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="muse-profiles-offline-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "accounts.json"
        self.store = Store(SimpleNamespace(accounts_file=str(self.path),
                                           tasks_file=str(Path(self.temp.name) / "tasks.json")))

    def test_adopts_legacy_and_keeps_stable_binding(self):
        legacy = self.store.add_account({"hatch_sess": "FAKE-ONE"}, "old")
        imported = self.store.upsert_profiles([profile()])[0]
        self.assertEqual(imported["account_id"], legacy["id"])
        self.assertNotIn("cookies", imported)
        self.assertNotIn("password", imported["proxy"])
        self.assertEqual(self.store.upsert_profiles([profile()])[0]["account_id"], legacy["id"])
        self.assertEqual(Store(self.store.cfg).accounts[0]["profile_id"], "one")

    def test_conflicts_are_atomic(self):
        self.store.upsert_profiles([profile()])
        before = self.path.read_bytes()
        for payload in ([profile("two")], [profile("two", "FAKE-TWO")],
                        [profile("two", "FAKE-TWO", 8082), profile("two", "FAKE-THREE", 8083)]):
            with self.assertRaises(ValueError):
                self.store.upsert_profiles(payload)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(len(self.store.accounts), 1)

    def test_busy_noop_allowed_changed_import_rejected(self):
        aid = self.store.upsert_profiles([profile()])[0]["id"]
        self.store.upsert_profiles([profile()], {aid})
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.upsert_profiles([profile(port=8082)], {aid})
        self.assertEqual(self.path.read_bytes(), before)

    def test_managed_selection_never_reverts_to_legacy(self):
        legacy = self.store.add_account({"hatch_sess": "FAKE-LEGACY"})
        self.assertEqual(self.store.pick_account()["id"], legacy["id"])
        managed = self.store.upsert_profiles([profile()])[0]
        self.assertEqual(self.store.pick_account(preferred_id=legacy["id"], rotate=False)["id"], managed["id"])
        self.store.update_account(managed["id"], enabled=False)
        self.assertIsNone(self.store.pick_account())
        self.assertIsNotNone(self.store.get_account(legacy["id"]))

    def test_distinct_credentials_allow_shared_proxy_endpoint(self):
        second = profile("two", "FAKE-TWO")
        second["proxy"]["username"] = "FAKE-USER"
        self.assertEqual(len(self.store.upsert_profiles([profile(), second])), 2)

    def test_same_revision_preserves_renewed_session_and_health(self):
        original = profile() | {"revision": "revision-one"}
        aid = self.store.upsert_profiles([original])[0]["id"]
        self.store.update_account(aid, cookies={"hatch_sess": "FAKE-RENEWED"},
                                  cookies_exp={"hatch_sess": 4102444800}, ok=True)
        self.store.upsert_profiles([original], {aid})
        account = self.store.get_account(aid)
        self.assertEqual(account["cookies"]["hatch_sess"], "FAKE-RENEWED")
        self.assertTrue(account["ok"])
        with self.assertRaises(ValueError):
            self.store.upsert_profiles([original | {"label": "changed"}])
        self.store.upsert_profiles([original | {"revision": "revision-two"}])
        self.assertEqual(self.store.get_account(aid)["cookies"]["hatch_sess"], "FAKE-ONE")

    def test_actual_api_generation_and_browser_context_routing(self):
        # Subprocess keeps gateway's top-level module names/config out of core tests.
        script = textwrap.dedent('''
            from unittest.mock import patch, MagicMock
            import app as gateway
            import engine as engine_module
            from fastapi.testclient import TestClient
            client = TestClient(gateway.app)  # no context manager: no lifespan/startup
            legacy = gateway.store.add_account({"hatch_sess": "FAKE-LEGACY"})
            def profile(pid, port):
                return {"profile_id": pid, "cookies": {"hatch_sess": "FAKE-" + pid},
                        "proxy": {"server": "http://127.0.0.1:" + str(port),
                                  "username": "fake", "password": "FAKE-SECRET"}}
            profiles = [profile("one", 18081), profile("two", 18082)]
            response = client.post("/admin/accounts/profiles", json={"profiles": profiles})
            assert response.status_code == 200, response.text
            assert "FAKE-SECRET" not in response.text and "FAKE-one" not in response.text
            ids = [p["account_id"] for p in response.json()["profiles"]]
            gateway.ACCOUNT_POOL.busy_accounts.add(ids[0])
            assert client.post("/admin/accounts/profiles", json={"profiles": profiles}).status_code == 200
            changed = profile("one", 19081)
            before = open(gateway.CFG.accounts_file, "rb").read()
            assert client.post("/admin/accounts/profiles", json={"profiles": [changed]}).status_code == 409
            assert open(gateway.CFG.accounts_file, "rb").read() == before
            gateway.ACCOUNT_POOL.busy_accounts.clear()
            captured = []
            def generate(cookies, *args, **kwargs):
                route = gateway.engine._ensure_local().proxy_snapshot
                captured.append((cookies["hatch_sess"], route["server"], kwargs["account_id"]))
                return {"url": "https://fake.invalid/video"}
            with patch.object(gateway.engine, "start"), patch.object(gateway.engine, "generate", side_effect=generate), patch.object(gateway.engine, "close_current_page"), patch.object(gateway, "_sync_cookies"), patch.object(gateway, "_renew_and_persist", side_effect=lambda aid, **kw: gateway.store.get_account(aid)):
                for aid in ids:
                    gateway._run_generation("synthetic", "video", 5, account_id=aid)
                try:
                    gateway._run_generation("synthetic", "video", 5, account_id=legacy["id"])
                    raise AssertionError("legacy direct account accepted")
                except gateway.MuseAuthError:
                    pass
            assert captured == [("FAKE-one", "http://127.0.0.1:18081", ids[0]), ("FAKE-two", "http://127.0.0.1:18082", ids[1])], captured
            # Every attempted retry must retain that attempt's own proxy, never direct.
            captured.clear()
            def failed(cookies, *args, **kwargs):
                generate(cookies, *args, **kwargs)
                raise gateway.MuseProxyError("synthetic proxy failure")
            with patch.object(gateway.engine, "start"), patch.object(gateway.engine, "generate", side_effect=failed), patch.object(gateway.engine, "close_current_page"), patch.object(gateway, "_renew_and_persist", side_effect=lambda aid, **kw: gateway.store.get_account(aid)):
                try:
                    gateway._run_generation("synthetic", "video", 5, account_id=ids[0])
                    raise AssertionError("proxy failure swallowed")
                except gateway.MuseProxyError:
                    pass
            assert captured and all(route and session != "FAKE-LEGACY" for session, route, aid in captured)
            browser = MagicMock()
            browser.send.side_effect = [{"result": {"browserContextId": "isolated-one"}}, {"result": {"targetId": "target-one"}}]
            gateway.engine.browser = browser
            relay = MagicMock(url="http://127.0.0.1:29001")
            with patch.object(engine_module, "HTTPProxyRelay", return_value=relay) as relay_type, patch.object(engine_module, "CDP"):
                gateway.engine._open_page(proxy=profiles[0]["proxy"])
            relay_type.assert_called_once_with(profiles[0]["proxy"])
            assert browser.send.call_args_list[0].args == ("Target.createBrowserContext", {"proxyServer": relay.url, "proxyBypassList": "<-loopback>"})
            assert browser.send.call_args_list[1].args[1]["browserContextId"] == "isolated-one"
            gateway.engine._close_page()
            client.close()
            print("API+generation+context routing PASS; no lifespan, no external calls")
        ''')
        env = dict(os.environ, MUSE2API_HOME=self.temp.name,
                   MUSE2API_PROFILE_ROOT=str(Path(self.temp.name) / "browser"),
                   MUSE2API_KEY="", PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(GATEWAY))
        completed = subprocess.run([sys.executable, "-B", "-c", script], env=env,
                                   capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_local_http_and_socks_relays_never_need_direct_target(self):
        spec = importlib.util.spec_from_file_location("profile_relay_test", GATEWAY / "proxy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for scheme in ("http", "socks5"):
            observed = []
            class Handler(socketserver.BaseRequestHandler):
                def handle(self, protocol=scheme, events=observed):
                    self.request.settimeout(3)
                    if protocol == "socks5":
                        assert module.HTTPProxyRelay._read_exact(self.request, 3) == b"\x05\x01\x00"
                        self.request.sendall(b"\x05\x00")
                        header = module.HTTPProxyRelay._read_exact(self.request, 4)
                        assert header == b"\x05\x01\x00\x03"
                        size = module.HTTPProxyRelay._read_exact(self.request, 1)[0]
                        destination = module.HTTPProxyRelay._read_exact(self.request, size + 2)
                        events.append(destination[:-2].decode())
                        self.request.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x50")
                    data = bytearray()
                    while not data.endswith(b"\r\n\r\n"):
                        data.extend(self.request.recv(1))
                    events.append(bytes(data))
                    self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\nConnection: close\r\n\r\nvia-proxy")
            server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
            worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
            worker.start()
            try:
                route = {"server": f"{scheme}://127.0.0.1:{server.server_address[1]}"}
                with module.request_proxy_context(route) as options:
                    # HTTP context uses the upstream directly; SOCKS uses the local relay.
                    with requests.Session() as session:
                        session.trust_env = False
                        response = session.get("http://unresolvable-target.invalid/test", timeout=3, **options)
                self.assertEqual(response.text, "via-proxy")
                self.assertIn(b"/test", observed[-1])
                if scheme == "socks5":
                    self.assertEqual(observed[0], "unresolvable-target.invalid")
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
