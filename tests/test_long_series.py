"""Long videos (parts + assemble), shared cast photos, series batches, single-scene regeneration."""
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_core import creative, db, long_video, shared_cast, workflow
from agent_core.creation_branches import SURI_FAMILY, normalise_brief
from agent_core.settings import Settings
from agent_core.videogen import engine

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"


@pytest.fixture
def s(tmp_path):
    return Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                    media_root=tmp_path / "media", muse_embedded=False)


OUTLINE = {"title": "Rừng ngập mặn thở thế nào", "takeaway": "Rễ thở giúp cây sống", "style_profile": "documentary",
           "music_mood": "chill", "characters": [],
           "segments": [{"title": "Câu hỏi", "beats": "Rễ mọc ngược?", "summary": "đặt câu hỏi"},
                        {"title": "Cơ chế", "beats": "lỗ khí, ống dẫn", "summary": "giải thích"},
                        {"title": "Kết", "beats": "vì sao quan trọng", "summary": "kết luận"}],
           "caption": "Rừng biết thở", "hashtags": ["#rungngapman"]}


def test_outline_creates_parts_with_shared_cast_and_flags(s):
    parent = long_video.create(s, "Rễ cây rừng ngập mặn", 90, "viral_creator")
    assert db.get_job(s.database_path, parent)["mode"] == "long"
    out = long_video.outline(s, parent, ask=lambda p: OUTLINE)
    kids = out["children"]
    assert len(kids) == 3
    first, second = (json.loads(db.get_job(s.database_path, k)["brief_json"]) for k in kids[:2])
    assert first["segment"]["index"] == 1 and not first.get("no_hook") and "shared_cast_from" not in first
    assert second["no_hook"] and second["no_music"] and second["no_variants"]
    assert second["shared_cast_from"] == kids[0] and second["fixed_style"] == "documentary"
    assert second["segment"]["previous"] == "đặt câu hỏi" and second["segment"]["next"] == "kết luận"
    assert db.get_job(s.database_path, parent)["status"] == long_video.PARENT_WAIT
    prompt = creative.branch_plan_prompt({**normalise_brief("viral_creator"), **second}, {}, True, False)
    assert "PHẦN 2/3" in prompt and "không hook mở đầu mới" in prompt


def test_outline_failure_does_not_hang(s):
    parent = long_video.create(s, "x", 60)

    def broken(prompt):
        raise RuntimeError("AI down")
    with pytest.raises(RuntimeError):
        long_video.outline(s, parent, ask=broken)
    assert db.get_job(s.database_path, parent)["status"] == "CONTENT_GENERATION_FAILED"


def _clip(path: Path, seconds: float, color: str) -> Path:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:size=180x320:rate=30", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=44100", "-t", str(seconds), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)
    return path


def test_parts_are_assembled_into_one_video(s, tmp_path):
    parent = long_video.create(s, "Rễ cây", 60)
    kids = long_video.outline(s, parent, ask=lambda p: {**OUTLINE, "segments": OUTLINE["segments"][:2]})["children"]
    for i, k in enumerate(kids):
        clip = _clip(tmp_path / f"p{i}.mp4", 2, ["red", "blue"][i])
        db.update_job(s.database_path, k, status="READY_FOR_REVIEW", output_path=str(clip))
    assert long_video.check_parents(s) == [parent]
    with patch("agent_core.editor.music.pick_track", return_value=None):
        result = long_video.assemble(s, parent)
    job = db.get_job(s.database_path, parent)
    assert job["status"] == "READY_FOR_REVIEW" and Path(job["output_path"]).is_file()
    assert 3.8 <= result["duration"] <= 4.3 and result["parts"] == 2
    assert json.loads(job["content_json"])["title"] == OUTLINE["title"]


def test_failed_part_is_reported_on_the_parent(s):
    parent = long_video.create(s, "Rễ cây", 60)
    kids = long_video.outline(s, parent, ask=lambda p: {**OUTLINE, "segments": OUTLINE["segments"][:2]})["children"]
    db.update_job(s.database_path, kids[1], status="RENDER_FAILED")
    assert long_video.check_parents(s) == []
    assert "Phần 2" in db.get_job(s.database_path, parent)["last_error"]


