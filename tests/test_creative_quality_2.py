"""Unit and integration tests for Creative Intelligence & Viral Quality 2.0."""

from unittest.mock import MagicMock, patch
from agent_core.settings import get_settings
from agent_core.creative import (
    _smart_truncate,
    determine_duration_strategy,
    generate_hook_variations,
)
from agent_core.quality import (
    evaluate_creative_quality,
    inspect_rendered_video,
)
from agent_core.performance_learning import (
    analyze_performance_insights,
    format_performance_feedback_for_prompt,
    get_video_performance,
    record_video_performance,
)
from agent_core.editor.subtitles import build_ass
from agent_core.editor.tts import Word


def test_smart_truncate_vietnamese():
    """Kiểm tra cắt chuỗi thông minh không cắt đôi từ tiếng Việt."""
    # Test case 1: Cắt ngắn nhưng không làm đứt từ
    text = "Chú chó chạy như vận động viên, nhưng đích đến mới bất ngờ và thú vị!"
    truncated = _smart_truncate(text, 40)
    assert not truncated.endswith("bất ng")
    assert not truncated.endswith("th")
    assert len(truncated) <= 40
    # Cắt gọn gàng ngay trước boundary từ tiếp theo
    assert truncated == "Chú chó chạy như vận động viên, nhưng"

    # Test case 2: Chuỗi ngắn hơn limit thì giữ nguyên
    short_text = "Mẹ ơi con về rồi"
    assert _smart_truncate(short_text, 50) == "Mẹ ơi con về rồi"

    # Test case 3: Chuỗi rỗng
    assert _smart_truncate("", 20) == ""


def test_adaptive_duration_strategy():
    """Kiểm tra chiến lược thời lượng thích ứng 4 khoảng chuẩn."""
    # Short viral (topic về chú chó/pet)
    d1 = determine_duration_strategy({"topic": "Chú chó chạy trên cánh đồng", "notes": "chó cưng vui nhộn"})
    assert d1["category"] == "short_viral"
    assert 10.0 <= d1["target_seconds"] <= 25.0

    # Story viral (topic về mẹ/gia đình)
    d2 = determine_duration_strategy({"topic": "Ai là người hiểu mẹ nhất", "notes": "tình cảm gia đình sâu sắc"})
    assert d2["category"] == "story_viral"
    assert 25.0 <= d2["target_seconds"] <= 45.0

    # Value & affiliate (review sản phẩm)
    d3 = determine_duration_strategy({"creation_branch": "product_review", "offer": {"price": 100000}})
    assert d3["category"] == "value_affiliate"
    assert 30.0 <= d3["target_seconds"] <= 60.0


def test_hook_variations_generator():
    """Kiểm tra bộ tạo 3 phương án hook đa góc nhìn."""
    hooks = generate_hook_variations(
        topic="Người mẹ thức khuya",
        angle="Sự thấu hiểu của con cái",
        premise="Ai là người hiểu mẹ nhất trong gia đình?",
    )
    assert len(hooks) == 3
    types = [h["type"] for h in hooks]
    assert "curiosity_gap" in types
    assert "pattern_interrupt" in types
    assert "relatable_empathy" in types

    for h in hooks:
        assert len(h["text_hook"]) > 5
        assert len(h["text_hook"]) <= 55
        assert len(h["voice_hook"]) > 5
        assert len(h["visual_action"]) > 5


def test_creative_quality_scorecard_100():
    """Kiểm tra thang điểm chất lượng sáng tạo 100 điểm với 8 tiêu chí chuẩn."""
    # Kịch bản chất lượng cao
    high_quality_plan = {
        "title": "Ai Là Người Hiểu Mẹ Nhất Trong Nhà?",
        "hook": "90% người con nghĩ mình hiểu mẹ nhất, cho đến khi thấy ngăn kéo này.",
        "angle": "Góc nhìn thấu cảm gia đình sâu sắc",
        "premise": "Chiếc ngăn kéo bí mật và sự hi sinh âm thầm",
        "content_mode": "viral",
        "music_mood": "emotional",
        "cta": "",
        "hook_options": [
            {"type": "curiosity_gap", "text": "Câu hỏi khiến cả gia đình im lặng."},
            {"type": "emotional_twist", "text": "Ngăn kéo mẹ không bao giờ cho ai mở."},
        ],
        "scenes": [
            {
                "scene_id": 1,
                "duration": 5.0,
                "purpose": "Thiết lập bối cảnh gây tò mò",
                "shot": {
                    "action": "Close-up tay chạm vào ngăn kéo gỗ cũ",
                    "camera": "slow push-in",
                    "lighting": "warm morning light",
                },
                "voiceover": "Có một ngăn kéo trong nhà mẹ không bao giờ cho ai mở.",
            },
            {
                "scene_id": 2,
                "duration": 5.0,
                "purpose": "Đẩy cao trào cảm xúc",
                "shot": {
                    "action": "Medium shot: Các hóa đơn viện phí và ảnh cũ của các con",
                    "camera": "steady panning",
                    "lighting": "soft warm glow",
                },
                "voiceover": "Bên trong toàn là hóa đơn tiền thuốc và ảnh cũ của các con.",
            },
            {
                "scene_id": 3,
                "duration": 6.0,
                "purpose": "Payoff và kết thúc trọn vẹn",
                "shot": {
                    "action": "Wide shot: Mẹ đứng bên khung cửa sổ chiều",
                    "camera": "slow pull out",
                    "lighting": "sunset golden hour",
                },
                "voiceover": "Đôi khi người hiểu mẹ nhất lại là người nhận ra mẹ đã mệt vì điều gì.",
            },
        ],
    }

    eval_result = evaluate_creative_quality(high_quality_plan)
    assert eval_result.total >= 80.0
    assert eval_result.hook >= 14.0
    assert eval_result.storytelling >= 10.0
    assert eval_result.emotion >= 12.0
    assert eval_result.creativity >= 10.0
    assert eval_result.status in ("PRIORITY_PUBLISH", "CONDITIONAL_PASS")
    assert len(eval_result.strengths) >= 3


