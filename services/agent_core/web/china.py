"""China Trend dashboard: Trend Radar, Niche Explorer, Idea Generator, sources, autopilot."""
from pathlib import Path

from fastapi import File, Form, HTTPException, Request, UploadFile

from .. import db
from ..cn_trends import analysis, collectors, store

TABS = ("radar", "niches", "ideas", "sources")
REF_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".webm"}


def install_routes(app, settings, render):
    def queue(kind, payload=None):
        tid = db.enqueue_task(settings().database_path, kind, None, payload)
        return {"queued": bool(tid), "task_id": tid}

    @app.get("/china")
    def china_page(request: Request, tab: str = "radar", stage: str = "", niche: str = "", goal: str = ""):
        s = settings()
        store.ensure_schema(s.database_path)
        analysis.ensure_niches(s)
        topics = store.list_topics(s.database_path, 300, since_hours=96)
        if stage:
            topics = [t for t in topics if t.get("stage") == stage]
        if niche:
            topics = [t for t in topics if t.get("niche") == niche]
        if goal:
            topics = [t for t in topics if t.get("goal") == goal]
        ideas = store.list_ideas(s.database_path, limit=300)
        topic_names = {t["key"]: t.get("title_vi") or t["title_zh"] for t in
                       store.list_topics(s.database_path, 2000, since_hours=None)}
        from ..cn_trends.pipeline import autopilot_today
        return render(request, "china.html", active="china", tab=tab if tab in TABS else "radar",
                      topics=topics, ideas=ideas, topic_names=topic_names,
                      niches=analysis.niche_stats(s), niche_names={n["id"]: n["name"] for n in store.niches(s.database_path)},
                      sources=collectors.SOURCES, source_state=store.source_state(s.database_path),
                      stage_labels=analysis.STAGE_LABELS, filters={"stage": stage, "niche": niche, "goal": goal},
                      autopilot={"enabled": s.autopilot_enabled, "cap": s.autopilot_daily_videos,
                                 "mix": s.autopilot_goal_mix, "min": s.autopilot_min_score,
                                 "today": len(autopilot_today(s))})

    @app.post("/api/china/fetch")
    def china_fetch():
        return queue("cn_trends")

    @app.post("/api/china/rescore")
    def china_rescore():
        return queue("cn_trends", {"no_collect": True})

    @app.post("/api/china/topics/{key}/ideas")
    def china_ideas(key: str):
        topic = store.get_topic(settings().database_path, key)
        if not topic:
            raise HTTPException(404, "Không có trend này")
        if topic.get("status") == "BLOCKED":
            raise HTTPException(400, "Trend nhạy cảm: không làm video")
        return queue("cn_ideas", {"key": key})

    @app.post("/api/china/topics/{key}/status")
    async def china_topic_status(key: str, request: Request):
        body = await request.json()
        status = str(body.get("status") or "")
        if status not in {"NEW", "IGNORED"}:
            raise HTTPException(400, "Trạng thái không hợp lệ")
        if not store.get_topic(settings().database_path, key):
            raise HTTPException(404, "Không có trend này")
        store.update_topic(settings().database_path, key, status=status)
        return {"ok": True}

    @app.post("/api/china/ideas/{iid}/produce")
    def china_produce(iid: str):
        idea = store.get_idea(settings().database_path, iid)
        if not idea:
            raise HTTPException(404, "Không có ý tưởng này")
        if idea.get("job_id"):
            return {"job_id": idea["job_id"]}
        store.update_idea(settings().database_path, iid, status="QUEUED")
        return queue("cn_idea_job", {"idea_id": iid})

    @app.post("/api/china/ideas/{iid}/reject")
    def china_reject(iid: str):
        if not store.get_idea(settings().database_path, iid):
            raise HTTPException(404, "Không có ý tưởng này")
        store.update_idea(settings().database_path, iid, status="REJECTED")
        return {"ok": True}

    @app.post("/api/china/niches/{nid}")
    async def china_niche(nid: str, request: Request):
        body = await request.json()
        status = str(body.get("status") or "")
        match = [n for n in store.niches(settings().database_path) if n["id"] == nid]
        if not match:
            raise HTTPException(404, "Không có ngách này")
        if status not in {"active", "rejected"}:
            raise HTTPException(400, "Trạng thái không hợp lệ")
        n = match[0]
        store.save_niche(settings().database_path, nid, n["name"], n["description"], status, n["evidence"])
        return {"ok": True}

    @app.post("/api/china/manual")
    async def china_manual(platform: str = Form(...), title: str = Form(...), url: str = Form(""),
                           notes: str = Form(""), reference: UploadFile | None = File(None)):
        s = settings()
        saved = ""
        if reference is not None and reference.filename:
            ext = Path(reference.filename).suffix.lower()
            if ext not in REF_EXTS:
                raise HTTPException(400, "Chỉ nhận ảnh (jpg/png/webp) hoặc clip (mp4/mov/webm)")
            content = await reference.read(80 * 1024 * 1024 + 1)
            if len(content) > 80 * 1024 * 1024:
                raise HTTPException(400, "File tham khảo tối đa 80 MB")
            folder = Path(s.work_dir) / "_cn_refs" / "uploads"
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / (store.topic_key(platform, title + reference.filename)[3:] + ext)
            target.write_bytes(content)
            saved = str(target)
        try:
            key = collectors.add_manual(s, platform, title, url, notes, saved)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        db.enqueue_task(s.database_path, "cn_trends", None, {"no_collect": True})
        return {"key": key}
