"""Unit and integration tests for VieNeu-TTS in agent_core."""

from agent_core.editor.tts import (
    LEGACY_VOICE_MAP,
    VOICES,
    cpu_supports_vnni,
    estimate_duration,
    evenly_timed_words,
    get_engine,
    synthesize,
)


def test_voice_list_and_legacy_map():
    assert "Mai Anh" in VOICES
    assert "Minh Quân" in VOICES
    assert "Mỹ Duyên" in VOICES
    assert "Xuân Tiên" in VOICES
    assert len(VOICES) == 11
    assert LEGACY_VOICE_MAP.get("vi-VN-HoaiMyNeural") == "Mai Anh"
    assert LEGACY_VOICE_MAP.get("vi-VN-NamMinhNeural") == "Minh Quân"


def test_vnni_detection():
    has_vnni = cpu_supports_vnni()
    assert isinstance(has_vnni, bool)


def test_duration_and_word_timing():
    text = "Sản phẩm chất lượng cao"
    est = estimate_duration(text)
    assert est > 0.5
    words = evenly_timed_words(text, 0.0, 2.0)
    assert len(words) == 5
    assert words[0].text == "Sản"
    assert words[-1].text == "cao"
    assert words[-1].end == 2.0


def test_get_engine_fast_and_quality():
    eng_fast, mode_fast = get_engine("fast")
    assert mode_fast == "v3nano"
    assert eng_fast is not None

    eng_qual, mode_qual = get_engine("quality")
    assert mode_qual == "v3turbo"
    assert eng_qual is not None


def test_synthesize_empty_text(tmp_path):
    out = tmp_path / "empty.wav"
    speech = synthesize("", out)
    assert speech.audio_path is None
    assert speech.duration == 0.0
    assert speech.words == []


def test_synthesize_fast_mode(tmp_path):
    out = tmp_path / "fast_test.wav"
    speech = synthesize("Chào bạn hôm nay", out, voice="Mai Anh", mode="fast")
    assert speech.audio_path is not None
    assert speech.audio_path.exists()
    assert speech.audio_path.stat().st_size > 1000
    assert speech.duration > 0.5
    assert len(speech.words) == 4
    out.unlink(missing_ok=True)


def test_synthesize_quality_mode(tmp_path):
    out = tmp_path / "qual_test.wav"
    speech = synthesize("Chào mừng quý khách", out, voice="Minh Quân", mode="quality")
    assert speech.audio_path is not None
    assert speech.audio_path.exists()
    assert speech.audio_path.stat().st_size > 1000
    assert speech.duration > 0.5
    assert len(speech.words) == 4
    out.unlink(missing_ok=True)


def test_synthesize_legacy_voice_mapping(tmp_path):
    out = tmp_path / "legacy_test.wav"
    speech = synthesize("Thử nghiệm giọng cũ", out, voice="vi-VN-HoaiMyNeural", mode="fast")
    assert speech.audio_path is not None
    assert speech.audio_path.exists()
    assert speech.audio_path.stat().st_size > 1000
    out.unlink(missing_ok=True)


def test_synthesize_bytes_preview():
    from agent_core.editor.tts import synthesize_bytes
    data, media_type = synthesize_bytes("Chào bạn", voice="Mai Anh", mode="fast")
    assert isinstance(data, bytes)
    assert len(data) > 1000
    assert media_type == "audio/wav"
    assert data[:4] == b"RIFF"


def test_parse_rate():
    from agent_core.editor.tts import parse_rate
    assert parse_rate("+0%") == 1.0
    assert parse_rate("-0%") == 1.0
    assert parse_rate("0%") == 1.0
    assert parse_rate("+20%") == 1.2
    assert parse_rate("-20%") == 0.8
    assert parse_rate("+15%") == 1.15
    assert parse_rate("-15%") == 0.85
    assert parse_rate("1.5") == 1.5
    assert parse_rate(1.2) == 1.2
    assert parse_rate(None) == 1.0
    assert parse_rate("invalid") == 1.0
    # Clamping
    assert parse_rate("+300%") == 2.0
    assert parse_rate("-90%") == 0.5


def test_synthesize_with_rate_effect():
    from agent_core.editor.tts import synthesize_bytes
    data_normal, _ = synthesize_bytes("Xin chào các bạn đã xem video", voice="Mai Anh", rate="+0%")
    data_fast, _ = synthesize_bytes("Xin chào các bạn đã xem video", voice="Mai Anh", rate="+50%")
    data_slow, _ = synthesize_bytes("Xin chào các bạn đã xem video", voice="Mai Anh", rate="-30%")
    # Bản đọc nhanh hơn (+50%) phải có kích thước file WAV nhỏ hơn bản bình thường
    assert len(data_fast) < len(data_normal)
    # Bản đọc chậm hơn (-30%) phải có kích thước file WAV lớn hơn bản bình thường
    assert len(data_slow) > len(data_normal)
