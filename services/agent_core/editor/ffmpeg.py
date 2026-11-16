import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("affiliate_agent.ffmpeg")


class FFmpegError(RuntimeError):
    pass


# Encodes run below normal priority so the UI and the rest of Windows stay responsive.
BACKGROUND_FLAGS = (getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                    | getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_ffmpeg(ffmpeg: str, args: list[str], cwd: Path | None = None, timeout: int = 1800) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(map(str, args)))
    result = subprocess.run(
        command, cwd=str(cwd) if cwd else None, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=BACKGROUND_FLAGS,
    )
    if result.returncode != 0:
        raise FFmpegError((result.stderr or result.stdout or "ffmpeg failed")[-2000:])


def probe(ffprobe: str, path: Path) -> dict:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise FFmpegError(result.stderr[-500:] or f"ffprobe failed for {path}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration_sec": float((data.get("format") or {}).get("duration") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "has_video": bool(video),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "fps": str(video.get("r_frame_rate") or ""),
    }


def media_duration(ffprobe: str, path: Path) -> float:
    return probe(ffprobe, path)["duration_sec"]


def extract_continuity_frame(
    video_path: Path,
    out_path: Path,
    offset_seconds: float = 0.3,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> Path:
    """Extract a high-quality frame shortly before the end of the video.

    Used as the starting reference frame (start_image) for the next scene to ensure
    seamless visual and posture continuity between AI-generated scenes.
    """
    video_path = Path(video_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        duration = media_duration(ffprobe_bin, video_path)
    except Exception:
        duration = 5.0

    target_time = max(0.0, duration - max(0.05, offset_seconds))
    run_ffmpeg(ffmpeg_bin, [
        "-ss", f"{target_time:.3f}",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ])
    return out_path


def optimize_reference_image(
    image_path: Path,
    out_path: Path,
    target_width: int = 1080,
    target_height: int = 1920,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Normalize and enhance a character or product reference image to 9:16 portrait.

    Applies letterboxing and subtle sharpening to provide the cleanest reference
    for video AI models.
    """
    image_path = Path(image_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    filter_graph = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:black,"
        "unsharp=5:5:0.8:3:3:0.4"
    )
    run_ffmpeg(ffmpeg_bin, [
        "-i", str(image_path),
        "-vf", filter_graph,
        "-q:v", "2",
        str(out_path),
    ])
    return out_path
