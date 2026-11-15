"""Dialogue series: the characters talk to each other, each with their own VieNeu voice.

Muse's own speech is never kept: in a real Suri episode (Nov 2026) none of the 8 takes said the
scripted line (it improvised, once even a channel outro), so every voice is dubbed with VieNeu.
"""
import pytest

from agent_core import creative, creative_series, muse_prompt
from agent_core.creation_branches import is_dialogue, normalise_brief
from agent_core.settings import Settings

NICHE = {"id": "plant-facts", "version": 1, "title": "Cây kể chuyện", "style": "documentary",
         "characters": [{"id": "host", "name": "Lan", "gender": "female", "age": "28"}]}


def test_dialogue_mode_per_branch_and_template():
    assert is_dialogue(normalise_brief("template_series"))                         # Suri family talks
    assert not is_dialogue(normalise_brief("template_series", template=NICHE))     # niche series narrate
    assert is_dialogue(normalise_brief("template_series", template={**NICHE, "voice_mode": "dialogue"}))
    assert not is_dialogue(normalise_brief("product_review"))
    assert not is_dialogue(normalise_brief("viral_creator"))
    old = {k: v for k, v in creative_series._BUILTIN.items() if k != "voice_mode"}   # saved before modes
    assert is_dialogue({"creation_branch": "template_series", "template": old})


def test_saved_templates_carry_a_voice_mode(tmp_path):
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                 media_root=tmp_path / "media", muse_embedded=False)
    assert creative_series.get_template(s, "suri-family")["voice_mode"] == "dialogue"
    saved = creative_series.save_template(s, {
        "id": "plant-facts", "label": "Cây", "style": "tài liệu", "setting": "vườn",
        "characters": [{"id": "host", "name": "Lan", "description": "người dẫn", "age": 28}]})
    assert saved["voice_mode"] == "narration"
    with pytest.raises(ValueError):
        creative_series.save_template(s, {**saved, "voice_mode": "sing"})


def test_dialogue_prompt_only_for_dialogue_series():
    talk = creative.branch_plan_prompt(normalise_brief("template_series"), {}, True, False, gen_limit=6)
    assert "SERIES HỘI THOẠI" in talk and 'KHÔNG dùng speaker "narrator"' in talk
    assert f"NHẮM KHOẢNG {creative.target_words(20, 35)[1]} TỪ" in talk   # VieNeu speed for every voice
    narr = creative.branch_plan_prompt(normalise_brief("template_series", template=NICHE), {}, True, False)
    review = creative.branch_plan_prompt(normalise_brief("product_review"), {}, True, False)
    assert "SERIES HỘI THOẠI" not in narr and "SERIES HỘI THOẠI" not in review


def _dialogue_plan(n_talking=4, dialogue=True):
    scenes = [{"visual": "generate", "duration": 5, "cast": ["suri", "mother"], "talking": True,
               "lines": [{"speaker": "suri", "text": f"Mẹ ơi con gấp được rồi nè {i}"},
                         {"speaker": "mother", "text": "Giỏi quá, đâu mẹ xem nào"}]} for i in range(n_talking)]
    return {"creation_branch": "template_series", "storyboard_version": 2, "dialogue": dialogue,
            "characters": [{"id": "suri", "name": "Suri", "age": "4", "gender": "female"},
                           {"id": "mother", "name": "Mẹ Suri", "age": "30"}], "scenes": scenes}


def test_dialogue_plans_keep_every_talking_scene_and_muse_only_mouths_the_line():
    plan = creative.normalise_plan(_dialogue_plan(4), "trend")
    assert plan["dialogue"] and sum(s["talking"] for s in plan["scenes"]) == 4
    normal = creative.normalise_plan(_dialogue_plan(4, dialogue=False), "trend")
    assert sum(s["talking"] for s in normal["scenes"]) == 2               # voice-over branches: max 2
    prompt = muse_prompt.compile_shot(plan["scenes"][0], plan)
    assert "talk to each other" in prompt and "say only this line once" in prompt
    assert len(prompt) <= muse_prompt.MAX_CHARS
    assert "says to camera" in muse_prompt.compile_shot(normal["scenes"][0], normal)


def test_dialogue_flag_travels_from_brief_to_plan():
    from agent_core import workflow
    assert workflow._brief_plan_fields(normalise_brief("template_series"))["dialogue"] is True
    assert "dialogue" not in workflow._brief_plan_fields(normalise_brief("viral_creator"))
