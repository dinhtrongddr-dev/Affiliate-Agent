"""Automatic clean-up removes only old junk and keeps profiles, fresh files and active jobs."""
import os
import time

from agent_core import db, housekeeping
from test_cleanup import _job, _settings


def _file(path, age_days):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 100)
    t = time.time() - age_days * 86400
    os.utime(path, (t, t))
    return path


def test_old_junk_goes_and_everything_else_stays(tmp_path):
    s = _settings(tmp_path)
    s.project_root = tmp_path
    media = tmp_path / "state" / "muse2api" / "data" / "media"
    old_clip, new_clip = _file(media / "a.mp4", 5), _file(media / "b.mp4", 1)
    profile = _file(tmp_path / "state" / "muse2api" / "profiles" / "p" / "Cookies", 60)
    old_log, new_log = _file(tmp_path / "logs" / "x.log", 30), _file(tmp_path / "logs" / "y.log", 2)
    keep = _file(tmp_path / "logs" / ".gitkeep", 90)
    _job(s, "posted01", "PUBLISHED_CONFIRMED")
    _job(s, "running1", "APPROVED")
    with db.connect(s.database_path) as conn:
        conn.execute("UPDATE jobs SET updated_at='2020-01-01T00:00:00+00:00'")

    out = housekeeping.run(s)

    assert not old_clip.exists() and new_clip.exists() and profile.exists()
    assert not old_log.exists() and new_log.exists() and keep.exists()
    assert not (s.work_dir / "posted01").exists()
    assert (s.work_dir / "running1").exists()
    assert (s.shopee_output / "posted01").exists()  # outbox is never touched
    assert out["muse_media"] == 100 and out["logs"] == 100
