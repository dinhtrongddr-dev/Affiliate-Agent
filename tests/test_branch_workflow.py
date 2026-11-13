"""Offline integration checks for explicit creation intents and affiliate separation."""
import json
from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from agent_core import catalog, db, workflow
from agent_core.settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, project_root=tmp_path, media_root=tmp_path / "media",
                    database_path=tmp_path / "state.sqlite3", ai_api_key=SecretStr("synthetic-only"),
                    shopee_affiliate_enabled=False, viral_review=False, videogen_provider="none",
                    auto_publish=False)


def storyboard(branch):
    return {"creation_branch": branch, "storyboard_version": 1,
            "title": "Bố và chiếc hộp bí mật", "hook": "Bố giấu gì thế?", "caption": "Một bất ngờ nhỏ",
            "style": "Photorealistic detail, playful family comedy", "cta": "",
            "characters": [{"id": "father", "name": "Bố", "description": "Warm father"}],
            "affiliate_keywords": ["hộp đựng đồ"],
            "scenes": [{"visual": "generate", "duration": 6, "gen_prompt": "Detailed original shot " * 50,
                        "shot": {"action": "Father reveals a toy", "camera": "medium shot"},
                        "reference_ids": []}]}


@pytest.mark.parametrize("branch", ["viral_creator", "template_series"])
def test_content_first_branch_reaches_generation_without_affiliate_product(settings, branch):
    job_id = workflow.create_trend_job(settings, "Bố giấu đồ chơi", creation_branch=branch)
    prompts = []

    def ask(client, configured, prompt, *args, **kwargs):
        prompts.append(prompt)
        if "JSON mẫu" in prompt:
            return storyboard(branch)
        return {"topic": "Bố giấu đồ chơi", "angle": "family surprise", "product_category": "",
                "product_search_keywords": [], "visual_summary": "Original family joke"}

    provider = Mock()
    provider.supports.return_value = True
    with patch.object(workflow, "_client", return_value=Mock()), \
            patch.object(workflow, "_ask_json", side_effect=ask), \
            patch.object(workflow, "get_provider", return_value=provider), \
            patch.object(workflow, "product_image", return_value=None), \
            patch.object(workflow.gen_engine, "remaining_today", return_value=10), \
            patch.object(workflow.gen_engine, "plan_job", return_value=1) as generate, \
            patch.object(workflow, "ShopeeAffiliateClient") as affiliate, \
            patch.object(workflow, "render_job") as render:
        result = workflow.process_job(settings, job_id)
    # Series without uploaded cast images no longer stops: Muse makes the cast photos first.
    assert result["status"] == workflow.gen_engine.WAIT_STATUS
    affiliate.assert_not_called()
    render.assert_not_called()
    assert generate.call_count == 1
    state = json.loads((settings.work_dir / job_id / "plan_state.json").read_text(encoding="utf-8"))
    plan = state["plan"]
    assert plan["creation_branch"] == branch
    assert plan["scenes"][0]["shot"]["camera"] == "medium shot"
    assert plan["affiliate_keywords"] == ["hộp đựng đồ"]
    assert any(branch in prompt for prompt in prompts)
    job = db.get_job(settings.database_path, job_id)
    assert not job["affiliate_link"]
    assert state["plan"]["creation_branch"] == branch
    assert "voice_cast" in state["plan"] and state["plan"]["style_profile"]


