"""Phase 1: one prompt system, compiled Muse prompts, voice cast, single text layer, real QA."""
import json

from agent_core import creative, muse_prompt, voice_cast
from agent_core.creation_branches import SURI_FAMILY, normalise_brief
from agent_core.editor.subtitles import build_ass
from agent_core.editor.tts import Word
from agent_core.qa_video import score_video, subtitle_overlaps

PLAN = {
    "creation_branch": "viral_creator", "storyboard_version": 2, "style_profile": "ugc_phone",
    "music_mood": "emotional",
    "characters": [
        {"id": "mom", "name": "Mẹ", "role": "mother", "gender": "female", "age": "45",
         "appearance": "short black hair, round face", "wardrobe": "sage-green blouse"},
        {"id": "son", "name": "Minh", "role": "son", "gender": "male", "age": "20",
         "appearance": "tall, short hair", "wardrobe": "navy t-shirt"},
        {"id": "suri", "name": "Suri", "role": "daughter", "gender": "female", "age": "4"},
    ],
    "scenes": [
        {"visual": "generate", "duration": 4, "group": "kitchen", "cast": ["son"], "talking": True,
         "lines": [{"speaker": "son", "text": "Mẹ ơi, hôm nay con nấu nhé"}],
         "shot": {"action": "Son lifts a pot lid and grins at the camera", "setting": "small kitchen"}},
        {"visual": "generate", "duration": 4, "group": "kitchen", "cast": ["mom", "son"],
         "lines": [{"speaker": "narrator", "text": "Mẹ đứng nhìn, không nói gì"},
                   {"speaker": "suri", "text": "Con cũng muốn nấu"}],
         "shot": {"action": "Mother watches from the doorway, smiling", "camera": "over the shoulder"}},
    ],
}


def _plan(**extra):
    raw = json.loads(json.dumps(PLAN))
    raw.update(extra)
    return creative.normalise_plan(raw, "trend", None, 0)


def test_storyboard_v2_fields_are_normalised():
    plan = _plan()
    s0, s1 = plan["scenes"]
    assert plan["storyboard_version"] == 2 and plan["style_profile"] == "ugc_phone"
    assert s0["talking"] and s0["cast"] == ["son"] and s0["group"] == "kitchen"
    assert s1["lines"][1] == {"speaker": "suri", "text": "Con cũng muốn nấu"}
    assert s1["voiceover"].startswith("Mẹ đứng nhìn")
    assert s0["text"] == ""  # storyboards keep a single text layer


def test_talking_shots_are_capped_at_two():
    raw = json.loads(json.dumps(PLAN))
    raw["scenes"] = [dict(raw["scenes"][0]) for _ in range(4)]
    plan = creative.normalise_plan(raw, "trend", None, 0)
    assert sum(s["talking"] for s in plan["scenes"]) == 2


def test_compiled_muse_prompt_is_short_and_phone_style():
    plan = _plan()
    for scene in plan["scenes"]:
        text = muse_prompt.compile_shot(scene, plan, [])
        assert len(text) <= muse_prompt.MAX_CHARS
        low = text.lower()
        assert "smartphone" in low and "cinematic look" in low  # only in the avoid list
        assert "photorealistic" not in low and "manifest digest" not in low
    talk = muse_prompt.compile_shot(plan["scenes"][0], plan, [])
    assert 'Minh says to camera: "Mẹ ơi, hôm nay con nấu nhé"' in talk
    quiet = muse_prompt.compile_shot(plan["scenes"][1], plan, [])
    assert "Nobody speaks" in quiet and "sage-green blouse" in quiet


def test_engine_prompt_hides_internal_identity():
    from agent_core.videogen import engine
    plan = _plan()
    stored = engine.storyboard_prompt(plan["scenes"][0], plan, [])
    assert muse_prompt.INTERNAL + "refs:" in stored
    assert muse_prompt.INTERNAL not in muse_prompt.strip_internal(stored)


