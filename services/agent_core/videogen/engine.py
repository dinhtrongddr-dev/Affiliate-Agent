"""Generation task queue: one row per clip/image, idempotent and restart-safe.

States: PENDING -> SUBMITTING -> SUBMITTED -> DOWNLOADED (QC passed) | FAILED | CANCELLED.

- The row id is the idempotency key (job, scene, kind, prompt, references, attempt). A row that
  already has a remote id is only polled, never sent again, so restarting the app cannot create
  duplicate clips. A row left in SUBMITTING by a crash is re-sent with the same Idempotency-Key.
- A failed or QC-rejected clip is retried once; after that the scene falls back to the user's
  clips, stock footage or the product image.
- A daily clip limit (AA_VIDEOGEN_DAILY_LIMIT) is enforced locally before every submit.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..db import connect, enqueue_task, get_job, init_db, now_iso, update_job
from . import providers

log = logging.getLogger("affiliate_agent.videogen")

ACTIVE = ("PENDING", "SUBMITTING", "SUBMITTED")
WAIT_STATUS = "WAITING_FOR_GENERATION"
MAX_ATTEMPTS = 2  # first try + one retry
SUBMIT_ERRORS_BEFORE_FAIL = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS gen_tasks (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    scene_index INTEGER NOT NULL,
    kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    prompt TEXT NOT NULL,
    refs_json TEXT NOT NULL DEFAULT '[]',
    duration INTEGER,
    depends_on TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL,
    remote_id TEXT,
    submit_errors INTEGER NOT NULL DEFAULT 0,
    result_path TEXT,
    qc_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gen_tasks_job ON gen_tasks(job_id, status);
CREATE INDEX IF NOT EXISTS idx_gen_tasks_status ON gen_tasks(status);
"""
_ready: set[str] = set()


def ensure_schema(db_path) -> None:
    key = str(Path(db_path).resolve())
    if key in _ready and Path(db_path).exists():
        return
    init_db(db_path)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
    _ready.add(key)


def _row(r) -> dict:
    d = dict(r)
    d["refs"] = json.loads(d.pop("refs_json") or "[]")
    d["qc"] = json.loads(d.pop("qc_json") or "null")
    return d


def task_id(job_id: str, scene_index: int, kind: str, prompt: str, refs: list[str],
            attempt: int = 1) -> str:
    material = json.dumps([job_id, scene_index, kind, prompt, sorted(refs), attempt],
                          ensure_ascii=False)
    return "g" + hashlib.sha256(material.encode()).hexdigest()[:23]


def _update(db_path, tid: str, **fields) -> None:
    for name in ("refs_json", "qc_json"):
        if name in fields and not isinstance(fields[name], str) and fields[name] is not None:
            fields[name] = json.dumps(fields[name], ensure_ascii=False)
    fields["updated_at"] = now_iso()
    clause = ", ".join(f"{k}=?" for k in fields)
    with connect(db_path) as conn:
        conn.execute(f"UPDATE gen_tasks SET {clause} WHERE id=?", [*fields.values(), tid])


def get_task(db_path, tid: str) -> dict | None:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        r = conn.execute("SELECT * FROM gen_tasks WHERE id=?", (tid,)).fetchone()
    return _row(r) if r else None


def list_tasks(db_path, job_id: str | None = None, limit: int = 200) -> list[dict]:
    ensure_schema(db_path)
    sql, args = "SELECT * FROM gen_tasks", []
    if job_id:
        sql += " WHERE job_id=?"
        args.append(job_id)
    sql += " ORDER BY created_at DESC, scene_index LIMIT ?"
    with connect(db_path) as conn:
        return [_row(r) for r in conn.execute(sql, [*args, int(limit)]).fetchall()]


def has_active(db_path) -> bool:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        return conn.execute(
            f"SELECT 1 FROM gen_tasks WHERE status IN ({','.join('?' * len(ACTIVE))}) LIMIT 1",
            ACTIVE).fetchone() is not None


def _insert(db_path, *, job_id, scene_index, kind, provider, prompt, refs, duration=None,
            depends_on=None, attempt=1) -> str:
    tid = task_id(job_id, scene_index, kind, prompt, refs, attempt)
    stamp = now_iso()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO gen_tasks (id, job_id, scene_index, kind, provider, prompt,"
            " refs_json, duration, depends_on, attempt, status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, job_id, scene_index, kind, provider, prompt, json.dumps(refs), duration,
             depends_on, attempt, "PENDING", stamp, stamp))
    return tid


