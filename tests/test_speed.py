"""Speed plan T2-T4: parallel Muse, reuse cuts, code checks, worker lanes."""
from types import SimpleNamespace

from agent_core import creative, db
from agent_core.videogen import engine


def test_stock_without_pexels_becomes_generate():
    plan = {"scenes": [{"visual": "stock", "stock_query": "woman cleaning"},
                       {"visual": "product_image"}, {"visual": "generate"}]}
    assert creative.fix_visual_sources(plan, has_stock=False, can_generate=True, has_image=True) == 1
    assert [s["visual"] for s in plan["scenes"]] == ["generate", "product_image", "generate"]
    assert plan["scenes"][0]["shot"]["action"] == "woman cleaning"
    plan2 = {"scenes": [{"visual": "stock"}, {"visual": "card"}]}
    creative.fix_visual_sources(plan2, has_stock=False, can_generate=False, has_image=True)
    assert plan2["scenes"][0]["visual"] == "product_image"


def test_reuse_of_points_to_an_earlier_generated_clip():
    plan = creative.normalise_plan({"creation_branch": "viral_creator", "scenes": [
        {"visual": "generate", "shot": {"action": "a"}},
        {"visual": "generate", "reuse_of": 0, "shot": {"action": "b"}},
        {"visual": "generate", "reuse_of": 5, "shot": {"action": "c"}}]}, "trend")
    assert [s["visual"] for s in plan["scenes"]] == ["generate", "reuse", "generate"]
    assert plan["scenes"][1]["reuse_of"] == 0
    compiled_scenes = [i for i, s in enumerate(plan["scenes"]) if s["visual"] == "generate"]
    assert compiled_scenes == [0, 2]                 # no Muse clip for the reuse cut


def test_storyboard_issues_are_checked_in_code():
    line = "Vì mẹ đi làm về muộn nên cả nhà quyết định nấu cơm thật ngon tặng mẹ"   # 16 words
    good = {"creation_branch": "viral_creator", "takeaway": "Cả nhà nấu cơm tặng mẹ",
            "scenes": [{"lines": [{"text": line}], "mission": "story"} for _ in range(3)]}
    assert creative.storyboard_issues(good) == []
    long = {"creation_branch": "viral_creator", "takeaway": "x",
            "scenes": [{"lines": [{"text": " ".join(["chữ"] * 12)}]} for _ in range(25)]}
    issues = creative.storyboard_issues(long)
    # the voice decides the length now: 300 words is far over 30 s
    assert any("Thời lượng ước theo lời" in i for i in issues) and any("quá dài" in i for i in issues)


def test_parallel_cap_limits_submissions(tmp_path, monkeypatch):
    s = SimpleNamespace(database_path=tmp_path / "q.sqlite3", videogen_parallel=2, work_dir=tmp_path,
                        videogen_daily_limit=100, videogen_task_timeout_minutes=15)
    engine.ensure_schema(s.database_path)
    for i in range(5):
        engine._insert(s.database_path, job_id="j", scene_index=i, kind="video", provider="m",
                       prompt=f"p{i}", refs=[], duration=10)
    sent = []

    class P:
        name = "m"

        def submit_video(self, prompt, refs, duration, key):
            sent.append(prompt)
            return "r" + prompt

    monkeypatch.setattr("agent_core.trend_intel.store.consume", lambda *a, **k: True)
    engine.poll(s, P())
    assert len(sent) == 2
    engine.poll(s, P())
    assert len(sent) == 2                            # still 2 in flight


def test_lanes_claim_only_their_kinds(tmp_path):
    path = tmp_path / "t.sqlite3"
    db.enqueue_task(path, "fetch_trends")
    db.enqueue_task(path, "render", "j1")
    db.enqueue_task(path, "process", "j2")
    assert db.claim_task(path, ("process",))["kind"] == "process"
    assert db.claim_task(path, ("render",))["kind"] == "render"
    assert db.claim_task(path, ("process",)) is None
    assert db.claim_task(path, ("fetch_trends",))["kind"] == "fetch_trends"