def test_product_review_retains_factual_provenance_and_separate_owner_notes(settings):
    context = {"identity": {"item_id": "123", "name": "Hộp đựng đồ"},
               "facts": {"price": 120000}, "provenance": {"provider": "addlive", "stale": True,
               "evidence_record_id": 42, "warning": "cached"}, "missing_info": ["real_usage_evidence"]}
    job_id = workflow.create_trend_job(settings, "Review hộp", offer={"itemId": "123",
        "productName": "Hộp đựng đồ", "affiliateLink": "https://s.shopee.vn/synthetic"},
        creation_branch="product_review", experience={"basis": "owner_experience", "notes": "Tôi dùng hai ngày"},
        product_context=context)
    job = db.get_job(settings.database_path, job_id)
    brief = json.loads(job["brief_json"])
    assert brief["product_context"]["provenance"]["evidence_record_id"] == 42
    assert brief["product_context"]["provenance"]["stale"] is True
    assert brief["experience"]["notes"] == "Tôi dùng hai ngày"
    assert "experience" not in brief["product_context"]["facts"]
    from agent_core.creative import branch_plan_prompt
    prompt = branch_plan_prompt(brief, {}, True, False)
    assert "Tôi dùng hai ngày" in prompt
    assert "không nói đã dùng" in prompt.lower()
    assert "real_usage_evidence" in prompt


def test_trend_job_without_branch_uses_the_creation_branches(settings):
    """One prompt system: a plain trend job is a viral_creator job, with a product a review."""
    job_id = workflow.create_trend_job(settings, "Legacy trend")
    brief = json.loads(db.get_job(settings.database_path, job_id)["brief_json"])
    assert brief["creation_branch"] == "viral_creator"
    assert db.get_job(settings.database_path, job_id)["mode"] == "trend"
    job_id = workflow.create_trend_job(settings, "Review", offer={"itemId": "1", "productName": "Hộp"})
    assert json.loads(db.get_job(settings.database_path, job_id)["brief_json"])["creation_branch"] == "product_review"


def test_unlinked_creator_caption_has_no_empty_affiliate_or_sales_claims():
    content = workflow.finalise_content({"caption": "Câu chuyện gia đình vui vẻ", "hashtags": ["#giadinh"],
                                         "creation_branch": "viral_creator"}, "")
    for field in ("shopee_caption", "facebook_post", "tiktok_caption"):
        assert "Câu chuyện gia đình vui vẻ" in content[field]
        assert "Link sản phẩm:" not in content[field]
        assert "tiếp thị liên kết" not in content[field]
        assert "Sản phẩm ở" not in content[field]
        assert "#affiliate" not in content[field]


def seed_catalog(settings):
    catalog.ensure_schema(settings.database_path)
    with db.connect(settings.database_path) as conn:
        for item_id, name in (("123", "Hộp đựng đồ chơi gia đình"), ("456", "Son môi đỏ")):
            conn.execute("INSERT INTO products (id,platform,item_id,name,affiliate_link,commission,"
                         "sold,imported_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         ("shopee:" + item_id, "shopee", item_id, name,
                          "https://s.shopee.vn/fake-" + item_id, 1000, 100, db.now_iso(), db.now_iso()))


def test_post_story_affiliate_match_follows_explicit_story_keywords_only(settings):
    seed_catalog(settings)
    checked = []

    def approve(_settings, items):   # the AI pair check sees exactly the keyword matches
        checked.extend(m["product_id"] for _, found in items for m in found)
        return {t["key"]: found for t, found in items}

    with patch("agent_core.trend_intel.matcher.verify_matches", side_effect=approve):
        matches = workflow._related_affiliates(settings, {
            "title": "Bố và bí mật nhỏ", "affiliate_keywords": ["hộp đựng đồ chơi"]}, {})
        unrelated_title_only = workflow._related_affiliates(settings, {"title": "Son môi đỏ"}, {})
    assert {row["product_id"] for row in matches} == {"shopee:123"}
    assert unrelated_title_only == []
    assert checked == ["shopee:123"]