def cancel_job_tasks(db_path, job_id: str, reason: str = "Kịch bản mới thay thế") -> int:
    ensure_schema(db_path)
    with connect(db_path) as conn:
        return conn.execute(
            f"UPDATE gen_tasks SET status='CANCELLED', error=?, updated_at=? WHERE job_id=?"
            f" AND status IN ({','.join('?' * len(ACTIVE))})",
            (reason, now_iso(), job_id, *ACTIVE)).rowcount


def remaining_today(settings) -> int:
    from ..trend_intel import store as usage_store
    return max(0, settings.videogen_daily_limit
               - usage_store.usage(settings.database_path, "videogen", "day"))


# ------------------------------------------------------------------ prompts

STYLE = ("Vertical 9:16 smartphone video, natural light, realistic, no on-screen text, "
         "no subtitles, no watermark, no logos.")


def video_prompt(scene: dict, plan: dict, has_product_ref: bool, has_character_ref: bool) -> str:
    parts = [str(scene.get("gen_prompt") or "").strip()]
    character = str(plan.get("character") or "").strip()
    if character:
        parts.append(f"Same person in every shot: {character}.")
        if has_character_ref:
            parts.append("The person must look exactly like the person in the character "
                         "reference image.")
    if has_product_ref:
        parts.append("The product must look exactly like the product reference image "
                     "(same shape, colour and packaging); do not invent labels or brand names.")
    parts.append(STYLE)
    parts.append("No speech." if not plan.get("muse_voice") else "")
    return " ".join(p for p in parts if p)


def character_prompt(plan: dict) -> str:
    return (f"Character reference photo, waist-up, neutral background, facing camera: "
            f"{plan['character']}. Photorealistic. {STYLE}")


SHOT_FIELDS = ("action", "setting", "camera", "lighting", "emotion", "continuity",
               "dialogue", "sound", "transition")
MAX_SCENE_REFERENCES = 6


def _storyboard_manifest(plan: dict) -> dict[str, dict]:
    """Accept only workflow-resolved local assets whose contents still match."""
    manifest = {}
    for item in plan.get("reference_manifest") or []:
        if not isinstance(item, dict):
            raise providers.ProviderError("Tham chiếu storyboard không hợp lệ")
        rid = str(item.get("id") or "")
        path = Path(str(item.get("path") or ""))
        expected = str(item.get("sha256") or "").lower()
        if not rid or rid in manifest or not path.is_absolute() or not path.is_file():
            raise providers.ProviderError("Thiếu file tham chiếu storyboard hợp lệ")
        try:
            with path.open("rb") as fh:
                digest = hashlib.file_digest(fh, "sha256").hexdigest()
        except OSError as exc:
            raise providers.ProviderError("Không đọc được file tham chiếu storyboard") from exc
        if digest != expected:
            raise providers.ProviderError("Nội dung file tham chiếu storyboard đã thay đổi")
        manifest[rid] = {"id": rid, "role": str(item.get("role") or "reference"),
                         "path": str(path), "sha256": digest}
    return manifest


def storyboard_prompt(scene: dict, plan: dict, references: list[dict]) -> str:
    """Compile explicit shot/cast instructions without imposing one realistic person."""
    parts = [str(scene.get("gen_prompt") or "").strip(),
             "Vertical 9:16 video. No subtitles, watermark or invented logos."]
    if plan.get("style"):
        parts.append("Visual style: " + str(plan["style"]))
    if plan.get("setting"):
        parts.append("Series setting: " + str(plan["setting"]))
    if plan.get("continuity_rules"):
        parts.append("Series continuity rules: " + json.dumps(plan["continuity_rules"], ensure_ascii=False))
    cast = plan.get("characters") or []
    if cast:
        parts.append("Recurring cast (preserve each distinct identity): "
                     + json.dumps(cast, ensure_ascii=False, sort_keys=True))
    shot = scene.get("shot") or {}
    for field in SHOT_FIELDS:
        if shot.get(field):
            parts.append(f"{field.capitalize()}: {shot[field]}")
    if references:
        parts.append("Attachment legend (exact upload order): " + "; ".join(
            f"attachment {index} = {ref['id']} (role: {ref['role']})"
            for index, ref in enumerate(references, 1)))
        parts.append("Use each attachment only for its stated role; preserve product and cast "
                     "identity, appearance and wardrobe across shots. Do not merge identities.")
    # task_id historically sorts paths. Include semantic order and content in the compiled
    # prompt so new storyboards distinguish reordered or replaced files at the same path.
    identity = [[r["id"], r["role"], r["sha256"]] for r in references]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    parts.append("Reference manifest digest: " + digest)
    if not plan.get("muse_voice"):
        parts.append("No speech. Dialogue is script context only; speech is added separately by the app.")
    return "\n".join(parts)