def test_voice_cast_rules():
    plan = _plan()
    cast = voice_cast.cast_voices(plan, "Mai Anh")
    assert set(cast) == {"son", "narrator", "suri"}
    assert cast["son"]["voice"] in voice_cast.MALE
    assert cast["suri"]["pitch"] > 1.1                    # child voice lifted
    assert not voice_cast.problems(plan, cast)
    assert len({v["voice"] for v in cast.values()}) == 3  # nobody shares a voice


def test_review_narrator_follows_product_audience():
    for audience, pool in (("female", voice_cast.FEMALE), ("male", voice_cast.MALE)):
        plan = {"creation_branch": "product_review", "audience_gender": audience,
                "scenes": [{"lines": [{"speaker": "narrator", "text": "x"}]}]}
        assert voice_cast.cast_voices(plan)["narrator"]["voice"] in pool


def test_series_voice_is_pinned():
    plan = _plan(creation_branch="template_series")
    cast = voice_cast.cast_voices(plan, pinned={"son": "Hữu Quân"})
    assert cast["son"]["voice"] == "Hữu Quân"


def test_suri_template_has_genders():
    brief = normalise_brief("template_series", template=SURI_FAMILY)
    genders = {c["id"]: c.get("gender") for c in brief["template"]["characters"]}
    assert genders == {"father": "male", "mother": "female", "suri": "female"}


def test_branch_prompt_is_v2_and_has_no_hook_examples():
    brief = normalise_brief("viral_creator")
    prompt = creative.branch_plan_prompt(brief, {}, True, False, recent_hooks=["Ai thật sự hiểu mẹ?"])
    assert '"storyboard_version": 2' in prompt and '"lines"' in prompt and '"talking"' in prompt
    assert "Ủa! Nó đang" not in prompt and "Ai thật sự hiểu mẹ?" in prompt
    assert "15-30 giây" in prompt


def test_single_layer_subtitles_do_not_overlap(tmp_path):
    captions = [(0.0, [Word("Mẹ", 0.2, 0.6), Word("ơi", 0.6, 1.0), Word("con", 1.0, 1.4),
                       Word("về", 1.4, 1.8), Word("rồi.", 1.8, 2.6)]),
                (2.4, [Word("Cả", 0.0, 0.4), Word("nhà", 0.4, 0.9)])]
    path = build_ass(tmp_path / "s.ass", 1080, 1920, "Arial", captions, "AI HIỂU MẸ NHẤT 🏆", 1.5,
                     [(0, 3, "NHÃN")], "", 10, 4.0, single_layer=True)
    text = path.read_text(encoding="utf-8-sig")
    assert "🏆" not in text and "AI HIỂU" not in text and "Ai hiểu mẹ nhất" in text
    assert "Label" not in text.split("[Events]")[1] and r"\kf" not in text
    assert subtitle_overlaps(path) == 0


def test_real_qa_has_no_base_points(tmp_path):
    ass = tmp_path / "s.ass"
    ass.write_text("[Events]\nDialogue: 1,0:00:00.00,0:00:02.00,Sub,,0,0,0,,a\n"
                   "Dialogue: 1,0:00:01.50,0:00:03.00,Sub,,0,0,0,,b\n", encoding="utf-8-sig")
    timeline = {"creation_branch": "product_review"}
    scenes = [{"rendered_duration": 3, "voice_end": 4.0}]
    bad = score_video(timeline, scenes, 5.3, False, {"product_match": 2, "artifacts": 3}, 4.0, ass)
    assert bad["status"] == "FAIL" and {"duration", "subtitle_overlap", "wrong_product"} <= set(bad["gates"])
    assert bad["total_score"] < 50
    good_ass = tmp_path / "g.ass"
    good_ass.write_text("[Events]\nDialogue: 1,0:00:00.00,0:00:02.00,Sub,,0,0,0,,a\n", encoding="utf-8-sig")
    visual = {k: 9 for k in ("face_consistency", "wardrobe_consistency", "product_match", "artifacts",
                             "subtitle_readable", "phone_realism", "hook_stop_power")}
    good = score_video(timeline, [{"rendered_duration": 4, "voice_end": 3.5}], 28, True, visual, 9.0, good_ass)
    assert good["status"] == "PRIORITY_PUBLISH" and good["total_score"] >= 85
    unchecked = score_video(timeline, [{"rendered_duration": 4, "voice_end": 3.5}], 28, True, None, 9.5, good_ass)
    assert unchecked["status"] != "PRIORITY_PUBLISH" and not unchecked["visual_checked"]