def test_attach_affiliate_preserves_story_and_binary_without_queue_or_paid_calls(settings, tmp_path):
    seed_catalog(settings)
    job_id = workflow.create_trend_job(settings, "Bố giấu đồ chơi", creation_branch="viral_creator")
    plan = storyboard("viral_creator")
    with patch("agent_core.trend_intel.matcher.verify_matches",
               side_effect=lambda _s, items: {t["key"]: f for t, f in items}):
        matches = workflow._related_affiliates(settings, plan, {})
    job = db.get_job(settings.database_path, job_id)
    brief = json.loads(job["brief_json"])
    brief["affiliate_suggestions"] = matches
    binary = tmp_path / "final.mp4"
    binary.write_bytes(b"synthetic immutable output")
    db.update_job(settings.database_path, job_id, status="READY_FOR_REVIEW", script_json=json.dumps(plan),
                  output_path=str(binary), content_json=json.dumps({"caption": "Bố giấu đồ chơi"}),
                  brief_json=json.dumps(brief))
    workflow._save_plan_state(settings, job_id, {"plan": plan, "product": {}, "affiliate_link": ""})
    with patch.object(workflow, "enqueue_task") as queue, \
            patch.object(workflow, "process_job") as process, \
            patch.object(workflow, "render_job") as render, \
            patch.object(workflow, "_client") as ai:
        result = workflow.attach_affiliate(settings, job_id, "shopee:123")
    updated = db.get_job(settings.database_path, job_id)
    assert result["regenerated"] is False
    assert updated["script_json"] == json.dumps(plan)
    assert updated["output_path"] == str(binary)
    assert updated["status"] == "READY_FOR_REVIEW"
    assert binary.read_bytes() == b"synthetic immutable output"
    assert updated["affiliate_link"] == "https://s.shopee.vn/fake-123"
    assert "tiếp thị liên kết" in json.loads(updated["content_json"])["shopee_caption"]
    state = json.loads((settings.work_dir / job_id / "plan_state.json").read_text(encoding="utf-8"))
    assert state["plan"] == plan and state["product"] == {}
    assert state["attached_affiliate"]["catalog_id"] == "shopee:123"
    for mock in (queue, process, render, ai):
        mock.assert_not_called()


def test_attach_rejects_unrelated_catalog_choice_and_approved_job(settings):
    seed_catalog(settings)
    job_id = workflow.create_trend_job(settings, "Bố giấu đồ chơi", creation_branch="viral_creator")
    brief = json.loads(db.get_job(settings.database_path, job_id)["brief_json"])
    brief["affiliate_suggestions"] = [{"product_id": "shopee:123"}]
    db.update_job(settings.database_path, job_id, brief_json=json.dumps(brief), status="READY_FOR_REVIEW")
    with pytest.raises(workflow.WorkflowError, match="liên quan"):
        workflow.attach_affiliate(settings, job_id, "shopee:456")
    db.update_job(settings.database_path, job_id, status="APPROVED")
    with pytest.raises(workflow.WorkflowError, match="duyệt"):
        workflow.attach_affiliate(settings, job_id, "shopee:123")


def test_review_affiliate_can_only_attach_original_item(settings):
    seed_catalog(settings)
    job_id = workflow.create_trend_job(settings, "Review hộp", creation_branch="product_review",
                                       offer={"itemId": "123", "productName": "Hộp đựng đồ"})
    db.update_job(settings.database_path, job_id, status="NEEDS_MEDIA_REVIEW")
    with pytest.raises(workflow.WorkflowError, match="đúng sản phẩm"):
        workflow.attach_affiliate(settings, job_id, "shopee:456")
    assert workflow.attach_affiliate(settings, job_id, "shopee:123")["affiliate_attached"]


def test_missing_muse_still_saves_creator_storyboard_without_searching_product(settings):
    job_id = workflow.create_trend_job(settings, "Bố giấu đồ chơi", creation_branch="viral_creator")
    plan = storyboard("viral_creator")
    replies = [{"topic": "Bố giấu đồ chơi", "product_search_keywords": []}, plan]
    with patch.object(workflow, "_client", return_value=Mock()), \
            patch.object(workflow, "_ask_json", side_effect=replies), \
            patch.object(workflow, "product_image", return_value=None), \
            patch.object(workflow, "get_provider", return_value=None), \
            patch.object(workflow, "ShopeeAffiliateClient") as affiliate:
        result = workflow.process_job(settings, job_id)
    assert result["status"] == "NEEDS_MEDIA_REVIEW"
    assert "Muse" in result["error"]
    saved = json.loads((settings.work_dir / job_id / "plan_state.json").read_text(encoding="utf-8"))
    assert saved["plan"]["creation_branch"] == "viral_creator"
    assert saved["plan"]["scenes"][0]["shot"]["action"] == "Father reveals a toy"
    affiliate.assert_not_called()