def _compile_storyboard(settings, plan: dict, provider=None) -> list:
    scenes = [(i, s) for i, s in enumerate(plan.get("scenes") or [])
              if s.get("visual") == "generate"]
    if not scenes:
        return []
    if provider and not provider.supports("video"):
        raise providers.ProviderError("Nhà cung cấp không hỗ trợ video storyboard")
    manifest = _storyboard_manifest(plan)
    compiled = []
    for index, scene in scenes:
        if not str(scene.get("gen_prompt") or "").strip():
            raise providers.ProviderError(f"Cảnh {index + 1} thiếu prompt tạo video")
        ids = scene.get("reference_ids") or []
        if not isinstance(ids, list) or len(ids) > MAX_SCENE_REFERENCES:
            raise providers.ProviderError(f"Mỗi cảnh chỉ nhận tối đa {MAX_SCENE_REFERENCES} tham chiếu")
        if any(not isinstance(rid, str) or rid not in manifest for rid in ids):
            raise providers.ProviderError(f"Cảnh {index + 1} có tham chiếu không xác định")
        if len(set(ids)) != len(ids):
            raise providers.ProviderError(f"Cảnh {index + 1} có tham chiếu trùng lặp")
        refs = [manifest[rid] for rid in ids]
        if refs and provider and not provider.supports("references"):
            raise providers.ProviderError("Nhà cung cấp không hỗ trợ tham chiếu storyboard")
        try:
            duration = max(5, int(round(float(scene.get("duration")
                                             or settings.videogen_clip_seconds))))
        except (ValueError, TypeError, OverflowError) as exc:
            raise providers.ProviderError(f"Thời lượng cảnh {index + 1} không hợp lệ") from exc
        prompt = storyboard_prompt(scene, plan, refs) + f"\nRequested duration: {duration} seconds."
        compiled.append((index, prompt,
                         [r["path"] for r in refs], duration))
    return compiled


def _matching_storyboard_tasks(db, job_id, compiled):
    expected = {index: (prompt, refs) for index, prompt, refs, _ in compiled}
    return [t for t in list_tasks(db, job_id, 10000)
            if t["kind"] == "video" and t["scene_index"] in expected
            and t["id"] == task_id(job_id, t["scene_index"], "video",
                                    *expected[t["scene_index"]], attempt=t["attempt"])]


def results_for_storyboard(settings, job_id: str, plan: dict) -> dict[int, Path]:
    compiled = _compile_storyboard(settings, plan)
    latest = {}
    for task in sorted(_matching_storyboard_tasks(settings.database_path, job_id, compiled),
                       key=lambda t: t["attempt"]):
        latest[task["scene_index"]] = task
    return {index: Path(t["result_path"]) for index, t in latest.items()
            if t["status"] == "DOWNLOADED" and t["result_path"]
            and Path(t["result_path"]).is_file()}


def _plan_storyboard(settings, job_id: str, plan: dict, provider) -> int:
    compiled = _compile_storyboard(settings, plan, provider)
    if len(compiled) > settings.videogen_max_clips:
        raise providers.ProviderError("Storyboard exceeds configured maximum clips")
    if not compiled:
        cancel_job_tasks(settings.database_path, job_id)
        return 0
    existing = _matching_storyboard_tasks(settings.database_path, job_id, compiled)
    existing_scenes = {t["scene_index"] for t in existing}
    new_count = sum(index not in existing_scenes for index, *_ in compiled)
    if new_count > remaining_today(settings):
        raise providers.ProviderError("Daily budget cannot cover new storyboard tasks")
    # All validation precedes mutation. Preserve matching accepted work on a repeated
    # planning pass; only superseded active rows are cancelled, never resubmitted.
    db = settings.database_path
    ensure_schema(db)
    retained = {task_id(job_id, i, "video", prompt, refs)
                for i, prompt, refs, _ in compiled}
    retained.update(t["id"] for t in existing)
    with connect(db) as conn:
        conn.execute(
            f"UPDATE gen_tasks SET status='CANCELLED', error=?, updated_at=? WHERE job_id=? "
            f"AND status IN ({','.join('?' * len(ACTIVE))}) "
            f"AND id NOT IN ({','.join('?' * len(retained))})",
            ("Kịch bản mới thay thế", now_iso(), job_id, *ACTIVE, *retained))
    last_sb_task = None
    use_chain = bool(getattr(settings, "videogen_continuity_chain", False) or plan.get("continuity_chain") or plan.get("creation_branch"))
    for index, prompt, refs, duration in compiled:
        depends = last_sb_task if use_chain else None
        created_id = _insert(db, job_id=job_id, scene_index=index, kind="video", provider=provider.name,
                prompt=prompt, refs=refs, duration=duration, depends_on=depends)
        if use_chain:
            last_sb_task = created_id
    return len(compiled)


