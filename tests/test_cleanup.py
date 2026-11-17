"""Deleting jobs for good frees their folders and never touches anything else."""
from types import SimpleNamespace

import pytest

from agent_core import cleanup, db


def _settings(tmp_path):
    s = SimpleNamespace(database_path=tmp_path / "a.sqlite3", work_dir=tmp_path / "work",
                        assets=tmp_path / "assets", shopee_output=tmp_path / "out" / "shopee",
                        facebook_output=tmp_path / "out" / "facebook")
    db.init_db(s.database_path)
    return s


def _job(s, job_id, status, source=""):
    db.register_job(s.database_path, {"id": job_id, "source_path": source, "file_name": job_id, "sha256": "h" + job_id,
                                      "mode": "trend", "status": status})
    db.update_job(s.database_path, job_id, status=status)
    for root in cleanup.roots(s):
        (root / job_id).mkdir(parents=True, exist_ok=True)
        (root / job_id / "f.bin").write_bytes(b"x" * 1000)


def test_purge_removes_rows_and_every_folder_but_not_the_source(tmp_path):
    s = _settings(tmp_path)
    source = tmp_path / "inbox" / "mine.mp4"
    source.parent.mkdir()
    source.write_bytes(b"owner video")
    _job(s, "job0001", "READY_FOR_REVIEW", str(source))
    out = cleanup.purge_job(s, "job0001")
    assert out["bytes"] == 4000
    assert db.get_job(s.database_path, "job0001") is None
    assert not any((r / "job0001").exists() for r in cleanup.roots(s))
    assert source.read_bytes() == b"owner video"


def test_running_jobs_are_kept(tmp_path):
    s = _settings(tmp_path)
    _job(s, "job0002", "RENDERING")
    with pytest.raises(ValueError):
        cleanup.purge_job(s, "job0002")
    assert cleanup.purge_groups(s, ["done", "failed", "unfinished"])["jobs"] == 0
    assert db.get_job(s.database_path, "job0002")


def test_groups_preview_and_orphans(tmp_path):
    s = _settings(tmp_path)
    _job(s, "job0003", "RENDER_FAILED")
    _job(s, "job0004", "READY_FOR_REVIEW")
    (s.work_dir / "gone0001").mkdir(parents=True)
    (s.work_dir / "gone0001" / "x").write_bytes(b"y" * 500)
    pv = cleanup.preview(s)
    assert pv["failed"]["jobs"] == 1 and pv["done"]["jobs"] == 1 and pv["orphans"]["bytes"] == 500
    res = cleanup.purge_groups(s, ["failed", "orphans"])
    assert res["jobs"] == 1 and not (s.work_dir / "gone0001").exists()
    assert db.get_job(s.database_path, "job0004")          # "done" was not chosen


def test_paths_outside_the_app_folders_are_refused(tmp_path):
    s = _settings(tmp_path)
    outside = tmp_path / "precious.txt"
    outside.write_text("keep")
    assert cleanup._safe_remove(s, outside) == 0 and outside.exists()
    assert cleanup.job_folders(s, "../etc") == []


def test_hidden_facebook_reel_is_an_unpublished_draft(tmp_path, monkeypatch):
    import httpx

    from agent_core import meta_reels
    sent = {}

    def handler(request):
        if request.url.host == "rupload.facebook.com":
            return httpx.Response(200, json={"success": True})
        if request.method == "POST" and b"upload_phase=start" in request.content:
            return httpx.Response(200, json={"video_id": "v1", "upload_url": "https://rupload.facebook.com/v1"})
        if request.method == "POST":
            sent["finish"] = request.content.decode()
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"status": {"video_status": "ready"}})

    real = httpx.Client
    monkeypatch.setattr(meta_reels.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 100)
    pub = meta_reels.MetaReelsPublisher("v21.0", "123", "token")
    out = pub.publish_reel(video, "mô tả", hidden=True, poll_interval=0)
    assert "video_state=DRAFT" in sent["finish"] and out["status"] == "DRAFT_SAVED_CONFIRMED"


def test_hidden_post_does_not_need_auto_publish():
    from types import SimpleNamespace

    from agent_core import workflow
    s = SimpleNamespace(meta_enabled=True, auto_publish=False)
    job = {"facebook_ready": 1, "affiliate_link": "", "brief_json": "{}", "script_json": "{}"}
    hidden = workflow.publish_blockers(s, job, "facebook_reels", "hidden")
    public = workflow.publish_blockers(s, job, "facebook_reels", "publish")
    assert not any("AUTO_PUBLISH" in b for b in hidden) and any("AUTO_PUBLISH" in b for b in public)


def test_preflight_blocks_when_ai_is_down(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from agent_core import preflight
    s = SimpleNamespace(database_path=tmp_path / "p.sqlite3", ai_model="m", videogen_provider="none",
                        ffmpeg_path="ffmpeg", ffprobe_path="ffprobe", media_root=tmp_path, meta_enabled=False)
    monkeypatch.setattr(preflight, "check_ai", lambda st: preflight._check("AI", False, "401"))
    out = preflight.run(s, fresh=True)
    assert not out["ok"] and any("AI" in b for b in out["blocking"])
    monkeypatch.setattr(preflight, "check_ai", lambda st: preflight._check("AI", True, "OK"))
    assert preflight.run(s, fresh=True)["ok"]