@pytest.mark.parametrize("status", ["SCRIPTING", "RENDERING", "WAITING_FOR_GENERATION", "DISCOVERED"])
def test_attach_affiliate_rejects_active_job_without_any_update(settings, status):
    seed_catalog(settings)
    job_id = workflow.create_trend_job(settings, "Bố giấu đồ chơi", creation_branch="viral_creator")
    brief = json.loads(db.get_job(settings.database_path, job_id)["brief_json"])
    brief["affiliate_suggestions"] = [{"product_id": "shopee:123"}]
    db.update_job(settings.database_path, job_id, brief_json=json.dumps(brief), status=status)
    before = db.get_job(settings.database_path, job_id)
    with patch.object(workflow, "update_job") as update, \
            patch.object(workflow, "_save_plan_state") as save:
        with pytest.raises(workflow.WorkflowError):
            workflow.attach_affiliate(settings, job_id, "shopee:123")
    update.assert_not_called()
    save.assert_not_called()
    assert db.get_job(settings.database_path, job_id) == before


def test_missing_saved_clip_does_not_shift_next_clip_or_render(settings):
    job_id = workflow.create_trend_job(settings, "Original story", creation_branch="viral_creator")
    plan = storyboard("viral_creator")
    plan["scenes"] = [{"visual": "user_clip", "clip": 0, "duration": 5}]
    workflow._save_plan_state(settings, job_id, {"plan": plan, "product": {}, "analysis": {},
                                               "affiliate_link": "", "clip_ids": ["clip_a", "clip_b"]})
    db.update_job(settings.database_path, job_id, status=workflow.gen_engine.WAIT_STATUS)
    with patch.object(workflow, "list_clips", return_value=[{"id": "clip_b", "file": "b.mp4"}]), \
            patch.object(workflow.gen_engine, "results_for_job", return_value={}), \
            patch.object(workflow, "_finish_timeline") as finish, \
            patch.object(workflow, "render_job") as render:
        result = workflow.resume_after_generation(settings, job_id)
    assert result["status"] == "NEEDS_MEDIA_REVIEW"
    finish.assert_not_called()
    render.assert_not_called()
    saved = json.loads((settings.work_dir / job_id / "plan_state.json").read_text(encoding="utf-8"))
    assert saved["clip_ids"] == ["clip_a", "clip_b"]
    assert saved["plan"]["scenes"][0]["clip"] == 0


def test_rewritten_unrelated_story_clears_previous_affiliate_before_generation(settings):
    seed_catalog(settings)
    job_id = workflow.create_trend_job(settings, "Bố giấu đồ chơi", creation_branch="viral_creator")
    brief = json.loads(db.get_job(settings.database_path, job_id)["brief_json"])
    brief["attached_affiliate"] = {"catalog_id": "shopee:123", "item_id": "123"}
    brief["affiliate_suggestions"] = [{"product_id": "shopee:123"}]
    db.update_job(settings.database_path, job_id, status="ANALYZED", product_item_id="123",
                  product_name="Hộp đựng đồ", affiliate_link="https://s.shopee.vn/fake-123",
                  brief_json=json.dumps(brief), analysis_json=json.dumps({"topic": "New unrelated story"}))
    plan = storyboard("viral_creator")
    plan["affiliate_keywords"] = []
    with patch.object(workflow, "_client", return_value=Mock()), \
            patch.object(workflow, "_ask_json", return_value=plan), \
            patch.object(workflow, "product_image", return_value=None), \
            patch.object(workflow, "get_provider", return_value=None), \
            patch.object(workflow.gen_engine, "plan_job") as dispatch:
        result = workflow.process_job(settings, job_id)
    assert result["status"] == "NEEDS_MEDIA_REVIEW"
    dispatch.assert_not_called()
    job = db.get_job(settings.database_path, job_id)
    assert not job["affiliate_link"] and job["product_item_id"] is None and job["product_name"] is None
    updated_brief = json.loads(job["brief_json"])
    assert "attached_affiliate" not in updated_brief
    assert updated_brief["affiliate_suggestions"] == []
    saved = json.loads((settings.work_dir / job_id / "plan_state.json").read_text(encoding="utf-8"))
    assert saved["affiliate_link"] == "" and saved["product"] == {}


