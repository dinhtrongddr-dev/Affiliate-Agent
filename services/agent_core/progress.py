"""Where a job is, how far along (%) and how long it probably still takes.

Steps come from the job status plus the stage marks in work_dir/<job>/timings.json
(workflow._mark). Expected step lengths are the median of the last finished jobs of the
same branch, so the estimate learns from this machine; defaults cover a fresh install.
Muse clips add their own % reported by the gateway (gen_tasks.progress).
"""
import json
import statistics
import time
from datetime import datetime, timezone

from .db import connect, init_db

# (key, label, start mark, end mark)
STEPS = [
    ("script", "Viết kịch bản & storyboard", "script_start", "script_done"),
    ("muse", "Muse tạo clip", "muse_start", "muse_done"),
    ("render", "Dựng video & QA", "render_start", "render_done"),
]
# Seconds per step before this machine has history (docs/reports/2026-11-14-speed-plan.md).
DEFAULT_SECONDS = {"script": 420, "muse": 1500, "render": 600}

STATUS_STEP = {
    "ANALYZING": "script", "SCRIPTING": "script", "DISCOVERED": "script", "ANALYZED": "script",
    "WAITING_FOR_CAST": "muse", "WAITING_FOR_GENERATION": "muse", "WAITING_FOR_SEGMENTS": "muse",
    "GENERATION_DONE": "render", "RENDERING": "render",
}
FINISHED = {"READY_FOR_REVIEW", "APPROVED", "PUBLISHED_CONFIRMED", "DRAFT_SAVED_CONFIRMED",
            "RECONCILED_POSTED", "TIKTOK_INBOX_CONFIRMED", "DRAFT_SUBMITTED_UNCONFIRMED",
            "SUBMITTED_UNCONFIRMED"}
GEN_DONE = {"DONE", "FAILED", "CANCELLED"}

_history: dict[str, tuple[float, dict]] = {}
HISTORY_TTL = 300


def _parse(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def read_marks(settings, job_id: str) -> dict[str, float]:
    path = settings.work_dir / job_id / "timings.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: t for k, v in raw.items() if (t := _parse(v)) is not None}


def expected_seconds(settings, branch: str) -> dict[str, float]:
    """Median step length over the last 20 finished jobs of this branch (cached 5 min)."""
    key = branch or "_none"
    hit = _history.get(key)
    if hit and time.monotonic() - hit[0] < HISTORY_TTL:
        return hit[1]
    samples: dict[str, list[float]] = {k: [] for k, *_ in STEPS}
    init_db(settings.database_path)
    with connect(settings.database_path) as conn:
        rows = conn.execute(
            "SELECT id, brief_json FROM jobs WHERE status IN ('READY_FOR_REVIEW','APPROVED',"
            "'PUBLISHED_CONFIRMED','DRAFT_SAVED_CONFIRMED') ORDER BY updated_at DESC LIMIT 80").fetchall()
    seen = 0
    for row in rows:
        try:
            job_branch = (json.loads(row["brief_json"] or "{}") or {}).get("creation_branch") or ""
        except ValueError:
            job_branch = ""
        if job_branch != branch:
            continue
        marks = read_marks(settings, row["id"])
        for k, _label, a, b in STEPS:
            if a in marks and b in marks and marks[b] > marks[a]:
                samples[k].append(marks[b] - marks[a])
        seen += 1
        if seen >= 20:
            break
    result = {k: (statistics.median(v) if len(v) >= 2 else DEFAULT_SECONDS[k]) for k, v in samples.items()}
    _history[key] = (time.monotonic(), result)
    return result


def clip_progress(settings, job_id: str) -> list[dict]:
    from .videogen import engine
    engine.ensure_schema(settings.database_path)
    with connect(settings.database_path) as conn:
        rows = conn.execute(
            "SELECT scene_index, kind, status, progress, attempt FROM gen_tasks WHERE job_id=? "
            "ORDER BY scene_index, attempt", (job_id,)).fetchall()
    latest: dict[tuple, dict] = {}
    for r in rows:          # newest attempt per scene/kind
        latest[(r["kind"], r["scene_index"])] = dict(r)
    clips = []
    for (kind, index), r in sorted(latest.items(), key=lambda kv: (kv[0][0] != "image", kv[0][1])):
        pct = 100 if r["status"] in GEN_DONE else int(r["progress"] or 0)
        clips.append({"scene": index, "kind": kind, "status": r["status"], "pct": pct,
                      "attempt": r["attempt"]})
    return clips


def job_progress(settings, job: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    status = job["status"]
    try:
        branch = (json.loads(job.get("brief_json") or "{}") or {}).get("creation_branch") or ""
    except ValueError:
        branch = ""
    marks = read_marks(settings, job["id"])
    expected = expected_seconds(settings, branch)
    clips = clip_progress(settings, job["id"])
    current = STATUS_STEP.get(status)
    finished = status in FINISHED
    # A job that never needed Muse (stock/own clips) has no Muse step once its script is done.
    skip_muse = not clips and "muse_start" not in marks and (
        "script_done" in marks or current == "render" or finished)
    steps, order = [], [k for k, *_ in STEPS if not (k == "muse" and skip_muse)]
    cur_index = order.index(current) if current in order else (len(order) if finished else -1)
    total = sum(expected[k] for k in order) or 1.0
    done_weight, remaining = 0.0, 0.0
    for i, (key, label, a, b) in enumerate(s for s in STEPS if s[0] in order):
        start, end = marks.get(a), marks.get(b)
        exp = expected[key]
        if i < cur_index:
            state, fraction = "done", 1.0
        elif i == cur_index:
            state = "running"
            elapsed = max(0.0, now - start) if start else 0.0
            if key == "muse" and clips:
                fraction = sum(c["pct"] for c in clips) / (100.0 * len(clips))
            else:
                fraction = min(0.95, elapsed / exp) if exp else 0.0
            # remaining: what the fraction says, but never less than a little while running
            remaining += max(exp * (1 - fraction), 15.0)
        else:
            state, fraction = "pending", 0.0
            remaining += exp
        done_weight += exp * fraction
        step = {"key": key, "label": label, "state": state, "pct": round(fraction * 100),
                "expected_sec": round(exp)}
        if start:
            step["started_at"] = start
            step["elapsed_sec"] = round((end or now) - start) if state != "pending" else 0
        steps.append(step)
    started = marks.get("script_start") or _parse(job.get("created_at"))
    busy = current is not None
    pct = 100 if finished else round(100 * done_weight / total) if busy else None
    return {"status": status, "busy": busy, "finished": finished, "pct": pct,
            "elapsed_sec": round(now - started) if started and busy else None,
            "eta_sec": round(remaining) if busy else 0 if finished else None,
            "steps": steps, "clips": clips, "branch": branch}
