"""Content-quality bench: measure the pipeline before/after each upgrade phase.

Usage (from repo root, venv active, PYTHONPATH=services):
    python scripts/bench_content.py                 # matcher + storyboards of existing jobs (no AI, no Muse)
    python scripts/bench_content.py --ai            # also draft 5 fixed briefs with the real AI (no Muse)
    python scripts/bench_content.py --out docs/reports/bench-2026-11-13.json

Nothing is sent to Muse: storyboards are compiled into the exact Muse prompts and measured.
"""
import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from agent_core.settings import get_settings  # noqa: E402

BANNED = ("cinematic", "photorealistic", "studio", "Reference manifest digest",
          "Dialogue is script context only")
WORDS_PER_SECOND = 3.5

BRIEFS = [
    {"creation_branch": "viral_creator", "topic": "Ai là người hiểu mẹ nhất",
     "notes": "Cả nhà thi xem ai hiểu mẹ nhất, kết ấm áp."},
    {"creation_branch": "viral_creator", "topic": "Con chó chạy ngoài đồng",
     "notes": "Một chú chó quê chạy ngoài đồng lúa, kết bất ngờ dễ thương."},
    {"creation_branch": "product_review", "catalog_query": "cờ vua"},
    {"creation_branch": "product_review", "catalog_query": "ốp lưng"},
    {"creation_branch": "template_series", "topic": "Suri giúp bố nấu cơm",
     "notes": "Tập ngắn hài gia đình."},
]


def plan_metrics(settings, plan: dict) -> dict:
    from agent_core.storyboard_support import attach_references
    from agent_core.videogen import engine
    scenes = plan.get("scenes") or []
    plan = json.loads(json.dumps(plan))
    if plan.get("creation_branch"):
        brief = {"creation_branch": plan["creation_branch"]}
        if plan["creation_branch"] == "template_series" and not plan.get("characters"):
            from agent_core.creation_branches import normalise_brief
            brief = normalise_brief("template_series")
        if not plan.get("reference_manifest"):
            try:
                attach_references(plan, brief, [])
            except Exception:  # noqa: BLE001 - series cast images not created yet
                for scene in plan.get("scenes") or []:
                    scene["reference_ids"] = []
                for character in plan.get("characters") or []:
                    character.pop("reference_id", None)
                attach_references(plan, brief, [])
        try:
            from agent_core.muse_prompt import strip_internal
            compiled = engine._compile_storyboard(settings, plan)
            prompts = [strip_internal(c[1]) for c in compiled]   # what Muse actually receives
            refs = [len(c[2]) for c in compiled]
        except Exception as exc:  # noqa: BLE001
            prompts, refs = [], []
            plan["_compile_error"] = str(exc)
    else:
        prompts = [engine.video_prompt(s, plan, False, False) for s in scenes
                   if s.get("visual") == "generate"]
        refs = [0] * len(prompts)
    duration = sum(float(s.get("duration") or 0) for s in scenes)
    overfull = 0
    for s in scenes:
        words = len(str(s.get("voiceover") or "").split())
        if s.get("duration") and words > float(s["duration"]) * WORDS_PER_SECOND:
            overfull += 1
    text_layers = int(bool(plan.get("hook"))) + int(any(s.get("text") for s in scenes)) + int(
        any(s.get("voiceover") for s in scenes)) + int(bool(plan.get("cta")))
    hook = str(plan.get("hook") or "")
    return {
        "branch": plan.get("creation_branch") or plan.get("mode") or "",
        "title": plan.get("title"),
        "hook": hook,
        "hook_opener": (re.findall(r"\w+", hook.lower()) or [""])[0],
        "scenes": len(scenes),
        "generate_scenes": len(prompts),
        "planned_duration_s": round(duration, 1),
        "muse_prompt_chars_max": max((len(p) for p in prompts), default=0),
        "muse_prompt_chars_avg": round(statistics.mean(len(p) for p in prompts), 0) if prompts else 0,
        # the "Avoid: no cinematic look ..." list is an instruction, not a use of the word
        "banned_terms": sorted({b for p in prompts for b in BANNED
                                if b.lower() in p.split("Avoid:")[0].lower()}),
        "scenes_with_refs": sum(1 for r in refs if r),
        "scenes_voice_overfull": overfull,
        "text_layers": text_layers,
        "music_mood": plan.get("music_mood"),
        "hashtags": plan.get("hashtags") or [],
        "compile_error": plan.get("_compile_error"),
    }


