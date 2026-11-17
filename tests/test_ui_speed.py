"""Page rendering must stay fast: no per-row settings reads, no heavy work on GET."""
import time

from agent_core import localtime


def test_localtime_filter_is_cheap():
    localtime.fmt("2026-11-17T01:00:00+00:00")
    t = time.perf_counter()
    for _ in range(1000):
        localtime.fmt("2026-11-17T01:00:00+00:00")
    assert time.perf_counter() - t < 0.2          # was ~70 ms per call (Settings() per row)


def test_timezone_follows_env_changes(monkeypatch):
    calls = []

    class FakeSettings:
        timezone = "UTC"

    import agent_core.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_settings", lambda: calls.append(1) or FakeSettings())
    localtime._zone_cache.clear()
    assert localtime.fmt("2026-11-17T01:00:00+00:00", "%H:%M") == "01:00"
    localtime.fmt("2026-11-17T01:00:00+00:00")
    assert len(calls) == 1                         # cached
    key, name, _ = localtime._zone_cache["name"]
    localtime._zone_cache["name"] = (key - 1, name, 0)   # .env changed and the 1 s window passed
    localtime.fmt("2026-11-17T01:00:00+00:00")
    assert len(calls) == 2
    localtime._zone_cache.clear()
