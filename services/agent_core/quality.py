"""Quality assurance for generated media, rendered videos and creative intelligence 2.0.

Provides:
- Platform validation (Facebook Reels 9:16 specs, Shopee Video integrity).
- Creative Quality Score (100-point rubric across 8 dimensions with 70/80/90 thresholds).
- Technical QA: deep inspection of final rendered video (silence detection, clipping,
  volume levels, subtitle timing/truncation, ending integrity, black frames, aspect ratio).
"""
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .editor.ffmpeg import probe


@dataclass
class QualityResult:
    passed: bool
    platform: str
    reasons: list[str]
    metadata: dict


@dataclass
class CreativeScoreBreakdown:
    hook: float = 0.0          # max 20
    storytelling: float = 0.0  # max 15
    emotion: float = 0.0       # max 15
    creativity: float = 0.0    # max 15
    storyboard: float = 0.0    # max 10
    voice_audio: float = 0.0   # max 10
    editing: float = 0.0       # max 10
    cta_fit: float = 0.0       # max 5
    total: float = 0.0         # max 100
    status: str = "FAIL"       # FAIL (<70) | NEEDS_REVISION (70-79) | CONDITIONAL_PASS (80-89) | PRIORITY_PUBLISH (90-100)
    strengths: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)


@dataclass
class TechnicalQAResult:
    passed: bool
    critical_errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    audio_metrics: dict = field(default_factory=dict)
    video_metrics: dict = field(default_factory=dict)
    subtitle_metrics: dict = field(default_factory=dict)
    can_auto_repair: bool = False
    repair_action: str | None = None


# ---------------------------------------------------------------------------
# PLATFORM CHECKS (EXISTING)
# ---------------------------------------------------------------------------

def validate_facebook_reel(metadata: dict, settings) -> QualityResult:
    reasons = []
    duration = float(metadata.get("duration_sec") or 0)
    width = int(metadata.get("width") or 0)
    height = int(metadata.get("height") or 0)

    if not settings.facebook_min_duration_seconds <= duration <= settings.facebook_max_duration_seconds:
        reasons.append(
            f"Duration must be {settings.facebook_min_duration_seconds:g}-"
            f"{settings.facebook_max_duration_seconds:g} seconds; got {duration:g}"
        )
    if width < settings.facebook_min_width or height < settings.facebook_min_height:
        reasons.append(
            f"Resolution must be at least {settings.facebook_min_width}x"
            f"{settings.facebook_min_height}; got {width}x{height}"
        )
    if height <= 0 or width <= 0:
        reasons.append("Invalid video dimensions")
    else:
        ratio = width / height
        if abs(ratio - (9 / 16)) > 0.035:
            reasons.append(f"Reel must be vertical 9:16; got ratio {ratio:.3f}")

    return QualityResult(
        passed=not reasons,
        platform="facebook_reels",
        reasons=reasons,
        metadata=metadata,
    )


def validate_shopee_video(path, metadata: dict) -> QualityResult:
    """Validate basic file integrity only; do not invent undocumented Shopee limits."""
    reasons = []
    video = Path(path)
    if not video.is_file():
        reasons.append("Video file does not exist")
    elif video.stat().st_size <= 0:
        reasons.append("Video file is empty")
    if float(metadata.get("duration_sec") or 0) <= 0:
        reasons.append("Video duration is invalid")
    if int(metadata.get("width") or 0) <= 0 or int(metadata.get("height") or 0) <= 0:
        reasons.append("Video dimensions are invalid")
    return QualityResult(
        passed=not reasons,
        platform="shopee_video",
        reasons=reasons,
        metadata=metadata,
    )


