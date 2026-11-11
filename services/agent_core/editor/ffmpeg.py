import json
import logging
import subprocess
from pathlib import Path

log = logging.getLogger("affiliate_agent.ffmpeg")


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(ffmpeg: str, args: list[str], cwd: Path | None = None, timeout: int = 1800) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(map(str, args)))
    result = subprocess.run(
        command, cwd=str(cwd) if cwd else None, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
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
