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
MAX_ATTEMPTS = 3  # first try + two retries (Muse sometimes answers with text only)
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
        cols = {r[1] for r in conn.execute("PRAGMA table_info(gen_tasks)")}
        if "progress" not in cols:   # additive migration: % reported by the gateway
            conn.execute("ALTER TABLE gen_tasks ADD COLUMN progress INTEGER NOT NULL DEFAULT 0")
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

STYLE = ("Vertical 9:16 video filmed on a phone, handheld, natural light, no on-screen text, "
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
            f"{plan['character']}. Smartphone photo, real skin texture. {STYLE}")


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


def storyboard_prompt(scene: dict, plan: dict, references: list[dict],
                      duration: int = 10, style_override: str | None = None) -> str:
    """Short structured Muse prompt; reference identity is kept as an internal line only."""
    from ..muse_prompt import INTERNAL, compile_shot
    text = compile_shot(scene, plan, references, duration, style_override)
    # task_id sorts paths; keep order and file contents in the stored identity so a reordered
    # or replaced file at the same path is a new task. Stripped before sending.
    identity = [[r["id"], r["role"], r["sha256"]] for r in references]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return text + f"\n{INTERNAL}refs:{digest}"


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
        if not (str(scene.get("gen_prompt") or "").strip()
                or str((scene.get("shot") or {}).get("action") or "").strip()):
            raise providers.ProviderError(f"Cảnh {index + 1} thiếu hành động/prompt tạo video")
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
            float(scene.get("duration") or settings.videogen_clip_seconds)
        except (ValueError, TypeError, OverflowError) as exc:
            raise providers.ProviderError(f"Thời lượng cảnh {index + 1} không hợp lệ") from exc
        # Muse returns ~10 s regardless; ask for a full take and let the edit pick the best part.
        duration = 10
        missing = [c["id"] for c in _scene_cast(scene, plan)
                   if not any(r["role"] == "character:" + c["id"] for r in refs)]
        sheets = [CHARREF + cid for cid in missing] if _wants_sheets(settings, plan) else []
        prompt = storyboard_prompt(scene, plan, refs, duration,
                                   getattr(settings, "style_profile", "") or None)
        compiled.append((index, prompt, [r["path"] for r in refs] + sheets, duration))
    return compiled


CHARREF = "charref:"
MAX_SHEETS = 4


def _scene_cast(scene, plan):
    from ..muse_prompt import scene_cast
    return scene_cast(scene, plan)


def _wants_sheets(settings, plan) -> bool:
    return bool(getattr(settings, "videogen_character", True) and plan.get("characters"))


