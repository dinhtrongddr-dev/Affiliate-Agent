"""Vietnamese voice-over with VieNeu-TTS (pnnbao97/VieNeu-TTS).

Supports:
- Fast mode: VieNeu Nano (v3nano - 24 kHz, lightweight, ultra-fast on CPU).
- Quality mode: VieNeu Turbo (v3turbo - 48 kHz, high-fidelity neural codec).
  Automatically selects INT8 if CPU supports VNNI (AVX-512 VNNI / AVX-VNNI),
  otherwise falls back to FP32 for maximum naturalness without distortion.
"""
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("affiliate_agent.tts")

VOICES = {
    "Mai Anh": "Nữ - Mai Anh (Miền Bắc, chuẩn)",
    "Minh Quân": "Nam - Minh Quân (Miền Nam, tự nhiên)",
    "Trúc Ly": "Nữ - Trúc Ly (Miền Nam, truyền cảm)",
    "Ái Hân": "Nữ - Ái Hân (Nhẹ nhàng, review)",
    "Mỹ Duyên": "Nữ - Mỹ Duyên (Dịu dàng, ngọt ngào)",
    "Đức Trí": "Nam - Đức Trí (Trầm ấm, thuyết minh)",
    "Hữu Quân": "Nam - Hữu Quân (Mạnh mẽ, rõ ràng)",
    "Xuân Tiên": "Nữ - Xuân Tiên (Biểu cảm, kể chuyện)",
    "Anh Khôi": "Nam - Anh Khôi (Trẻ trung, tự nhiên)",
    "Mạnh Dũng": "Nam - Mạnh Dũng (Chững chạc, tin cậy)",
    "Adam": "Nam - Adam (Năng động, bán hàng)",
}
DEFAULT_VOICE = "Mai Anh"

# Backward compatibility with legacy Edge-TTS names and missing aliases
LEGACY_VOICE_MAP = {
    "vi-VN-HoaiMyNeural": "Mai Anh",
    "vi-VN-NamMinhNeural": "Minh Quân",
    "Hải Đăng": "Đức Trí",
    "Thùy Dung": "Trúc Ly",
    "Thiện Minh": "Minh Quân",
}


@dataclass
class Word:
    text: str
    start: float
    end: float


@dataclass
class Speech:
    audio_path: Path | None
    duration: float
    words: list[Word] = field(default_factory=list)


def estimate_duration(text: str) -> float:
    """~2.9 spoken Vietnamese words per second at a standard/fast pace."""
    words = len((text or "").split())
    return max(1.2, words / 2.9)


def evenly_timed_words(text: str, start: float, duration: float) -> list[Word]:
    tokens = (text or "").split()
    if not tokens:
        return []
    step = duration / len(tokens)
    return [
        Word(tok, round(start + i * step, 3), round(start + (i + 1) * step, 3))
        for i, tok in enumerate(tokens)
    ]


def cpu_supports_vnni() -> bool:
    """Check if host CPU supports Vector Neural Network Instructions (VNNI)."""
    try:
        import cpuinfo
        flags = [str(f).lower() for f in cpuinfo.get_cpu_info().get("flags", [])]
        return any("vnni" in f for f in flags)
    except Exception:
        return False


_engines: dict[str, Any] = {}
_engine_lock = threading.Lock()


def get_engine(mode: str = "fast", precision: str = "auto") -> tuple[Any, str]:
    """Return (engine_instance, effective_mode).

    - Fast: 'v3nano' (24 kHz)
    - Quality: 'v3turbo' (48 kHz) with precision chosen by VNNI support.
    """
    m = (mode or "fast").strip().lower()
    if m in ("fast", "nano", "v3nano"):
        engine_mode = "v3nano"
        cache_key = "v3nano"
        prec = None
    else:
        engine_mode = "v3turbo"
        p = (precision or "auto").strip().lower()
        if p in ("int8", "fp32"):
            selected_precision = p
        else:
            selected_precision = "int8" if cpu_supports_vnni() else "fp32"
        cache_key = f"v3turbo_{selected_precision}"
        prec = selected_precision

    with _engine_lock:
        if cache_key not in _engines:
            from vieneu import Vieneu
            log.info("Loading VieNeu-TTS engine: mode=%s, precision=%s", engine_mode, prec)
            if engine_mode == "v3nano":
                _engines[cache_key] = Vieneu(mode="v3nano")
            else:
                _engines[cache_key] = Vieneu(mode="v3turbo", precision=prec)
        return _engines[cache_key], engine_mode


def warmup_engine(mode: str = "fast", precision: str = "auto") -> None:
    """Preload engine in a background thread to eliminate first-call cold-start latency."""
    try:
        log.info("Warming up VieNeu-TTS engine in background: mode=%s", mode)
        get_engine(mode=mode, precision=precision)
    except Exception as exc:
        log.debug("Warmup VieNeu-TTS engine failed: %s", exc)