def test_subtitles_ending_not_dropped_with_cta(tmp_path):
    """Xác minh phụ đề cuối không bao giờ bị cắt bỏ khi có CTA."""
    captions = [
        (
            0.0,
            [
                Word(text="Mở", start=0.5, end=1.0),
                Word(text="đầu", start=1.0, end=1.5),
                Word(text="câu", start=1.5, end=2.0),
                Word(text="chuyện", start=2.0, end=2.5),
            ],
        ),
        (
            8.0,
            [
                Word(text="Nhận", start=0.0, end=0.5),
                Word(text="ra", start=0.5, end=1.0),
                Word(text="họ", start=1.0, end=1.5),
                Word(text="đã", start=1.5, end=2.0),
                Word(text="mệt", start=2.0, end=2.5),
                Word(text="vì", start=2.5, end=2.9),
                Word(text="điều", start=2.9, end=3.3),
                Word(text="gì", start=3.3, end=3.8),
            ],
        ),
    ]

    ass_path = tmp_path / "test_ending.ass"
    build_ass(
        path=ass_path,
        width=1080,
        height=1920,
        font="Be Vietnam Pro",
        captions=captions,
        hook="Ai là người hiểu mẹ nhất",
        hook_end=3.5,
        labels=[],
        cta="Mua ngay tại giỏ hàng",
        cta_start=12.2,
        total=15.0,
    )

    assert ass_path.exists()
    content = ass_path.read_text(encoding="utf-8-sig")

    # Kiểm tra cả câu thoại cuối vẫn tồn tại đầy đủ trong phụ đề ASS
    assert "mệt" in content
    assert "điều" in content
    assert "gì" in content


def test_performance_learning_sqlite_and_insights(tmp_path):
    """Kiểm tra hệ thống học hỏi dữ liệu hiệu suất và trích xuất gợi ý."""
    db_path = tmp_path / "test_perf.sqlite3"

    # Ghi nhận hiệu suất video 1
    record_video_performance(
        database_path=db_path,
        job_id="job_dog_running_01",
        platform="facebook",
        metrics={"views": 25000, "retention_3s_rate": 0.78, "completion_rate": 0.52, "shares": 320},
        content_mode="viral",
        hook_style="curiosity_gap",
        duration_category="short_viral",
    )

    # Ghi nhận hiệu suất video 2
    record_video_performance(
        database_path=db_path,
        job_id="job_slow_intro_02",
        platform="facebook",
        metrics={"views": 800, "retention_3s_rate": 0.28, "completion_rate": 0.15, "shares": 2},
        content_mode="viral",
        hook_style="direct_provocative",
        duration_category="deep_story",
    )

    # Kiểm tra truy vấn theo job
    records = get_video_performance(db_path, "job_dog_running_01")
    assert len(records) == 1
    assert records[0]["retention_3s_rate"] == 0.78
    assert records[0]["hook_style"] == "curiosity_gap"

    # Phân tích insights
    insights = analyze_performance_insights(db_path)
    assert insights["total_videos_analyzed"] == 2
    assert insights["best_hook_style"] == "curiosity_gap"
    assert insights["optimal_duration_category"] == "short_viral"

    # Định dạng prompt feedback
    feedback_text = format_performance_feedback_for_prompt(db_path)
    assert "curiosity_gap" in feedback_text
    assert "short_viral" in feedback_text


def test_technical_qa_detects_subtitles_truncation(tmp_path):
    """Kiểm tra Technical QA phát hiện khi phụ đề bị thiếu câu kết của lời thoại."""
    fake_video = tmp_path / "test_vid.mp4"
    fake_video.write_bytes(b"dummy video content")

    timeline = {
        "duration": 12.0,
        "scenes": [
            {
                "scene_id": 1,
                "voiceover": "Mở đầu câu chuyện xúc động.",
            },
            {
                "scene_id": 2,
                "voiceover": "Nhận ra họ đã mệt vì điều gì.",
            },
        ],
    }

    # Giả lập file ASS bị cắt mất các từ cuối ("vì điều gì")
    ass_path = tmp_path / "test_vid.ass"
    ass_content = """[Script Info]
PlayResX: 1080
PlayResY: 1920

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,0:00:01.00,0:00:05.00,Sub,,0,0,0,,{\\kf50}Mở {\\kf50}đầu
Dialogue: 1,0:00:06.00,0:00:09.00,Sub,,0,0,0,,{\\kf50}Nhận {\\kf50}ra {\\kf50}họ {\\kf50}đã {\\kf50}mệt
Dialogue: 4,0:00:09.50,0:00:12.00,CTA,,0,0,0,,Xem ngay
"""
    ass_path.write_text(ass_content, encoding="utf-8-sig")

    settings = get_settings()

    fake_meta = {"duration_sec": 12.0, "width": 1080, "height": 1920, "has_audio": True}
    fake_proc = MagicMock(returncode=0, stdout="", stderr="max_volume: -6.0 dB\nmean_volume: -20.0 dB\n")

    with patch("agent_core.quality.probe", return_value=fake_meta), patch("subprocess.run", return_value=fake_proc):
        res = inspect_rendered_video(video_path=fake_video, timeline=timeline, settings=settings, subtitles_path=ass_path)

    # Technical QA phải phát hiện ra lỗi cắt dở câu kết
    assert any("cắt dở" in err for err in res.critical_errors)
    assert res.can_auto_repair is True
    assert res.repair_action == "rerender_subtitles"