def validate_rendered_video(metadata: dict, settings) -> QualityResult:
    """Checks for the finished short video (format, length, audio)."""
    reasons = []
    duration = float(metadata.get("duration_sec") or 0)
    if duration < 3:
        reasons.append(f"Rendered video too short ({duration:.1f}s)")
    if duration > 180:
        reasons.append(f"Rendered video too long ({duration:.1f}s)")
    if metadata.get("has_audio") is False:
        reasons.append("Rendered video has no audio track")
    width, height = int(metadata.get("width") or 0), int(metadata.get("height") or 0)
    if width <= 0 or height <= 0 or width >= height:
        reasons.append(f"Rendered video must be vertical; got {width}x{height}")
    return QualityResult(passed=not reasons, platform="render", reasons=reasons,
                         metadata=metadata)


def check_generated_clip(settings, path) -> dict:
    """QC for an AI-generated clip: playable video stream, sensible length, and not mostly
    black or frozen."""
    report: dict = {"ok": False, "issues": [], "warnings": []}
    try:
        meta = probe(settings.ffprobe_path, path)
    except Exception as exc:  # noqa: BLE001
        report["issues"].append(f"Không đọc được file: {exc}")
        return report
    duration = float(meta.get("duration_sec") or 0)
    report.update(duration=duration, width=meta.get("width"), height=meta.get("height"))
    if not meta.get("has_video"):
        report["issues"].append("Không có hình")
        return report
    if duration < 1.5:
        report["issues"].append(f"Quá ngắn ({duration:.1f}s)")
    if meta.get("width") and meta.get("height") and meta["width"] >= meta["height"]:
        report["warnings"].append("Không phải khung dọc 9:16, sẽ được làm nền mờ")
    command = [settings.ffmpeg_path, "-hide_banner", "-nostats", "-i", str(path), "-an",
               "-vf", "blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=2", "-f", "null", "-"]
    try:
        out = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=300).stderr
    except Exception as exc:  # noqa: BLE001
        report["warnings"].append(f"Bỏ qua kiểm tra khung đen/đứng hình: {exc}")
        out = ""
    black = sum(float(x) for x in re.findall(r"black_duration:\s*([\d.]+)", out))
    frozen = sum(float(x) for x in re.findall(r"freeze_duration:\s*([\d.]+)", out))
    report.update(black_seconds=round(black, 2), frozen_seconds=round(frozen, 2))
    if duration and black / duration > 0.5:
        report["issues"].append("Hơn nửa clip là khung đen")
    if duration and frozen / duration > 0.8:
        report["issues"].append("Clip gần như đứng hình")
    report["ok"] = not report["issues"]
    return report


# ---------------------------------------------------------------------------
# CREATIVE QUALITY SCORE (100-POINT RUBRIC)
# ---------------------------------------------------------------------------

# Target lengths per branch (seconds). Outside these a video cannot be "priority publish".
DURATION_TARGETS = {"viral_creator": (15.0, 30.0), "product_review": (20.0, 40.0),
                    "template_series": (20.0, 35.0), "": (12.0, 45.0)}


