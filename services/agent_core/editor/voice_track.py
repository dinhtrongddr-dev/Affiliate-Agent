"""Per-scene voice: one VieNeu voice per speaker, child pitch, and re-voicing of talking AI shots.

Re-voicing (method A): a talking AI shot was generated with the character mouthing the line.
We detect where the clip's own (discarded) speech is, then fit each TTS line into that window
so the dubbed Vietnamese starts and stops with the mouth. Lines that cannot fit within
+/-25 % speed fall back to normal placement and the scene is flagged for review.
"""
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .ffmpeg import media_duration, run_ffmpeg
from .tts import Speech, Word, estimate_duration, evenly_timed_words, synthesize

log = logging.getLogger("affiliate_agent.voice")

LINE_GAP = 0.12
FIT_MIN, FIT_MAX = 0.8, 1.25


@dataclass
class VoicePart:
    audio: Path | None
    offset: float          # seconds from the scene start
    duration: float
    words: list[Word]
    speaker: str = "narrator"


@dataclass
class SceneVoice:
    parts: list[VoicePart] = field(default_factory=list)
    revoice: str = ""      # "" | "fitted" | "unfitted" | "no_speech_found"

    @property
    def end(self) -> float:
        return max((p.offset + p.duration for p in self.parts), default=0.0)

    @property
    def words(self) -> list[Word]:
        out = []
        for p in self.parts:
            out += [Word(w.text, w.start + p.offset, w.end + p.offset) for w in p.words]
        return out


def _ffmpeg_from(ffprobe: str) -> str:
    low = str(ffprobe)
    return re.sub(r"ffprobe(\.exe)?$", lambda m: "ffmpeg" + (m.group(1) or ""), low, flags=re.I)


def _retime(ffmpeg: str, src: Path, out: Path, tempo: float = 1.0, pitch: float = 1.0) -> Path:
    """Speed change (tempo>1 = shorter) and/or pitch lift without changing speed."""
    filters = []
    if abs(pitch - 1.0) > 0.01:
        filters += [f"asetrate=44100*{pitch:.3f}", "aresample=44100", f"atempo={1 / pitch:.4f}"]
    if abs(tempo - 1.0) > 0.01:
        filters.append(f"atempo={tempo:.4f}")
    run_ffmpeg(ffmpeg, ["-i", str(src), "-af", "aresample=44100," + ",".join(filters) if filters
                        else "aresample=44100", str(out)])
    return out


def speech_windows(ffmpeg: str, clip: Path, start: float, length: float,
                   noise_db: int = -32, min_silence: float = 0.25) -> list[tuple[float, float]]:
    """Non-silent stretches of the clip's own audio in [start, start+length], relative times."""
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
           "-i", str(clip), "-vn", "-af", f"silencedetect=n={noise_db}dB:d={min_silence}",
           "-f", "null", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=120).stderr
    except (OSError, subprocess.SubprocessError):
        return []
    if "Audio:" not in out:
        return []
    silences, current = [], None
    for line in out.splitlines():
        if m := re.search(r"silence_start: (-?[\d.]+)", line):
            current = max(0.0, float(m.group(1)))
        elif (m := re.search(r"silence_end: ([\d.]+)", line)) and current is not None:
            silences.append((current, float(m.group(1))))
            current = None
    if current is not None:
        silences.append((current, length))
    windows, cursor = [], 0.0
    for s0, s1 in silences:
        if s0 - cursor >= 0.35:
            windows.append((round(cursor, 3), round(s0, 3)))
        cursor = s1
    if length - cursor >= 0.35:
        windows.append((round(cursor, 3), round(length, 3)))
    return windows


def scene_voice(settings, scene: dict, cast: dict, work_dir: Path, index: int,
                clip_window: tuple[Path, float, float] | None = None) -> SceneVoice:
    """Synthesize every line of a scene with its speaker's voice.

    clip_window: (video, start, usable_length) of a talking AI shot to re-voice.
    """
    lines = [ln for ln in scene.get("lines") or [] if str(ln.get("text") or "").strip()]
    if not lines and scene.get("voiceover"):
        lines = [{"speaker": "narrator", "text": scene["voiceover"]}]
    result = SceneVoice()
    if not lines:
        return result
    ffmpeg = getattr(settings, "ffmpeg_path", "ffmpeg")
    raw: list[tuple[dict, Speech, float]] = []
    for k, line in enumerate(lines):
        info = cast.get(line.get("speaker") or "narrator") or {}
        voice = info.get("voice") or settings.tts_voice
        out = Path(work_dir) / f"voice_{index:02d}_{k}.wav"
        if settings.tts_enabled:
            speech = synthesize(line["text"], out, voice, settings.tts_rate, settings.ffprobe_path,
                                mode=getattr(settings, "tts_mode", "fast"),
                                precision=getattr(settings, "tts_precision", "auto"))
        else:
            est = estimate_duration(line["text"])
            speech = Speech(None, est, evenly_timed_words(line["text"], 0.0, est))
        raw.append((line, speech, float(info.get("pitch") or 1.0)))

    # Re-voice: on-camera lines go where the generated mouth actually moves.
    fitted: dict[int, tuple[float, float]] = {}
    if clip_window and scene.get("talking") and getattr(settings, "voice_revoice", True):
        clip, start, length = clip_window
        on_cam = [k for k, (ln, _, _) in enumerate(raw) if ln.get("speaker") in (scene.get("cast") or [])]
        windows = speech_windows(ffmpeg, clip, start, length)
        if not windows:
            result.revoice = "no_speech_found"
        else:
            if len(windows) > len(on_cam) and on_cam:   # merge extra windows into the last line
                windows = windows[:len(on_cam) - 1] + [(windows[len(on_cam) - 1][0], windows[-1][1])]
            ok = True
            for k, (w0, w1) in zip(on_cam, windows, strict=False):
                speech = raw[k][1]
                tempo = speech.duration / max(0.3, w1 - w0)
                if FIT_MIN <= tempo <= FIT_MAX:
                    fitted[k] = (w0, tempo)
                else:
                    ok = False
            result.revoice = "fitted" if ok and len(fitted) == len(on_cam) else "unfitted"

    cursor = 0.12
    for k, (line, speech, pitch) in enumerate(raw):
        tempo = fitted.get(k, (None, 1.0))[1]
        audio, duration = speech.audio_path, speech.duration
        if audio and (abs(pitch - 1) > 0.01 or abs(tempo - 1) > 0.01):
            try:
                audio = _retime(ffmpeg, Path(audio), Path(audio).with_name(Path(audio).stem + "_fx.wav"),
                                tempo, pitch)
                duration = media_duration(settings.ffprobe_path, audio) or duration / tempo
            except Exception as exc:  # noqa: BLE001 - keep the plain voice
                log.warning("voice retime failed: %s", exc)
                audio, tempo = speech.audio_path, 1.0
        offset = fitted[k][0] if k in fitted else cursor
        scale = duration / speech.duration if speech.duration else 1.0
        words = [Word(w.text, w.start * scale, w.end * scale) for w in speech.words]
        result.parts.append(VoicePart(Path(audio) if audio else None, round(offset, 3),
                                      round(duration, 3), words, line.get("speaker") or "narrator"))
        cursor = max(cursor, offset + duration + LINE_GAP)
    return result