def test_zero_remaining_budget_still_consults_engine_for_retained_tasks(settings):
    job_id = workflow.create_trend_job(settings, "Original story", creation_branch="viral_creator")
    plan = storyboard("viral_creator")
    provider = Mock()
    provider.supports.return_value = True
    with patch.object(workflow, "_client", return_value=Mock()), \
            patch.object(workflow, "_ask_json", side_effect=[{"topic": "Original story"}, plan]), \
            patch.object(workflow, "product_image", return_value=None), \
            patch.object(workflow, "get_provider", return_value=provider), \
            patch.object(workflow.gen_engine, "remaining_today", return_value=0), \
            patch.object(workflow.gen_engine, "plan_job", return_value=1) as planner, \
            patch.object(workflow, "render_job") as render:
        result = workflow.process_job(settings, job_id)
    assert result["status"] == workflow.gen_engine.WAIT_STATUS
    planner.assert_called_once()
    # free-text gen_prompt is only a short fallback; the Muse prompt is compiled from shot fields
    assert plan["scenes"][0]["gen_prompt"].strip().startswith(planner.call_args.args[2]["scenes"][0]["gen_prompt"].rstrip("…").split("…")[0][:50])
    render.assert_not_called()


def test_attached_tiktok_link_survives_generation_resume_without_changing_media_product(settings):
    seed_catalog(settings)
    catalog.update_product(settings.database_path, "shopee:123", tiktok_link="https://www.tiktok.com/shop/fake")
    job_id = workflow.create_trend_job(settings, "Original story", creation_branch="viral_creator")
    plan = storyboard("viral_creator")
    brief = json.loads(db.get_job(settings.database_path, job_id)["brief_json"])
    brief["affiliate_suggestions"] = [{"product_id": "shopee:123"}]
    db.update_job(settings.database_path, job_id, brief_json=json.dumps(brief), status="NEEDS_MEDIA_REVIEW")
    workflow._save_plan_state(settings, job_id, {"plan": plan, "product": {}, "analysis": {},
                                               "affiliate_link": "", "clip_ids": []})
    workflow.attach_affiliate(settings, job_id, "shopee:123")
    with patch.object(workflow.gen_engine, "results_for_job", return_value={}), \
            patch.object(workflow.gen_engine, "results_for_storyboard", return_value={}), \
            patch.object(workflow, "list_clips", return_value=[]), \
            patch.object(workflow, "product_image", return_value=None), \
            patch.object(workflow, "build_timeline", return_value={"scenes": [{"source": "fake-generated.mp4"}]}), \
            patch.object(workflow, "render_job", return_value={"status": "READY_FOR_REVIEW"}) as render:
        result = workflow.resume_after_generation(settings, job_id)
    assert result["status"] == "READY_FOR_REVIEW"
    render.assert_called_once()
    assert render.call_args.kwargs["product"] == {}
    content = json.loads(db.get_job(settings.database_path, job_id)["content_json"])
    assert content["tiktok_link"] == "https://www.tiktok.com/shop/fake"
    assert "giỏ hàng (TikTok Shop)" in content["tiktok_caption"]
    assert "Sản phẩm ở bio" not in content["tiktok_caption"]
