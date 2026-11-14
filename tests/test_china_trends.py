"""China Trend Intelligence: collectors (mocked), stages, scoring, ideas, autopilot, gate, learning."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from agent_core import db, formats
from agent_core.cn_trends import analysis, collectors, formula, pipeline, store

BILI = {"code": 0, "data": {"list": [{"title": "手工敲虾到底有多好吃", "tname": "美食", "duration": 95, "desc": "x",
                                      "bvid": "BV1", "pic": "http://i0.hdslb.com/a.jpg", "owner": {"name": "u"},
                                      "stat": {"view": 100000, "like": 9000, "share": 800, "reply": 300}}]}}
DOUYIN = {"data": {"word_list": [{"word": "手工敲虾到底有多好吃", "hot_value": 11063882, "position": 2,
                                  "video_count": 3, "event_time": 1}]}}
WEIBO = {"data": {"realtime": [{"word": "无人健身房火了", "num": 1133780, "label_name": "热"},
                               {"word": "广告", "is_ad": 1}]}}
TOUTIAO = {"data": [{"Title": "天价药进医保", "HotValue": "51152821", "Url": "https://t/1"}]}


def handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "bilibili" in url:
        return httpx.Response(200, json=BILI)
    if "douyin" in url:
        return httpx.Response(200, json=DOUYIN)
    if "weibo" in url:
        return httpx.Response(200, json=WEIBO)
    if "toutiao" in url:
        return httpx.Response(200, json=TOUTIAO)
    return httpx.Response(404)


@pytest.fixture
def s(tmp_path):
    return SimpleNamespace(database_path=tmp_path / "t.sqlite3", work_dir=tmp_path / "work",
                           cn_trend_sources="", cn_trends_enabled=True, cn_auto_ideas=0,
                           autopilot_enabled=True, autopilot_daily_videos=3, autopilot_min_score=60,
                           autopilot_goal_mix="fb_growth:70,affiliate:30", ffmpeg_path="ffmpeg",
                           ffprobe_path="ffprobe", trend_min_purchase_intent=40)


def test_collectors_parse_public_lists_and_skip_ads(s):
    out = collectors.collect_all(s, transport=httpx.MockTransport(handler))
    assert out == {"bilibili": 1, "douyin": 1, "weibo": 1, "toutiao": 1}
    topics = {t["title_zh"]: t for t in store.list_topics(s.database_path, since_hours=None)}
    shrimp = topics["手工敲虾到底有多好吃"]
    assert sorted(shrimp["platforms"]) == ["bilibili", "douyin"]      # same title, two platforms
    obs = store.observations(s.database_path, shrimp["key"])
    assert {o["platform"] for o in obs} == {"bilibili", "douyin"}
    assert "广告" not in topics
    state = store.source_state(s.database_path)
    assert state["bilibili"]["items_last"] == 1 and state["xiaohongshu"]["supported"] == "manual"


def test_failing_source_pauses_without_stopping_others(s):
    def broken(client):
        raise RuntimeError("HTTP 412")
    fetchers = {"bilibili": broken, "weibo": lambda c: collectors.weibo(c)}
    for _ in range(collectors.FAILS_BEFORE_PAUSE):
        out = collectors.collect_all(s, transport=httpx.MockTransport(handler), fetchers=fetchers)
    assert out["weibo"] == 1 and "lỗi" in out["bilibili"]
    assert store.source_state(s.database_path)["bilibili"]["paused_until"]
    again = collectors.collect_all(s, transport=httpx.MockTransport(handler), fetchers=fetchers)
    assert again["bilibili"].startswith("tạm dừng")


def test_manual_trend_validation(s):
    with pytest.raises(ValueError):
        collectors.add_manual(s, "xiaohongshu", " ")
    with pytest.raises(ValueError):
        collectors.add_manual(s, "nope", "x")
    key = collectors.add_manual(s, "kuaishou", "修复老物件", "https://k/1", "quy trình phục chế")
    assert store.get_topic(s.database_path, key)["source_kind"] == "manual"


def test_stage_rules_and_low_confidence():
    few = [{"heat": 100, "rank": 20}]
    stage, conf, facts = analysis.stage_of(few, age_hours=2, last_seen_hours=0)
    assert stage == "early_signal" and conf <= 0.3 and "độ tin cậy thấp" in facts["why"]
    rising = [{"heat": h, "rank": r} for h, r in ((100, 30), (130, 25), (180, 18))]
    assert analysis.stage_of(rising, 6, 0)[0] == "early_signal"
    assert analysis.stage_of(rising, 30, 0)[0] == "rising"
    falling = [{"heat": h, "rank": r} for h, r in ((200, 2), (150, 6), (100, 9))]
    assert analysis.stage_of(falling, 30, 0)[0] == "saturated"
    assert analysis.stage_of(rising, 30, 20)[0] == "saturated"      # gone from the list


def _label_reply(prompt):
    keys = [json.loads(line)["key"] for line in prompt.splitlines() if line.startswith('{"key"')]
    return {"topics": [{"key": k, "title_vi": "Tôm đập tay ngon cỡ nào", "summary_vi": "Món tôm đập tay",
                        "niche": "ky_nang" if i == 0 else "new:Ẩm thực đường phố", "formats": ["process"],
                        "vn_adaptable": 85, "producible": 80, "commercial": 40, "sensitive": False,
                        "facebook_risk": "low", "copyright_risk": "low", "reason": "quy trình đẹp"}
                       for i, k in enumerate(keys)]}


def test_label_and_score(s):
    collectors.collect_all(s, transport=httpx.MockTransport(handler))
    topics = store.list_topics(s.database_path, since_hours=None)
    assert analysis.label_topics(s, topics, ask=_label_reply) == len(topics)
    assert analysis.score_all(s) == len(topics)
    shrimp = next(t for t in store.list_topics(s.database_path, since_hours=None) if "敲虾" in t["title_zh"])
    assert shrimp["title_vi"] and shrimp["opportunity"] is not None and shrimp["stage"]
    assert shrimp["scores"]["parts"]["spread"] >= 70                 # on two platforms
    assert 0 < shrimp["confidence"] < 1


def test_sensitive_topic_is_blocked(s):
    key = collectors.add_manual(s, "weibo", "某敏感事件")
    t = store.get_topic(s.database_path, key)
    analysis.label_topics(s, [t], ask=lambda p: {"topics": [{"key": key, "title_vi": "x", "niche": "khac",
                                                              "sensitive": True}]})
    analysis.score_all(s)
    t = store.get_topic(s.database_path, key)
    assert t["status"] == "BLOCKED" and t["opportunity"] is None
    with pytest.raises(ValueError):
        formula.generate_ideas(s, key, ask=lambda p, i: {})


def test_new_niche_is_proposed_after_five_topics(s):
    analysis.ensure_niches(s)
    for i in range(5):
        key = collectors.add_manual(s, "douyin", f"街边小吃 {i}")
        store.update_topic(s.database_path, key, labels_json={"niche_raw": "new:Ẩm thực đường phố"})
    assert analysis.propose_niches(s) == ["ẩm thực đường phố"]
    proposed = [n for n in store.niches(s.database_path) if n["status"] == "proposed"]
    assert len(proposed) == 1 and len(proposed[0]["evidence"]) == 5


IDEAS = {"formula": {"hook_mechanism": "kết quả ở giây 0", "reusable_formula": "trước/sau + quy trình lạ"},
         "ideas": [{"title": f"Ý tưởng {i}", "premise": "p", "hook": "Nhìn tay làm này", "format": "process",
                    "duration_s": 25, "niche": "ky_nang", "goal": "fb_growth", "est_score": 80 - i,
                    "why": "w"} for i in range(3)]}


def test_ideas_and_job_from_idea(s, tmp_path):
    key = collectors.add_manual(s, "douyin", "手工敲虾到底有多好吃")
    store.update_topic(s.database_path, key, title_vi="Tôm đập tay", summary_vi="Món tôm đập tay",
                       opportunity=70, stage="rising", niche="ky_nang")
    with patch("agent_core.workflow._recent_hooks", return_value=[]):
        ids = formula.generate_ideas(s, key, ask=lambda prompt, images: IDEAS)
    assert len(ids) == 3
    assert store.get_topic(s.database_path, key)["formula"]["reusable_formula"]
    idea = store.get_idea(s.database_path, ids[0])
    assert idea["format"] == "process" and idea["branch"] == "viral_creator" and 60 <= idea["est_score"] <= 80
    from agent_core.settings import Settings
    real = Settings(_env_file=None, project_root=tmp_path, database_path=s.database_path,
                    media_root=tmp_path / "m")
    with patch("agent_core.workflow.enqueue_task") as queued:
        job_id = formula.idea_to_job(real, ids[0])
    brief = json.loads(db.get_job(s.database_path, job_id)["brief_json"])
    assert brief["creation_branch"] == "viral_creator" and brief["format"] == "process"
    assert brief["formula"]["reusable_formula"] and brief["trend_source"]["platform"] == "douyin"
    assert 15 <= brief["duration_range"][0] < brief["duration_range"][1] <= 35
    queued.assert_called_once()
    assert store.get_idea(s.database_path, ids[0])["status"] == "QUEUED"


def test_autopilot_respects_cap_floor_and_mix(s):
    key = collectors.add_manual(s, "weibo", "x")
    for i, (goal, score) in enumerate([("fb_growth", 90), ("fb_growth", 85), ("affiliate", 80),
                                       ("fb_growth", 75), ("fb_growth", 50)]):
        store.save_idea(s.database_path, {"topic_key": key, "title": f"i{i}", "goal": goal, "est_score": score})
    made = []

    def fake_job(settings, iid):
        jid = f"j{len(made)}"
        db.register_job(settings.database_path, {"id": jid, "source_path": "", "sha256": jid, "file_name": jid,
                                                 "mode": "trend", "brief": {"goal": store.get_idea(
                                                     settings.database_path, iid)["goal"]}})
        made.append(iid)
        store.update_idea(settings.database_path, iid, status="QUEUED", job_id=jid)
        return jid
    with patch.object(formula, "idea_to_job", side_effect=fake_job):
        out = pipeline.autopilot(s)
        assert len(out["created"]) == 3
        again = pipeline.autopilot(s)
    goals = [store.get_idea(s.database_path, i)["goal"] for i in made]
    assert goals.count("affiliate") == 1 and "skipped" in again
    s.autopilot_enabled = False
    assert "skipped" in pipeline.autopilot(s)


def test_formats_drive_duration_and_facts():
    assert formats.duration_range({"format": "mini_doc"}) == (40.0, 90.0)
    assert formats.duration_range({"creation_branch": "viral_creator"}) == (15.0, 30.0)
    assert formats.duration_range({"duration_range": [20, 400]}) == (20.0, 120.0)
    assert formats.needs_facts({"format": "explainer"}) and formats.needs_facts({"niche": "kien_thuc"})


def test_storyboard_checks_facts_and_copying():
    from agent_core import creative
    plan = {"creation_branch": "viral_creator", "format": "explainer", "hook": "Tôm đập tay ngon cỡ nào",
            "source_texts": ["Tôm đập tay ngon cỡ nào"],
            "facts": [{"claim": "Tôm có 3 tim", "confidence": 40}],
            "scenes": [{"duration": 35, "lines": [{"speaker": "narrator", "text": "Ai cũng thích"}]}]}
    issues = " | ".join(creative.storyboard_issues(plan))
    assert "chưa chắc" in issues and "Giống nội dung trend gốc" in issues


def test_quality_gate_decisions():
    from agent_core.qa_video import gate_decision
    assert gate_decision({"status": "PRIORITY_PUBLISH", "visual": {"bad_scenes": []}}, 0)[0] == "PASS"
    d, scenes, _ = gate_decision({"status": "NEEDS_REVISION", "visual": {"bad_scenes": [1, 3]}}, 0)
    assert d == "FIX" and scenes == [1, 3]
    assert gate_decision({"status": "NEEDS_REVISION", "visual": {"bad_scenes": [1]}}, 1)[0] == "REJECT"
    assert gate_decision({"status": "FAIL", "gates": ["wrong_product"], "visual": {}}, 0)[0] == "REJECT"


def test_regenerate_scenes_adds_a_new_attempt(tmp_path):
    from agent_core.videogen import engine
    st = SimpleNamespace(database_path=tmp_path / "q.sqlite3")
    db.register_job(st.database_path, {"id": "j", "source_path": "", "sha256": "j", "file_name": "j"})
    engine.ensure_schema(st.database_path)
    tid = engine._insert(st.database_path, job_id="j", scene_index=1, kind="video", provider="muse2api",
                         prompt="p", refs=[], duration=10)
    engine._update(st.database_path, tid, status="DOWNLOADED")
    assert engine.regenerate_scenes(st, "j", [1, 1, 5]) == 1
    rows = [t for t in engine.list_tasks(st.database_path, "j") if t["scene_index"] == 1]
    assert sorted(t["attempt"] for t in rows) == [1, 2]
    assert db.get_job(st.database_path, "j")["status"] == engine.WAIT_STATUS


def test_learning_suggestions_need_enough_videos(tmp_path):
    from agent_core import learning
    path = tmp_path / "d.sqlite3"
    def video(jid, niche, retention):
        db.register_job(path, {"id": jid, "source_path": "", "sha256": jid, "file_name": jid})
        learning.save_features(path, jid, {"niche": niche, "format": "story", "goal": "fb_growth",
                                           "predicted": 60, "hook": "h"})
        learning.record(path, jid, "facebook", {"views": 1000, "retention_3s_rate": retention,
                                                "completion_rate": retention / 2})
    for i in range(4):
        video(f"a{i}", "ky_nang", 0.7)
        video(f"b{i}", "mini_drama", 0.3)
    assert learning.suggestions(path) == []                    # 4 per group < 5
    video("a4", "ky_nang", 0.72)
    video("b4", "mini_drama", 0.28)
    sug = learning.suggestions(path)
    assert sug and sug[0]["increase"] == "ky_nang" and sug[0]["shift"] <= learning.MAX_SHIFT
    assert learning.prediction_check(path)["pairs"] == 10
    kpi = learning.goal_kpis(path)
    assert kpi["fb_growth"]["videos"] == 10 and kpi["affiliate"]["clicks"] is None


def test_china_page_and_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from agent_core.settings import Settings
    from agent_core.web import app as web
    st = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                  media_root=tmp_path / "m", muse_embedded=False)
    monkeypatch.setattr(web, "get_settings", lambda: st)
    h = {"X-Requested-With": "affiliate-agent"}
    with patch.object(web, "setup_logging"), TestClient(web.create_app(start_worker=False)) as c:
        assert c.get("/china").status_code == 200
        r = c.post("/api/china/manual", headers=h, data={"platform": "xiaohongshu", "title": "收纳技巧",
                                                         "notes": "mẹo xếp tủ"})
        assert r.status_code == 200
        key = r.json()["key"]
        assert c.post("/api/china/manual", headers=h, data={"platform": "x", "title": "y"}).status_code == 400
        bad = c.post("/api/china/manual", headers=h, data={"platform": "douyin", "title": "z"},
                     files={"reference": ("a.exe", b"x", "application/octet-stream")})
        assert bad.status_code == 400
        assert c.post(f"/api/china/topics/{key}/ideas", headers=h).json()["queued"]
        assert c.post(f"/api/china/topics/{key}/status", headers=h, json={"status": "IGNORED"}).status_code == 200
        assert c.post("/api/china/topics/nope/ideas", headers=h).status_code == 404
        page = c.get("/china?tab=sources")
        assert "Xiaohongshu" in page.text and "收纳技巧" in c.get("/china").text
        assert c.get("/performance").status_code == 200