def test_shared_cast_photos_and_readiness(s, tmp_path):
    db.register_job(s.database_path, {"id": "ep1", "source_path": "", "sha256": "1", "file_name": "e1"})
    engine.ensure_schema(s.database_path)
    assert not shared_cast.ready(s, "ep1")            # nothing planned yet
    tid = engine._insert(s.database_path, job_id="ep1", scene_index=-1, kind="image", provider="m",
                         prompt="photo\n§cast:suri", refs=[])
    assert not shared_cast.ready(s, "ep1")            # photo still being made
    photo = tmp_path / "suri.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    engine._update(s.database_path, tid, status="DOWNLOADED", result_path=str(photo))
    assert shared_cast.ready(s, "ep1")
    assert shared_cast.photos(s, "ep1") == {"suri": photo}
    refs = shared_cast.references(s, {"shared_cast_from": "ep1"})
    assert refs == [("cast-suri", "character:suri", photo)]


def test_series_batch_and_scene_regenerate_api(s, monkeypatch):
    from agent_core.web import app as web
    monkeypatch.setattr(web, "get_settings", lambda: s)
    with patch.object(web, "setup_logging"), TestClient(web.create_app(start_worker=False)) as c:
        h = {"X-Requested-With": "affiliate-agent"}
        r = c.post("/api/create", headers=h, json={"creation_branch": "template_series",
                   "template_id": "suri-family", "ideas": ["Suri gấp áo", "Suri đi chợ", "Suri trồng cây"]})
        assert r.status_code == 200, r.text
        jobs = r.json()["jobs"]
        briefs = [json.loads(db.get_job(s.database_path, j)["brief_json"]) for j in jobs]
        assert [b["episode"]["number"] for b in briefs] == [1, 2, 3]
        assert briefs[2]["episode"]["previous"] == ["Suri gấp áo", "Suri đi chợ"]
        assert "shared_cast_from" not in briefs[0] and briefs[1]["shared_cast_from"] == jobs[0]
        long = c.post("/api/create", headers=h, json={"creation_branch": "viral_creator",
                      "topic": "Rễ cây", "duration_seconds": 120})
        assert long.json().get("long") and db.get_job(s.database_path, long.json()["job_id"])["mode"] == "long"
        short = c.post("/api/create", headers=h, json={"creation_branch": "viral_creator",
                       "topic": "Mèo", "duration_seconds": 30})
        assert json.loads(db.get_job(s.database_path, short.json()["job_id"])["brief_json"])["duration_range"] == [25, 35]
        assert c.post(f"/api/jobs/{jobs[0]}/scenes/0/regenerate", headers=h, json={}).status_code == 400


def test_regenerate_scene_queues_a_new_take(s):
    job_id = workflow.create_trend_job(s, "Mèo", creation_branch="viral_creator")
    plan = creative.normalise_plan({"creation_branch": "viral_creator", "storyboard_version": 2, "scenes": [
        {"visual": "generate", "duration": 4, "cast": [], "shot": {"action": "cat looks at mirror"}},
        {"visual": "generate", "duration": 4, "cast": [], "shot": {"action": "cat paws mirror"}}]}, "trend")
    workflow._save_plan_state(s, job_id, {"plan": plan})

    class P:
        name = "muse2api"

        def supports(self, cap):
            return True
    with patch.object(workflow, "get_provider", return_value=P()):
        first = workflow.regenerate_scene(s, job_id, 1, action="cat jumps back from the mirror",
                                          camera="low angle close-up")
        again = workflow.regenerate_scene(s, job_id, 1)
    assert first["attempt"] == 1 and again["attempt"] == 2
    tasks = engine.list_tasks(s.database_path, job_id)
    assert len(tasks) == 2 and all("jumps back" in t["prompt"] and "low angle" in t["prompt"] for t in tasks)
    assert db.get_job(s.database_path, job_id)["status"] == engine.WAIT_STATUS
    with pytest.raises(workflow.WorkflowError):
        workflow.regenerate_scene(s, job_id, 7)


def test_suri_series_is_chibi():
    assert "chibi" in SURI_FAMILY["style"].lower()
