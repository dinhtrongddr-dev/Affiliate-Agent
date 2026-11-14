"""Video encoder choice: NVIDIA NVENC when it works on this machine, else x264.

Measured on the owner's PC (i5-4590 + GTX 750 Ti), 10 s of 1080x1920:
libx264 fast 26.3 s, libx264 veryfast 13.0 s, h264_nvenc p4 6.1 s.
NVENC is probed once with a tiny real encode (driver/session problems show up there),
and AA_VIDEO_ENCODER=x264 forces the CPU path.
"""
import logging
import subprocess

log = logging.getLogger("affiliate_agent.encoder")

_PROBED: dict[str, bool] = {}


def nvenc_available(ffmpeg: str) -> bool:
    if ffmpeg in _PROBED:
        return _PROBED[ffmpeg]
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             "testsrc2=size=256x256:rate=30", "-t", "0.3", "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=30)
        ok = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    _PROBED[ffmpeg] = ok
    log.info("NVENC %s", "available: using the GPU encoder" if ok else "not available: using libx264")
    return ok


def use_nvenc(settings) -> bool:
    choice = str(getattr(settings, "video_encoder", "auto") or "auto").lower()
    if choice in {"x264", "cpu", "libx264"}:
        return False
    return nvenc_available(getattr(settings, "ffmpeg_path", "ffmpeg"))


def video_args(settings, role: str = "final", keyframe_at: float | None = None) -> list[str]:
    """Encoder arguments. role: "final" (what viewers see) or "intermediate" (re-encoded later).
    keyframe_at forces an IDR frame there, so a later cut at that time needs no re-encode."""
    nvenc = use_nvenc(settings)
    if nvenc:
        args = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-b:v", "0",
                "-cq", "21" if role == "final" else "19", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    else:
        args = ["-c:v", "libx264", "-preset", "veryfast" if role == "final" else "ultrafast",
                "-crf", "20" if role == "final" else "18", "-pix_fmt", "yuv420p"]
        if role == "final":
            args += ["-profile:v", "high"]
    if keyframe_at is not None:
        args += ["-force_key_frames", f"{keyframe_at:.3f}"] + (["-forced-idr", "1"] if nvenc else [])
    return args
