"""Long videos (1-3 minutes) made of 25-35 s parts.

1. ``create``: a parent job (mode "long") with the topic, length and branch.
2. ``outline`` (AI, one call): title, cast, style, music and one beat list per part.
3. Each part becomes a normal creation job with the part's beats, the fixed cast and voices,
   no hook after part 1 and no music of its own. Part 1 makes the cast photos; the others reuse
   them (shared_cast) so faces stay the same.
4. ``assemble``: when every part is rendered, the parts are joined without re-encoding the
   picture, one music track is laid under the whole video and loudness is normalised.
"""
import json
import logging
import math
import uuid
from pathlib import Path

from .db import enqueue_task, get_job, list_jobs_by_status, now_iso, register_job, update_job

log = logging.getLogger("affiliate_agent.long_video")

SEGMENT_SECONDS = 30
MAX_MINUTES = 3
PARENT_WAIT = "WAITING_FOR_SEGMENTS"
DONE = {"READY_FOR_REVIEW", "APPROVED", "PUBLISHED"}
FAILED = {"RENDER_FAILED", "CONTENT_GENERATION_FAILED", "AI_ANALYSIS_FAILED", "NEEDS_MEDIA_REVIEW",
          "WAITING_FOR_AI_CREDENTIALS"}


def segment_count(seconds: float) -> int:
    return max(2, min(6, math.ceil(float(seconds) / SEGMENT_SECONDS)))


def create(settings, topic: str, seconds: float, branch: str = "viral_creator", notes: str = "",
           template: dict | None = None, fmt: str = "") -> str:
    seconds = max(45.0, min(MAX_MINUTES * 60.0, float(seconds)))
    if not topic.strip():
        raise ValueError("Nhập chủ đề cho video dài")
    job_id = "L" + uuid.uuid4().hex[:23]
    brief = {"topic": " ".join(topic.split()), "notes": notes, "creation_branch": branch,
             "long": {"seconds": seconds, "segments": segment_count(seconds), "children": []}}
    if template:
        brief["template"] = template
    if fmt:
        brief["format"] = fmt
    register_job(settings.database_path, {
        "id": job_id, "source_path": "", "sha256": f"long:{job_id}",
        "file_name": f"[Video dài {round(seconds / 60, 1)} phút] {brief['topic']}"[:120],
        "mode": "long", "brief": brief})
    enqueue_task(settings.database_path, "long_outline", job_id)
    return job_id


def outline_prompt(brief: dict) -> str:
    n = brief["long"]["segments"]
    tpl = brief.get("template") or {}
    cast_hint = (f"Dùng đúng dàn nhân vật của series (giữ nguyên id): "
                 f"{json.dumps(tpl.get('characters') or [], ensure_ascii=False)}" if tpl else
                 "Tự đặt nhân vật nếu cần (có thể không có người nếu là nội dung kiến thức).")
    return f"""Bạn là biên kịch video dài cho mạng xã hội Việt Nam (Reels/YouTube Shorts dài, Shopee Video).
Chủ đề: {brief['topic']}. Ghi chú: {brief.get('notes') or ''}
Tổng thời lượng khoảng {int(brief['long']['seconds'])} giây, chia đúng {n} phần, mỗi phần {SEGMENT_SECONDS} giây.
{cast_hint}
Yêu cầu: phần 1 có hook mạnh ở 3 giây đầu; mỗi phần có một mục tiêu rõ và kết bằng một câu nối sang phần sau;
phần cuối có payoff/kết luận. Nội dung kiến thức: chỉ nói điều chắc chắn đúng.
Trả DUY NHẤT JSON:
{{"title": "", "takeaway": "một câu người xem nhớ", "style_profile": "pro_social|documentary|chibi_3d",
"music_mood": "emotional|chill|upbeat|funny", "characters": [{{"id": "", "name": "", "role": "", "gender": "",
"age": "", "appearance": "English", "wardrobe": "English"}}],
"segments": [{{"title": "", "beats": "nội dung chi tiết phần này", "summary": "1 câu tóm tắt"}}],
"caption": "", "hashtags": ["#..."]}}"""


