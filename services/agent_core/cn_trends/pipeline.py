"""One China-trend pass and the optional production autopilot.

run():       collect -> label new topics (AI, batched, cached) -> stage/score -> propose niches
             -> ideas for the best few topics that have none yet.
autopilot(): when switched on, turns the best NEW ideas into jobs, within a daily cap and a
             goal mix (fb_growth vs affiliate). Publishing still follows the approval rules.
"""
import json
import logging
from datetime import datetime, timezone

from . import analysis, collectors, formula, store

log = logging.getLogger("affiliate_agent.cn_trends")

LABEL_PER_RUN = 60


def run(settings, collect: bool = True, ask=None, ideas_ask=None) -> dict:
    db = settings.database_path
    store.ensure_schema(db)
    analysis.ensure_niches(settings)
    summary: dict = {}
    if collect and getattr(settings, "cn_trends_enabled", True):
        summary["collected"] = collectors.collect_all(settings)
    todo = [t for t in store.list_topics(db, 400, since_hours=96)
            if not t.get("labeled_at") or (store.hours_since(t["labeled_at"]) or 0) > 7 * 24][:LABEL_PER_RUN]
    try:
        summary["labeled"] = analysis.label_topics(settings, todo, ask=ask)
    except Exception as exc:  # noqa: BLE001 - AI down: scores still update from the numbers
        log.warning("cn labelling failed: %s", exc)
        summary["label_error"] = str(exc)[:200]
    summary["scored"] = analysis.score_all(settings)
    summary["proposed_niches"] = analysis.propose_niches(settings)
    want = int(getattr(settings, "cn_auto_ideas", 3) or 0)
    summary["ideas_for_topics"] = _ideas(settings, want, ideas_ask) if want else 0
    return summary


IDEA_RETRY_HOURS = 24
_idea_failed: dict[str, float] = {}   # topic -> time of the last failed attempt (this worker process)


def _ideas(settings, want: int, ask=None) -> int:
    """Ideas for the best `want` topics that have none: all at once, at most two waves.

    Before, topics were tried one after another and a failure (an AI time-out cost 3 minutes)
    did not count, so the loop kept trying more topics, and the same failing topics were tried
    again on every run. Now a failed topic waits IDEA_RETRY_HOURS and a run tries at most
    2 x `want` topics.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor
    db = settings.database_path
    now = time.time()
    queue = [t["key"] for t in store.list_topics(db, 100, since_hours=48, status=["NEW"])
             if (t.get("opportunity") or 0) >= 55 and t.get("stage") != "saturated"
             and now - _idea_failed.get(t["key"], 0) > IDEA_RETRY_HOURS * 3600
             and not store.list_ideas(db, topic_key=t["key"], limit=1)][:2 * want]

    def one(key):
        try:
            formula.generate_ideas(settings, key, ask=ask)
            return True
        except Exception as exc:  # noqa: BLE001
            _idea_failed[key] = time.time()
            log.warning("ideas for %s failed: %s", key, exc)
            return False

    made = 0
    while queue and made < want:
        wave, queue = queue[:want - made], queue[want - made:]
        with ThreadPoolExecutor(min(analysis.PARALLEL_AI, len(wave))) as ex:
            made += sum(ex.map(one, wave))
    return made


def _mix(text: str) -> dict[str, float]:
    out = {}
    for part in str(text or "").split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            try:
                out[k.strip()] = max(0.0, float(v))
            except ValueError:
                pass
    total = sum(out.values()) or 1.0
    return {k: v / total for k, v in out.items()} or {"fb_growth": 1.0}


def autopilot_today(settings) -> list[dict]:
    from ..db import connect
    today = datetime.now(timezone.utc).date().isoformat()
    with connect(settings.database_path) as conn:
        rows = conn.execute("SELECT id, brief_json, created_at FROM jobs WHERE created_at >= ?",
                            (today,)).fetchall()
    out = []
    for r in rows:
        brief = json.loads(r["brief_json"] or "{}")
        if brief.get("autopilot"):
            out.append({"id": r["id"], "goal": brief.get("goal") or "fb_growth"})
    return out


def autopilot(settings) -> dict:
    if not getattr(settings, "autopilot_enabled", False):
        return {"skipped": "chế độ tự động đang tắt"}
    db = settings.database_path
    done = autopilot_today(settings)
    cap = int(getattr(settings, "autopilot_daily_videos", 3) or 0)
    if len(done) >= cap:
        return {"skipped": f"đã đủ {cap} video hôm nay"}
    mix = _mix(getattr(settings, "autopilot_goal_mix", "fb_growth:70,affiliate:30"))
    floor = float(getattr(settings, "autopilot_min_score", 70) or 0)
    ideas = [i for i in store.list_ideas(db, status=["NEW"], limit=200) if (i.get("est_score") or 0) >= floor]
    created = []
    while len(done) + len(created) < cap and ideas:
        counts = {g: sum(1 for d in done + created if d["goal"] == g) for g in mix}
        total = max(1, len(done) + len(created))
        # the goal furthest below its share goes next
        goal = min(mix, key=lambda g: counts.get(g, 0) / total - mix[g])
        pick = next((i for i in ideas if i["goal"] == goal), ideas[0])
        ideas.remove(pick)
        try:
            job_id = formula.idea_to_job(settings, pick["id"])
        except Exception as exc:  # noqa: BLE001
            log.warning("autopilot idea %s failed: %s", pick["id"], exc)
            store.update_idea(db, pick["id"], status="FAILED")
            continue
        from .. import workflow
        job = workflow.get_job(db, job_id)
        brief = json.loads(job.get("brief_json") or "{}")
        brief["autopilot"] = True
        workflow.update_job(db, job_id, brief_json=json.dumps(brief, ensure_ascii=False))
        created.append({"id": job_id, "goal": pick["goal"]})
    return {"created": [c["id"] for c in created], "today": len(done) + len(created), "cap": cap}
