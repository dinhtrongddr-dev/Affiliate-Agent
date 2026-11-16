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
                   min_len: float = 0.35, max_gap: float = 0.3) -> list[tuple[float, float]]:
    """Stretches where someone speaks in the clip's own audio, relative to ``start``.

    AI clips carry room tone or music, so a fixed silence threshold sees one long "speech".
    The audio is band-passed to the voice range and each 50 ms frame is compared with the
    clip's own noise floor (20th percentile) instead.
    """
    try:
        import numpy as np
        raw = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", str(clip), "-vn", "-ac", "1", "-ar", "8000",
             "-af", "highpass=f=250,lowpass=f=3400", "-f", "s16le", "-"],
            capture_output=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError, ImportError):
        return []
    if len(raw) < 1600:
        return []
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    hop = 400  # 50 ms at 8 kHz
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-3)
    floor, peak = float(np.percentile(db, 20)), float(np.percentile(db, 95))
    if peak - floor < 6:          # flat audio: no clear speech to follow
        return []
    active = db > floor + max(6.0, (peak - floor) * 0.45)
    windows, begin, gap = [], None, 0
    for i, on in enumerate(active):
        if on:
            begin = i if begin is None else begin
            gap = 0
        elif begin is not None:
            gap += 1
            if gap * 0.05 > max_gap:
                windows.append((begin * 0.05, (i - gap + 1) * 0.05))
                begin, gap = None, 0
    if begin is not None:
        windows.append((begin * 0.05, (len(active) - gap) * 0.05))
    return [(round(w0, 3), round(w1, 3)) for w0, w1 in windows if w1 - w0 >= min_len]


def _tts_key(settings, text: str, voice: str) -> str:
    import hashlib
    material = "|".join([text, voice, str(settings.tts_rate), str(getattr(settings, "tts_mode", "fast")),
                         str(getattr(settings, "tts_precision", "auto"))])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def cached_tts(settings, text: str, voice: str, out: Path) -> Speech:
    """VieNeu on this CPU takes ~6 s per line: identical (text, voice, rate) lines are made once
    and reused, e.g. prepared while Muse is still generating, or on a re-render."""
    import json
    import shutil
    cache = Path(settings.work_dir) / "_tts_cache"
    key = _tts_key(settings, text, voice)
    wav, meta = cache / f"{key}.wav", cache / f"{key}.json"
    if wav.is_file() and meta.is_file():
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(wav, out)
        duration = float(json.loads(meta.read_text(encoding="utf-8"))["duration"])
        return Speech(out, duration, evenly_timed_words(text, 0.0, duration))
    speech = synthesize(text, out, voice, settings.tts_rate, settings.ffprobe_path,
                        mode=getattr(settings, "tts_mode", "fast"),
                        precision=getattr(settings, "tts_precision", "auto"))
    if speech.audio_path and Path(speech.audio_path).is_file():
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(speech.audio_path, wav)
        meta.write_text(json.dumps({"duration": speech.duration}), encoding="utf-8")
    return speech


def prepare_voices(settings, timeline: dict, work_dir: Path) -> int:
    """Synthesize every line now (while Muse works) so the render only reads the cache."""
    from .lexicon import load, spoken
    lexicon = load(getattr(settings, "project_root", None))
    cast = timeline.get("voice_cast") or {}
    made = 0
    for i, scene in enumerate(timeline.get("scenes") or []):
        lines = [ln for ln in scene.get("lines") or [] if str(ln.get("text") or "").strip()]
        if not lines and scene.get("voiceover"):
            lines = [{"speaker": "narrator", "text": scene["voiceover"]}]
        for k, line in enumerate(lines):
            voice = (cast.get(line.get("speaker") or "narrator") or {}).get("voice") or settings.tts_voice
            cached_tts(settings, spoken(line["text"], lexicon), voice,
                       Path(work_dir) / f"prep_{i:02d}_{k}.wav")
            made += 1
    return made


def fit_lines(durations: list[float], windows: list[tuple[float, float]]) -> dict[int, tuple[float, float]]:
    """Each line takes the next speech stretch(es): the run of windows whose total span best
    matches the voice. Speed is changed only within FIT_MIN..FIT_MAX; a voice up to 40 %
    shorter than the mouth movement plays at normal speed from the first window.
    Returns {line index: (start, tempo)} for the lines that fit."""
    out, w = {}, 0
    for j, dur in enumerate(durations):
        if w >= len(windows):
            break
        best = None
        for end_i in range(w, len(windows)):
            span = max(0.3, windows[end_i][1] - windows[w][0])
            tempo = dur / span
            if tempo < 0.6:
                break
            score = abs(1 - min(max(tempo, FIT_MIN), FIT_MAX)) + abs(tempo - min(max(tempo, FIT_MIN), FIT_MAX)) * 3
            if best is None or score < best[0]:
                best = (score, end_i, tempo)
        if best is None:
            break
        _, end_i, tempo = best
        if FIT_MIN <= tempo <= FIT_MAX:
            out[j] = (windows[w][0], round(tempo, 4))
        elif 0.6 <= tempo < FIT_MIN:
            out[j] = (windows[w][0], 1.0)
        w = end_i + 1
    return out


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
    native = scene.get("native_voice")
    if native and clip_window:
        # Muse already said this line in the clip (checked by speech_check): no TTS; the
        # subtitles follow the speech the check found. The clip's own audio is kept by render.
        _clip, start, _length = clip_window
        begin = max(0.0, float(native["start"]) - start)
        dur = max(0.3, float(native["end"]) - float(native["start"]))
        text = " ".join(ln["text"] for ln in lines)
        result.parts.append(VoicePart(None, round(begin, 3), round(dur, 3),
                                      evenly_timed_words(text, 0.0, dur), lines[0].get("speaker") or "narrator"))
        result.revoice = "native"
        return result
    ffmpeg = getattr(settings, "ffmpeg_path", "ffmpeg")
    raw: list[tuple[dict, Speech, float]] = []
    from .lexicon import load, spoken
    lexicon = load(getattr(settings, "project_root", None))
    for k, line in enumerate(lines):
        info = cast.get(line.get("speaker") or "narrator") or {}
        voice = info.get("voice") or settings.tts_voice
        out = Path(work_dir) / f"voice_{index:02d}_{k}.wav"
        if settings.tts_enabled:
            # the voice reads brands/units the Vietnamese way; subtitles keep the original words
            speech = cached_tts(settings, spoken(line["text"], lexicon), voice, out)
            speech = Speech(speech.audio_path, speech.duration,
                            evenly_timed_words(line["text"], 0.0, speech.duration))
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
            fitted = fit_lines([raw[k][1].duration for k in on_cam], windows)
            fitted = {on_cam[j]: v for j, v in fitted.items()}
            ok = True
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
