"""Each branch's storyboard prompt carries only its own rules (no "hot pot")."""
import json

import pytest

from agent_core import creative
from agent_core.creation_branches import normalise_brief
from agent_core.prompts import branch_issues, multi_voice

REVIEW_ONLY = ("xưng \"mình\"", "product_truth", "missing_info", "giỏ hàng", "REVIEW SẢN PHẨM")
SERIES_ONLY = ("SERIES HỘI THOẠI", "SERIES THUYẾT MINH", "SERIES THEO MẪU", "TẬP ")
VIRAL_ONLY = ("VIDEO VIRAL", "formula")


def _plan(branch, **kw):
    return creative.branch_plan_prompt(normalise_brief(branch, **kw), {}, True, False, gen_limit=4)


def test_review_prompt_has_no_series_or_viral_rules():
    p = _plan("product_review")
    assert all(k not in p for k in SERIES_ONLY + VIRAL_ONLY)
    assert "Suri" not in p and "dialogue" not in p.lower().replace('"dialogue"', "")
    assert all(k in p for k in ("product_truth", "REVIEW SẢN PHẨM", "giỏ hàng"))


def test_viral_prompt_has_no_review_or_series_rules():
    p = _plan("viral_creator")
    assert all(k not in p for k in REVIEW_ONLY + SERIES_ONLY)
    assert "VIDEO VIRAL" in p and '"cta" ĐỂ RỖNG' in p


def test_dialogue_series_prompt_has_no_talking_cap_or_review_rules():
    p = _plan("template_series")
    assert all(k not in p for k in REVIEW_ONLY + VIRAL_ONLY)
    assert "SERIES HỘI THOẠI" in p and "Không giới hạn số cảnh talking" in p
    assert "tối đa 2 cảnh talking" not in p.lower()
    assert "reuse_of" in p and 'Không dùng "reuse_of"' in p          # no reuse for dialogue takes
    fix = creative.branch_review_prompt({}, {}, 7, normalise_brief("template_series"))
    assert "KHÔNG giới hạn số cảnh talking" in fix and "tối đa 2 cảnh talking" not in fix


def test_custom_narration_series_uses_template_not_suri():
    tpl = {"id": "plants", "version": 1, "style": "documentary macro", "setting": "Vườn Đà Lạt",
           "script_notes": "Kiến thức cây cảnh nhẹ nhàng", "voice_mode": "narration",
           "characters": [{"id": "host", "name": "An", "age": 30}]}
    p = _plan("template_series", template=tpl)
    assert "SERIES THUYẾT MINH" in p and "SERIES HỘI THOẠI" not in p
    assert "Vườn Đà Lạt" in p and "Kiến thức cây cảnh" in p
    assert "Suri" not in p and "hài gia đình" not in p and "photorealistic" not in p.replace('"photorealistic"', "")


@pytest.mark.parametrize("branch", ["product_review", "viral_creator", "template_series"])
def test_every_branch_keeps_the_schema_contract(branch):
    p = _plan(branch)
    schema = json.loads(p[p.index("{\"storyboard_version\""):p.rindex("}") + 1])
    assert schema["creation_branch"] == branch
    for key in ("scenes", "characters", "script", "takeaway", "hook", "hook_variants"):
        assert key in schema
    assert {"mission", "lines", "cast", "group", "shot", "talking"} <= set(schema["scenes"][0])


def test_branch_issues_apply_only_to_their_branch():
    review = {"creation_branch": "product_review", "cta": "", "scenes": [{}, {}, {}],
              "product_truth": {"features": [{"feature": "a", "scene": 1}]}}
    msgs = branch_issues(review)
    assert any("product_truth" in m for m in msgs) and any("CTA" in m for m in msgs)
    viral = {"creation_branch": "viral_creator", "cta": "Mua ngay link giỏ hàng", "scenes": []}
    assert any("CTA bán hàng" in m for m in branch_issues(viral))
    series = {"creation_branch": "template_series", "dialogue": True,
              "scenes": [{"talking": False, "lines": [{"speaker": "narrator", "text": "Hôm ấy trời mưa"}]}]}
    msgs = branch_issues(series)
    assert any("không có người dẫn" in m for m in msgs) and any("talking=true" in m for m in msgs)
    assert branch_issues({**series, "creation_branch": "viral_creator", "cta": ""}) == []


def test_one_definition_of_multi_voice():
    assert multi_voice(normalise_brief("template_series"))                       # Suri dialogue
    assert not multi_voice(normalise_brief("template_series", template={"id": "x", "voice_mode": "narration"}))
    assert not multi_voice(normalise_brief("viral_creator"))
    assert multi_voice({**normalise_brief("viral_creator"), "format": "story"})
    assert not multi_voice(normalise_brief("product_review"))
