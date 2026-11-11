"""Vietnamese voice-over with word timings (Microsoft Edge neural voices)."""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("affiliate_agent.tts")

VOICES = {
    "vi-VN-HoaiMyNeural": "Nữ - Hoài My",
    "vi-VN-NamMinhNeural": "Nam - Nam Minh",
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
    """~2.9 spoken Vietnamese words per second at a slightly fast pace."""
    words = len(text.split())
    return max(1.2, words / 2.9)


def evenly_timed_words(text: str, start: float, duration: float) -> list[Word]:
    tokens = text.split()
    if not tokens:
        return []
    step = duration / len(tokens)
    return [Word(tok, start + i * step, start + (i + 1) * step) for i, tok in enumerate(tokens)]


async def _synthesize(text: str, voice: str, rate: str, out: Path) -> list[Word]:
    import edge_tts

    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")
    except TypeError:  # older edge-tts without the boundary argument
        communicate = edge_tts.Communicate(text, voice, rate=rate)
    words: list[Word] = []
    with out.open("wb") as stream:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                stream.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                end = start + chunk["duration"] / 10_000_000
                words.append(Word(chunk["text"], start, end))
    return words


def synthesize(text: str, out: Path, voice: str, rate: str, ffprobe: str) -> Speech:
    """Return speech audio + word timings, or a silent estimate when TTS is unavailable."""
    from .ffmpeg import media_duration

    text = " ".join((text or "").split())
    if not text:
        return Speech(None, 0.0, [])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        words: list[Word] = []
        last_error: Exception | None = None
        # The Edge TTS service intermittently returns "No audio was received".
        for attempt in range(5):
            try:
                words = asyncio.run(_synthesize(text, voice, rate, out))
                if out.is_file() and out.stat().st_size >= 1000:
                    last_error = None
                    break
                last_error = RuntimeError("TTS produced no audio")
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
            time.sleep(0.8 * (attempt + 1))
        if last_error:
            raise last_error
        duration = media_duration(ffprobe, out)
        if not words:
            words = evenly_timed_words(text, 0.0, duration)
        return Speech(out, duration, words)
    except Exception as exc:
        log.warning("TTS unavailable (%s); continuing with on-screen text only", exc)
        out.unlink(missing_ok=True)
        duration = estimate_duration(text)
        return Speech(None, duration, evenly_timed_words(text, 0.0, duration))
