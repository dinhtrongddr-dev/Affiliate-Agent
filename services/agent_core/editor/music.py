"""Background music picker. Only tracks the user owns/licenses (music folder)."""
import hashlib
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}


def list_tracks(music_dir: Path) -> list[Path]:
    music_dir = Path(music_dir)
    if not music_dir.is_dir():
        return []
    return sorted(p for p in music_dir.rglob("*") if p.suffix.lower() in AUDIO_EXTENSIONS)


def pick_track(music_dir: Path, mood: str = "", seed: str = "") -> Path | None:
    """Prefer files whose name/folder contains the mood word (e.g. upbeat, chill)."""
    tracks = list_tracks(music_dir)
    if not tracks:
        return None
    mood_words = [w for w in (mood or "").lower().replace(",", " ").split() if len(w) > 2]
    matching = [t for t in tracks if any(w in str(t).lower() for w in mood_words)]
    pool = matching or tracks
    index = int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(pool) if seed else 0
    return pool[index]
