"""Did the clip say the scripted line, in the character's voice?

- transcribe(): Vietnamese speech-to-text with PhoWhisper-small (CTranslate2 via faster-whisper)
- word_error_rate(): words wrong / words in the script line (case, punctuation-insensitive)
- voice_embedding() / voice_similarity(): WeSpeaker ResNet34 (ONNX) speaker vectors, cosine
Both models run on the CPU, load on first use and unload after 10 idle minutes.
"""
import logging
import re
import subprocess
import tempfile
import threading
import time
import unicodedata
from pathlib import Path

log = logging.getLogger("affiliate_agent.speech_check")

ASR_REPO = "qbsmlabs/PhoWhisper-small"                       # CTranslate2 conversion of vinai/PhoWhisper-small
SPK_REPO, SPK_FILE = "Wespeaker/wespeaker-voxceleb-resnet34-LM", "voxceleb_resnet34_LM.onnx"
IDLE_UNLOAD_SECONDS = 600
MIN_WORD_PROB = 0.5
MIN_WORD_SECONDS = 0.05

_lock = threading.Lock()
_models: dict[str, object] = {}
_last_used: dict[str, float] = {}


class SpeechCheckUnavailable(RuntimeError):
    pass


def _touch(key: str) -> None:
    _last_used[key] = time.monotonic()
    if not getattr(_touch, "reaper", None) or not _touch.reaper.is_alive():
        def loop():
            while _models:
                time.sleep(60)
                now = time.monotonic()
                with _lock:
                    for k in list(_models):
                        if now - _last_used.get(k, now) >= IDLE_UNLOAD_SECONDS:
                            _models.pop(k, None)
                            log.info("speech check: unloaded idle model %s", k)
        _touch.reaper = threading.Thread(target=loop, name="speech-check-reaper", daemon=True)
        _touch.reaper.start()


def _asr():
    with _lock:
        if "asr" not in _models:
            try:
                from faster_whisper import WhisperModel
                from huggingface_hub import snapshot_download
            except ImportError as exc:
                raise SpeechCheckUnavailable("Thiếu faster-whisper (chạy scripts\\setup.ps1)") from exc
            path = snapshot_download(ASR_REPO)
            _models["asr"] = WhisperModel(path, device="cpu", compute_type="int8", cpu_threads=2)
        _touch("asr")
        return _models["asr"]


def _speaker():
    with _lock:
        if "spk" not in _models:
            try:
                import onnxruntime as ort
                from huggingface_hub import hf_hub_download
            except ImportError as exc:
                raise SpeechCheckUnavailable("Thiếu onnxruntime") from exc
            path = hf_hub_download(SPK_REPO, SPK_FILE)
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            _models["spk"] = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
        _touch("spk")
        return _models["spk"]


def to_wav16k(source: Path, ffmpeg: str = "ffmpeg", start: float = 0.0, length: float | None = None) -> Path:
    """Mono 16 kHz wav of a clip's audio (or a part of it)."""
    out = Path(tempfile.mkdtemp(prefix="aa_sc_")) / "a.wav"
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, start):.3f}", "-i", str(source)]
    if length:
        args += ["-t", f"{length:.3f}"]
    args += ["-vn", "-ac", "1", "-ar", "16000", str(out)]
    subprocess.run(args, check=True, capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                   | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    return out


def transcribe(source: Path, ffmpeg: str = "ffmpeg") -> dict:
    """{"text", "start", "end"}: what is said in the clip and where the speech is."""
    import soundfile as sf
    wav = to_wav16k(Path(source), ffmpeg)
    audio, _sr = sf.read(str(wav), dtype="float32")   # array input: no PyAV decode (version-sensitive)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    segments, _info = _asr().transcribe(audio, language="vi", beam_size=3, vad_filter=True,
                                        condition_on_previous_text=False, word_timestamps=True)
    segs = list(segments)
    # Whisper "hears" words in room tone after the speech: they come with low probability and
    # ~0 s length. Keep only real words (measured on Muse clips: spoken words p=1.0, ghosts < 0.5).
    words = [w for s in segs for w in (s.words or [])
             if w.probability >= MIN_WORD_PROB and (w.end - w.start) >= MIN_WORD_SECONDS]
    text = " ".join(w.word.strip() for w in words).strip()
    start = float(words[0].start) if words else None
    end = float(words[-1].end) if words else None
    return {"text": text, "start": start, "end": end}