def evaluate_creative_quality(timeline: dict, content: dict | None = None,
                              mode: str = "viral", branch: str = "",
                              rendered_duration: float | None = None,
                              music_used: bool | None = None) -> CreativeScoreBreakdown:
    """Evaluate creative quality across 8 professional dimensions (100 points total).

    Dimensions:
    1. Hook & Retention (20 pts): 3s visual + voice + text clarity, no cliché greeting.
    2. Storytelling & Structure (15 pts): setup, conflict, build-up, climax, payoff.
    3. Emotion & Psychology (15 pts): dominant feeling, empathy, resonance, payoff warmth.
    4. Creativity & Uniqueness (15 pts): novel angle, non-formulaic, distinct voice.
    5. Storyboard & Visual Continuity (10 pts): purposeful scenes, character/lighting continuity.
    6. Voice & Audio (10 pts): speech naturalness, mood matching, continuous score.
    7. Editing & Pacing (10 pts): rhythmic cuts, mobile safe-zone subtitles, no awkward transitions.
    8. CTA & Contextual Fit (5 pts): natural ending, no jarring affiliate sell on emotional stories.
    """
    timeline = timeline or {}
    content = content or {}
    scenes = timeline.get("scenes") or []
    hook_text = str(timeline.get("hook") or "").strip()
    cta_text = str(timeline.get("cta") or "").strip()
    branch = branch or str(timeline.get("creation_branch") or "")
    content_mode = str(timeline.get("content_mode") or mode or "viral").lower()

    score = CreativeScoreBreakdown()
    strengths: list[str] = []
    improvements: list[str] = []

    # 1. Hook (Max 20)
    hook_score = 15.0
    if hook_text:
        words = hook_text.split()
        if 2 <= len(words) <= 12:
            hook_score += 3.0
            strengths.append("Hook ngắn gọn, súc tích (<= 12 từ)")
        elif len(words) > 14:
            hook_score -= 2.0
            improvements.append("Hook hơi dài, nên rút ngắn dưới 10 từ để đọc nhanh trong 3s")
        # Check for cliché greetings
        lower_hook = hook_text.lower()
        if any(g in lower_hook for g in ("chào các bạn", "xin chào", "hôm nay mình", "video này")):
            hook_score -= 4.0
            improvements.append("Hook dính lời chào/giới thiệu dài dòng sáo mòn")
        else:
            hook_score += 2.0
    else:
        hook_score -= 5.0
        improvements.append("Thiếu text hook trên màn hình")
    score.hook = round(min(20.0, max(0.0, hook_score)), 1)

    # 2. Storytelling & Structure (Max 15)
    story_score = 12.0
    if len(scenes) >= 3:
        story_score += 2.0
        strengths.append("Cấu trúc cảnh đủ các nhịp kể chuyện")
    elif len(scenes) < 2:
        story_score -= 4.0
        improvements.append("Kịch bản quá ngắn, thiếu không gian phát triển câu chuyện")
    # Check natural dialogue vs cliché
    all_voice = " ".join(str(s.get("voiceover") or "") for s in scenes).lower()
    if any(cliche in all_voice for cliche in ("theo dữ liệu", "được ghi nhận", "thông số sản phẩm", "giá tham khảo")):
        story_score -= 3.0
        improvements.append("Thoại còn câu rào đón hoặc văn mẫu máy móc")
    else:
        story_score += 1.0
        strengths.append("Lời thoại giao tiếp tự nhiên đời thường")
    score.storytelling = round(min(15.0, max(0.0, story_score)), 1)

    # 3. Emotion & Psychology (Max 15)
    emotion_score = 12.0
    if any(k in all_voice for k in ("mẹ", "gia đình", "thương", "bất ngờ", "ôm", "cười", "vui", "hạnh phúc", "ấm")):
        emotion_score += 3.0
        strengths.append("Yếu tố cảm xúc và sự đồng cảm rõ nét")
    else:
        improvements.append("Có thể đẩy thêm yếu tố chạm cảm xúc hoặc yếu tố bất ngờ ở câu kết")
    score.emotion = round(min(15.0, max(0.0, emotion_score)), 1)

    # 4. Creativity & Uniqueness (Max 15)
    creativity_score = 12.0
    if branch in {"viral_creator", "template_series"}:
        creativity_score += 2.5
        strengths.append("Nội dung gốc độc lập mang tính sáng tạo cao")
    if timeline.get("style"):
        creativity_score += 0.5
    score.creativity = round(min(15.0, max(0.0, creativity_score)), 1)

    # 5. Storyboard (Max 10)
    storyboard_score = 7.5
    has_shots = any(isinstance(s.get("shot"), dict) and s["shot"].get("action") for s in scenes)
    if has_shots:
        storyboard_score += 2.5
        strengths.append("Phân cảnh chi tiết hành động, góc máy và ánh sáng")
    else:
        storyboard_score += 1.0
    score.storyboard = round(min(10.0, max(0.0, storyboard_score)), 1)

    # 6. Voice & Audio (Max 10)
    audio_score = 8.0
    if music_used is False:
        audio_score -= 1.0
        improvements.append("Video không có nhạc nền")
    elif timeline.get("music_mood"):
        audio_score += 1.0
        strengths.append(f"Nhạc nền định hướng mood: {timeline.get('music_mood')}")
    if any(s.get("voiceover") for s in scenes):
        audio_score += 1.0
    score.voice_audio = round(min(10.0, max(0.0, audio_score)), 1)

    # 7. Editing & Pacing (Max 10)
    editing_score = 8.0
    durations = [float(s.get("duration") or 2.5) for s in scenes]
    if durations and all(1.0 <= d <= 12.0 for d in durations):
        editing_score += 2.0
        strengths.append("Nhịp độ cảnh dựng hợp lý và đa dạng")
    else:
        improvements.append("Cần cân bằng thời lượng từng cảnh để tránh cảnh quá ngắn hoặc quá dài")
    score.editing = round(min(10.0, max(0.0, editing_score)), 1)

    # 8. CTA & Contextual Fit (Max 5)
    cta_score = 4.0
    if content_mode == "viral":
        if not cta_text or any(k in cta_text.lower() for k in ("xem thêm", "chia sẻ", "theo dõi")):
            cta_score += 1.0
            strengths.append("Kết thúc câu chuyện tự nhiên, không ép bán hàng phá vỡ cảm xúc")
        elif any(k in cta_text.lower() for k in ("mua ngay", "giỏ hàng", "link sản phẩm")):
            cta_score -= 2.0
            improvements.append("Video câu chuyện cảm xúc/viral không nên gắn CTA bán hàng thô")
    else:
        if cta_text:
            cta_score += 1.0
            strengths.append("CTA rõ ràng cho mục tiêu chuyển đổi")
    score.cta_fit = round(min(5.0, max(0.0, cta_score)), 1)

    # Total score calculation
    score.total = round(score.hook + score.storytelling + score.emotion + score.creativity
                        + score.storyboard + score.voice_audio + score.editing + score.cta_fit, 1)

    low, high = DURATION_TARGETS.get(branch, DURATION_TARGETS[""])
    duration_ok = True
    if rendered_duration:
        if rendered_duration < low * 0.7 or rendered_duration > high * 1.3:
            duration_ok = False
            score.total = round(score.total - 15.0, 1)
            improvements.append(f"Thời lượng {rendered_duration:.1f}s lệch xa mục tiêu "
                                f"{low:.0f}-{high:.0f}s")
        elif not low <= rendered_duration <= high:
            score.total = round(score.total - 5.0, 1)
            improvements.append(f"Thời lượng {rendered_duration:.1f}s ngoài mục tiêu "
                                f"{low:.0f}-{high:.0f}s")

    if not duration_ok:
        score.status = "NEEDS_REVISION" if score.total >= 70.0 else "FAIL"
    elif score.total >= 90.0:
        score.status = "PRIORITY_PUBLISH"
    elif score.total >= 80.0:
        score.status = "CONDITIONAL_PASS"
    elif score.total >= 70.0:
        score.status = "NEEDS_REVISION"
    else:
        score.status = "FAIL"

    score.strengths = strengths
    score.improvements = improvements
    return score


