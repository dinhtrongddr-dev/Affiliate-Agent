"""Script-vs-speech scoring and the gateway's series-thread request fields."""
from agent_core.editor import speech_check as sc


def test_wer_ignores_case_and_punctuation():
    assert sc.word_error_rate("Mẹ ơi, sao mặt trăng cứ đi theo con vậy?", "mẹ ơi sao mặt trăng cứ đi theo con vậy") == 0
    assert sc.word_error_rate("Vậy bố là mặt trăng của con rồi!", "vậy bố là mặt trời của con rồi") == round(1 / 8, 3)
    assert sc.word_error_rate("a b c", "") == 1.0


def test_wer_without_diacritics_forgives_tone_marks_only():
    ref, heard = "Tại mặt trăng thương con nhất nhà đó", "tai mat trang thuong con nhat nha do"
    assert sc.word_error_rate(ref, heard) == 0.875          # only "con" has no tone mark
    assert sc.word_error_rate(ref, heard, keep_marks=False) == 0.0


def test_gateway_minimal_wrapper_has_no_ignore_history_text():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "muse_gateway"))
    import importlib
    app = importlib.import_module("app")
    req = app.VideoRequest(prompt='Spoken line (Vietnamese): "Mẹ ơi"', duration=10, size="9:16", wrapper="minimal",
                           images=[{"url": "data:image/png;base64,AAAA"}])
    text = app.build_video_prompt(req, ["image"])
    assert text.startswith("Create one vertical 9:16 video") and "Mẹ ơi" in text
    assert "严禁" not in text and "历史" not in text
    threaded = app.VideoRequest(prompt="x", duration=10, thread_url="https://muse.ai/thread/abc123")
    assert "严禁" not in app.build_video_prompt(threaded, [])
    plain = app.VideoRequest(prompt="x", duration=10, size="9:16")
    assert "严禁" in app.build_video_prompt(plain, [])      # normal jobs keep the upstream wrapper


def test_line_match_forgives_ghost_words_but_counts_extras():
    from agent_core.editor.speech_check import line_match
    assert line_match("Mẹ ơi, sao mặt trăng cứ đi theo con vậy?", "mẹ ơi sao mặt trăng cứ đi theo con vậy. dàng") == (0.0, 1)
    wer, extra = line_match("Mẹ ơi, sao mặt trăng cứ đi theo con vậy?",
                            "chào các bạn mẹ ơi sao mặt trời cứ đi theo con vậy hẹn gặp lại")
    assert wer == 0.1 and extra == 6


def test_native_voice_prompt_puts_the_exact_line_first():
    from agent_core.muse_prompt import compile_shot, voice_description
    from agent_core.videogen.engine import spoken_line
    plan = {"creation_branch": "template_series", "dialogue": True, "native_voice": True, "style_profile": "chibi_3d",
            "characters": [{"id": "suri", "name": "Suri", "gender": "female", "age": "4"},
                           {"id": "mother", "name": "Mẹ", "gender": "female", "age": "30"}]}
    scene = {"talking": True, "cast": ["suri", "mother"], "shot": {"action": "Suri points at the moon"},
             "lines": [{"speaker": "suri", "text": "Mẹ ơi, sao mặt trăng cứ đi theo con vậy?"}]}
    text = compile_shot(scene, plan)
    assert text.startswith('Spoken line (Vietnamese): "Mẹ ơi, sao mặt trăng cứ đi theo con vậy?"')
    assert "word for word" in text and "4-year-old Vietnamese girl" in text
    assert spoken_line(text) == "Mẹ ơi, sao mặt trăng cứ đi theo con vậy?"
    assert voice_description({"gender": "male", "age": 32}) == "a Vietnamese man about 32, warm medium-low voice"
    plain = compile_shot(scene, {**plan, "native_voice": False})
    assert not plain.startswith("Spoken line") and spoken_line(plain) == ""


def test_native_scene_voice_uses_the_clip_speech_not_tts(tmp_path):
    from types import SimpleNamespace

    from agent_core.editor.voice_track import scene_voice
    s = SimpleNamespace(tts_enabled=True, tts_voice="x", ffmpeg_path="ffmpeg", project_root=tmp_path)
    scene = {"talking": True, "cast": ["suri"], "native_voice": {"start": 2.2, "end": 4.6},
             "lines": [{"speaker": "suri", "text": "Mẹ ơi sao mặt trăng đi theo con"}]}
    voice = scene_voice(s, scene, {}, tmp_path, 0, clip_window=(tmp_path / "c.mp4", 1.9, 8.0))
    assert voice.revoice == "native" and len(voice.parts) == 1
    part = voice.parts[0]
    assert part.audio is None and abs(part.offset - 0.3) < 1e-6 and abs(part.duration - 2.4) < 1e-6
    assert [w.text for w in part.words][:2] == ["Mẹ", "ơi"]