def parse_rate(rate: str | float | int | None) -> float:
    """Parse rate string or number into a float speed factor (e.g. '+15%' -> 1.15, '1.2' -> 1.2).

    Clamped to safe speech range [0.5, 2.0].
    """
    if rate is None:
        return 1.0
    if isinstance(rate, (int, float)):
        return max(0.5, min(2.0, float(rate)))
    s = str(rate).strip()
    if not s or s in ("+0%", "-0%", "0%", "0"):
        return 1.0
    if s.endswith("%"):
        try:
            val = float(s[:-1].strip().lstrip("+"))
            return max(0.5, min(2.0, round(1.0 + (val / 100.0), 3)))
        except ValueError:
            return 1.0
    try:
        val = float(s.lstrip("+"))
        return max(0.5, min(2.0, round(val, 3)))
    except ValueError:
        return 1.0


def _apply_speed(audio, speed_factor: float):
    """Adjust audio speed while preserving pitch using librosa."""
    if abs(speed_factor - 1.0) < 0.01:
        return audio
    try:
        import librosa
        import numpy as np
        return librosa.effects.time_stretch(np.ascontiguousarray(audio, dtype=np.float32), rate=float(speed_factor))
    except Exception as exc:
        log.warning("librosa time_stretch failed (%s); using original audio", exc)
        return audio


def _infer_audio(engine, engine_mode: str, text: str, voice: str, speed_factor: float):
    """Infer speech from engine and apply speed factor appropriately."""
    if engine_mode == "v3nano":
        try:
            return engine.infer(text, voice=voice, speed=speed_factor)
        except (TypeError, Exception) as exc:
            log.debug("v3nano infer with speed failed (%s); falling back to post-processing", exc)
            audio = engine.infer(text, voice=voice)
            return _apply_speed(audio, speed_factor)
    else:
        audio = engine.infer(text, voice=voice)
        return _apply_speed(audio, speed_factor)


def synthesize(
    text: str,
    out: Path,
    voice: str = "Mai Anh",
    rate: str = "+0%",
    ffprobe: str = "ffprobe",
    mode: str = "fast",
    precision: str = "auto",
) -> Speech:
    """Synthesize speech audio using VieNeu-TTS.

    Saves audio to `out` (mp3/wav) and returns a Speech dataclass with word timings.
    """
    from .ffmpeg import media_duration

    text = " ".join((text or "").split())
    if not text:
        return Speech(None, 0.0, [])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    voice = LEGACY_VOICE_MAP.get(voice, voice)
    if voice not in VOICES:
        voice = DEFAULT_VOICE

    try:
        engine, engine_mode = get_engine(mode=mode, precision=precision)
        speed_factor = parse_rate(rate)
        audio = _infer_audio(engine, engine_mode, text, voice, speed_factor)
        sr = getattr(engine, "sample_rate", 24000 if engine_mode == "v3nano" else 48000)

        # Write audio file
        import soundfile as sf
        try:
            sf.write(str(out), audio, sr)
        except Exception:
            temp_wav = out.with_suffix(".tmp.wav")
            sf.write(str(temp_wav), audio, sr)
            import subprocess
            ffmpeg_exe = str(ffprobe).lower().replace("ffprobe.exe", "ffmpeg.exe").replace("ffprobe", "ffmpeg")
            subprocess.run(
                [ffmpeg_exe, "-y", "-i", str(temp_wav), str(out)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            temp_wav.unlink(missing_ok=True)

        duration = media_duration(ffprobe, out)
        if duration <= 0:
            duration = max(0.8, len(audio) / sr)
        words = evenly_timed_words(text, 0.0, duration)
        return Speech(out, duration, words)
    except Exception as exc:
        log.warning("VieNeu-TTS failed (%s); continuing with on-screen text only", exc)
        out.unlink(missing_ok=True)
        duration = estimate_duration(text)
        return Speech(None, duration, evenly_timed_words(text, 0.0, duration))


def synthesize_bytes(
    text: str,
    voice: str = "Mai Anh",
    mode: str = "fast",
    precision: str = "auto",
    rate: str = "+0%",
) -> tuple[bytes, str]:
    """Synthesize speech directly into RAM as a WAV byte buffer (Zero-Waste)."""
    import io
    import soundfile as sf

    text = " ".join((text or "").split())
    if not text:
        text = "Xin chào, đây là giọng đọc thử nghiệm."

    voice = LEGACY_VOICE_MAP.get(voice, voice)
    if voice not in VOICES:
        voice = DEFAULT_VOICE

    engine, engine_mode = get_engine(mode=mode, precision=precision)
    speed_factor = parse_rate(rate)
    audio = _infer_audio(engine, engine_mode, text, voice, speed_factor)
    sr = getattr(engine, "sample_rate", 24000 if engine_mode == "v3nano" else 48000)

    bio = io.BytesIO()
    sf.write(bio, audio, sr, format="WAV")
    return bio.getvalue(), "audio/wav"