# ------------------------------------------------------------------ planning

def plan_job(settings, job_id: str, plan: dict, product_img: Path | None,
             provider: providers.VideoProvider) -> int:
    """Create generation tasks for the plan's "generate" scenes. Returns the number of
    video tasks; 0 means nothing to wait for (render can continue right away)."""
    if plan.get("creation_branch"):
        return _plan_storyboard(settings, job_id, plan, provider)
    db = settings.database_path
    ensure_schema(db)
    cancel_job_tasks(db, job_id)
    budget = min(settings.videogen_max_clips, remaining_today(settings))
    scenes = [(i, s) for i, s in enumerate(plan.get("scenes") or [])
              if s.get("visual") == "generate" and str(s.get("gen_prompt") or "").strip()]
    scenes = scenes[:budget]
    if not scenes or not provider.supports("video"):
        return 0
    refs = [str(product_img)] if product_img and provider.supports("references") else []
    character_task = None
    if (settings.videogen_character and str(plan.get("character") or "").strip()
            and provider.supports("image")):
        character_task = _insert(db, job_id=job_id, scene_index=-1, kind="image",
                                 provider=provider.name, prompt=character_prompt(plan), refs=[])
    last_video_task = character_task
    use_chain = bool(getattr(settings, "videogen_continuity_chain", False) or plan.get("continuity_chain") or plan.get("creation_branch"))
    for index, scene in scenes:
        duration = int(round(float(scene.get("duration") or settings.videogen_clip_seconds)))
        prompt = video_prompt(scene, plan, bool(refs), bool(character_task))
        depends = last_video_task if use_chain else character_task
        task_id_created = _insert(db, job_id=job_id, scene_index=index, kind="video", provider=provider.name,
                prompt=prompt, refs=refs, duration=max(5, duration), depends_on=depends)
        if use_chain:
            last_video_task = task_id_created
    return len(scenes)


def results_for_job(db_path, job_id: str) -> dict[int, Path]:
    """scene index -> downloaded, QC-passed clip (latest attempt wins)."""
    out: dict[int, Path] = {}
    for t in sorted(list_tasks(db_path, job_id, 500), key=lambda t: t["attempt"]):
        if t["kind"] == "video" and t["status"] == "DOWNLOADED" and t["result_path"] \
                and Path(t["result_path"]).is_file():
            out[t["scene_index"]] = Path(t["result_path"])
    return out


# ------------------------------------------------------------------ polling

def _older_than(ts: str | None, minutes: float) -> bool:
    if not ts:
        return False
    at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.now(timezone.utc) - at > timedelta(minutes=minutes)


def _retry_or_fail(settings, t: dict, error: str, qc: dict | None = None) -> None:
    db = settings.database_path
    _update(db, t["id"], status="FAILED", error=error[:500], qc_json=qc)
    if t["kind"] == "video" and t["attempt"] < MAX_ATTEMPTS:
        _insert(db, job_id=t["job_id"], scene_index=t["scene_index"], kind=t["kind"],
                provider=t["provider"], prompt=t["prompt"], refs=t["refs"],
                duration=t["duration"], depends_on=t["depends_on"], attempt=t["attempt"] + 1)
        log.info("gen task %s failed (%s); retry scheduled", t["id"], error)


