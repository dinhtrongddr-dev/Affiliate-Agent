"""China -> Vietnam transformation: viral formula + >= 3 original Vietnamese ideas per trend.

The source video is never downloaded or reused. Inputs: the hot-list metadata, the cover image
(Bilibili) and, optionally, screenshots/clips the owner saved, used only for analysis.
"""
import json
import logging
from pathlib import Path

import httpx

from .. import formats
from . import store

log = logging.getLogger("affiliate_agent.cn_trends")

COVER_HOSTS = ("hdslb.com", "bilibili.com", "biliimg.com")
BRANCH_FOR = {"product": "product_review"}
IDEAS_MIN = 3


def _cover(settings, topic: dict) -> Path | None:
    url = str(topic.get("cover") or "")
    if not url or not any(h in url for h in COVER_HOSTS):
        return None
    target = Path(settings.work_dir) / "_cn_covers" / f"{topic['key']}.jpg"
    if target.is_file():
        return target
    try:
        resp = httpx.get(url.replace("http://", "https://"), timeout=20,
                         headers={"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)
        return target
    except Exception as exc:  # noqa: BLE001 - analysis continues from text only
        log.info("cover download skipped: %s", exc)
        return None


def reference_frames(settings, topic: dict) -> list[Path]:
    """Frames from a reference the owner saved (image or short clip), analysis only."""
    ref = ((topic.get("meta") or {}).get("reference") or "")
    path = Path(ref) if ref else None
    if not path or not path.is_file():
        return []
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return [path]
    try:
        from ..media import extract_keyframes
        return extract_keyframes(path, Path(settings.work_dir) / "_cn_refs" / topic["key"],
                                 settings.ffmpeg_path, count=4, ffprobe_path=settings.ffprobe_path)
    except Exception as exc:  # noqa: BLE001
        log.info("reference frames skipped: %s", exc)
        return []


def ideas_prompt(topic: dict, meta: dict, recent_hooks: list[str], n_images: int) -> str:
    labels = topic.get("labels") or {}
    fmt_names = {k: v["label"] for k, v in formats.FORMATS.items()}
    return f"""Bạn là Creative Director kênh video ngắn Việt Nam (Facebook Reels + Shopee Video).
Trend Trung Quốc ({topic['platform']}): "{topic['title_zh']}" = "{topic.get('title_vi') or ''}".
Tóm tắt: {topic.get('summary_vi') or ''}
Số liệu/metadata: {json.dumps(meta, ensure_ascii=False)[:1200]}
Nhãn: ngách {topic.get('niche')}, giai đoạn {topic.get('stage')}, định dạng gợi ý {labels.get('formats')}.
{"Kèm " + str(n_images) + " ảnh tham khảo (ảnh bìa / ảnh chủ kênh lưu) chỉ để hiểu công thức." if n_images else ""}

Bước 1 - formula: phân tích CƠ CHẾ hấp dẫn, không mô tả lại video:
hook_mechanism (1-3 giây đầu giữ người xem bằng gì), retention_device (điều khiến xem tiếp),
climax_or_twist, share_trigger (vì sao người ta chia sẻ), reusable_formula (công thức dùng lại được),
vn_adaptation (cần đổi gì cho văn hoá/khán giả Việt Nam), do_not_copy (những gì KHÔNG được chép).
Bước 2 - ideas: ÍT NHẤT 3 ý tưởng GỐC cho Việt Nam dùng công thức đó (bối cảnh, nhân vật, nội dung mới;
không dịch lại tiêu đề hay câu chữ gốc). Mỗi ý tưởng:
title, premise (2-3 câu), hook (<= 8 từ), format (một trong {json.dumps(fmt_names, ensure_ascii=False)}),
duration_s (theo định dạng), niche, goal ("fb_growth" nội dung giữ chân/chia sẻ, hoặc "affiliate" khi có
sản phẩm Shopee gắn tự nhiên), product_keywords (khi goal=affiliate), est_score 0-100 (khả năng viral thật,
khắt khe), why (1 câu), needs_facts (true nếu có khẳng định kiến thức cần kiểm chứng).
Không trùng ý với các hook kênh đã làm: {json.dumps(recent_hooks[:12], ensure_ascii=False)}
Trả DUY NHẤT JSON: {{"formula":{{"hook_mechanism":"","retention_device":"","climax_or_twist":"",
"share_trigger":"","reusable_formula":"","vn_adaptation":"","do_not_copy":""}},
"ideas":[{{"title":"","premise":"","hook":"","format":"story","duration_s":25,"niche":"","goal":"fb_growth",
"product_keywords":[],"est_score":70,"why":"","needs_facts":false}}]}}"""


def generate_ideas(settings, key: str, ask=None) -> list[str]:
    """Formula + ideas for one topic (one AI call, with images when available)."""
    db = settings.database_path
    topic = store.get_topic(db, key)
    if not topic:
        raise ValueError("Không có trend này")
    if topic.get("status") == "BLOCKED":
        raise ValueError("Trend nhạy cảm: không làm video")
    obs = store.observations(db, key, 1)
    meta = json.loads(obs[-1]["meta_json"] or "{}") if obs else {}
    if obs:
        meta.update({k: obs[-1].get(k) for k in ("rank", "heat", "views", "likes", "shares", "comments")
                     if obs[-1].get(k) is not None})
    topic["meta"] = meta
    images = [p for p in [_cover(settings, topic)] if p] + reference_frames(settings, topic)
    from ..workflow import _recent_hooks
    prompt = ideas_prompt(topic, meta, _recent_hooks(settings), len(images))
    if ask is None:
        from ..workflow import _ask_json, _client
        client = _client(settings)
        reply = _ask_json(client, settings, prompt, images or None, temperature=0.8, max_tokens=4000)
    else:
        reply = ask(prompt, images)
    formula = reply.get("formula") if isinstance(reply.get("formula"), dict) else {}
    store.update_topic(db, key, formula_json={k: str(v)[:500] for k, v in formula.items()},
                       status="IDEAS" if topic.get("status") in (None, "NEW") else topic["status"])
    saved = []
    for raw in (reply.get("ideas") or [])[:6]:
        if not isinstance(raw, dict) or not str(raw.get("title") or "").strip():
            continue
        fmt = raw.get("format") if raw.get("format") in formats.FORMATS else "story"
        low, high = formats.FORMATS[fmt]["duration"]
        try:
            duration = int(min(high, max(low, float(raw.get("duration_s") or (low + high) / 2))))
        except (TypeError, ValueError):
            duration = int((low + high) / 2)
        goal = "affiliate" if raw.get("goal") == "affiliate" else "fb_growth"
        try:
            est = max(0.0, min(100.0, float(raw.get("est_score") or 0)))
        except (TypeError, ValueError):
            est = 0.0
        saved.append(store.save_idea(db, {
            "topic_key": key, "title": str(raw["title"])[:150], "premise": str(raw.get("premise") or "")[:800],
            "hook": str(raw.get("hook") or "")[:80], "format": fmt, "duration": duration,
            "niche": str(raw.get("niche") or topic.get("niche") or "")[:40], "goal": goal,
            "branch": BRANCH_FOR.get(fmt, "viral_creator"),
            # blend the AI's own guess with the measured trend opportunity
            "est_score": round(0.6 * est + 0.4 * float(topic.get("opportunity") or est), 1),
            "data": {"why": str(raw.get("why") or "")[:300], "needs_facts": bool(raw.get("needs_facts")),
                     "product_keywords": [str(k)[:60] for k in (raw.get("product_keywords") or [])][:4]},
        }))
    if len(saved) < IDEAS_MIN:
        log.warning("only %s ideas for %s", len(saved), key)
    return saved


def idea_to_job(settings, idea_id: str) -> str:
    """Turn an idea into a production job on the existing pipeline (storyboard v2, Muse, QA)."""
    from .. import workflow
    db = settings.database_path
    idea = store.get_idea(db, idea_id)
    if not idea:
        raise ValueError("Không có ý tưởng này")
    if idea.get("job_id"):
        return idea["job_id"]
    topic = store.get_topic(db, idea["topic_key"]) or {}
    fmt = formats.get(idea.get("format")) or formats.FORMATS["story"]
    offer = None
    branch = idea.get("branch") or "viral_creator"
    if idea.get("goal") == "affiliate" or branch == "product_review":
        offer = _verified_offer(settings, idea, topic)
        branch = "product_review" if offer else "viral_creator"
    notes = (f"{idea.get('premise') or ''}\nHook dự kiến: {idea.get('hook') or ''}\n"
             f"Định dạng: {fmt['label']}")
    job_id = workflow.create_trend_job(settings, idea["title"], notes,
                                       keywords=(idea.get("data") or {}).get("product_keywords") or [],
                                       offer=offer, creation_branch=branch)
    job = workflow.get_job(db, job_id)
    brief = json.loads(job.get("brief_json") or "{}")
    low, high = fmt["duration"]
    target = float(idea.get("duration") or (low + high) / 2)
    brief.update({
        "format": idea.get("format") or "story", "niche": idea.get("niche"), "goal": idea.get("goal"),
        "duration_range": [max(low, target - 8), min(high, target + 8)],
        "formula": topic.get("formula") or {}, "idea_id": idea_id,
        "trend_source": {"platform": topic.get("platform"), "title_zh": topic.get("title_zh"),
                         "title_vi": topic.get("title_vi"), "summary_vi": topic.get("summary_vi"),
                         "url": topic.get("url"), "stage": topic.get("stage"),
                         "opportunity": topic.get("opportunity")},
    })
    workflow.update_job(db, job_id, brief_json=json.dumps(brief, ensure_ascii=False))
    store.update_idea(db, idea_id, status="QUEUED", job_id=job_id)
    store.update_topic(db, idea["topic_key"], status="USED")
    workflow.enqueue_task(db, "process", job_id)
    return job_id


def _verified_offer(settings, idea: dict, topic: dict) -> dict | None:
    """Affiliate ideas need a catalog product that passes the same AI pair check as trends."""
    keywords = (idea.get("data") or {}).get("product_keywords") or \
        (topic.get("labels") or {}).get("product_keywords_vi") or []
    if not keywords:
        return None
    from .. import catalog
    from ..trend_intel.matcher import match_products, verify_matches
    probe = {"key": "idea:" + idea["id"], "keyword": idea["title"],
             "intent": {"product_keywords": keywords, "purchase_intent": 60}}
    found = match_products(settings, probe, limit=5)
    if not found:
        return None
    try:
        kept = verify_matches(settings, [(probe, found)]).get(probe["key"], [])
    except Exception as exc:  # noqa: BLE001
        log.warning("idea product check failed: %s", exc)
        return None
    if not kept:
        return None
    product = catalog.get_product(settings.database_path, kept[0]["product_id"])
    if not product or not product.get("affiliate_link"):
        return None
    from ..web.creation import product_offer
    offer, _ = product_offer(settings, product)
    return offer
