import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .db import register_job


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".wmv"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_until_stable(path: Path, stable_seconds: float = 3.0, timeout_seconds: int = 300) -> bool:
    deadline = time.monotonic() + timeout_seconds
    previous_size = -1
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        if not path.exists() or not path.is_file():
            return False
        size = path.stat().st_size
        if size > 0 and size == previous_size:
            if time.monotonic() - stable_since >= stable_seconds:
                return True
        else:
            previous_size = size
            stable_since = time.monotonic()
        time.sleep(1)
    return False


def probe_video(path: Path, ffprobe_path: str = "ffprobe") -> dict[str, Any]:
    command = [
        ffprobe_path, "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    if not streams:
        raise ValueError("No video stream found")
    stream = streams[0]
    duration = float((data.get("format") or {}).get("duration") or 0)
    return {
        "duration_sec": duration,
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": str(stream.get("r_frame_rate") or ""),
    }


def register_video(path: Path, settings, allow_deleted: bool = False) -> tuple[bool, dict[str, Any]]:
    path = path.resolve()
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False, {"path": str(path), "reason": "unsupported_extension"}
    if not wait_until_stable(
        path, settings.stable_file_seconds, settings.file_stable_timeout_seconds
    ):
        return False, {"path": str(path), "reason": "file_not_stable_or_missing"}

    metadata = probe_video(path, settings.ffprobe_path)
    digest = sha256_file(path)
    job = {
        "id": digest[:24],
        "source_path": str(path),
        "sha256": digest,
        "file_name": path.name,
        "allow_deleted": allow_deleted,
        **metadata,
    }
    if allow_deleted:
        from .db import forget_deleted_source
        forget_deleted_source(settings.database_path, digest)
    inserted = register_job(settings.database_path, job)
    return inserted, {
        "job_id": job["id"],
        "inserted": inserted,
        "file_name": path.name,
        **metadata,
    }


def scan_inbox(settings) -> list[dict[str, Any]]:
    settings.inbox.mkdir(parents=True, exist_ok=True)
    results = []
    for path in sorted(settings.inbox.rglob("*")):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            try:
                _, result = register_video(path, settings)
            except Exception as exc:
                result = {"path": str(path), "reason": "probe_failed", "error": str(exc)}
            results.append(result)
    return results


def extract_keyframes(
    path: Path,
    output_dir: Path,
    ffmpeg_path: str = "ffmpeg",
    count: int = 3,
    ffprobe_path: str = "ffprobe",
) -> list[Path]:
    """Extract evenly spaced review frames without changing the source video."""
    path = Path(path).resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = probe_video(path, ffprobe_path)
    duration = float(metadata["duration_sec"])
    if duration <= 0:
        raise ValueError("Video duration is invalid")

    count = max(1, min(int(count), 6))
    fractions = [0.15] if count == 1 else [
        0.1 + (0.8 * index / (count - 1)) for index in range(count)
    ]
    outputs: list[Path] = []
    for index, fraction in enumerate(fractions, start=1):
        timestamp = min(duration * fraction, max(0.0, duration - 0.05))
        destination = output_dir / f"frame_{index:02d}.jpg"
        command = [
            ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{timestamp:.3f}", "-i", str(path),
            "-frames:v", "1",
            "-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease",
            "-q:v", "3", str(destination),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "no frame produced")[-500:]
            raise RuntimeError(f"Could not extract keyframe {index}: {detail}")
        outputs.append(destination)
    return outputs