def _submit(settings, provider, t: dict) -> None:
    db = settings.database_path
    if t["status"] == "PENDING":
        job = get_job(db, t["job_id"])
        if job and json.loads(job.get("brief_json") or "{}").get("creation_branch"):
            try:
                state_path = Path(settings.work_dir) / t["job_id"] / "plan_state.json"
                plan = json.loads(state_path.read_text(encoding="utf-8"))["plan"]
                compiled = _compile_storyboard(settings, plan)
                expected = {task_id(t["job_id"], index, "video", prompt, refs,
                                    attempt=t["attempt"])
                            for index, prompt, refs, _ in compiled}
                if t["id"] not in expected:
                    raise providers.ProviderError("Queued task no longer matches current storyboard")
            except (OSError, ValueError, KeyError, TypeError, providers.ProviderError) as exc:
                _update(db, t["id"], status="FAILED",
                        error=f"Storyboard references changed or task superseded; review and replan: {exc}"[:500])
                return
    refs = [Path(r) for r in t["refs"] if Path(r).is_file()]
    if t["depends_on"]:
        dep = get_task(db, t["depends_on"])
        if dep and dep["status"] in ACTIVE:
            return  # wait for preceding reference or scene
        if dep and dep["status"] == "DOWNLOADED":
            cont_frame = Path(settings.work_dir) / t["job_id"] / "generated" / f"continuity_{dep['id']}.jpg"
            if cont_frame.is_file():
                refs = [cont_frame] + [r for r in refs if r != cont_frame]
                continuity_hint = (
                    "Attachment 1 is the seamless starting frame from the preceding scene. "
                    "Continue character motion, posture, wardrobe and lighting seamlessly from this exact frame."
                )
                if continuity_hint not in t["prompt"]:
                    t["prompt"] = f"{t['prompt']}\n{continuity_hint}"
            elif dep["result_path"] and Path(dep["result_path"]).is_file():
                refs.append(Path(dep["result_path"]))
    if t["status"] == "PENDING":
        from ..trend_intel import store as usage_store
        if not usage_store.consume(db, "videogen", settings.videogen_daily_limit, kind="day"):
            _update(db, t["id"], error="Hết hạn mức tạo clip hôm nay (AA_VIDEOGEN_DAILY_LIMIT)")
            return
    _update(db, t["id"], status="SUBMITTING")
    try:
        if t["kind"] == "image":
            remote = provider.submit_image(t["prompt"], refs, t["id"])
        else:
            remote = provider.submit_video(t["prompt"], refs, t["duration"] or 10, t["id"])
    except providers.ProviderError as exc:
        errors = t["submit_errors"] + 1
        if errors >= SUBMIT_ERRORS_BEFORE_FAIL or isinstance(exc, providers.NotSupported):
            _update(db, t["id"], submit_errors=errors)
            _retry_or_fail(settings, {**t, "attempt": MAX_ATTEMPTS}, f"Gửi thất bại: {exc}")
        else:
            # Back to SUBMITTING-without-id: the next poll re-sends with the same key.
            _update(db, t["id"], submit_errors=errors, error=str(exc)[:500])
        return
    _update(db, t["id"], status="SUBMITTED", remote_id=remote, submitted_at=now_iso(),
            error=None)


def _check(settings, provider, t: dict) -> None:
    db = settings.database_path
    if _older_than(t["submitted_at"], settings.videogen_task_timeout_minutes):
        _retry_or_fail(settings, t, "Quá thời gian chờ")
        return
    try:
        st = provider.status(t["kind"], t["remote_id"])
    except providers.ProviderError as exc:
        _update(db, t["id"], error=f"Đọc trạng thái lỗi (thử lại sau): {exc}"[:500])
        return
    if st.state == "failed":
        _retry_or_fail(settings, t, st.error or "Muse báo lỗi")
        return
    if st.state != "succeeded":
        _update(db, t["id"], error=None)
        return
    if not st.url:
        _retry_or_fail(settings, t, "Xong nhưng không có link file")
        return
    ext = ".mp4" if t["kind"] == "video" else (Path(st.url.split("?")[0]).suffix or ".png")
    out = settings.work_dir / t["job_id"] / "generated" / f"{t['id']}{ext}"
    try:
        provider.download(st.url, out)
    except Exception as exc:  # noqa: BLE001
        _update(db, t["id"], error=f"Tải file lỗi (thử lại sau): {exc}"[:500])
        return
    if t["kind"] == "video":
        from ..quality import check_generated_clip
        qc = check_generated_clip(settings, out)
        if not qc["ok"]:
            _retry_or_fail(settings, t, "QC: " + "; ".join(qc["issues"]), qc)
            return
        try:
            from ..editor.ffmpeg import extract_continuity_frame
            cont_frame = settings.work_dir / t["job_id"] / "generated" / f"continuity_{t['id']}.jpg"
            extract_continuity_frame(
                out, cont_frame, offset_seconds=0.3,
                ffmpeg_bin=getattr(settings, "ffmpeg_path", "ffmpeg"),
                ffprobe_bin=getattr(settings, "ffprobe_path", "ffprobe"),
            )
        except Exception as exc:
            log.debug("extract_continuity_frame failed for %s: %s", t["id"], exc)
        _update(db, t["id"], status="DOWNLOADED", result_path=str(out), qc_json=qc, error=None)
        _add_to_clip_library(settings, t, out)
    else:
        _update(db, t["id"], status="DOWNLOADED", result_path=str(out), error=None)