def _sheet_characters(plan, compiled) -> list[dict]:
    """Characters that need an app-made reference photo (no uploaded/template image)."""
    wanted = []
    for _, _, refs, _ in compiled:
        for ref in refs:
            if ref.startswith(CHARREF) and ref[len(CHARREF):] not in wanted:
                wanted.append(ref[len(CHARREF):])
    chars = {c["id"]: c for c in plan.get("characters") or [] if c.get("id")}
    return [chars[cid] for cid in wanted if cid in chars][:MAX_SHEETS]


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
    from ..muse_prompt import character_sheet_prompt
    style = getattr(settings, "style_profile", "") or None
    sheets = []   # (scene_index, prompt, depends_on) - chained so cast photos come first
    if provider.supports("image"):
        for k, character in enumerate(_sheet_characters(plan, compiled)):
            prompt = character_sheet_prompt(character, plan, style)
            sheets.append((-(k + 1), prompt, None))   # cast photos are made in parallel
    retained = {task_id(job_id, i, "video", prompt, refs)
                for i, prompt, refs, _ in compiled}
    wanted_sheets = {(i, prompt) for i, prompt, _ in sheets}
    retained.update(t["id"] for t in list_tasks(db, job_id, 10000)
                    if t["kind"] == "image" and (t["scene_index"], t["prompt"]) in wanted_sheets)
    retained.update(task_id(job_id, i, "image", prompt, []) for i, prompt, _ in sheets)
    retained.update(t["id"] for t in existing)
    # a character photo that was already made (any attempt) is reused, never regenerated
    done_sheets = {(t["scene_index"], t["prompt"]) for t in list_tasks(db, job_id, 10000)
                   if t["kind"] == "image" and t["status"] == "DOWNLOADED"}
    with connect(db) as conn:
        conn.execute(
            f"UPDATE gen_tasks SET status='CANCELLED', error=?, updated_at=? WHERE job_id=? "
            f"AND status IN ({','.join('?' * len(ACTIVE))}) "
            f"AND id NOT IN ({','.join('?' * len(retained))})",
            ("Kịch bản mới thay thế", now_iso(), job_id, *ACTIVE, *retained))
    last_sheet = None
    rows = list_tasks(db, job_id, 10000)
    for index, prompt, depends in sheets:
        tid = task_id(job_id, index, "image", prompt, [])
        if (index, prompt) not in done_sheets:
            tries = sorted((t for t in rows if t["kind"] == "image" and t["scene_index"] == index
                            and t["prompt"] == prompt), key=lambda t: t["attempt"])
            if tries and tries[-1]["status"] in ("FAILED", "CANCELLED"):
                tid = _insert(db, job_id=job_id, scene_index=index, kind="image",
                              provider=provider.name, depends_on=depends, prompt=prompt, refs=[],
                              attempt=tries[-1]["attempt"] + 1)   # a failed photo is made again
            elif tries:
                tid = tries[-1]["id"]
            else:
                _insert(db, job_id=job_id, scene_index=index, kind="image", provider=provider.name,
                        depends_on=depends, prompt=prompt, refs=[])
        last_sheet = tid
    # Takes of the same sequence (location group) continue from the previous take's last
    # frame (measured seam SSIM 0.73 vs 0.17 for independent clips); sequences run in
    # parallel, so only 2-3 takes wait on each other.
    links = chain_map(plan.get("scenes") or [], settings)
    by_index: dict[int, str] = {}
    for index, prompt, refs, duration in compiled:
        prev = links.get(index)
        # cast photos are awaited per clip (charref), so the clip itself does not chain on them
        depends = by_index.get(prev) if prev is not None else (None if sheets else last_sheet)
        by_index[index] = _insert(db, job_id=job_id, scene_index=index, kind="video",
                                  provider=provider.name, prompt=prompt, refs=refs,
                                  duration=duration, depends_on=depends)
    return len(compiled)


def chain_map(scenes: list[dict], settings=None) -> dict[int, int]:
    """scene index -> index of the generated take it continues from (same sequence/group,
    every scene in between belongs to that sequence too). Shared by planning and editing."""
    on = bool(getattr(settings, "videogen_continuity_chain", True)) if settings is not None else True
    links: dict[int, int] = {}
    last_take, last_group = None, None
    for i, scene in enumerate(scenes):
        group = str(scene.get("group") or "")
        if scene.get("visual") != "generate":
            if scene.get("visual") != "reuse" or group != last_group:
                last_take, last_group = None, None
            continue
        if last_take is not None and group and group == last_group and (on or scene.get("continuous")):
            links[i] = last_take
        last_take, last_group = i, group
    return links


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
    # Legacy (non-storyboard) plans have no location groups: chain only when asked.
    use_chain = bool(plan.get("continuity_chain"))
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
    if t["kind"] in ("video", "image") and t["attempt"] < MAX_ATTEMPTS:
        _insert(db, job_id=t["job_id"], scene_index=t["scene_index"], kind=t["kind"],
                provider=t["provider"], prompt=t["prompt"], refs=t["refs"],
                duration=t["duration"], depends_on=t["depends_on"], attempt=t["attempt"] + 1)
        log.info("gen task %s failed (%s); retry scheduled", t["id"], error)


def _submit(settings, provider, t: dict) -> None:
    db = settings.database_path
    if t["status"] == "PENDING" and t["kind"] == "video":
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
    refs = _resolve_refs(db, t)
    if refs is None:
        return  # character reference photos still generating
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
            elif (dep["result_path"] and Path(dep["result_path"]).is_file()
                  and Path(dep["result_path"]) not in refs
                  and not any(str(r).startswith(CHARREF) for r in t["refs"])):
                refs.append(Path(dep["result_path"]))
    if t["status"] == "PENDING":
        from ..trend_intel import store as usage_store
        if not usage_store.consume(db, "videogen", settings.videogen_daily_limit, kind="day"):
            _update(db, t["id"], error="Hết hạn mức tạo clip hôm nay (AA_VIDEOGEN_DAILY_LIMIT)")
            return
    _update(db, t["id"], status="SUBMITTING")
    try:
        from ..muse_prompt import strip_internal
        prompt = strip_internal(t["prompt"])
        if t["kind"] == "image":
            remote = provider.submit_image(prompt, refs, t["id"])
        else:
            remote = provider.submit_video(prompt, refs, t["duration"] or 10, t["id"])
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


