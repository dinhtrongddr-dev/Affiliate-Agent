from dataclasses import dataclass


@dataclass
class QualityResult:
    passed: bool
    platform: str
    reasons: list[str]
    metadata: dict


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
    from pathlib import Path

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
    black or frozen. Portrait framing is preferred but not required (render blur-fills)."""
    import re
    import subprocess

    from .editor.ffmpeg import probe

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