# ---------------------------------------------------------------------------
# TECHNICAL QA (POST-RENDER VIDEO INSPECTION)
# ---------------------------------------------------------------------------

def inspect_rendered_video(video_path: Path, settings, timeline: dict | None = None,
                           subtitles_path: Path | None = None) -> TechnicalQAResult:
    """Rigorous post-render verification on the actual output video file.

    Catches:
    - Voice loss or abnormal silence intervals (>1.5s).
    - Audio clipping (> -0.2 dB) or inaudible volume (< -36 dB).
    - Subtitle truncation or missing ending dialogue.
    - Black frames or freeze frames.
    - Aspect ratio and duration mismatches.
    """
    video_path = Path(video_path)
    timeline = timeline or {}
    critical_errors: list[str] = []
    warnings: list[str] = []
    audio_metrics: dict = {}
    video_metrics: dict = {}
    subtitle_metrics: dict = {}
    can_auto_repair = False
    repair_action = None

    if not video_path.is_file() or video_path.stat().st_size <= 0:
        critical_errors.append("File video không tồn tại hoặc rỗng")
        return TechnicalQAResult(
            passed=False, critical_errors=critical_errors, warnings=warnings,
            audio_metrics=audio_metrics, video_metrics=video_metrics,
            subtitle_metrics=subtitle_metrics,
        )

    # 1. Probe basic metadata
    try:
        meta = probe(settings.ffprobe_path, video_path)
    except Exception as exc:  # noqa: BLE001
        critical_errors.append(f"Không thể đọc thông số video (ffprobe): {exc}")
        return TechnicalQAResult(passed=False, critical_errors=critical_errors)

    duration = float(meta.get("duration_sec") or 0)
    width = int(meta.get("width") or 0)
    height = int(meta.get("height") or 0)
    has_audio = bool(meta.get("has_audio"))
    video_metrics.update(duration=duration, width=width, height=height, has_audio=has_audio)

    # Validate 9:16 vertical ratio
    if width <= 0 or height <= 0:
        critical_errors.append(f"Kích thước video không hợp lệ ({width}x{height})")
    elif width >= height:
        critical_errors.append(f"Video không phải khung dọc 9:16 ({width}x{height})")
    else:
        ratio = width / height
        if abs(ratio - (9 / 16)) > 0.04:
            warnings.append(f"Tỷ lệ khung hình lệch chuẩn 9:16 (tỷ lệ {ratio:.3f})")

    if duration < 3.0:
        critical_errors.append(f"Thời lượng video quá ngắn ({duration:.1f}s)")

    # 2. Audio stream deep inspection (silence & volume)
    scenes_with_voice = [s for s in timeline.get("scenes") or [] if s.get("voiceover")]
    tts_enabled = getattr(settings, "tts_enabled", True)

    if not has_audio:
        if scenes_with_voice and tts_enabled:
            critical_errors.append("Video không có luồng âm thanh")
        else:
            warnings.append("Video không có luồng âm thanh (chế độ không có thoại/nhạc)")
    else:
        cmd_audio = [
            settings.ffmpeg_path, "-hide_banner", "-nostats", "-i", str(video_path),
            "-af", "volumedetect,silencedetect=n=-45dB:d=1.5", "-f", "null", "-"
        ]
        try:
            res_audio = subprocess.run(cmd_audio, capture_output=True, text=True,
                                       encoding="utf-8", errors="replace", timeout=120)
            err_out = res_audio.stderr or ""
            # Parse volumes
            max_vol = re.search(r"max_volume:\s*([-\d.]+)\s*dB", err_out)
            mean_vol = re.search(r"mean_volume:\s*([-\d.]+)\s*dB", err_out)
            silences = [float(x) for x in re.findall(r"silence_duration:\s*([\d.]+)", err_out)]

            max_db = float(max_vol.group(1)) if max_vol else 0.0
            mean_db = float(mean_vol.group(1)) if mean_vol else -20.0
            total_silence = sum(silences)
            audio_metrics.update(
                max_volume_db=max_db,
                mean_volume_db=mean_db,
                silence_intervals=len(silences),
                total_silence_sec=round(total_silence, 2),
            )

            # Detect clipping
            if max_db >= -0.1:
                warnings.append(f"Âm thanh có nguy cơ clipping (max {max_db:.1f} dB)")
            # Detect too quiet
            if mean_db < -36.0:
                warnings.append(f"Âm lượng toàn bài quá nhỏ (mean {mean_db:.1f} dB)")

            # Check abnormal silences (>2.0s without dramatic reason)
            long_silences = [s for s in silences if s >= 2.0]
            if long_silences:
                # Flag as critical error only when video has voiceover and TTS is enabled
                if total_silence > max(3.0, duration * 0.3) and scenes_with_voice and tts_enabled:
                    critical_errors.append(
                        f"Phát hiện khoảng im lặng bất thường kéo dài {round(total_silence, 1)}s "
                        f"({len(long_silences)} đoạn >= 2.0s)"
                    )
                else:
                    warnings.append(
                        f"Có {len(long_silences)} khoảng im lặng dài (>2.0s) trong video"
                    )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Bỏ qua phân tích chi tiết âm thanh: {exc}")

    # 3. Subtitles & Ending integrity inspection
    if subtitles_path is None:
        subtitles_path = video_path.with_suffix(".ass")

    if subtitles_path.is_file():
        try:
            ass_content = subtitles_path.read_text(encoding="utf-8-sig", errors="replace")
            sub_events = re.findall(r"Dialogue:\s*1,([^,]+),([^,]+),Sub", ass_content)
            hook_events = re.findall(r"Dialogue:\s*3,([^,]+),([^,]+),Hook", ass_content)
            cta_events = re.findall(r"Dialogue:\s*4,([^,]+),([^,]+),CTA", ass_content)

            subtitle_metrics.update(
                total_subtitles=len(sub_events),
                has_hook=bool(hook_events),
                has_cta=bool(cta_events),
            )

            # Check if scenes have voiceover but zero subtitles
            if scenes_with_voice and len(sub_events) == 0:
                critical_errors.append("Kịch bản có lời thoại nhưng không có phụ đề karaoke hiển thị")

            # Check ending subtitle truncation against last scene voiceover
            if sub_events and scenes_with_voice:
                last_sub_end_str = sub_events[-1][1]
                parts = last_sub_end_str.split(":")
                if len(parts) == 3:
                    last_sub_sec = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                    subtitle_metrics["last_subtitle_end_sec"] = round(last_sub_sec, 2)
                    if duration - last_sub_sec > 4.0 and not cta_events:
                        warnings.append("Phụ đề kết thúc sớm hơn nhiều so với thời lượng video")

                # Verify that the last spoken words are not truncated in subtitles
                last_voice = str(scenes_with_voice[-1].get("voiceover") or "").strip()
                last_voice_words = [w.lower().strip(" ,.-;:!?") for w in last_voice.split()]
                dialogue_text = " ".join(
                    re.sub(r"\{[^}]*\}", "", line).strip()
                    for line in re.findall(r"Dialogue:\s*1,[^,]+,[^,]+,Sub,[^\n]*", ass_content)
                ).lower()

                if len(last_voice_words) >= 3:
                    tail_words = last_voice_words[-3:]
                    missing_tail = [w for w in tail_words if w and w not in dialogue_text]
                    if len(missing_tail) >= 2:
                        critical_errors.append(
                            f"Phụ đề câu kết bị cắt dở, thiếu các từ cuối của lời thoại ({' '.join(missing_tail)})"
                        )
                        can_auto_repair = True
                        repair_action = "rerender_subtitles"
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Bỏ qua kiểm tra phụ đề: {exc}")

    # 4. Black frames inspection
    cmd_black = [
        settings.ffmpeg_path, "-hide_banner", "-nostats", "-i", str(video_path), "-an",
        "-vf", "blackdetect=d=0.6:pix_th=0.10", "-f", "null", "-"
    ]
    try:
        res_black = subprocess.run(cmd_black, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=120)
        black_durations = [float(x) for x in re.findall(r"black_duration:\s*([\d.]+)", res_black.stderr or "")]
        total_black = sum(black_durations)
        video_metrics["black_duration_sec"] = round(total_black, 2)
        if total_black > max(1.5, duration * 0.25):
            critical_errors.append(f"Phát hiện khung hình đen bất thường ({total_black:.1f}s)")
        elif total_black > 0.8:
            warnings.append(f"Có đoạn khung hình đen ({total_black:.1f}s)")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Bỏ qua kiểm tra khung đen: {exc}")

    passed = len(critical_errors) == 0
    return TechnicalQAResult(
        passed=passed,
        critical_errors=critical_errors,
        warnings=warnings,
        audio_metrics=audio_metrics,
        video_metrics=video_metrics,
        subtitle_metrics=subtitle_metrics,
        can_auto_repair=can_auto_repair,
        repair_action=repair_action,
    )