def _norm_words(text: str, keep_marks: bool = True) -> list[str]:
    text = unicodedata.normalize("NFC", str(text or "").lower())
    if not keep_marks:
        text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
        text = text.replace("đ", "d")
    return re.findall(r"[\w]+", text)


def word_error_rate(reference: str, hypothesis: str, keep_marks: bool = True) -> float:
    """Levenshtein over words / words in the reference (0 = exact, 1+ = nothing right)."""
    ref, hyp = _norm_words(reference, keep_marks), _norm_words(hypothesis, keep_marks)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return round(prev[-1] / len(ref), 3)


def line_match(reference: str, hypothesis: str, keep_marks: bool = True) -> tuple[float, int]:
    """(WER of the best-matching stretch of the hypothesis, extra words outside that stretch).

    Words heard before/after the line cost nothing in the WER (they are reported as extras), so
    a ghost word in the room tone does not fail a take that said the whole line."""
    ref, hyp = _norm_words(reference, keep_marks), _norm_words(hypothesis, keep_marks)
    if not ref:
        return (0.0, len(hyp))
    if not hyp:
        return (1.0, 0)
    # semi-global alignment: free start/end in the hypothesis; track where the match starts
    prev = [(0, j) for j in range(len(hyp) + 1)]          # (cost, start index in hyp)
    for i, r in enumerate(ref, 1):
        cur = [(i, 0)] + [(0, 0)] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min((prev[j][0] + 1, prev[j][1]), (cur[j - 1][0] + 1, cur[j - 1][1]),
                         (prev[j - 1][0] + (r != h), prev[j - 1][1]))
        prev = cur
    end = min(range(len(hyp) + 1), key=lambda j: (prev[j][0], -j))
    cost, start = prev[end]
    return round(cost / len(ref), 3), start + (len(hyp) - end)


def _fbank(wav: Path):
    """80-dim log-mel features close to Kaldi fbank (25 ms / 10 ms), mean-normalised."""
    import librosa
    import numpy as np
    import soundfile as sf
    audio, sr = sf.read(str(wav), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    mel = librosa.feature.melspectrogram(y=audio * 32768.0, sr=16000, n_fft=512, win_length=400, hop_length=160,
                                         n_mels=80, window="hamming", power=2.0, center=False)
    feats = np.log(np.maximum(mel, 1e-10)).T.astype("float32")
    return feats - feats.mean(axis=0, keepdims=True)


def voice_embedding(source: Path, ffmpeg: str = "ffmpeg", start: float = 0.0, length: float | None = None):
    import numpy as np
    src = Path(source)
    wav = src if src.suffix.lower() == ".wav" and not start and not length else to_wav16k(src, ffmpeg, start, length)
    feats = _fbank(wav)
    if len(feats) < 50:
        raise ValueError("Đoạn tiếng quá ngắn để so giọng")
    sess = _speaker()
    out = sess.run(None, {sess.get_inputs()[0].name: feats[None, :, :]})[0][0]
    return out / (np.linalg.norm(out) + 1e-9)


def voice_similarity(a, b) -> float:
    import numpy as np
    return round(float(np.dot(a, b)), 3)


def check_line(clip: Path, line: str, voice_ref=None, ffmpeg: str = "ffmpeg") -> dict:
    """One spoken take: transcript, WER (with and without diacritics), voice similarity."""
    heard = transcribe(clip, ffmpeg)
    wer, extra = line_match(line, heard["text"])
    result = {"heard": heard["text"], "wer": wer, "extra_words": extra,
              "wer_plain": line_match(line, heard["text"], keep_marks=False)[0],
              "wer_full": word_error_rate(line, heard["text"]),
              "speech_start": heard["start"], "speech_end": heard["end"]}
    if voice_ref is not None and heard["start"] is not None:
        try:
            emb = voice_embedding(clip, ffmpeg, heard["start"], max(0.8, (heard["end"] or 0) - heard["start"]))
            result["voice_sim"] = voice_similarity(emb, voice_ref)
        except Exception as exc:  # noqa: BLE001
            result["voice_error"] = str(exc)[:200]
    return result
