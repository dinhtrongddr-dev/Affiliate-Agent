"""Phase 3: video features, owner-entered metrics, channel lessons, pronunciation."""
import json
import sqlite3

import pytest

from agent_core import db, learning
from agent_core.creative import branch_plan_prompt
from agent_core.creation_branches import normalise_brief
from agent_core.editor.lexicon import spoken


def _video(path, job_id, hook, style, retention, completion, views=1000):
    db.register_job(path, {"id": job_id, "source_path": "", "sha256": job_id, "file_name": job_id})
    timeline = {"creation_branch": "viral_creator", "style_profile": style, "hook": hook,
                "music_mood": "funny", "scenes": [{"talking": True}, {}], "characters": [{"id": "a"}]}
    learning.save_features(path, job_id, learning.features_from_timeline(timeline, 22))
    learning.record(path, job_id, "facebook", {"views": views, "retention_3s_rate": retention,
                                               "completion_rate": completion, "shares": "12"})


def test_metrics_are_cleaned():
    assert learning.clean_metrics({"retention_3s_rate": "45%", "completion_rate": "0,3", "views": "1200"}) == \
        {"retention_3s_rate": 0.45, "completion_rate": 0.3, "views": 1200}
    with pytest.raises(ValueError):
        learning.clean_metrics({})
    with pytest.raises(ValueError):
        learning.clean_metrics({"views": "nhiều"})


def test_features_and_buckets():
    f = learning.features_from_timeline({"hook": "Ai hiểu mẹ nhất?", "scenes": [], "creation_branch": "viral_creator"}, 27)
    assert f["length"] == "25-35s" and f["hook_type"] == "câu hỏi" and f["branch"] == "viral_creator"
    assert learning.hook_type("14.869 người đã mua") == "con số"


def test_lessons_only_after_enough_videos(tmp_path):
    path = tmp_path / "d.sqlite3"
    for i in range(4):
        _video(path, f"q{i}", "Ai hiểu mẹ nhất?", "ugc_phone", 0.7, 0.5)
        _video(path, f"s{i}", "Cả nhà im lặng", "sitcom_bright", 0.3, 0.15)
    info = learning.insights(path, min_videos=10)
    assert info["videos"] == 8 and not info["ready"]
    assert learning.prompt_block(path) == ""                 # 8 < 10: nothing steers the prompt yet
    for i in range(2):
        _video(path, f"x{i}", "Ai hiểu mẹ nhất?", "ugc_phone", 0.65, 0.45)
    block = learning.prompt_block(path)
    assert "câu hỏi" in block and "ugc_phone" in block and "10 video" in block
    top = learning.insights(path)["by_feature"]["hook_type"][0]
    assert top[0] == "câu hỏi"


def test_storyboard_prompt_carries_the_lessons():
    prompt = branch_plan_prompt(normalise_brief("viral_creator"), {}, True, False,
                                learning="Bài học từ số liệu thật của kênh: hook câu hỏi tốt hơn")
    assert "hook câu hỏi tốt hơn" in prompt


def test_spoken_text_reads_units_and_brands():
    assert spoken("Cây lau 45cm microfiber giá 99k") == "Cây lau 45 xen-ti-mét mai-crô-phai-bơ giá 99 nghìn"
    assert spoken("Ốp iPhone") == "Ốp ai-phôn"
    assert spoken("Chạy setup") == "Chạy setup"                # whole words only


def test_custom_pronunciations(tmp_path):
    from agent_core.editor.lexicon import load
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "pronunciations.json").write_text(json.dumps({"Topgia": "tóp gia"}), encoding="utf-8")
    assert spoken("Giấy Topgia", load(tmp_path)) == "Giấy tóp gia"


def test_performance_api_and_page(tmp_path, monkeypatch):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from agent_core.settings import Settings
    from agent_core.web import app as web
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                 media_root=tmp_path / "m", muse_embedded=False)
    monkeypatch.setattr(web, "get_settings", lambda: s)
    db.register_job(s.database_path, {"id": "j1", "source_path": "", "sha256": "j1", "file_name": "Video 1"})
    with patch.object(web, "setup_logging"), TestClient(web.create_app(start_worker=False)) as c:
        h = {"X-Requested-With": "affiliate-agent"}
        assert c.post("/api/jobs/j1/performance", json={"metrics": {}}, headers=h).status_code == 400
        assert c.post("/api/jobs/j1/performance", json={"platform": "x", "metrics": {"views": 1}},
                      headers=h).status_code == 400
        r = c.post("/api/jobs/j1/performance", json={"platform": "facebook",
                   "metrics": {"views": "1500", "retention_3s_rate": "52"}}, headers=h)
        assert r.status_code == 200
        page = c.get("/performance")
        assert page.status_code == 200 and "Video 1" in page.text
    with sqlite3.connect(s.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM video_performance").fetchone()[0] == 1
