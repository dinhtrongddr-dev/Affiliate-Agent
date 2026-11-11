"""Scene-cut detection and candidate segment thumbnails for base videos."""
import re
import subprocess
from pathlib import Path

from .ffmpeg import run_ffmpeg

PTS = re.compile(r"pts_time:([0-9.]+)")


def detect_cuts(ffmpeg: str, source: Path, threshold: float = 0.32) -> list[float]:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(source), "-an", "-vf",
         f"scale=320:-2,select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    return sorted({round(float(m), 3) for m in PTS.findall(result.stderr or "")})


def build_segments(cuts: list[float], duration: float, max_len: float = 3.6,
                   min_len: float = 0.9, limit: int = 16) -> list[dict]:
    bounds = [0.0] + [c for c in cuts if 0.3 < c < duration - 0.3] + [duration]
    segments: list[dict] = []
    for start, end in zip(bounds, bounds[1:], strict=False):
        length = end - start
        if length < min_len:
            continue
        pieces = max(1, round(length / max_len))
        step = length / pieces
        for i in range(pieces):
            s = start + i * step
            segments.append({"start": round(s, 3), "duration": round(min(step, max_len + 0.6), 3)})
    if len(segments) > limit:  # keep an even spread across the whole video
        stride = len(segments) / limit
        segments = [segments[int(i * stride)] for i in range(limit)]
    for index, seg in enumerate(segments):
        seg["index"] = index
    return segments


def segment_thumbnails(ffmpeg: str, source: Path, segments: list[dict], out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for seg in segments:
        target = out_dir / f"seg_{seg['index']:02d}.jpg"
        t = seg["start"] + seg["duration"] / 2
        run_ffmpeg(ffmpeg, ["-ss", f"{t:.3f}", "-i", str(source), "-frames:v", "1",
                            "-vf", "scale=360:-2", "-q:v", "4", str(target)])
        paths.append(target)
    return paths