def outline(settings, job_id: str, ask=None) -> dict:
    """Write the outline, then create and queue one creation job per part."""
    from . import workflow
    from .creation_branches import normalise_characters
    db = settings.database_path
    job = get_job(db, job_id)
    brief = json.loads(job["brief_json"] or "{}")
    if brief["long"].get("children"):
        return {"children": brief["long"]["children"]}
    update_job(db, job_id, status="SCRIPTING", last_error=None)
    if ask is None:
        client = workflow._client(settings)

        def ask(prompt):
            return workflow._ask_json(client, settings, prompt, temperature=0.6, max_tokens=4000)
    try:
        plan = ask(outline_prompt(brief))
        segments = [s for s in plan.get("segments") or [] if isinstance(s, dict)][:brief["long"]["segments"]]
        if len(segments) < 2:
            raise ValueError("Dàn ý video dài thiếu phần")
    except Exception as exc:
        update_job(db, job_id, status="CONTENT_GENERATION_FAILED",
                   last_error=f"Viết dàn ý video dài lỗi: {exc}"[:500])
        raise
    tpl = brief.get("template") or {}
    characters = normalise_characters(tpl.get("characters") or plan.get("characters") or [])
    branch = brief["creation_branch"]
    children = []
    for k, seg in enumerate(segments):
        part = k + 1
        child = workflow.create_trend_job(
            settings, f"{plan.get('title') or brief['topic']} - phần {part}/{len(segments)}",
            str(seg.get("beats") or ""), creation_branch=branch, template=tpl or None)
        cbrief = json.loads(get_job(db, child)["brief_json"] or "{}")
        cbrief.update({
            "segment": {"parent": job_id, "index": part, "count": len(segments),
                        "title": plan.get("title") or brief["topic"], "beats": seg.get("beats") or "",
                        "previous": segments[k - 1].get("summary") if k else "",
                        "next": segments[k + 1].get("summary") if k + 1 < len(segments) else ""},
            "fixed_characters": characters, "fixed_style": plan.get("style_profile") or "",
            "fixed_music_mood": plan.get("music_mood") or "",
            "duration_range": [SEGMENT_SECONDS - 6, SEGMENT_SECONDS + 6],
            "no_music": True, "no_variants": True, "no_hook": part > 1,
        })
        if brief.get("format"):
            cbrief["format"] = brief["format"]
        if children:
            cbrief["shared_cast_from"] = children[0]
        update_job(db, child, brief_json=json.dumps(cbrief, ensure_ascii=False))
        enqueue_task(db, "process", child)
        children.append(child)
    brief["long"]["children"] = children
    brief["long"]["outline"] = {k: plan.get(k) for k in ("title", "takeaway", "music_mood", "caption",
                                                          "hashtags", "style_profile")}
    update_job(db, job_id, status=PARENT_WAIT, brief_json=json.dumps(brief, ensure_ascii=False))
    return {"children": children}


def check_parents(settings) -> list[str]:
    """Scheduler: assemble a long video once all of its parts are rendered."""
    queued = []
    for parent in list_jobs_by_status(settings.database_path, [PARENT_WAIT], 50):
        brief = json.loads(parent["brief_json"] or "{}")
        kids = [get_job(settings.database_path, c) for c in brief.get("long", {}).get("children", [])]
        if not kids or any(k is None for k in kids):
            continue
        if all(k["status"] in DONE for k in kids):
            if enqueue_task(settings.database_path, "long_assemble", parent["id"]):
                queued.append(parent["id"])
        elif any(k["status"] in FAILED for k in kids):
            bad = [str(i + 1) for i, k in enumerate(kids) if k["status"] in FAILED]
            update_job(settings.database_path, parent["id"],
                       last_error=f"Phần {', '.join(bad)} cần kiểm tra trước khi ghép (mở từng phần để sửa)")
    return queued


