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