def bench_matcher(settings, verify: bool = False) -> dict:
    from agent_core import catalog
    from agent_core.trend_intel import matcher
    import sqlite3
    labels = json.loads((ROOT / "scripts/bench/match_labels.json").read_text(encoding="utf-8"))
    by_trend: dict[str, dict[str, bool]] = {}
    for pair in labels["pairs"]:
        by_trend.setdefault(pair["trend"], {})[pair["product_id"]] = pair["ok"]
    conn = sqlite3.connect(settings.database_path)
    conn.row_factory = sqlite3.Row
    products = catalog.list_products(settings.database_path, "score", limit=2000)
    tp = fp = fn = 0
    details = []
    for trend, truth in by_trend.items():
        row = conn.execute("SELECT * FROM trend_topics WHERE keyword=?", (trend,)).fetchone()
        if not row:
            continue
        topic = {**dict(row), "intent": json.loads(row["intent_json"] or "{}")}
        found = {m["product_id"] for m in matcher.match_products(settings, topic, products=products)}
        stored = {r["product_id"] for r in conn.execute(
            "SELECT product_id FROM trend_products WHERE topic_key=?", (row["key"],))}
        for pid, ok in truth.items():
            kept = pid in stored
            if ok and kept:
                tp += 1
            elif not ok and kept:
                fp += 1
            elif ok and not kept:
                fn += 1
        details.append({"trend": trend, "matcher_now": len(found), "stored": len(stored),
                        "bad_stored": sum(1 for p in stored if truth.get(p) is False)})
    precision = tp / (tp + fp) if tp + fp else None
    if verify:
        from agent_core.trend_intel.matcher import verify_matches
        items, rows = [], {}
        for trend in by_trend:
            row = conn.execute("SELECT * FROM trend_topics WHERE keyword=?", (trend,)).fetchone()
            if not row:
                continue
            topic = {**dict(row), "intent": json.loads(row["intent_json"] or "{}")}
            rows[topic["key"]] = trend
            items.append((topic, matcher.match_products(settings, topic, products=products)))
        kept = verify_matches(settings, [(t, f) for t, f in items if f])
        ntp = nfp = nlost = 0
        for topic, _ in items:
            truth = by_trend[rows[topic["key"]]]
            now = {m["product_id"] for m in kept.get(topic["key"], [])}
            ntp += sum(1 for p in now if truth.get(p) is True)
            nfp += sum(1 for p in now if truth.get(p) is False)
            nlost += sum(1 for p, ok in truth.items() if ok and p not in now)
        details.append({"now_verified": {"true_pos": ntp, "false_pos": nfp, "good_lost": nlost,
                                         "precision": round(ntp / (ntp + nfp), 3) if ntp + nfp else None}})
    return {"labelled_pairs": len(labels["pairs"]), "precision_stored": precision and round(precision, 3),
            "false_matches_stored": fp, "good_lost": fn, "per_trend": details}


def existing_jobs(settings) -> list[dict]:
    out = []
    for state in sorted(Path(settings.work_dir).glob("*/plan_state.json")):
        try:
            plan = json.loads(state.read_text(encoding="utf-8"))["plan"]
        except (OSError, ValueError, KeyError):
            continue
        out.append({"job_id": state.parent.name, **plan_metrics(settings, plan)})
    return out


def draft_with_ai(settings) -> list[dict]:
    from agent_core import catalog, creative, workflow
    from agent_core.creation_branches import normalise_brief
    client = workflow._client(settings)
    results = []
    cache_path = ROOT / "cache" / "bench_drafts.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    for raw in BRIEFS:
        key = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        if key in cache:
            cached = cache[key]
            results.append({**cached["meta"], **plan_metrics(settings, cached["plan"])})
            print(f"  cached: {cached['meta']['brief']}", flush=True)
            continue
        branch = raw["creation_branch"]
        brief = {**normalise_brief(branch), **{k: v for k, v in raw.items() if k != "catalog_query"}}
        product = {}
        if raw.get("catalog_query"):
            hits = [p for p in catalog.list_products(settings.database_path, "score", limit=2000)
                    if raw["catalog_query"] in (p.get("name") or "").lower()]
            if hits:
                product = {"name": hits[0]["name"], "price_min": hits[0].get("price"),
                           "sales": hits[0].get("sold"), "shop_name": hits[0].get("shop_name")}
                brief["topic"] = hits[0]["name"]
        timings, calls = {}, 0
        t = time.time()
        analysis = workflow._ask_json(client, settings, creative.branch_ideation_prompt(brief),
                                      temperature=0.5, max_tokens=1800)
        timings["ideation_s"], calls = round(time.time() - t, 1), calls + 1
        t = time.time()
        prompt = creative.branch_plan_prompt({**brief, "analysis": analysis}, product, True, False,
                                             "", 1, settings.videogen_max_clips)
        raw_plan = workflow._ask_json(client, settings, prompt, temperature=0.7, max_tokens=7000)
        timings["storyboard_s"], calls = round(time.time() - t, 1), calls + 1
        plan = creative.normalise_plan({**raw_plan, "creation_branch": branch,
                                        "storyboard_version": 1}, "trend", None, 0)
        meta = {"brief": raw.get("topic") or raw.get("catalog_query"), **timings, "ai_calls": calls}
        cache[key] = {"meta": meta, "plan": plan}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        results.append({**meta, **plan_metrics(settings, plan)})
        print(f"  drafted: {results[-1]['brief']} ({timings})", flush=True)
    return results


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    openers = [r["hook_opener"] for r in rows if r.get("hook_opener")]
    return {
        "videos": len(rows),
        "muse_prompt_chars_max": max(r["muse_prompt_chars_max"] for r in rows),
        "with_banned_terms": sum(1 for r in rows if r["banned_terms"]),
        "generate_scenes": sum(r["generate_scenes"] for r in rows),
        "scenes_with_refs": sum(r["scenes_with_refs"] for r in rows),
        "duration_s": [r["planned_duration_s"] for r in rows],
        "voice_overfull_scenes": sum(r["scenes_voice_overfull"] for r in rows),
        "max_text_layers": max(r["text_layers"] for r in rows),
        "distinct_hook_openers": len(set(openers)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="store_true", help="draft the 5 fixed briefs with the real AI")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    settings = get_settings()
    report = {"at": datetime.now().isoformat(timespec="seconds")}
    print("matcher ...", flush=True)
    report["matcher"] = bench_matcher(settings, verify=args.ai)
    print("existing storyboards ...", flush=True)
    report["existing_jobs"] = existing_jobs(settings)
    report["existing_summary"] = summarise(report["existing_jobs"])
    if args.ai:
        print("AI drafts ...", flush=True)
        report["ai_drafts"] = draft_with_ai(settings)
        report["ai_summary"] = summarise(report["ai_drafts"])
    out = Path(args.out or ROOT / "docs/reports" / f"bench-{datetime.now():%Y-%m-%d}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k.endswith("summary") or k == "matcher"},
                     ensure_ascii=False, indent=1))
    print("saved", out)


if __name__ == "__main__":
    main()