def assemble(settings, job_id: str) -> dict:
    """Join the parts (picture copied), add one music track under everything, normalise loudness."""
    from .editor.ffmpeg import probe, run_ffmpeg
    from .editor.music import pick_track
    db = settings.database_path
    parent = get_job(db, job_id)
    brief = json.loads(parent["brief_json"] or "{}")
    kids = [get_job(db, c) for c in brief["long"]["children"]]
    files = [Path(k["output_path"]) for k in kids]
    missing = [str(i + 1) for i, f in enumerate(files) if not f.is_file()]
    if missing:
        raise ValueError(f"Thiếu file video của phần {', '.join(missing)}")
    update_job(db, job_id, status="RENDERING", last_error=None)
    out_dir = settings.shopee_output / job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    work = settings.work_dir / job_id
    work.mkdir(parents=True, exist_ok=True)
    listing = work / "parts.txt"
    listing.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
    joined = work / "joined.mp4"
    run_ffmpeg(settings.ffmpeg_path, ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                                      str(joined)])
    total = probe(settings.ffprobe_path, joined)["duration_sec"]
    outline_info = brief["long"].get("outline") or {}
    final = out_dir / f"{job_id}.mp4"
    music = pick_track(settings.music, outline_info.get("music_mood") or "", seed=job_id,
                       timeline={"music_mood": outline_info.get("music_mood") or "",
                                 "title": outline_info.get("title") or brief["topic"],
                                 "scenes": [{"voiceover": brief["topic"], "duration": total}]},
                       settings=settings, job_id=job_id)
    try:
        if music:
            graph = (f"[0:a]asplit=2[mix][sc];[1:a]aresample=44100,atrim=duration={total:.3f},"
                     f"volume={settings.music_volume},afade=t=out:st={max(0, total - 1.5):.3f}:d=1.5[mus];"
                     "[mus][sc]sidechaincompress=threshold=0.06:ratio=4:attack=20:release=400[duck];"
                     "[mix][duck]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
            run_ffmpeg(settings.ffmpeg_path, ["-i", str(joined), "-stream_loop", "-1", "-i", str(Path(music)),
                                              "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
                                              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                                              "-movflags", "+faststart", "-t", f"{total:.3f}", str(final)])
        else:
            run_ffmpeg(settings.ffmpeg_path, ["-i", str(joined), "-c:v", "copy", "-af",
                                              "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k",
                                              "-movflags", "+faststart", str(final)])
    finally:
        if hasattr(music, "cleanup"):
            music.cleanup()
    meta = probe(settings.ffprobe_path, final)
    from .editor.render import extract_cover
    extract_cover(settings, final, min(1.0, total / 2), out_dir / "cover.jpg")
    from .workflow import finalise_content
    content = finalise_content({
        "title": outline_info.get("title") or brief["topic"], "hook": outline_info.get("title") or "",
        "caption": outline_info.get("caption") or outline_info.get("title") or brief["topic"],
        "hashtags": outline_info.get("hashtags") or [], "cover_ratio": 0.05,
        "facebook_description": outline_info.get("caption") or ""}, parent.get("affiliate_link") or "")
    manifest = {"job_id": job_id, "mode": "long", "video": str(final), "parts": [str(f) for f in files],
                "music": getattr(music, "to_dict", lambda: None)() if music else None,
                "metadata": meta, "content": content, "created_at": now_iso()}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
                                           encoding="utf-8")
    update_job(db, job_id, status="READY_FOR_REVIEW", review_status="PENDING", output_path=str(final),
               duration_sec=meta["duration_sec"], width=meta["width"], height=meta["height"],
               content_json=json.dumps(content, ensure_ascii=False), shopee_ready=1,
               facebook_ready=int(meta["duration_sec"] <= 90), last_error=None)
    return {"job_id": job_id, "duration": meta["duration_sec"], "parts": len(files)}