def _add_to_clip_library(settings, t: dict, path: Path) -> None:
    """Show the clip in the job's 'Clip & hình' tab (tagged AI) so it can be reused."""
    try:
        from ..clips import add_clip
        with path.open("rb") as fh:
            add_clip(settings, t["job_id"], path.name, fh, ai_generated=True,
                     note=f"Video AI cảnh {t['scene_index'] + 1}")
    except Exception as exc:  # noqa: BLE001
        log.info("clip library skip: %s", exc)


def _finish_jobs(settings, job_ids: set[str]) -> list[str]:
    db = settings.database_path
    resumed = []
    for job_id in job_ids:
        job = get_job(db, job_id)
        if not job or job["status"] != WAIT_STATUS:
            continue
        with connect(db) as conn:
            busy = conn.execute(
                f"SELECT 1 FROM gen_tasks WHERE job_id=? AND status IN ({','.join('?' * len(ACTIVE))})",
                (job_id, *ACTIVE)).fetchone()
        if busy:
            continue
        update_job(db, job_id, status="GENERATION_DONE")
        enqueue_task(db, "render_generated", job_id)
        resumed.append(job_id)
    return resumed


def poll(settings, provider=None) -> dict:
    """Advance every active task once. Safe to call repeatedly and after a restart."""
    db = settings.database_path
    ensure_schema(db)
    provider = provider or providers.get_provider(settings)
    touched: set[str] = set()
    with connect(db) as conn:
        rows = [_row(r) for r in conn.execute(
            f"SELECT * FROM gen_tasks WHERE status IN ({','.join('?' * len(ACTIVE))})"
            " ORDER BY scene_index, created_at", ACTIVE).fetchall()]
    if provider is None:
        for t in rows:
            _update(db, t["id"], status="FAILED", error="Chưa cấu hình nhà cung cấp video AI")
            touched.add(t["job_id"])
        return {"tasks": len(rows), "resumed": _finish_jobs(settings, touched)}
    for t in rows:
        touched.add(t["job_id"])
        try:
            if t["status"] in ("PENDING", "SUBMITTING") or not t["remote_id"]:
                _submit(settings, provider, t)
            else:
                _check(settings, provider, t)
        except Exception as exc:  # noqa: BLE001 - one bad task must not stop the others
            log.exception("gen task %s error", t["id"])
            _update(db, t["id"], error=str(exc)[:500])
    return {"tasks": len(rows), "resumed": _finish_jobs(settings, touched)}


def retry_task(settings, tid: str) -> str | None:
    t = get_task(settings.database_path, tid)
    if not t or t["status"] not in ("FAILED", "CANCELLED"):
        return None
    with connect(settings.database_path) as conn:
        top = conn.execute("SELECT MAX(attempt) FROM gen_tasks WHERE job_id=? AND scene_index=?"
                           " AND kind=?", (t["job_id"], t["scene_index"], t["kind"])).fetchone()[0]
    new = _insert(settings.database_path, job_id=t["job_id"], scene_index=t["scene_index"],
                  kind=t["kind"], provider=t["provider"], prompt=t["prompt"], refs=t["refs"],
                  duration=t["duration"], depends_on=t["depends_on"], attempt=(top or 1) + 1)
    job = get_job(settings.database_path, t["job_id"])
    if job and job["status"] in ("READY_FOR_REVIEW", "NEEDS_MEDIA_REVIEW", "GENERATION_DONE"):
        update_job(settings.database_path, t["job_id"], status=WAIT_STATUS)
    return new


def cancel_task(settings, tid: str) -> bool:
    t = get_task(settings.database_path, tid)
    if not t or t["status"] not in ACTIVE:
        return False
    provider = providers.get_provider(settings)
    if provider and t["remote_id"]:
        try:
            provider.cancel(t["kind"], t["remote_id"])
        except providers.ProviderError:
            pass  # muse2api has no cancel endpoint: the remote job just finishes unused
    _update(settings.database_path, tid, status="CANCELLED", error="Người dùng huỷ")
    _finish_jobs(settings, {t["job_id"]})
    return True
