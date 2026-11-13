"""Automated tests for AI Background Music Engine 2.0 (Mixkit Dynamic Discovery & Evaluation)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from agent_core.editor.music import (
    AudioAnalysisMetrics,
    MixkitCandidate,
    MixkitMusicClient,
    MusicBrief,
    analyze_audio_file,
    discover_and_select_music,
    evaluate_music_candidate,
    generate_music_brief,
    pick_track,
)
from agent_core.settings import get_settings


# ---------------------------------------------------------------------------
# TEST 1: MUSIC BRIEF GENERATION
# ---------------------------------------------------------------------------

def test_music_brief_generation_mother_story():
    """Video câu chuyện người mẹ phải sinh Music Brief Emotional / Piano / Strings."""
    timeline = {
        "title": "Ai Là Người Hiểu Mẹ Nhất Trong Nhà?",
        "hook": "Nếu mẹ được nghỉ đúng một ngày, mẹ muốn làm gì nhất?",
        "duration": 27.5,
        "scenes": [
            {"voiceover": "Có một ngăn kéo trong nhà mẹ không bao giờ cho ai mở."},
            {"voiceover": "Bên trong toàn là hóa đơn tiền thuốc và ảnh cũ của các con."},
            {"voiceover": "Đôi khi người hiểu mẹ nhất lại là người nhận ra mẹ đã mệt vì điều gì."},
        ],
    }
    brief = generate_music_brief(timeline)
    assert "Emotional" in brief.primary_mood or "Warm" in brief.primary_mood
    assert any("piano" in inst.lower() for inst in brief.instruments)
    assert "emotional" in brief.categories
    assert any("piano" in q for q in brief.search_queries)
    assert brief.target_duration_sec == pytest.approx(27.5, 0.1)


def test_music_brief_generation_dog_story():
    """Video chú chó chạy trên cánh đồng phải sinh Music Brief Playful / Acoustic / Happy."""
    timeline = {
        "title": "Chú chó chạy như vận động viên, nhưng đích đến mới bất ngờ!",
        "hook": "Một chú chó bất ngờ phóng như tên bắn giữa cánh đồng. Nó đang đuổi theo cái gì?",
        "duration": 20.4,
        "scenes": [
            {"voiceover": "Chú chó chạy bạt mạng qua cánh đồng vàng."},
            {"voiceover": "Tưởng nó săn bắt con gì, ai ngờ đích đến chỉ để xin một cái ôm của chủ nhân."},
        ],
    }
    brief = generate_music_brief(timeline)
    assert "Happy" in brief.primary_mood or "Playful" in brief.primary_mood
    assert any("guitar" in inst.lower() or "acoustic" in inst.lower() for inst in brief.instruments)
    assert "happy" in brief.categories or "acoustic" in brief.categories
    assert any("playful" in q or "happy" in q for q in brief.search_queries)
    assert brief.target_duration_sec == pytest.approx(20.4, 0.1)


# ---------------------------------------------------------------------------
# TEST 2: MIXKIT CANDIDATE HTML CARD PARSING
# ---------------------------------------------------------------------------

def test_mixkit_card_parsing():
    """Mixkit HTML card parser must correctly extract title, author, mp3 URL and license."""
    sample_html = """
    <div class="item-grid__item">
      <div class="item-grid-card item-grid-card--show-meta">
        <div data-controller="audio-player"
             data-audio-player-preview-url-value="https://assets.mixkit.co/music/614/614.mp3"
             data-audio-player-waveform-url-value="https://assets.mixkit.co/music/614/614-waveform.json"
             data-audio-player-item-id-value="614">
        </div>
        <div class="item-grid-card__meta">
          <h2 class="item-grid-card__title">Warm Emotional Memories</h2>
          <p class="author">by Lily J</p>
          <div data-test-id="duration">2:15</div>
          <a class="meta-links__link" href="/free-stock-music/emotional/">Emotional</a>
          <a class="meta-links__link" href="/free-stock-music/instrument/piano/">Piano</a>
        </div>
      </div>
    </div>
    """
    client = MixkitMusicClient()
    candidates = client._parse_cards(sample_html, limit=5)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.track_id == "614"
    assert c.title == "Warm Emotional Memories"
    assert c.author == "Lily J"
    assert c.preview_url == "https://assets.mixkit.co/music/614/614.mp3"
    assert "Emotional" in c.tags
    assert "Piano" in c.tags
    assert "Mixkit Stock Music Free License" in c.license_type


# ---------------------------------------------------------------------------
# TEST 3: AUDIO ANALYSIS METRICS (MOCK INSPECTION)
# ---------------------------------------------------------------------------

def test_audio_analysis_metrics(tmp_path):
    """Kiểm tra trích xuất thông số audio properties từ file âm thanh."""
    fake_mp3 = tmp_path / "test.mp3"
    fake_mp3.write_bytes(b"dummy mp3 data" * 100)

    fake_probe_json = json.dumps({
        "format": {"duration": "65.5", "bit_rate": "320000"},
        "streams": [{"codec_name": "mp3", "sample_rate": "44100", "channels": 2}]
    })
    fake_vol_stderr = "max_volume: -1.5 dB\nmean_volume: -18.5 dB\nsilence_duration: 0.8\n"

    with patch("subprocess.run") as mock_run:
        # Mock probe call then volumedetect call
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=fake_probe_json, stderr=""),
            MagicMock(returncode=0, stdout="", stderr=fake_vol_stderr),
        ]
        metrics = analyze_audio_file(fake_mp3)

    assert metrics.is_valid_audio is True
    assert metrics.duration_sec == 65.5
    assert metrics.mean_volume_db == -18.5
    assert metrics.max_volume_db == -1.5
    assert metrics.dynamic_range_db == 17.0
    assert metrics.sample_rate == 44100


# ---------------------------------------------------------------------------
# TEST 4: 100-POINT AI MUSIC SCORING & GATING
# ---------------------------------------------------------------------------

def test_evaluate_music_candidate_accepted():
    """Bản nhạc Piano Emotional phù hợp video Người Mẹ phải đạt >= 80 điểm (PASS/PRIORITY)."""
    brief = MusicBrief(
        primary_mood="Emotional, Warm",
        secondary_mood="Nostalgic",
        instruments=["Piano", "Strings"],
        energy_progression="Build to climax",
        tempo="Slow",
        genre="Cinematic",
        search_queries=[],
        categories=[],
        target_duration_sec=25.0,
    )
    candidate = MixkitCandidate(
        track_id="614",
        title="Gentle Piano Memories",
        author="Lily J",
        preview_url="https://assets.mixkit.co/music/614/614.mp3",
        waveform_url="",
        source_url="https://mixkit.co/",
        tags=["Emotional", "Piano", "Cinematic", "Warm"],
        duration_str="2:15",
    )
    metrics = AudioAnalysisMetrics(
        duration_sec=135.0,
        mean_volume_db=-18.0,
        max_volume_db=-2.0,
        dynamic_range_db=16.0,
        silence_duration_sec=0.5,
        sample_rate=44100,
        channels=2,
        bitrate=320000,
        is_valid_audio=True,
    )
    score = evaluate_music_candidate(metrics, candidate, brief)
    assert score.total >= 80.0
    assert score.decision in ("PRIORITY", "PASS")
    assert score.mood_match >= 18.0
    assert score.story_progression >= 15.0
    assert score.voice_compatibility >= 12.0


def test_evaluate_music_candidate_rejected_clash():
    """Bản nhạc Heavy Metal / Club Disco cho video Người Mẹ phải bị REJECT (< 70 điểm)."""
    brief = MusicBrief(
        primary_mood="Emotional, Warm",
        secondary_mood="Nostalgic",
        instruments=["Piano", "Strings"],
        energy_progression="Gentle build",
        tempo="Slow",
        genre="Cinematic",
        search_queries=[],
        categories=[],
        target_duration_sec=25.0,
    )
    candidate = MixkitCandidate(
        track_id="999",
        title="Heavy Metal Hard Rock Party",
        author="RockBand",
        preview_url="https://assets.mixkit.co/music/999/999.mp3",
        waveform_url="",
        source_url="https://mixkit.co/",
        tags=["Metal", "Rock", "Disco", "Party", "Club"],
        duration_str="3:00",
    )
    metrics = AudioAnalysisMetrics(
        duration_sec=180.0,
        mean_volume_db=-6.0,   # Quá to, lấn át voice
        max_volume_db=0.0,
        dynamic_range_db=6.0,
        is_valid_audio=True,
    )
    score = evaluate_music_candidate(metrics, candidate, brief)
    assert score.total < 70.0
    assert score.decision == "REJECT"
    assert any("xung đột" in r.lower() for r in score.reasons)


# ---------------------------------------------------------------------------
# TEST 5: MULTI-CANDIDATE RETRY LOOP & ZERO-WASTE DISK HYGIENE
# ---------------------------------------------------------------------------

def test_discover_and_select_music_retry_and_cleanup(tmp_path):
    """Quy trình tự động thử lại khi ứng viên 1 bị loại, chọn ứng viên 2 và dọn dẹp sạch sẽ."""
    settings = get_settings()
    # Override project_root temp dir to tmp_path
    settings.project_root = tmp_path
    job_id = "test_job_music_retry_01"

    cand1 = MixkitCandidate(
        track_id="bad_metal_101",
        title="Heavy Rock Madness",
        author="Rocker",
        preview_url="https://assets.mixkit.co/music/101/101.mp3",
        waveform_url="",
        source_url="",
        tags=["Rock", "Metal", "Party"],
        duration_str="2:00",
    )
    cand2 = MixkitCandidate(
        track_id="good_piano_202",
        title="Heartwarming Piano Touch",
        author="Lily J",
        preview_url="https://assets.mixkit.co/music/202/202.mp3",
        waveform_url="",
        source_url="",
        tags=["Emotional", "Piano", "Warm"],
        duration_str="2:30",
    )

    timeline = {
        "title": "Ai Là Người Hiểu Mẹ Nhất?",
        "hook": "Câu hỏi làm cả nhà im lặng.",
        "duration": 25.0,
        "scenes": [{"voiceover": "Mẹ đã mệt vì điều gì"}],
    }

    # Mock download_candidate_track to create dummy files
    def fake_download(cand, dest_dir):
        p = dest_dir / f"mixkit_{cand.track_id}.mp3"
        p.write_bytes(b"fake audio data" * 5000)
        return p

    # Mock analyze_audio_file
    def fake_analyze(p, *args):
        if "bad_metal" in str(p):
            return AudioAnalysisMetrics(duration_sec=60.0, mean_volume_db=-6.0, max_volume_db=0.0, dynamic_range_db=5.0)
        return AudioAnalysisMetrics(duration_sec=120.0, mean_volume_db=-18.0, max_volume_db=-2.0, dynamic_range_db=15.0)

    with patch.object(MixkitMusicClient, "search_candidates", return_value=[cand1, cand2]), \
         patch("agent_core.editor.music.download_candidate_track", side_effect=fake_download), \
         patch("agent_core.editor.music.analyze_audio_file", side_effect=fake_analyze):

        selected = discover_and_select_music(timeline, job_id, settings, max_candidates=5)

    assert selected is not None
    assert selected.candidate.track_id == "good_piano_202"
    assert selected.score.decision in ("PRIORITY", "PASS")
    assert selected.path.exists()

    # Verify candidate 1 was purged immediately upon rejection
    bad_cand_file = selected.temp_dir / "mixkit_bad_metal_101.mp3"
    assert not bad_cand_file.exists(), "Rejected candidate file must be deleted immediately!"

    # Verify cleanup function removes entire temp directory
    selected.cleanup()
    assert not selected.temp_dir.exists(), "Job temp music folder must be purged completely upon cleanup!"
    assert selected._cleaned is True

    # Metadata must remain intact even after audio file is purged
    meta = selected.to_dict()
    assert meta["track_id"] == "good_piano_202"
    assert meta["title"] == "Heartwarming Piano Touch"
    assert meta["license"] == "Mixkit Stock Music Free License"
    assert meta["cleaned_up"] is True


def test_pick_track_fallback_when_offline(tmp_path):
    """Khi Mixkit không có kết nối, pick_track phải tự động fallback an toàn không gây crash."""
    music_folder = tmp_path / "music"
    music_folder.mkdir()
    local_song = music_folder / "chill_warm_melody.mp3"
    local_song.write_bytes(b"dummy local song")

    timeline = {"title": "Test Fallback", "music_mood": "chill", "scenes": []}

    # Simulate network failure from Mixkit
    with patch.object(MixkitMusicClient, "search_candidates", return_value=[]), \
         patch.object(MixkitMusicClient, "get_category_candidates", return_value=[]):
        chosen = pick_track(music_dir=music_folder, mood="chill", seed="job_fail_01",
                            timeline=timeline, settings=get_settings(), job_id="job_fail_01")

    assert chosen is not None
    assert Path(chosen).name == "chill_warm_melody.mp3"


@pytest.mark.network
def test_live_mixkit_music_engine_benchmarks(tmp_path):
    """Kiểm thử thực tế E2E: Tìm kiếm, tải về, AI nghe và đánh giá nhạc thật từ Mixkit cho 2 benchmark."""
    settings = get_settings()
    settings.project_root = tmp_path

    # 1. Benchmark 1: Video câu chuyện người mẹ
    timeline_mother = {
        "title": "Ai Là Người Hiểu Mẹ Nhất Trong Nhà?",
        "hook": "Nếu mẹ được nghỉ đúng một ngày, mẹ muốn làm gì nhất?",
        "duration": 27.5,
        "scenes": [
            {"voiceover": "Có một ngăn kéo trong nhà mẹ không bao giờ cho ai mở."},
            {"voiceover": "Đôi khi người hiểu mẹ nhất lại là người nhận ra mẹ đã mệt vì điều gì."},
        ],
    }

    try:
        selected_mother = discover_and_select_music(
            timeline=timeline_mother,
            job_id="live_bench_mother_01",
            settings=settings,
            max_candidates=3,
        )
    except Exception as exc:
        pytest.skip(f"Mạng Mixkit tạm thời không khả dụng: {exc}")

    if selected_mother is not None:
        assert selected_mother.score.total >= 70.0
        assert selected_mother.candidate.preview_url.startswith("http")
        assert selected_mother.candidate.license_type == "Mixkit Stock Music Free License"
        # Đã phân tích âm thanh thực tế
        assert selected_mother.metrics.duration_sec > 10.0
        assert selected_mother.metrics.sample_rate >= 22050
        # Xóa dọn dẹp sạch sẽ
        selected_mother.cleanup()
        assert not selected_mother.temp_dir.exists()

