import pytest

from agent_core import creative
from agent_core.creation_branches import BRANCHES, SURI_FAMILY, normalise_brief, validate_branch


@pytest.mark.parametrize("branch", BRANCHES)
def test_explicit_branch_round_trip(branch):
    brief = normalise_brief(branch, product_context={"price": 123, "warning": "cached"})
    assert validate_branch(branch) == branch
    assert brief["creative_brief_version"] == 1
    assert brief["product_context"]["warning"] == "cached"
    assert brief["experience"] == {"basis": "product_facts", "notes": ""}


@pytest.mark.parametrize("value", [None, "", "trend", [], {}, True])
def test_invalid_branch_rejected(value):
    with pytest.raises(ValueError):
        validate_branch(value)


def test_suri_default_is_versioned_three_person_family_and_independent_snapshot():
    brief = normalise_brief("template_series")
    template = brief["template"]
    assert template["id"] == "suri-family"
    assert template["version"] == 1
    assert [c["id"] for c in template["characters"]] == ["father", "mother", "suri"]
    assert template["characters"][2]["age"] == "4"
    # phone/vlog look instead of "photorealistic" (phase 1: UGC realism)
    assert "family vlog" in template["style"] and "Photorealistic" not in template["style"]
    assert [c["gender"] for c in template["characters"]] == ["male", "female", "female"]
    template["characters"][2]["name"] = "Changed"
    assert SURI_FAMILY["characters"][2]["name"] == "Suri"


def test_owner_experience_remains_separate_from_product_facts():
    brief = normalise_brief("product_review", experience={"basis": "owner_experience", "notes": " Tôi thử 2 ngày "},
                            product_context={"name": "Máy xay", "rating": 4.5})
    assert brief["experience"]["notes"] == "Tôi thử 2 ngày"
    assert "experience" not in brief["product_context"]
    with pytest.raises(ValueError):
        normalise_brief("product_review", experience={"basis": "model_imagined"})


def test_template_and_context_bounds_and_no_path_references():
    brief = normalise_brief("template_series", template={"id": "../escape", "version": 2,
        "style": "s" * 2000, "characters": [{"id": "suri", "reference_id": "C:/secret.png",
                                               "appearance": "a" * 2000}]},
        product_context={"many": list(range(50)), "bad": float("inf")})
    assert brief["template"]["id"] == "custom-series"
    assert len(brief["template"]["style"]) == 1500
    assert "reference_id" not in brief["template"]["characters"][0]
    assert len(brief["template"]["characters"][0]["appearance"]) == 800
    assert len(brief["product_context"]["many"]) == 30
    assert brief["product_context"]["bad"] is None


def test_rich_storyboard_survives_normalization_but_paths_do_not():
    plan = {"creation_branch": "template_series", "storyboard_version": 1, "style": "photo detail, comic timing",
        "characters": [{"id": "father", "name": "Bố", "reference_id": "father"}],
        "reference_manifest": [{"path": "C:/secret"}],
        "scenes": [{"visual": "generate", "duration": 8, "gen_prompt": "x" * 3500,
                    "reference_ids": ["father", "suri"],
                    "shot": {"action": "spills harmlessly", "camera": "medium shot", "sound": "comic beat",
                             "dialogue": "Bố ơi!", "arbitrary_path": "C:/secret"}}]}
    result = creative.normalise_plan(plan, "trend")
    assert result["creation_branch"] == "template_series"
    assert result["storyboard_version"] == 1
    # the Muse prompt is compiled from shot fields; free text is only a short fallback
    assert len(result["scenes"][0]["gen_prompt"]) <= 600
    assert result["scenes"][0]["reference_ids"] == ["father", "suri"]
    assert result["scenes"][0]["shot"]["sound"] == "comic beat"
    assert "arbitrary_path" not in result["scenes"][0]["shot"]
    assert "reference_manifest" not in result


def test_legacy_plan_keeps_existing_shape_and_defaults():
    result = creative.normalise_plan({"scenes": [{"segment": 0, "duration": 3}]}, "base",
                                     [{"index": 0, "start": 1, "duration": 4}])
    assert result["scenes"][0]["start"] == 1
    assert result["character"] == ""
    assert "creation_branch" not in result
    assert "storyboard_version" not in result
    assert "reference_manifest" not in result


@pytest.mark.parametrize("branch", BRANCHES)
def test_prompts_keep_branch_honesty_budget_and_storyboard_contract(branch):
    brief = normalise_brief(branch)
    idea = creative.branch_ideation_prompt(brief)
    plan = creative.branch_plan_prompt(brief, {}, True, False, gen_limit=2)
    review = creative.branch_review_prompt({}, {}, 7, brief)
    assert branch in idea and branch in plan and branch in review
    assert "Tối đa 2 cảnh generate" in plan
    assert "2-6 giây" in plan
    assert "reference_ids" in plan and '"group"' in plan and '"lines"' in plan and '"talking"' in plan
    assert "không nói đã dùng" in plan
    assert "không có CTA bán hàng" in review
    if branch != "product_review":
        assert "không ép" in idea.lower()


