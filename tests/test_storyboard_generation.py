"""Offline storyboard planning checks: no browser, provider requests or rendering."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_core.videogen import engine, providers
from agent_core.db import register_job


class OfflineProvider:
    name = "muse2api"

    def supports(self, capability):
        return capability in {"video", "image", "references"}


@pytest.fixture
def setup_plan(tmp_path):
    settings = SimpleNamespace(database_path=tmp_path / "queue.sqlite3",
                               videogen_max_clips=3, videogen_clip_seconds=6,
                               videogen_character=False)  # cast photos: own test below
    assets = []
    for rid, role in [("product", "product"), ("cast-a", "character")]:
        path = tmp_path / (rid + ".png")
        path.write_bytes(rid.encode())
        assets.append({"id": rid, "role": role, "path": str(path),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    plan = {"creation_branch": "series", "storyboard_version": 1,
            "style": "hand-drawn watercolor animation", "character": "legacy single person",
            "characters": [{"id": "a", "name": "An", "wardrobe": "red jacket"},
                           {"id": "b", "name": "Bo", "wardrobe": "blue coat"}],
            "reference_manifest": assets,
            "scenes": [{"visual": "generate", "gen_prompt": "An passes the product to Bo",
                        "duration": 6, "reference_ids": ["cast-a", "product"],
                        "shot": {"camera": "slow close-up", "action": "pass carefully",
                                 "continuity": "keep the red jacket", "sound": "rain"}}]}
    return settings, plan, OfflineProvider()


def plan_once(settings, plan, provider):
    with patch.object(engine, "remaining_today", return_value=20):
        return engine.plan_job(settings, "job-storyboard", plan, None, provider)


def test_ordered_refs_compile_shot_style_and_distinct_cast_without_new_identity(setup_plan):
    settings, plan, provider = setup_plan
    assert plan_once(settings, plan, provider) == 1
    tasks = engine.list_tasks(settings.database_path)
    assert len(tasks) == 1 and tasks[0]["kind"] == "video"
    task = tasks[0]
    assert task["depends_on"] is None
    assert task["refs"] == [plan["reference_manifest"][1]["path"],
                            plan["reference_manifest"][0]["path"]]
    prompt = task["prompt"]
    for text in ["watercolor", "An", "Bo", "red jacket", "blue coat", "slow close-up",
                 "pass carefully", "1 = cast-a (character)", "2 = product (product)",
                 "attachment 2: same shape"]:
        assert text in prompt
    assert "Photorealistic" not in prompt and "Same person" not in prompt
    assert "legacy single person" not in prompt


def test_reference_order_and_content_both_change_task_identity(setup_plan):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    first = engine.list_tasks(settings.database_path)[0]["id"]
    plan["scenes"][0]["reference_ids"].reverse()
    plan_once(settings, plan, provider)
    second = next(t["id"] for t in engine.list_tasks(settings.database_path)
                  if t["status"] == "PENDING")
    assert second != first
    asset = plan["reference_manifest"][0]
    Path(asset["path"]).write_bytes(b"replacement product")
    asset["sha256"] = hashlib.sha256(b"replacement product").hexdigest()
    plan_once(settings, plan, provider)
    third = next(t["id"] for t in engine.list_tasks(settings.database_path)
                 if t["status"] == "PENDING")
    assert third not in {first, second}


@pytest.mark.parametrize("failure", ["budget", "hash", "missing-id", "too-many", "duration"])
def test_invalid_plan_preserves_accepted_work(setup_plan, failure):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    original = engine.list_tasks(settings.database_path)[0]
    engine._update(settings.database_path, original["id"], status="SUBMITTED", remote_id="accepted")
    if failure == "budget":
        settings.videogen_max_clips = 0
    elif failure == "hash":
        Path(plan["reference_manifest"][0]["path"]).write_bytes(b"changed")
    elif failure == "missing-id":
        plan["scenes"][0]["reference_ids"] = ["unknown"]
    elif failure == "duration":
        plan["scenes"][0]["duration"] = "bad"
    else:
        plan["scenes"][0]["reference_ids"] = ["product"] * 7
    with pytest.raises(providers.ProviderError):
        plan_once(settings, plan, provider)
    tasks = engine.list_tasks(settings.database_path)
    assert len(tasks) == 1
    assert tasks[0]["status"] == "SUBMITTED" and tasks[0]["remote_id"] == "accepted"


def test_daily_budget_is_checked_before_cancelling_or_inserting(setup_plan):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    before = engine.list_tasks(settings.database_path)
    plan["scenes"][0]["shot"]["action"] = "drops the product"
    with patch.object(engine, "remaining_today", return_value=0), pytest.raises(providers.ProviderError):
        engine.plan_job(settings, "job-storyboard", plan, None, provider)
    assert engine.list_tasks(settings.database_path) == before


def test_retained_tasks_need_no_new_daily_budget(setup_plan):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    task = engine.list_tasks(settings.database_path)[0]
    engine._update(settings.database_path, task["id"], status="SUBMITTED", remote_id="accepted")
    with patch.object(engine, "remaining_today", return_value=0):
        assert engine.plan_job(settings, "job-storyboard", plan, None, provider) == 1
    assert engine.list_tasks(settings.database_path)[0]["status"] == "SUBMITTED"


def test_current_results_exclude_old_plan_and_include_matching_retry(setup_plan, tmp_path):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    old = engine.list_tasks(settings.database_path)[0]
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    engine._update(settings.database_path, old["id"], status="DOWNLOADED", result_path=str(clip))
    plan["scenes"][0]["shot"]["action"] = "drops the product"
    plan_once(settings, plan, provider)
    assert engine.results_for_storyboard(settings, "job-storyboard", plan) == {}
    current = next(t for t in engine.list_tasks(settings.database_path) if t["status"] == "PENDING")
    retry = engine._insert(settings.database_path, job_id="job-storyboard", scene_index=0,
                           kind="video", provider=provider.name, prompt=current["prompt"],
                           refs=current["refs"], duration=10, attempt=2)
    engine._update(settings.database_path, retry, status="DOWNLOADED", result_path=str(clip))
    assert engine.results_for_storyboard(settings, "job-storyboard", plan) == {0: clip}


def test_template_context_and_silent_muse_despite_dialogue(setup_plan):
    settings, plan, provider = setup_plan
    plan.update(setting="Vietnamese home", continuity_rules=["same wardrobe"])
    plan["scenes"][0]["shot"]["dialogue"] = "Hello!"
    plan_once(settings, plan, provider)
    prompt = engine.list_tasks(settings.database_path)[0]["prompt"]
    assert "Vietnamese home" in prompt and "same wardrobe" in prompt
    assert "Nobody speaks" in prompt   # dialogue is voiced by the app, not by Muse


@pytest.mark.parametrize("change", ["delete", "alter", "supersede"])
def test_pending_dispatch_revalidates_current_storyboard_before_consumption(setup_plan, tmp_path, change):
    settings, plan, provider = setup_plan
    settings.work_dir = tmp_path / "work"
    register_job(settings.database_path, {"id": "job-storyboard", "source_path": "",
                 "sha256": "", "file_name": "storyboard", "mode": "trend",
                 "brief": {"creation_branch": "template_series"}})
    plan_once(settings, plan, provider)
    task = engine.list_tasks(settings.database_path)[0]
    state = settings.work_dir / "job-storyboard" / "plan_state.json"
    state.parent.mkdir(parents=True)
    if change == "delete":
        Path(plan["reference_manifest"][0]["path"]).unlink()
    elif change == "alter":
        Path(plan["reference_manifest"][0]["path"]).write_bytes(b"changed")
    else:
        plan["scenes"][0]["shot"]["action"] = "drops the product"
    state.write_text(json.dumps({"plan": plan}), encoding="utf-8")
    with patch("agent_core.trend_intel.store.consume") as consume, \
            patch.object(OfflineProvider, "submit_video", create=True) as submit:
        engine._submit(settings, provider, task)
    consume.assert_not_called()
    submit.assert_not_called()
    tasks = engine.list_tasks(settings.database_path)
    assert len(tasks) == 1 and tasks[0]["status"] == "FAILED"
    assert "review and replan" in tasks[0]["error"]


def test_uncertain_submitting_does_not_run_pending_reference_guard(setup_plan):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    task = engine.list_tasks(settings.database_path)[0]
    task["status"] = "SUBMITTING"
    with patch.object(engine, "get_job") as lookup, \
            patch("agent_core.trend_intel.store.consume") as consume, \
            patch.object(OfflineProvider, "submit_video", create=True, return_value="accepted") as submit:
        engine._submit(settings, provider, task)
    lookup.assert_not_called()
    consume.assert_not_called()
    submit.assert_called_once()
    assert engine.get_task(settings.database_path, task["id"])["status"] == "SUBMITTED"


def test_replan_reuses_accepted_task_and_uploaded_cast_across_episodes(setup_plan):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    task = engine.list_tasks(settings.database_path)[0]
    engine._update(settings.database_path, task["id"], status="SUBMITTED", remote_id="accepted")
    plan_once(settings, plan, provider)
    assert engine.list_tasks(settings.database_path)[0]["status"] == "SUBMITTED"
    with patch.object(engine, "remaining_today", return_value=20):
        engine.plan_job(settings, "episode-two", plan, None, provider)
    tasks = engine.list_tasks(settings.database_path)
    assert len(tasks) == 2 and all(t["kind"] == "video" for t in tasks)
    assert tasks[0]["refs"] == tasks[1]["refs"]


def test_legacy_plan_keeps_existing_prompt_and_reference_contract(setup_plan):
    settings, plan, provider = setup_plan
    legacy = {"scenes": [{"visual": "generate", "gen_prompt": "legacy clip", "duration": 6}]}
    product = Path(plan["reference_manifest"][0]["path"])
    with patch.object(engine, "remaining_today", return_value=20):
        engine.plan_job(settings, "legacy", legacy, product, provider)
    task = engine.list_tasks(settings.database_path)[0]
    expected = engine.video_prompt(legacy["scenes"][0], legacy, True, False)
    assert task["prompt"] == expected
    assert task["id"] == engine.task_id("legacy", 0, "video", expected, [str(product)])


def test_shot_change_supersedes_but_cut_length_does_not(setup_plan):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    first = engine.list_tasks(settings.database_path)[0]
    assert first["duration"] == 10          # Muse always renders a full take
    plan["scenes"][0]["duration"] = 3       # only the edit changes: same clip, no new quota
    plan_once(settings, plan, provider)
    assert [t["id"] for t in engine.list_tasks(settings.database_path)] == [first["id"]]
    plan["scenes"][0]["shot"]["action"] = "drops the product"
    plan_once(settings, plan, provider)
    tasks = engine.list_tasks(settings.database_path)
    pending = next(t for t in tasks if t["status"] == "PENDING")
    assert pending["id"] != first["id"]
    assert next(t for t in tasks if t["id"] == first["id"])["status"] == "CANCELLED"


def test_no_generate_scenes_cancels_active_but_preserves_completed(setup_plan):
    settings, plan, provider = setup_plan
    plan_once(settings, plan, provider)
    task = engine.list_tasks(settings.database_path)[0]
    engine._update(settings.database_path, task["id"], status="SUCCEEDED")
    plan["scenes"][0]["shot"]["action"] = "drops the product"
    plan_once(settings, plan, provider)
    plan["scenes"] = [{"visual": "product_image"}]
    assert plan_once(settings, plan, provider) == 0
    tasks = engine.list_tasks(settings.database_path)
    assert {t["status"] for t in tasks} == {"SUCCEEDED", "CANCELLED"}


def test_missing_cast_photos_are_generated_first_and_reused(setup_plan, tmp_path):
    settings, plan, provider = setup_plan
    settings.videogen_character = True
    plan["scenes"][0]["cast"] = ["a", "b"]
    plan_once(settings, plan, provider)
    tasks = engine.list_tasks(settings.database_path)
    sheets = sorted((t for t in tasks if t["kind"] == "image"), key=lambda t: -t["scene_index"])
    video = next(t for t in tasks if t["kind"] == "video")
    assert [t["scene_index"] for t in sheets] == [-1, -2]
    assert sheets[0]["depends_on"] is None and sheets[1]["depends_on"] is None   # made in parallel
    assert video["depends_on"] is None          # the clip waits for its photos via charref
    assert "charref:a" in video["refs"] and "charref:b" in video["refs"]
    assert "cast:a" in sheets[0]["prompt"] and "cast:a" not in engine.muse_prompt.strip_internal(
        sheets[0]["prompt"]) if hasattr(engine, "muse_prompt") else True
    # waiting: photo not ready -> refs unresolved
    assert engine._resolve_refs(settings.database_path, video) is None
    photo = tmp_path / "a.png"
    photo.write_bytes(b"png")
    for t in sheets:
        engine._update(settings.database_path, t["id"], status="DOWNLOADED", result_path=str(photo))
    refs = engine._resolve_refs(settings.database_path, video)
    assert refs and refs.count(photo) == 2
    # replanning keeps the finished photos instead of making new ones
    plan_once(settings, plan, provider)
    again = [t for t in engine.list_tasks(settings.database_path) if t["kind"] == "image"]
    assert len(again) == 2 and all(t["status"] == "DOWNLOADED" for t in again)


def test_cast_photo_is_not_failed_by_the_storyboard_revalidation(setup_plan, tmp_path):
    settings, plan, provider = setup_plan
    settings.videogen_character = True
    settings.work_dir = tmp_path / "work"
    settings.videogen_daily_limit = 50
    register_job(settings.database_path, {"id": "job-storyboard", "source_path": "",
                 "sha256": "", "file_name": "storyboard", "mode": "trend",
                 "brief": {"creation_branch": "template_series"}})
    plan["scenes"][0]["cast"] = ["a"]
    plan_once(settings, plan, provider)
    state = settings.work_dir / "job-storyboard" / "plan_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"plan": plan}), encoding="utf-8")
    sheet = next(t for t in engine.list_tasks(settings.database_path) if t["kind"] == "image")
    with patch.object(OfflineProvider, "submit_image", create=True, return_value="r1") as submit:
        engine._submit(settings, provider, sheet)
    submit.assert_called_once()
    assert engine.get_task(settings.database_path, sheet["id"])["status"] == "SUBMITTED"
