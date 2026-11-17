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


def test_trend_batches_match_the_single_queries(tmp_path):
    from agent_core.trend_intel import store
    db_path = tmp_path / "t.sqlite3"
    store.ensure_schema(db_path)
    from agent_core.db import connect
    with connect(db_path) as conn:
        for k in ("a", "b"):
            for i in range(3):
                conn.execute("INSERT INTO trend_observations (topic_key, source, observed_at, meta_json) "
                             "VALUES (?, 'x', ?, '{}')", (k, f"2026-11-1{i}T00:00:00+00:00"))
    many = store.observations_many(db_path, ["a", "b", "c"], limit=2)
    assert [o["observed_at"] for o in many["a"]] == [o["observed_at"] for o in store.observations(db_path, "a", limit=2)]
    assert len(many["b"]) == 2 and many["c"] == []
    assert store.matches_for_many(db_path, []) == {}


def test_catalog_schema_check_runs_once(tmp_path, monkeypatch):
    from agent_core import catalog
    db_path = tmp_path / "c.sqlite3"
    catalog.ensure_schema(db_path)
    calls = []
    monkeypatch.setattr(catalog, "init_db", lambda p: calls.append(p))
    catalog.ensure_schema(db_path)
    assert calls == []                               # cached after the first call
    assert catalog.get_products(db_path, ["missing"]) == {}