def test_speech_windows_from_silence(tmp_path):
    import subprocess
    from agent_core.editor.voice_track import speech_windows
    clip = tmp_path / "c.wav"
    # 1 s silence, 1.5 s tone, 1 s silence
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,1,2.5),0.5*sin(2*PI*300*t),0)':d=3.5:s=44100",
                    str(clip)], check=True)
    windows = speech_windows("ffmpeg", clip, 0, 3.5)
    assert len(windows) == 1 and abs(windows[0][0] - 1.0) < 0.15 and abs(windows[0][1] - 2.5) < 0.15


def test_fit_lines_follows_the_mouth():
    from agent_core.editor.voice_track import fit_lines
    assert fit_lines([3.88], [(3.85, 4.8), (5.1, 5.65), (6.55, 9.0)]) == {0: (3.85, 1.0)}
    assert fit_lines([3.49], [(2.75, 4.75), (5.05, 6.65)])[0][0] == 2.75
    two = fit_lines([1.5, 2.0], [(0.5, 2.0), (2.5, 4.4)])
    assert two[0][0] == 0.5 and two[1][0] == 2.5
    assert fit_lines([5.0], [(0, 1.0)]) == {}       # far too long for the mouth: not forced


def test_report_words_are_detected():
    plan = {"hook": "Cây lau nhà 99k", "scenes": [{"lines": [
        {"speaker": "me", "text": "Giá hiển thị trong dữ liệu là 99.000 đồng"},
        {"speaker": "me", "text": "Chưa tới 100 nghìn thôi nè"}]}]}
    assert creative.meta_word_lines(plan) == ["Giá hiển thị trong dữ liệu là 99.000 đồng"]


def test_music_pages_by_mood_skip_seasonal_and_recent(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from agent_core.editor.music import SEASONAL, generate_music_brief, mood_pages, recent_track_ids
    sad = {"music_mood": "emotional", "title": "mẹ", "scenes": []}
    fun = {"music_mood": "funny", "title": "chó con", "scenes": []}
    assert mood_pages(generate_music_brief(sad), sad)[0] == "mood/sad"
    assert mood_pages(generate_music_brief(fun), fun)[0] == "mood/funny"
    assert SEASONAL.search("A Very Happy Christmas") and not SEASONAL.search("Piano Reflections")
    db = tmp_path / "d.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE jobs (id TEXT, script_json TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO jobs VALUES ('a', ?, '2')",
                     (json.dumps({"render": {"music_metadata": {"track_id": "738"}}}),))
    assert recent_track_ids(SimpleNamespace(database_path=db)) == {"738"}
    assert recent_track_ids(SimpleNamespace(database_path=db), exclude_job="a") == set()


def test_more_than_one_hedge_counts_as_report_talk():
    plan = {"scenes": [{"lines": [{"speaker": "me", "text": "Ảnh từ trang bán thôi nha"},
                                  {"speaker": "me", "text": "Độ thấm chưa xác minh"},
                                  {"speaker": "me", "text": "Vắt một cái là khô"}]}]}
    assert len(creative.meta_word_lines(plan)) == 2
    plan["scenes"][0]["lines"].pop(1)
    assert creative.meta_word_lines(plan) == []     # one honest hedge is fine


def test_animal_lead_gets_an_animal_reference_photo():
    dog = {"id": "cun", "name": "Cún", "role": "dog", "appearance": "golden puppy, white chest"}
    text = muse_prompt.character_sheet_prompt(dog, {})
    assert "real animal" in text and "Vietnamese person" not in text and "cast:cun" in text
