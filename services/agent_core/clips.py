"""Per-video clip library: footage the user adds by hand (Muse/Meta AI exports,
CapCut, phone recordings, product photos). Clips become scene sources.

Layout: <media>/assets/clips/<job_id>/<file> plus clips.json with metadata.
"""
import json
import re
import shutil
import uuid
from pathlib import Path

from .editor.ffmpeg import probe, run_ffmpeg
from .media import VIDEO_EXTENSIONS

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
MAX_CLIPS = 40


class ClipError(RuntimeError):
    pass


def clip_dir(settings, job_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", job_id or ""):
        raise ClipError("Invalid job id")
    return Path(settings.assets) / "clips" / job_id


def _meta_path(settings, job_id: str) -> Path:
    return clip_dir(settings, job_id) / "clips.json"


def list_clips(settings, job_id: str) -> list[dict]:
    path = _meta_path(settings, job_id)
    if not path.is_file():
        return []
    try:
        clips = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    base = clip_dir(settings, job_id)
    return [c for c in clips if (base / c["file"]).is_file()]


def _save(settings, job_id: str, clips: list[dict]) -> None:
    path = _meta_path(settings, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clips, ensure_ascii=False, indent=2), encoding="utf-8")


def add_clip(settings, job_id: str, filename: str, stream, ai_generated: bool = False,
             note: str = "") -> dict:
    """Store an uploaded file (binary stream) and probe it. Returns clip metadata."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ClipError(f"Định dạng không hỗ trợ: {ext or '?'}")
    clips = list_clips(settings, job_id)
    if len(clips) >= MAX_CLIPS:
        raise ClipError(f"Tối đa {MAX_CLIPS} clip mỗi video")
    base = clip_dir(settings, job_id)
    base.mkdir(parents=True, exist_ok=True)
    clip_id = uuid.uuid4().hex[:10]
    target = base / f"{clip_id}{ext}"
    with target.open("wb") as out:
        shutil.copyfileobj(stream, out, length=1024 * 1024)
    kind = "image" if ext in IMAGE_EXTENSIONS else "video"
    try:
        meta = probe(settings.ffprobe_path, target)
    except Exception as exc:  # noqa: BLE001
        target.unlink(missing_ok=True)
        raise ClipError(f"File hỏng hoặc không đọc được: {exc}") from exc
    if kind == "video" and (not meta["has_video"] or meta["duration_sec"] <= 0.3):
        target.unlink(missing_ok=True)
        raise ClipError("Video không có hình hoặc quá ngắn")
    if kind == "image":
        thumb = target  # the photo is its own preview
    else:
        thumb = base / f"{clip_id}_thumb.jpg"
        at = min(0.5, meta["duration_sec"] / 2)
        try:
            run_ffmpeg(settings.ffmpeg_path, ["-ss", f"{at:.2f}", "-i", str(target),
                                              "-frames:v", "1", "-update", "1",
                                              "-vf", "scale=240:-2", "-q:v", "5", str(thumb)])
        except Exception:  # noqa: BLE001
            thumb = None
    clip = {
        "id": clip_id, "file": target.name, "name": Path(filename).name[:120], "kind": kind,
        "duration": round(meta["duration_sec"], 2) if kind == "video" else None,
        "width": meta["width"], "height": meta["height"], "has_audio": meta["has_audio"],
        "ai_generated": bool(ai_generated), "note": note[:200],
        "thumb": thumb.name if thumb and thumb.is_file() else None,
    }
    _save(settings, job_id, [*clips, clip])
    return clip


def update_clip(settings, job_id: str, clip_id: str, note: str | None = None,
                ai_generated: bool | None = None) -> bool:
    clips = list_clips(settings, job_id)
    found = False
    for clip in clips:
        if clip["id"] == clip_id:
            found = True
            if note is not None:
                clip["note"] = note[:200]
            if ai_generated is not None:
                clip["ai_generated"] = bool(ai_generated)
    if found:
        _save(settings, job_id, clips)
    return found


def delete_clip(settings, job_id: str, clip_id: str) -> None:
    clips = list_clips(settings, job_id)
    keep = []
    base = clip_dir(settings, job_id)
    for clip in clips:
        if clip["id"] == clip_id:
            (base / clip["file"]).unlink(missing_ok=True)
            if clip.get("thumb") and clip["thumb"] != clip["file"]:
                (base / clip["thumb"]).unlink(missing_ok=True)
        else:
            keep.append(clip)
    _save(settings, job_id, keep)


def clip_path(settings, job_id: str, clip_id: str) -> Path:
    for clip in list_clips(settings, job_id):
        if clip["id"] == clip_id:
            return clip_dir(settings, job_id) / clip["file"]
    raise ClipError(f"Clip không tồn tại: {clip_id}")


def is_allowed_source(settings, job: dict, source: str) -> bool:
    """A scene may only read the job's own source video, its clips or its work assets."""
    try:
        path = Path(source).resolve()
    except (OSError, ValueError):
        return False
    if job.get("source_path") and path == Path(job["source_path"]).resolve():
        return True
    roots = [clip_dir(settings, job["id"]), Path(settings.work_dir) / job["id"]]
    return any(path.is_relative_to(root.resolve()) for root in roots) and path.is_file()


def describe_for_ai(clips: list[dict]) -> str:
    lines = []
    for index, clip in enumerate(clips):
        what = f"video {clip['duration']}s" if clip["kind"] == "video" else "ảnh"
        lines.append(f"- clip {index}: {what}, {clip['width']}x{clip['height']}"
                     f"{', do AI tạo' if clip.get('ai_generated') else ''}"
                     f" — {clip.get('note') or clip['name']}")
    return "\n".join(lines)