def test_disabled_generation_prompt_never_offers_positive_budget():
    prompt = creative.branch_plan_prompt(normalise_brief("viral_creator"), {}, False, True, gen_limit=3)
    assert "Tối đa 0 cảnh generate" in prompt


def test_template_snapshot_schema_and_attachment_paths_stay_out_of_prompts():
    brief = normalise_brief("template_series", template={"id": "family", "version": 3,
        "label": "Family", "setting": "kitchen", "continuity_rules": ["same clothes"],
        "characters": [{"id": "suri", "description": "four-year-old girl"}]})
    assert brief["template"]["setting"] == "kitchen"
    assert brief["template"]["continuity_rules"] == ["same clothes"]
    assert brief["template"]["characters"][0]["description"] == "four-year-old girl"
    brief["reference_assets"] = [{"path": "C:/private/family.png"}]
    plan = {"reference_manifest": [{"path": "C:/private/family.png"}]}
    for prompt in (creative.branch_ideation_prompt(brief),
                   creative.branch_plan_prompt(brief, {}, True, False),
                   creative.branch_review_prompt(plan, {}, 7, brief)):
        assert "C:/private" not in prompt


def test_new_branch_generated_duration_minimum_and_legacy_duration():
    scene = {"visual": "generate", "duration": 2}
    branch = creative.normalise_plan({"creation_branch": "viral_creator", "scenes": [scene]}, "trend")
    legacy = creative.normalise_plan({"scenes": [scene]}, "trend")
    assert branch["scenes"][0]["duration"] == 2   # AI clips are cut to 2-6 s
    assert legacy["scenes"][0]["duration"] == 2


def test_affiliate_keywords_bounded_and_legacy_absence_preserved():
    plan = {"scenes": [{"visual": "generate"}],
            "affiliate_keywords": ["  đồ chơi  ", "đồ chơi", "", "x" * 200] + ["other"] * 10}
    result = creative.normalise_plan(plan, "trend")
    assert result["affiliate_keywords"][:2] == ["đồ chơi", "x" * 100]
    assert len(result["affiliate_keywords"]) <= 8
    assert "affiliate_keywords" not in creative.normalise_plan({"scenes": [{"visual": "generate"}]}, "trend")


def test_new_branch_rejects_excess_scene_coverage_legacy_remains_compatible():
    scenes = [{"visual": "generate"}] * 11
    with pytest.raises(ValueError, match="10 scenes"):
        creative.normalise_plan({"creation_branch": "viral_creator", "scenes": scenes}, "trend")
    assert len(creative.normalise_plan({"scenes": scenes}, "trend")["scenes"]) == 10


def test_explicit_storyboard_rejects_excess_cast_or_reference_instead_of_dropping():
    for extras in ({"characters": [{"id": "c"}] * 7},
                   {"scenes": [{"visual": "generate", "reference_ids": ["ref"] * 7}]}):
        plan = {"creation_branch": "template_series", "scenes": [{"visual": "generate"}], **extras}
        with pytest.raises(ValueError, match="6"):
            creative.normalise_plan(plan, "trend")
    with pytest.raises(ValueError, match="6 nhân vật"):
        normalise_brief("template_series", template={"characters": [{"id": "c"}] * 7})


@pytest.mark.parametrize("scenes", [None, {}, ["invalid"], [{"visual": "unknown"}],
    [{"visual": "generate", "reference_ids": "father"}],
    [{"visual": "generate", "reference_ids": ["../private"]}],
    [{"visual": "generate", "reference_ids": [123]}],
    [{"visual": "generate", "reference_ids": ["father", "father"]}],
    [{"visual": "generate", "shot": "not an object"}]])
def test_explicit_branch_rejects_malformed_scenes_or_refs_without_dropping(scenes):
    with pytest.raises(ValueError):
        creative.normalise_plan({"creation_branch": "viral_creator", "scenes": scenes}, "trend")


def test_legacy_tolerant_scene_and_reference_cleanup_remains():
    plan = creative.normalise_plan({"scenes": ["invalid", {"visual": "unknown",
        "reference_ids": ["father", "../private", "father"]}]}, "trend")
    assert len(plan["scenes"]) == 1
    assert plan["scenes"][0]["visual"] == "product_image"
    assert plan["scenes"][0]["reference_ids"] == ["father"]


def test_custom_series_snapshot_is_authoritative_not_forced_suri_cast():
    brief = normalise_brief("template_series", template={"id": "grandma-kitchen", "version": 2,
        "characters": [{"id": "grandma", "name": "Bà", "age": 70}], "style": "stop motion"})
    for prompt in (creative.branch_ideation_prompt(brief), creative.branch_plan_prompt(brief, {}, True, False),
                   creative.branch_review_prompt({}, {}, 7, brief)):
        assert "template snapshot là nguồn quyết định cast" in prompt
        assert "Chỉ khi chọn preset suri-family" in prompt
        assert "grandma-kitchen" in prompt


@pytest.mark.parametrize("version", [True, 3, "1"])
def test_unsupported_storyboard_version_rejected(version):
    with pytest.raises(ValueError):
        creative.normalise_plan({"storyboard_version": version,
                                 "scenes": [{"visual": "generate"}]}, "trend")
