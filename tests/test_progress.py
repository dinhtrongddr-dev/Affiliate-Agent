"""Job timeline: step states, %, ETA, Muse clip %."""
import json
from types import SimpleNamespace

from agent_core import db, progress
from agent_core.videogen import engine


def _setup(tmp_path, status, marks, branch="viral_creator"):
    s = SimpleNamespace(database_path=tmp_path / "a.sqlite3", work_dir=tmp_path / "work")
    db.init_db(s.database_path)
    job = {"id": "j1", "status": status, "brief_json": json.dumps({"creation_branch": branch}),
           "created_at": "2026-11-16T10:00:00+00:00"}
    (s.work_dir / "j1").mkdir(parents=True)
    (s.work_dir / "j1" / "timings.json").write_text(json.dumps(marks), encoding="utf-8")
    progress._history.clear()
    return s, job


def _t(minute):
    return f"2026-11-16T10:{minute:02d}:00+00:00"


def test_script_step_running(tmp_path):
    s, job = _setup(tmp_path, "SCRIPTING", {"script_start": _t(0)})
    now = progress._parse(_t(3))
    p = progress.job_progress(s, job, now)
    assert p["busy"] and [st["state"] for st in p["steps"]] == ["running", "pending", "pending"]
    assert 0 < p["pct"] < 20
    assert p["elapsed_sec"] == 180
    assert p["eta_sec"] > progress.DEFAULT_SECONDS["muse"]


def test_muse_step_uses_clip_progress(tmp_path):
    s, job = _setup(tmp_path, "WAITING_FOR_GENERATION",
                    {"script_start": _t(0), "script_done": _t(7), "muse_start": _t(7)})
    engine.ensure_schema(s.database_path)
    with db.connect(s.database_path) as conn:
        for i, (st, pct) in enumerate([("DONE", 0), ("SUBMITTED", 50)]):
            conn.execute("INSERT INTO gen_tasks (id, job_id, scene_index, kind, provider, prompt, status, "
                         "progress, created_at, updated_at) VALUES (?, 'j1', ?, 'video', 'muse2api', 'p', ?, ?, "
                         "'x', 'x')", (f"t{i}", i, st, pct))
    p = progress.job_progress(s, job, progress._parse(_t(20)))
    muse = p["steps"][1]
    assert muse["state"] == "running" and muse["pct"] == 75
    assert [c["pct"] for c in p["clips"]] == [100, 50]
    assert p["steps"][0]["state"] == "done" and p["steps"][0]["elapsed_sec"] == 420


def test_finished_and_no_muse(tmp_path):
    s, job = _setup(tmp_path, "READY_FOR_REVIEW",
                    {"script_start": _t(0), "script_done": _t(5), "render_start": _t(5), "render_done": _t(9)})
    p = progress.job_progress(s, job, progress._parse(_t(30)))
    assert p["pct"] == 100 and not p["busy"]
    assert [st["key"] for st in p["steps"]] == ["script", "render"]     # Muse never ran


def test_pct_clamped_below_100_until_checked():
    assert engine._pct("99.6") == 99 and engine._pct(None) == 0 and engine._pct("x") == 0


def test_muse_eta_counts_clips_of_other_videos_ahead(tmp_path):
    s, job = _setup(tmp_path, "WAITING_FOR_GENERATION",
                    {"script_start": _t(0), "script_done": _t(2), "muse_start": _t(2)})
    engine.ensure_schema(s.database_path)
    with db.connect(s.database_path) as conn:
        for i, (jid, st, at) in enumerate([("other", "PENDING", "a"), ("other", "SUBMITTED", "a"),
                                           ("j1", "PENDING", "b")]):
            conn.execute("INSERT INTO gen_tasks (id, job_id, scene_index, kind, provider, prompt, status, "
                         "progress, created_at, updated_at) VALUES (?, ?, ?, 'video', 'm', 'p', ?, 0, ?, 'x')",
                         (f"g{i}", jid, i, st, at))
    p = progress.job_progress(s, job, progress._parse(_t(5)))
    muse = p["steps"][1]
    assert muse["queued"] == 2 and p["eta_sec"] >= 3 * progress.CLIP_SECONDS
