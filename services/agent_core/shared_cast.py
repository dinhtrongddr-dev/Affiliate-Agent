"""Cast photos shared between jobs: the segments of a long video and the episodes of a series
use the same character photos, so faces stay the same from part to part and episode to episode.

- The first job (segment 1 / episode 1) makes the photos; later jobs set
  ``brief["shared_cast_from"]`` and wait (status WAITING_FOR_CAST) until those photos exist.
- A series keeps the photos for good: they are saved into its template (rights "owned"), so
  every later episode starts with them and Muse makes no new photos.
"""
import logging
from pathlib import Path

log = logging.getLogger("affiliate_agent.shared_cast")

WAIT_STATUS = "WAITING_FOR_CAST"


def photos(settings, job_id: str) -> dict[str, Path]:
    """character id -> downloaded photo made for that job (latest attempt)."""
    from .videogen import engine
    best: dict[str, tuple[int, Path]] = {}
    for t in engine.list_tasks(settings.database_path, job_id, 10000):
        if t["kind"] != "image" or t["status"] != "DOWNLOADED" or not t["result_path"]:
            continue
        marker = next((ln for ln in str(t["prompt"]).splitlines() if "cast:" in ln), "")
        cid = marker.split("cast:", 1)[1].strip() if marker else ""
        path = Path(t["result_path"])
        if cid and path.is_file() and t["attempt"] >= best.get(cid, (0, path))[0]:
            best[cid] = (t["attempt"], path)
    return {cid: p for cid, (_, p) in best.items()}


def ready(settings, source_job: str) -> bool:
    """The source job has finished making its photos (or never needed any)."""
    from .db import get_job
    from .videogen import engine
    job = get_job(settings.database_path, source_job)
    if not job:
        return True
    tasks = engine.list_tasks(settings.database_path, source_job, 10000)
    images = [t for t in tasks if t["kind"] == "image"]
    if any(t["status"] in engine.ACTIVE for t in images):
        return False
    # photos are planned together with the clips: before that, the source is still writing
    return bool(tasks) or job["status"] in {"READY_FOR_REVIEW", "NEEDS_MEDIA_REVIEW", "RENDER_FAILED",
                                            "GENERATION_DONE", "CONTENT_GENERATION_FAILED"}


def references(settings, brief: dict) -> list[tuple[str, str, Path]]:
    """(reference id, role, path) for the shared photos this job should attach."""
    source = brief.get("shared_cast_from")
    if not source:
        return []
    return [(f"cast-{cid}", f"character:{cid}", path) for cid, path in photos(settings, source).items()]


def save_to_series(settings, job_id: str, task: dict, path: Path) -> None:
    """Keep a new series character photo in the template, once per character."""
    import json

    from . import creative_series
    from .db import get_job
    job = get_job(settings.database_path, job_id)
    brief = json.loads((job or {}).get("brief_json") or "{}")
    template = brief.get("template") or {}
    if brief.get("creation_branch") != "template_series" or not template.get("id"):
        return
    marker = next((ln for ln in str(task["prompt"]).splitlines() if "cast:" in ln), "")
    cid = marker.split("cast:", 1)[1].strip() if marker else ""
    if not cid:
        return
    try:
        current = creative_series.get_template(settings, template["id"])
        if any(r.get("character_id") == cid for r in current.get("reference_assets") or []):
            return
        creative_series.add_reference(settings, template["id"], cid, path.name, path.read_bytes(),
                                      rights="owned")
        log.info("series %s: saved the photo of %s for later episodes", template["id"], cid)
    except Exception as exc:  # noqa: BLE001 - the episode itself is unaffected
        log.warning("could not save series photo %s: %s", cid, exc)
