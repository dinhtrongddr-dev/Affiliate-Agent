"""Dialogue series keep the characters' own (Muse) voices; narration/review/trend use VieNeu."""
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_core import creative, creative_series, muse_prompt
from agent_core.creation_branches import normalise_brief, voice_source
from agent_core.editor import voice_track
from agent_core.settings import Settings

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
NICHE = {"id": "plant-facts", "version": 1, "title": "Cây kể chuyện", "style": "documentary",
         "characters": [{"id": "host", "name": "Lan", "gender": "female", "age": "28"}]}


def test_voice_source_per_branch_and_template():
    assert voice_source(normalise_brief("template_series")) == "native"          # Suri family talks
    assert voice_source(normalise_brief("template_series", template=NICHE)) == "tts"
    assert voice_source(normalise_brief("template_series", template={**NICHE, "voice_mode": "dialogue"})) == "native"
    assert voice_source(normalise_brief("product_review")) == "tts"
    assert voice_source(normalise_brief("viral_creator")) == "tts"
    # a Suri template saved before voice modes existed still talks
    old = {k: v for k, v in creative_series._BUILTIN.items() if k != "voice_mode"}
    assert voice_source({"creation_branch": "template_series", "template": old}) == "native"


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


def test_dialogue_prompt_and_word_budget():
    talk = creative.branch_plan_prompt(normalise_brief("template_series"), {}, True, False, gen_limit=6)
    assert "SERIES HỘI THOẠI" in talk and 'KHÔNG dùng speaker "narrator"' in talk
    assert f"NHẮM KHOẢNG {creative.target_words(20, 35, 2.5)[1]} TỪ" in talk
    narr = creative.branch_plan_prompt(normalise_brief("template_series", template=NICHE), {}, True, False)
    review = creative.branch_plan_prompt(normalise_brief("product_review"), {}, True, False)
    assert "SERIES HỘI THOẠI" not in narr and "SERIES HỘI THOẠI" not in review
    assert creative.speech_rate({"voice_source": "native"}) == 2.5 and creative.speech_rate({}) == 3.5


def _dialogue_plan(n_talking=4):
    scenes = [{"visual": "generate", "duration": 5, "cast": ["suri", "mother"], "talking": True,
               "lines": [{"speaker": "suri", "text": f"Mẹ ơi con gấp được rồi nè {i}"},
                         {"speaker": "mother", "text": "Giỏi quá, đâu mẹ xem nào"}]} for i in range(n_talking)]
    return {"creation_branch": "template_series", "storyboard_version": 2, "voice_source": "native",
            "characters": [{"id": "suri", "name": "Suri", "age": "4", "gender": "female"},
                           {"id": "mother", "name": "Mẹ Suri", "age": "30"}], "scenes": scenes}


def test_dialogue_plans_keep_every_talking_scene_and_ask_muse_for_vietnamese_speech():
    plan = creative.normalise_plan(_dialogue_plan(4), "trend")
    assert plan["voice_source"] == "native" and sum(s["talking"] for s in plan["scenes"]) == 4
    normal = creative.normalise_plan({**_dialogue_plan(4), "voice_source": ""}, "trend")
    assert sum(s["talking"] for s in normal["scenes"]) == 2               # voice-over branches: max 2
    prompt = muse_prompt.compile_shot(plan["scenes"][0], plan)
    assert "says in Vietnamese" in prompt and "no background music" in prompt
    assert "high bright child voice" in prompt and "warm young woman's voice" in prompt   # mother from name
    assert len(prompt) <= muse_prompt.MAX_CHARS
    plain = muse_prompt.compile_shot(normal["scenes"][0], normal)
    assert "says to camera" in plain and "says in Vietnamese" not in plain


def test_native_parts_follow_heard_speech_and_fall_back_otherwise(tmp_path):
    scene = {"native_voice": True, "talking": True, "cast": ["suri", "mother"]}
    lines = [{"speaker": "suri", "text": "một hai ba bốn"}, {"speaker": "mother", "text": "năm sáu"}]
    window = (tmp_path / "c.mp4", 0.0, 8.0)
    with patch.object(voice_track, "speech_windows", return_value=[(0.5, 2.0), (2.4, 3.5)]):
        parts = voice_track.native_parts("ffmpeg", scene, lines, window)
        assert [p.speaker for p in parts] == ["suri", "mother"] and all(p.audio is None for p in parts)
        assert parts[0].offset == 0.5 and abs(parts[1].offset + parts[1].duration - 3.5) < 0.01
        assert parts[0].duration > parts[1].duration                     # 4 words vs 2 words
        narrator = lines + [{"speaker": "narrator", "text": "thế là"}]
        assert voice_track.native_parts("ffmpeg", scene, narrator, window) == []   # someone off screen
        assert voice_track.native_parts("ffmpeg", {**scene, "native_voice": False}, lines, window) == []
    with patch.object(voice_track, "speech_windows", return_value=[]):
        assert voice_track.native_parts("ffmpeg", scene, lines, window) == []      # Muse stayed silent


def _talking_clip(path: Path) -> Path:
    """10 s clip whose audio has 'speech' (a tone) from 1.0 to 3.5 s."""
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=orange:size=360x640:rate=30", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,1,3.5),0.5*sin(2*PI*300*t),0)':s=44100", "-t", "10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True)
    return path


def test_render_keeps_the_clip_voice_without_tts(tmp_path):
    from agent_core.editor.render import render_timeline
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                 media_root=tmp_path / "media", muse_embedded=False, tts_enabled=False,
                 render_width=360, render_height=640)
    clip = _talking_clip(tmp_path / "g.mp4")
    timeline = {"creation_branch": "template_series", "storyboard_version": 2, "voice_source": "native",
                "hook": "", "cta": "", "music_mood": "funny",
                "scenes": [{"kind": "video", "source": str(clip), "generated": True, "talking": True,
                            "cast": ["suri"], "duration": 4,
                            "lines": [{"speaker": "suri", "text": "Mẹ ơi con gấp được áo rồi nè"}]}]}
    with patch("agent_core.editor.render.pick_track", return_value=None), \
            patch.object(voice_track, "cached_tts", side_effect=AssertionError("TTS must not run")):
        result = render_timeline(s, timeline, tmp_path / "work", tmp_path / "out.mp4", "t1")
    assert result.scenes[0]["revoice"] == "native" and result.voice
    vol = subprocess.run([FFMPEG, "-hide_banner", "-i", str(result.path), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    mean = float(vol.split("mean_volume:")[1].split("dB")[0])
    assert mean > -40          # the clip's own speech is in the final mix
    subs = Path(result.subtitles).read_text(encoding="utf-8")
    assert "gấp" in subs


def test_voice_source_travels_from_brief_to_plan():
    from agent_core import workflow
    assert workflow._brief_plan_fields(normalise_brief("template_series"))["voice_source"] == "native"
    assert "voice_source" not in workflow._brief_plan_fields(normalise_brief("viral_creator"))