def _resolve_refs(db, t: dict) -> list[Path] | None:
    """Files to attach; "charref:<id>" becomes this job's generated photo of that character.
    None means a needed character photo is not ready yet (wait); a failed one is skipped."""
    refs: list[Path] = []
    sheets = None
    for raw in t["refs"]:
        if not str(raw).startswith(CHARREF):
            if Path(raw).is_file():
                refs.append(Path(raw))
            continue
        cid = str(raw)[len(CHARREF):]
        if sheets is None:
            sheets = [x for x in list_tasks(db, t["job_id"], 10000)
                      if x["kind"] == "image" and x["scene_index"] < 0]
        mine = sorted((x for x in sheets if f"cast:{cid}" in x["prompt"]), key=lambda x: x["attempt"])
        if not mine:
            continue
        latest = mine[-1]
        if latest["status"] in ACTIVE:
            return None
        if latest["status"] == "DOWNLOADED" and latest["result_path"] and Path(latest["result_path"]).is_file():
            refs.append(Path(latest["result_path"]))
    return refs


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
        _update(db, t["id"], error=None, progress=_pct(st.progress))
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
        if t["kind"] == "image":
            from ..shared_cast import save_to_series
            save_to_series(settings, t["job_id"], t, out)


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
            # cast photos first (clips wait for them), then oldest jobs first
            " ORDER BY CASE kind WHEN 'image' THEN 0 ELSE 1 END, created_at, scene_index",
            ACTIVE).fetchall()]
    if provider is None:
        for t in rows:
            _update(db, t["id"], status="FAILED", error="Chưa cấu hình nhà cung cấp video AI")
            touched.add(t["job_id"])
        return {"tasks": len(rows), "resumed": _finish_jobs(settings, touched)}
    # Muse renders several clips at once; cap what is in flight so the account is not flooded.
    cap = max(1, int(getattr(settings, "videogen_parallel", 6) or 6))
    in_flight = sum(1 for t in rows if t["status"] == "SUBMITTED" and t["remote_id"])
    for t in rows:
        touched.add(t["job_id"])
        try:
            if t["status"] in ("PENDING", "SUBMITTING") or not t["remote_id"]:
                if t["status"] == "PENDING" and in_flight >= cap:
                    continue
                before = t["status"]
                _submit(settings, provider, t)
                if before == "PENDING" and (get_task(db, t["id"]) or {}).get("status") == "SUBMITTED":
                    in_flight += 1
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


def regenerate_scenes(settings, job_id: str, scene_indexes: list[int]) -> int:
    """Quality gate: make the clip of each listed scene again (new attempt, same prompt/refs).
    Scenes that reuse another scene's clip regenerate that source clip instead."""
    db = settings.database_path
    tasks = [t for t in list_tasks(db, job_id, 10000) if t["kind"] == "video"]
    made = 0
    for index in sorted(set(scene_indexes)):
        mine = sorted((t for t in tasks if t["scene_index"] == index), key=lambda t: t["attempt"])
        if not mine or mine[-1]["status"] in ACTIVE:
            continue
        last = mine[-1]
        _insert(db, job_id=job_id, scene_index=index, kind="video", provider=last["provider"],
                prompt=last["prompt"], refs=last["refs"], duration=last["duration"], depends_on=None,
                attempt=last["attempt"] + 1)
        made += 1
    if made:
        update_job(db, job_id, status=WAIT_STATUS)
    return made


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


def _pct(value) -> int:
    """Gateway progress (0-100, may be a string) clamped below 100 until the clip is checked."""
    try:
        return max(0, min(99, int(float(value or 0))))
    except (TypeError, ValueError):
        return 0
