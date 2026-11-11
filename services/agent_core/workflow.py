"""End-to-end pipeline: analyse -> product -> script -> render -> review -> publish."""
import base64
import html
import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import creative
from .ai_client import AIClient, AIClientError
from .affiliate_client import ShopeeAffiliateClient, ShopeeAffiliateError
from .assets import VideoGenerator, pexels_video, product_image
from .db import (
    JOB_FIELDS,
    enqueue_task,
    get_job,
    list_platform_posts,
    now_iso,
    register_job,
    update_job,
    upsert_platform_post,
)
from .editor.ffmpeg import probe
from .editor.render import RenderError, extract_cover, render_timeline
from .editor.scenes import build_segments, detect_cuts, segment_thumbnails
from .media import extract_keyframes, probe_video
from .product_matcher import rank_products
from .quality import validate_facebook_reel, validate_rendered_video, validate_shopee_video
from .shopee_video import upload_shopee_video

log = logging.getLogger("affiliate_agent.workflow")

DISCLOSURE = ("Thông báo: Đây là liên kết tiếp thị liên kết; tôi có thể nhận hoa hồng "
              "nếu bạn mua hàng qua liên kết này.")

FINISHED_STATUSES = {
    "READY_FOR_REVIEW", "APPROVED", "DRAFT_SUBMITTED_UNCONFIRMED", "DRAFT_SAVED_CONFIRMED",
    "PUBLISHED_CONFIRMED", "SUBMITTED_UNCONFIRMED", "UPLOAD_NEEDS_RECONCILIATION",
}
PENDING_STATUSES = [
    "DISCOVERED", "WAITING_FOR_AI_CREDENTIALS", "WAITING_FOR_AI_SERVICE",
    "WAITING_FOR_SHOPEE_CREDENTIALS", "WAITING_FOR_SHOPEE_API", "ANALYZED",
    "CONTENT_GENERATION_FAILED", "KEYFRAME_EXTRACTION_FAILED", "RENDER_FAILED",
]
MANUAL_LINK_HINT = ("Chưa có Shopee Open API: vào tab Sản phẩm, dán link sản phẩm và link "
                    "affiliate tạo ở Shopee Affiliate → Custom Link để tiếp tục.")
RETRYABLE_STATUSES = {"WAITING_FOR_AI_SERVICE", "WAITING_FOR_SHOPEE_API"}
ROTATE_MOTION = {"zoom_in": "pan_right", "pan_right": "zoom_out", "zoom_out": "pan_left",
                 "pan_left": "zoom_in"}
RETRYABLE_POST_STATUSES = {"UPLOAD_FAILED_CONFIRMED", "QUEUED", "SCHEDULED"}


class WorkflowError(RuntimeError):
    pass


class _Stop(Exception):
    def __init__(self, result: dict):
        super().__init__(result.get("status"))
        self.result = result


def _stop(settings, job_id: str, status: str, error: Any = None, **extra) -> None:
    fields = {k: v for k, v in extra.items() if k in JOB_FIELDS}
    report = {k: v for k, v in extra.items() if k not in JOB_FIELDS}
    update_job(settings.database_path, job_id, status=status,
               last_error=str(error)[:1000] if error else None, **fields)
    result = {"job_id": job_id, "status": status, **report}
    if error:
        result["error"] = str(error)
    raise _Stop(result)


# ---------------------------------------------------------------- AI helpers

def _client(settings) -> AIClient:
    key = settings.ai_api_key.get_secret_value()
    if not key:
        raise AIClientError(
            f"AI API key is empty. Add AA_AI_API_KEY to {settings.project_root / '.env'}."
        )
    return AIClient(
        base_url=settings.ai_base_url,
        api_key=key,
        model=settings.ai_model,
        protocol=settings.ai_protocol,
        thinking_effort=settings.ai_thinking_effort,
        timeout=getattr(settings, "ai_timeout_seconds", 180),
    )


def _reply_text(reply: dict[str, Any]) -> str:
    content = reply.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
    return str(content).strip()


def _parse_json_reply(reply: dict[str, Any]) -> dict[str, Any]:
    raw = _reply_text(reply)
    raw = re.sub(r"^\s*```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise WorkflowError("AI response did not contain a JSON object") from None
        try:
            value = json.loads(raw[start:end + 1])
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"AI returned malformed JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkflowError("AI response must be a JSON object")
    return value


def _image_messages(prompt: str, frame_paths: list[Path], protocol: str) -> list[dict]:
    parts: list[dict] = [{"type": "text", "text": prompt}]
    for path in frame_paths:
        encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        if protocol.lower() == "anthropic-messages":
            parts.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded},
            })
        else:
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            })
    return [{"role": "user", "content": parts}]


def _ask_json(client: AIClient, settings, prompt: str, images: list[Path] | None = None,
              temperature: float = 0.4, max_tokens: int = 2400) -> dict:
    messages = (_image_messages(prompt, images, settings.ai_protocol) if images
                else [{"role": "user", "content": prompt}])
    return _parse_json_reply(client.chat(messages, temperature=temperature, max_tokens=max_tokens))


def _analyse_prompt(job: dict, metadata: dict) -> str:
    return f"""
Bạn là chuyên gia phân tích video affiliate tại Việt Nam.
Hãy xem các khung hình đính kèm và chỉ mô tả điều thực sự nhìn thấy.
Tên file: {job['file_name']}
Thời lượng: {metadata['duration_sec']:.2f} giây
Kích thước: {metadata['width']}x{metadata['height']}

Trả về DUY NHẤT một JSON object, không markdown, đúng các trường:
{{
  "language": "vi",
  "video_type": "product_demo|unboxing|review|lifestyle|other",
  "topic": "chủ đề ngắn",
  "visual_summary": "mô tả hình ảnh trung thực",
  "product_category": "loại sản phẩm nhìn thấy, hoặc chuỗi rỗng nếu không có",
  "visible_brand": "nhãn hiệu đọc được, hoặc chuỗi rỗng",
  "product_search_keywords": ["2-4 cụm từ tìm kiếm tiếng Việt ngắn"],
  "video_hook": "một câu mở đầu phù hợp nội dung",
  "observed_features": ["chỉ liệt kê đặc điểm quan sát được"],
  "uncertainties": ["điểm không thể xác minh từ hình ảnh"]
}}

Không suy đoán thông số, giá, xuất xứ, công dụng y tế, bảo hành hoặc hiệu quả.
Nếu video không thể hiện sản phẩm rõ ràng, để product_category rỗng và keywords là [].
""".strip()


def _normalise_hashtags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        tag = re.sub(r"[^\wÀ-ỹ]", "", str(value).replace("#", ""), flags=re.UNICODE)
        if tag:
            result.append("#" + tag[:40])
    return list(dict.fromkeys(result))[:10]


def _ai_status(exc: Exception) -> str:
    message = str(exc)
    return ("WAITING_FOR_AI_CREDENTIALS" if "401" in message or "API key" in message
            else "WAITING_FOR_AI_SERVICE")


# ---------------------------------------------------------------- products

def offer_to_product(offer: dict) -> dict:
    """Shopee productOfferV2 node -> internal product dict."""
    return {
        "item_id": str(offer.get("itemId") or offer.get("item_id") or ""),
        "name": str(offer.get("productName") or offer.get("name") or ""),
        "offer_link": offer.get("offerLink") or offer.get("offer_link") or "",
        "product_link": offer.get("productLink") or "",
        "image_url": offer.get("imageUrl") or offer.get("image_url") or "",
        "price_min": offer.get("priceMin") or offer.get("price_min"),
        "price_max": offer.get("priceMax") or offer.get("price_max"),
        "commission_rate": offer.get("commissionRate") or offer.get("commission_rate"),
        "sales": offer.get("sales"),
        "rating_star": offer.get("ratingStar") or offer.get("rating_star"),
        "shop_name": offer.get("shopName") or offer.get("shop_name"),
        "score": offer.get("score"),
    }


def _affiliate_configured(affiliate: ShopeeAffiliateClient) -> bool:
    credentials = affiliate.credentials_status()
    return bool(
        credentials.get("sections", {}).get("shopee_affiliate", {}).get("configured", False)
    )


def _load_or_extract_keyframes(settings, job: dict, source: Path) -> list[Path]:
    stored = json.loads(job.get("keyframes_json") or "[]")
    frame_paths = [Path(path) for path in stored if Path(path).is_file()]
    if len(frame_paths) >= 3:
        return frame_paths
    return extract_keyframes(
        source,
        settings.project_root / "cache" / "keyframes" / job["id"],
        settings.ffmpeg_path,
        count=3,
        ffprobe_path=settings.ffprobe_path,
    )


def _ai_pick_product(client, settings, job_id: str, frames: list[Path],
                     candidates: list[dict]) -> dict | None:
    """Visual tie-break for ambiguous matches: compare video frames to product photos."""
    work = settings.work_dir / job_id / "candidates"
    images, usable = [], []
    for cand in candidates[:4]:
        img = product_image(cand, work)
        if img:
            images.append(img)
            usable.append(cand)
    if not usable:
        return None
    names = "\n".join(f"- ứng viên {i}: {c['name']} (ảnh thứ {len(frames) + i + 1})"
                      for i, c in enumerate(usable))
    prompt = f"""
{len(frames)} ảnh đầu là khung hình video. Các ảnh sau là ảnh sản phẩm ứng viên:
{names}
Sản phẩm nào là CÙNG LOẠI và giống nhất với sản phẩm trong video?
Trả về DUY NHẤT JSON: {{"index": 0, "confidence": 0.0, "reason": "..."}}
Nếu không cái nào khớp, index = -1.
""".strip()
    try:
        choice = _ask_json(client, settings, prompt, [*frames, *images], temperature=0.0,
                           max_tokens=400)
        index, confidence = int(choice.get("index", -1)), float(choice.get("confidence", 0))
    except Exception as exc:  # noqa: BLE001
        log.warning("AI product tie-break failed: %s", exc)
        return None
    if 0 <= index < len(usable) and confidence >= 0.75:
        return usable[index]
    return None


# ---------------------------------------------------------------- job creation

def create_trend_job(settings, topic: str, notes: str = "", keywords: list[str] | None = None,
                     offer: dict | None = None, trend_id: int | None = None,
                     url: str = "") -> str:
    topic = " ".join((topic or "").split())
    if not topic and not offer:
        raise WorkflowError("A trend topic or a product is required")
    job_id = "t" + uuid.uuid4().hex[:23]
    brief = {"topic": topic or (offer or {}).get("productName", ""), "notes": notes,
             "keywords": keywords or [], "trend_id": trend_id, "reference_url": url}
    if offer:
        brief["offer"] = offer
    register_job(settings.database_path, {
        "id": job_id, "source_path": "", "sha256": f"trend:{job_id}",
        "file_name": (brief["topic"] or "trend")[:120], "mode": "trend", "brief": brief,
    })
    return job_id


# ---------------------------------------------------------------- pipeline

def process_job(settings, job_id: str, force: bool = False) -> dict:
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if job["status"] in FINISHED_STATUSES and not force:
        return {
            "job_id": job_id,
            "status": job["status"],
            "message": "Already processed; refusing to regenerate or duplicate work.",
            "output_path": job.get("output_path"),
        }
    try:
        return _process(settings, job)
    except _Stop as stop:
        return stop.result


def _process(settings, job: dict) -> dict:
    job_id = job["id"]
    mode = job.get("mode") or "base"
    brief = json.loads(job.get("brief_json") or "{}")
    db = settings.database_path

    # 1) source + keyframes ------------------------------------------------
    source, metadata, frame_paths = None, {}, []
    if mode == "base":
        source = Path(job["source_path"])
        if not source.is_file():
            _stop(settings, job_id, "SOURCE_MISSING", "Source video file no longer exists.")
        metadata = probe_video(source, settings.ffprobe_path)
        try:
            frame_paths = _load_or_extract_keyframes(settings, job, source)
        except Exception as exc:  # noqa: BLE001
            _stop(settings, job_id, "KEYFRAME_EXTRACTION_FAILED", exc)
        update_job(db, job_id, last_error=None, keyframes_json=json.dumps(
            [str(p) for p in frame_paths], ensure_ascii=False))

    try:
        client = _client(settings)
    except AIClientError as exc:
        _stop(settings, job_id, "WAITING_FOR_AI_CREDENTIALS", exc,
              keyframes=[str(p) for p in frame_paths])

    # 2) analysis ------------------------------------------------------------
    analysis = None
    if job.get("analysis_json"):
        try:
            analysis = json.loads(job["analysis_json"])
        except (TypeError, json.JSONDecodeError):
            analysis = None
    if not isinstance(analysis, dict):
        update_job(db, job_id, status="ANALYZING")
        try:
            if mode == "base":
                analysis = _ask_json(client, settings, _analyse_prompt(job, metadata),
                                     frame_paths, temperature=0.1, max_tokens=1400)
            elif brief.get("offer"):
                name = str(brief["offer"].get("productName") or brief["offer"].get("name") or "")
                analysis = {"topic": brief.get("topic") or name, "product_category": name,
                            "product_search_keywords": [name], "visual_summary": name,
                            "angle": brief.get("notes", "")}
            else:
                analysis = _ask_json(client, settings, creative.ideation_prompt(brief),
                                     temperature=0.3, max_tokens=900)
                if brief.get("keywords"):
                    analysis["product_search_keywords"] = brief["keywords"]
        except AIClientError as exc:
            _stop(settings, job_id, _ai_status(exc), exc, keyframes=[str(p) for p in frame_paths])
        except Exception as exc:  # noqa: BLE001
            _stop(settings, job_id, "AI_ANALYSIS_FAILED", exc)
        update_job(db, job_id, status="ANALYZED", ai_generated=1, last_error=None,
                   analysis_json=json.dumps(analysis, ensure_ascii=False))

    keywords = analysis.get("product_search_keywords")
    keywords = [str(v).strip() for v in keywords if str(v).strip()][:4] if isinstance(
        keywords, list) else []
    has_product = bool(job.get("product_item_id") and job.get("affiliate_link"))
    if (not analysis.get("product_category") or not keywords) and not has_product:
        _stop(settings, job_id, "NEEDS_PRODUCT_REVIEW",
              "AI could not identify a product confidently.",
              review_status="NEEDS_PRODUCT", analysis=analysis)

    # 3) product + affiliate link ------------------------------------------
    product, affiliate_link = None, str(job.get("affiliate_link") or "")
    saved_candidates = json.loads(job.get("product_candidates_json") or "[]")
    if job.get("product_item_id") and affiliate_link:
        product = next((c for c in saved_candidates
                        if str(c.get("item_id")) == str(job["product_item_id"])), None) or {
            "item_id": str(job["product_item_id"]), "name": job.get("product_name") or "",
            "offer_link": ""}
    if product is None:
        if not settings.shopee_affiliate_enabled:
            _stop(settings, job_id, "WAITING_FOR_SHOPEE_CREDENTIALS", MANUAL_LINK_HINT,
                  analysis=analysis)
        affiliate = ShopeeAffiliateClient(settings)
        try:
            if not _affiliate_configured(affiliate):
                _stop(settings, job_id, "WAITING_FOR_SHOPEE_CREDENTIALS", MANUAL_LINK_HINT)
        except _Stop:
            raise
        except Exception as exc:  # noqa: BLE001
            _stop(settings, job_id, "WAITING_FOR_SHOPEE_CREDENTIALS", exc)
        if brief.get("offer"):
            product = offer_to_product(brief["offer"])
            candidates = [product]
        else:
            offers_by_id: dict[str, dict] = {}
            try:
                for keyword in keywords[:3]:
                    for offer in affiliate.search_offers(keyword, limit=20):
                        item_id = str(offer.get("itemId") or "")
                        if item_id:
                            offers_by_id[item_id] = offer
            except ShopeeAffiliateError as exc:
                _stop(settings, job_id, "WAITING_FOR_SHOPEE_API", exc)
            context = " ".join([
                str(analysis.get("product_category") or ""),
                str(analysis.get("visual_summary") or ""),
                " ".join(keywords), str(analysis.get("visible_brand") or ""),
            ])
            offers = list(offers_by_id.values())
            match = rank_products(context, offers, min_score=68, min_margin=10)
            details = {str(o.get("itemId")): offer_to_product(o) for o in offers}
            candidates = [{**details.get(c["item_id"], {}), **c} for c in match.get("candidates", [])]
            if match.get("status") == "MATCHED":
                product = {**details.get(match["product"]["item_id"], {}), **match["product"]}
            elif candidates and frame_paths:
                product = _ai_pick_product(client, settings, job_id, frame_paths, candidates)
            update_job(db, job_id, product_candidates_json=json.dumps(candidates, ensure_ascii=False))
            if product is None:
                _stop(settings, job_id, "NEEDS_PRODUCT_REVIEW",
                      f"No safe automatic product match: {match.get('status')}",
                      review_status="NEEDS_PRODUCT", match=match)
        if product["item_id"] not in {str(c.get("item_id")) for c in candidates}:
            candidates = [product, *candidates]
        update_job(db, job_id, product_candidates_json=json.dumps(candidates, ensure_ascii=False))
        offer_url = str(product.get("offer_link") or product.get("product_link") or "")
        if not offer_url:
            _stop(settings, job_id, "NEEDS_PRODUCT_REVIEW",
                  "Matched offer did not contain a Shopee product URL.",
                  review_status="NEEDS_PRODUCT")
        try:
            affiliate_link = affiliate.create_short_link(offer_url, sub_id=job_id)
        except Exception as exc:  # noqa: BLE001
            _stop(settings, job_id, "WAITING_FOR_SHOPEE_API", exc)
        update_job(db, job_id, product_item_id=product["item_id"], product_name=product["name"],
                   affiliate_link=affiliate_link)

    return _create_and_render(settings, get_job(db, job_id), client, analysis, product,
                              affiliate_link, frame_paths, metadata)


def _create_and_render(settings, job, client, analysis, product, affiliate_link,
                       frame_paths, metadata) -> dict:
    job_id, mode = job["id"], job.get("mode") or "base"
    db = settings.database_path
    brief = json.loads(job.get("brief_json") or "{}")
    asset_dir = settings.work_dir / job_id / "assets"
    product_img = product_image(product, asset_dir)

    # 4) creative plan ---------------------------------------------------------
    update_job(db, job_id, status="SCRIPTING")
    segments: list[dict] = []
    generator = VideoGenerator(settings)
    try:
        if mode == "base":
            source = Path(job["source_path"])
            cuts = detect_cuts(settings.ffmpeg_path, source)
            segments = build_segments(cuts, float(metadata["duration_sec"]))
            thumbs = segment_thumbnails(settings.ffmpeg_path, source, segments,
                                        settings.work_dir / job_id / "segments")
            prompt = creative.base_plan_prompt(analysis, product, segments, metadata)
            try:
                raw = _ask_json(client, settings, prompt, thumbs, temperature=0.6)
                plan = creative.normalise_plan(raw, "base", segments)
            except AIClientError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("AI edit plan unusable (%s); using fallback cut", exc)
                plan = creative.normalise_plan(
                    creative.fallback_base_plan(analysis, segments), "base", segments)
        else:
            prompt = creative.trend_plan_prompt(
                {**brief, **{k: analysis.get(k) for k in ("topic", "angle")}}, product,
                generator.available, bool(settings.pexels_api_key.get_secret_value()))
            raw = _ask_json(client, settings, prompt, [product_img] if product_img else None,
                            temperature=0.7)
            plan = creative.normalise_plan(raw, "trend")
        score = None
        if settings.viral_review:
            try:
                review = _ask_json(client, settings, creative.review_prompt(
                    plan, product, settings.viral_min_score), temperature=0.3)
                score = float(review.get("score")) if review.get("score") is not None else None
                if review.get("revised") and (score is None or score < settings.viral_min_score):
                    plan = creative.normalise_plan(review["revised"], mode, segments)
                    plan["revised_after_review"] = review.get("issues") or True
                    try:  # score the rewrite so the UI shows the final script's score
                        again = _ask_json(client, settings, creative.review_prompt(
                            plan, product, 0), temperature=0.2, max_tokens=600)
                        score = float(again.get("score", score))
                    except Exception:  # noqa: BLE001
                        pass
            except AIClientError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Viral review skipped: %s", exc)
    except AIClientError as exc:
        _stop(settings, job_id, _ai_status(exc), exc)
    except Exception as exc:  # noqa: BLE001
        _stop(settings, job_id, "CONTENT_GENERATION_FAILED", exc)

    # 5) visual assets -> timeline -------------------------------------------
    timeline = build_timeline(settings, job, plan, product_img, generator, asset_dir)
    content = _content_from_plan(plan, affiliate_link)
    update_job(db, job_id, script_json=json.dumps(timeline, ensure_ascii=False),
               content_json=json.dumps(content, ensure_ascii=False), viral_score=score)
    return render_job(settings, job_id, analysis=analysis, product=product,
                      frame_paths=frame_paths)


def build_timeline(settings, job: dict, plan: dict, product_img: Path | None,
                   generator: VideoGenerator, asset_dir: Path) -> dict:
    mode = job.get("mode") or "base"
    scenes: list[dict] = []
    used_stock: set[str] = set()
    generated = 0
    for i, scene in enumerate(plan["scenes"]):
        item = {k: scene.get(k) for k in ("duration", "voiceover", "text", "motion")}
        if mode == "base":
            item.update(kind="video", source=job["source_path"], start=scene["start"],
                        segment=scene.get("segment"))
        else:
            visual = scene.get("visual")
            path, kind = None, "image"
            if visual == "generate" and generated < settings.videogen_max_clips:
                path = generator.generate(scene.get("gen_prompt") or "",
                                          asset_dir / f"gen_{i:02d}.mp4",
                                          float(scene.get("duration") or 4))
                if path:
                    generated += 1
                    kind = "video"
            if path is None and visual in {"stock", "generate"}:
                path = pexels_video(settings, scene.get("stock_query") or "", asset_dir, used_stock)
                kind = "video" if path else kind
            if path is None:
                path = product_img
                kind = "card" if visual == "card" else "image"
                # Same still image twice in a row looks static: alternate framing.
                prev = scenes[-1] if scenes else None
                if prev and prev.get("source") == str(path) and prev.get("kind") == kind:
                    kind = "card" if kind == "image" else "image"
                    item["motion"] = ROTATE_MOTION.get(prev.get("motion") or "", "zoom_out")
            if path is None:
                continue
            item.update(kind=kind, source=str(path), start=0.0, visual=visual,
                        generated=kind == "video" and visual == "generate")
        scenes.append(item)
    if mode == "base" and product_img and scenes:
        scenes.append({"kind": "card", "source": str(product_img), "duration": 2.4,
                       "voiceover": "", "text": "", "motion": "zoom_in"})
    if not scenes:
        raise WorkflowError("No visual assets available. Add Pexels/video generator keys or "
                            "pick a product with an image.")
    return {"hook": plan.get("hook", ""), "cta": plan.get("cta") or settings.cta_text,
            "music_mood": plan.get("music_mood", "upbeat"), "scenes": scenes}


def _content_from_plan(plan: dict, affiliate_link: str) -> dict:
    caption = str(plan.get("caption") or plan.get("title") or plan.get("hook") or "").strip()
    content = {
        "title": plan.get("title") or plan.get("hook") or "",
        "hook": plan.get("hook", ""),
        "caption": caption,
        "hashtags": _normalise_hashtags(plan.get("hashtags")),
        "cover_ratio": float(plan.get("cover_ratio") or 0.1),
        "facebook_description": plan.get("facebook_description") or caption,
    }
    return finalise_content(content, affiliate_link)


def finalise_content(content: dict, affiliate_link: str) -> dict:
    caption = str(content.get("caption") or "").strip()
    content["shopee_caption"] = f"{caption}\n\nLink sản phẩm: {affiliate_link}\n{DISCLOSURE}"
    content["facebook_post"] = (
        f"{str(content.get('facebook_description') or caption).strip()}\n\n"
        f"👉 Link sản phẩm: {affiliate_link}\n{DISCLOSURE}\n{' '.join(content.get('hashtags') or [])}"
    )
    content["tiktok_caption"] = (
        f"{caption} {' '.join(content.get('hashtags') or [])}\n"
        "Link sản phẩm ở bio/giỏ hàng. #affiliate #lienkettiepthi"
    )[:2200]
    content["affiliate_link"] = affiliate_link
    return content


def render_job(settings, job_id: str, analysis: dict | None = None, product: dict | None = None,
               frame_paths: list[Path] | None = None) -> dict:
    """Render (or re-render after edits) the stored timeline and rebuild the review package."""
    db = settings.database_path
    job = get_job(db, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    try:
        timeline = json.loads(job.get("script_json") or "{}")
        content = json.loads(job.get("content_json") or "{}")
    except json.JSONDecodeError as exc:
        raise WorkflowError("Stored script is not valid JSON") from exc
    if not timeline.get("scenes"):
        raise WorkflowError("Job has no script to render")
    analysis = analysis or json.loads(job.get("analysis_json") or "{}")
    if product is None:
        candidates = json.loads(job.get("product_candidates_json") or "[]")
        product = next((c for c in candidates
                        if str(c.get("item_id")) == str(job.get("product_item_id"))),
                       {"item_id": job.get("product_item_id"), "name": job.get("product_name")})
    if frame_paths is None:
        frame_paths = [Path(p) for p in json.loads(job.get("keyframes_json") or "[]")
                       if Path(p).is_file()]

    output_dir = settings.shopee_output / job_id
    final = output_dir / f"{job_id}.mp4"
    update_job(db, job_id, status="RENDERING", last_error=None)
    try:
        result = render_timeline(settings, timeline, settings.work_dir / job_id / "render",
                                 final, seed_text=job_id)
    except (RenderError, Exception) as exc:  # noqa: BLE001
        log.exception("Render failed for %s", job_id)
        update_job(db, job_id, status="RENDER_FAILED", last_error=str(exc)[:1000])
        return {"job_id": job_id, "status": "RENDER_FAILED", "error": str(exc)}

    content = finalise_content(content, job.get("affiliate_link") or content.get(
        "affiliate_link", ""))
    meta = probe(settings.ffprobe_path, final)
    cover = extract_cover(settings, final, min(result.duration * float(
        content.get("cover_ratio") or 0.1), max(0.0, result.duration - 0.5)) or 0.5,
        output_dir / "cover.jpg")
    manifest = _save_review_package(settings, job, meta, analysis, product, content,
                                    job.get("affiliate_link") or "", frame_paths, final,
                                    timeline, result, cover)
    shopee_ready = manifest["quality"]["shopee"]["passed"]
    facebook_ready = manifest["quality"]["facebook_reels"]["passed"]
    status = "READY_FOR_REVIEW" if shopee_ready else "NEEDS_MEDIA_REVIEW"
    update_job(
        db, job_id,
        status=status, review_status="PENDING",
        output_path=str(final),
        facebook_output_path=manifest["quality"]["facebook_reels"]["output"],
        content_json=json.dumps(content, ensure_ascii=False),
        script_json=json.dumps({**timeline, "render": {
            "duration": result.duration, "music": result.music, "voice": result.voice,
            "scenes": result.scenes}}, ensure_ascii=False),
        shopee_ready=int(shopee_ready), facebook_ready=int(facebook_ready),
        duration_sec=meta["duration_sec"], width=meta["width"], height=meta["height"],
        last_error=None if shopee_ready else "; ".join(manifest["quality"]["shopee"]["reasons"]),
    )
    return {
        "job_id": job_id, "status": status, "output_path": str(final),
        "shopee_ready": shopee_ready, "facebook_ready": facebook_ready, "manifest": manifest,
    }


def _write_review_page(output_dir: Path, manifest: dict) -> Path:
    def esc(value) -> str:
        return html.escape(str(value or ""), quote=True)

    content = manifest.get("content") or {}
    product = manifest.get("product") or {}
    quality = manifest.get("quality") or {}
    video_name = Path(manifest["video"]).name
    frames = []
    for frame in manifest.get("keyframes") or []:
        frame_path = Path(frame)
        try:
            relative = frame_path.relative_to(output_dir)
        except ValueError:
            continue
        frames.append(
            f'<figure><img src="{esc(relative.as_posix())}" alt="Video preview frame">'
            f'<figcaption>{esc(frame_path.name)}</figcaption></figure>'
        )
    scenes = "".join(
        f"<li><b>{esc(s.get('text') or s.get('kind'))}</b> — {esc(s.get('voiceover'))}</li>"
        for s in (manifest.get("timeline") or {}).get("scenes") or []
    )
    shopee = quality.get("shopee") or {}
    facebook = quality.get("facebook_reels") or {}
    hashtags = " ".join(content.get("hashtags") or [])
    page = f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Affiliate review - {esc(manifest.get("job_id"))}</title>
<style>
body{{font:16px/1.55 system-ui,Segoe UI,sans-serif;margin:0;background:#f4f6f8;color:#17212b}}
main{{max-width:1000px;margin:32px auto;padding:24px;background:#fff;border-radius:14px}}
video{{width:100%;max-height:75vh;background:#111;border-radius:10px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}
figure{{margin:0}}img{{width:100%;border-radius:8px}}figcaption,.muted{{color:#637080;font-size:13px}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:16px;border-radius:8px}}
a{{color:#075db8}}.status{{display:inline-block;padding:4px 10px;border-radius:20px;background:#edf2f7;margin-right:8px}}
section{{margin-top:26px}}h1{{line-height:1.2}}
</style>
</head>
<body><main>
<h1>Kiểm duyệt video affiliate</h1>
<p class="muted">Job: {esc(manifest.get("job_id"))}</p>
<video controls preload="metadata"><source src="{esc(video_name)}"></video>
<section><h2>Sản phẩm đề xuất</h2>
<p><strong>{esc(product.get("name"))}</strong></p>
<p>Mã sản phẩm: {esc(product.get("item_id"))}</p>
<p>Điểm khớp: {esc(product.get("score"))}</p>
<p><a href="{esc(manifest.get("affiliate_link"))}" target="_blank" rel="noopener noreferrer">Mở link affiliate Shopee</a></p></section>
<section><h2>Kịch bản</h2><p><b>Hook:</b> {esc((manifest.get("timeline") or {}).get("hook"))}</p><ol>{scenes}</ol></section>
<section><h2>Nội dung Shopee</h2><pre>{esc(content.get("shopee_caption"))}</pre>
<p>{esc(hashtags)}</p></section>
<section><h2>Khung hình xem trước</h2><div class="grid">{''.join(frames)}</div></section>
<section><h2>Kiểm tra chất lượng</h2>
<p><span class="status">Shopee: {"PASS" if shopee.get("passed") else "CHECK"}</span>
<span class="status">Facebook Reels: {"PASS" if facebook.get("passed") else "CHECK"}</span></p>
<p class="muted">Shopee: {esc("; ".join(shopee.get("reasons") or [])) or "Basic file integrity passed"}</p>
<p class="muted">Facebook: {esc("; ".join(facebook.get("reasons") or [])) or "Existing 9:16 QA passed"}</p></section>
<section><h2>Bước tiếp theo</h2>
<p>Duyệt trong giao diện web (Duyệt &amp; đăng), hoặc dùng lệnh:</p>
<pre>python -m agent_core.cli show {esc(manifest.get("job_id"))}
python -m agent_core.cli shopee-draft {esc(manifest.get("job_id"))} --confirm</pre>
<p class="muted">Lệnh thứ hai chỉ tải lên dạng bản nháp; không đăng công khai.</p>
</section></main></body></html>
"""
    target = output_dir / "review.html"
    target.write_text(page, encoding="utf-8")
    return target


def _save_review_package(settings, job, metadata, analysis, product, content, affiliate_link,
                         frame_paths, video_out: Path, timeline: dict, result,
                         cover: Path) -> dict:
    output_dir = video_out.parent
    preview_dir = output_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    saved_frames = []
    for frame in frame_paths:
        target = preview_dir / Path(frame).name
        if Path(frame).resolve() != target.resolve():
            shutil.copy2(frame, target)
        saved_frames.append(str(target))
    if cover.is_file():
        saved_frames.insert(0, str(cover))

    shopee_qa = validate_shopee_video(video_out, metadata)
    render_qa = validate_rendered_video(metadata, settings)
    shopee_qa.reasons.extend(render_qa.reasons)
    shopee_qa.passed = shopee_qa.passed and render_qa.passed
    facebook_qa = validate_facebook_reel(metadata, settings)
    facebook_out = None
    if facebook_qa.passed:
        fb_dir = settings.facebook_output / job["id"]
        fb_dir.mkdir(parents=True, exist_ok=True)
        facebook_out = fb_dir / video_out.name
        shutil.copy2(video_out, facebook_out)

    warnings = []
    if not result.music:
        warnings.append(f"Chưa có nhạc nền: thêm nhạc bản quyền vào {settings.music}")
    if not result.voice:
        warnings.append("Không tạo được giọng đọc (TTS); video chỉ có phụ đề.")
    manifest = {
        "job_id": job["id"], "mode": job.get("mode") or "base",
        "source": job.get("source_path"), "video": str(video_out), "cover": str(cover),
        "metadata": metadata, "analysis": analysis, "product": product,
        "affiliate_link": affiliate_link, "content": content, "timeline": timeline,
        "keyframes": saved_frames, "warnings": warnings,
        "quality": {
            "shopee": {"passed": shopee_qa.passed, "reasons": shopee_qa.reasons},
            "facebook_reels": {"passed": facebook_qa.passed, "reasons": facebook_qa.reasons,
                               "output": str(facebook_out) if facebook_out else None},
        },
        "publishing": "manual_review_required",
        "created_at": now_iso(),
    }
    (output_dir / "caption.txt").write_text(
        content["shopee_caption"] + "\n\n" + " ".join(content.get("hashtags") or []) + "\n",
        encoding="utf-8")
    manifest["review_page"] = str(_write_review_page(output_dir, manifest))
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------- manual edits

def update_script(settings, job_id: str, timeline: dict | None = None,
                  content_updates: dict | None = None) -> None:
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    fields = {}
    if timeline is not None:
        if not isinstance(timeline, dict) or not timeline.get("scenes"):
            raise WorkflowError("Script must contain scenes")
        fields["script_json"] = json.dumps(timeline, ensure_ascii=False)
    if content_updates:
        content = json.loads(job.get("content_json") or "{}")
        for key in ("title", "caption", "facebook_description"):
            if key in content_updates:
                content[key] = str(content_updates[key]).strip()
        if "hashtags" in content_updates:
            raw = content_updates["hashtags"]
            raw = raw.split() if isinstance(raw, str) else raw
            content["hashtags"] = _normalise_hashtags(raw)
        fields["content_json"] = json.dumps(
            finalise_content(content, job.get("affiliate_link") or ""), ensure_ascii=False)
    if fields:
        update_job(settings.database_path, job_id, **fields)


def select_product_for_job(settings, job_id: str, item_id: str, confirmed: bool = False) -> dict:
    """Use an explicitly user-selected candidate, then script + render."""
    if not confirmed:
        raise WorkflowError("Product selection requires --confirm; no action was taken.")
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if job["status"] in FINISHED_STATUSES - {"READY_FOR_REVIEW"}:
        raise WorkflowError("Job already moved past review; create a new job instead.")
    if not settings.shopee_affiliate_enabled:
        raise WorkflowError("Shopee Affiliate API is disabled in local settings.")
    candidates = json.loads(job.get("product_candidates_json") or "[]")
    product = next((c for c in candidates if str(c.get("item_id")) == str(item_id)), None)
    if not product:
        raise WorkflowError(
            f"Product ID {item_id} is not in this job's candidate list. "
            "Run process again or inspect the current candidate IDs.")
    offer_url = product.get("offer_link") or product.get("product_link")
    if not offer_url:
        raise WorkflowError("Selected product has no Shopee offer URL.")
    affiliate = ShopeeAffiliateClient(settings)
    if not _affiliate_configured(affiliate):
        update_job(settings.database_path, job_id, status="WAITING_FOR_SHOPEE_CREDENTIALS",
                   last_error="Shopee Affiliate App ID or Secret is missing.")
        raise WorkflowError("Shopee Affiliate App ID or Secret is missing.")
    link = affiliate.create_short_link(str(offer_url), sub_id=job_id)
    update_job(settings.database_path, job_id, product_item_id=str(product["item_id"]),
               product_name=str(product.get("name") or ""), affiliate_link=link,
               script_json=None, status="ANALYZED", review_status="PENDING")
    result = process_job(settings, job_id, force=True)
    return {**result, "selected_product": product}


def search_products_for_job(settings, job_id: str, keyword: str) -> list[dict]:
    """Manual product search from the UI; results become this job's candidates."""
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if not keyword.strip():
        raise WorkflowError("Nhập từ khoá sản phẩm")
    offers = ShopeeAffiliateClient(settings).search_offers(keyword, limit=20)
    candidates = [offer_to_product(o) for o in offers]
    update_job(settings.database_path, job_id,
               product_candidates_json=json.dumps(candidates, ensure_ascii=False))
    return candidates


def set_manual_product(settings, job_id: str, product_url: str, name: str,
                       affiliate_link: str = "", image_url: str = "") -> dict:
    """Use a Shopee product the user pasted.

    With affiliate_link (made by hand in Shopee Affiliate -> Custom Link) no Open
    API access is needed; otherwise the link is generated through the API.
    """
    from .affiliate_client import _is_shopee_vn

    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if not _is_shopee_vn(product_url.strip()):
        raise WorkflowError("Link sản phẩm phải là https://shopee.vn/...")
    match = re.search(r"i\.(\d+)\.(\d+)|/product/(\d+)/(\d+)", product_url)
    item_id = (match.group(2) or match.group(4)) if match else uuid.uuid4().hex[:10]
    product = {"item_id": str(item_id), "name": name.strip() or "Sản phẩm Shopee",
               "offer_link": product_url.strip(), "image_url": image_url.strip()}
    candidates = json.loads(job.get("product_candidates_json") or "[]")
    candidates = [product, *[c for c in candidates if str(c.get("item_id")) != product["item_id"]]]
    update_job(settings.database_path, job_id,
               product_candidates_json=json.dumps(candidates, ensure_ascii=False))
    affiliate_link = affiliate_link.strip()
    if not affiliate_link:
        return select_product_for_job(settings, job_id, product["item_id"], confirmed=True)
    if not _is_shopee_vn(affiliate_link):
        raise WorkflowError("Link affiliate phải là link Shopee (https://s.shopee.vn/... )")
    update_job(settings.database_path, job_id, product_item_id=product["item_id"],
               product_name=product["name"], affiliate_link=affiliate_link,
               script_json=None, status="ANALYZED", review_status="PENDING")
    result = process_job(settings, job_id, force=True)
    return {**result, "selected_product": product}


def regenerate_script(settings, job_id: str) -> dict:
    """Ask the AI for a fresh script (keeps analysis, product and link)."""
    update_job(settings.database_path, job_id, script_json=None, status="ANALYZED")
    return process_job(settings, job_id, force=True)


# ---------------------------------------------------------------- approval + publishing

def _local_to_utc(value: str, tz_name: str) -> str | None:
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
    except ValueError as exc:
        raise WorkflowError(f"Invalid schedule time: {value}") from exc
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo(tz_name))
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def publish_blockers(settings, job: dict, platform: str, action: str = "draft") -> list[str]:
    issues = []
    if platform == "shopee":
        if not job.get("shopee_ready"):
            issues.append("Video chưa qua QA Shopee")
        if action == "publish" and not settings.auto_publish:
            issues.append("Đăng công khai Shopee cần bật AA_AUTO_PUBLISH")
        profile = settings.project_root / "profiles" / "shopee"
        if not profile.exists():
            issues.append("Chưa đăng nhập Shopee Creator Center (mở scripts/open-shopee-login.cmd)")
    elif platform == "facebook_reels":
        if not job.get("facebook_ready"):
            issues.append("Video chưa qua QA Facebook Reels")
        if not settings.meta_enabled:
            issues.append("Chưa bật/cấu hình Facebook Page (AA_META_ENABLED)")
        if not settings.auto_publish:
            issues.append("Facebook đăng công khai cần bật AA_AUTO_PUBLISH")
    elif platform == "tiktok":
        if not settings.tiktok_enabled or not settings.tiktok_access_token.get_secret_value():
            issues.append("Chưa cấu hình TikTok (AA_TIKTOK_ENABLED + access token)")
        if settings.tiktok_mode == "direct" and not settings.auto_publish:
            issues.append("TikTok đăng trực tiếp cần bật AA_AUTO_PUBLISH (hoặc dùng chế độ inbox)")
    else:
        issues.append(f"Nền tảng không hỗ trợ: {platform}")
    return issues


def approve_job(settings, job_id: str, platforms: dict[str, str], schedule_at: str = "") -> dict:
    """Human approval from the UI: queue (optionally scheduled) uploads per platform."""
    db = settings.database_path
    job = get_job(db, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if job["status"] not in {"READY_FOR_REVIEW", "APPROVED"}:
        raise WorkflowError(f"Job is {job['status']}, not ready for approval.")
    if not platforms:
        raise WorkflowError("Chọn ít nhất một nền tảng để đăng.")
    existing = {p["platform"]: p for p in list_platform_posts(db, job_id)}
    problems = {}
    for platform, action in platforms.items():
        prev = existing.get(platform)
        if prev and prev["status"] not in {"UPLOAD_FAILED_CONFIRMED"}:
            problems[platform] = [f"Đã có lượt đăng ({prev['status']}); đối soát trước khi thử lại"]
            continue
        issues = publish_blockers(settings, job, platform, action)
        if issues:
            problems[platform] = issues
    if problems:
        raise WorkflowError("Không thể duyệt: " + json.dumps(problems, ensure_ascii=False))
    run_after = _local_to_utc(schedule_at, settings.timezone)
    for platform, action in platforms.items():
        upsert_platform_post(db, job_id, platform, "SCHEDULED" if run_after else "QUEUED",
                             scheduled_at=run_after, count_attempt=False)
        enqueue_task(db, "publish", job_id, {"platform": platform, "action": action},
                     run_after=run_after, max_attempts=1)
    update_job(db, job_id, status="APPROVED", review_status="APPROVED", approved_at=now_iso())
    return {"job_id": job_id, "status": "APPROVED", "platforms": platforms,
            "scheduled_at_utc": run_after}


def _check_previous(settings, job_id: str, platform: str) -> None:
    previous = next((p for p in list_platform_posts(settings.database_path, job_id)
                     if p["platform"] == platform), None)
    if previous and previous["status"] not in RETRYABLE_POST_STATUSES:
        raise WorkflowError(
            f"{platform} already has an upload attempt ({previous['status']}); "
            "reconcile it before retrying to avoid duplicate posts."
        )


def upload_shopee_draft(settings, job_id: str, confirmed: bool = False,
                        action: str = "draft") -> dict:
    if not confirmed:
        raise WorkflowError("Draft upload requires --confirm; no action was taken.")
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if job["status"] not in {"READY_FOR_REVIEW", "APPROVED"} or not job.get("shopee_ready"):
        raise WorkflowError("Job is not READY_FOR_REVIEW for Shopee.")
    _check_previous(settings, job_id, "shopee")
    content = json.loads(job.get("content_json") or "{}")
    if not content.get("shopee_caption") or not job.get("product_item_id"):
        raise WorkflowError("Review package is missing caption or matched product ID.")
    try:
        result = upload_shopee_video(
            settings,
            Path(job["output_path"]),
            caption=content["shopee_caption"],
            hashtags=content.get("hashtags") or [],
            cover_ratio=float(content.get("cover_ratio", 0.15)),
            product_query=job.get("product_name") or "",
            product_id=job.get("product_item_id"),
            action=action,
            confirmed=True,
        )
    except Exception as exc:
        upsert_platform_post(settings.database_path, job_id, "shopee",
                             "UPLOAD_NEEDS_RECONCILIATION", last_error=str(exc)[:1000])
        update_job(settings.database_path, job_id, status="UPLOAD_NEEDS_RECONCILIATION",
                   review_status="UPLOAD_NEEDS_RECONCILIATION", last_error=str(exc)[:1000])
        raise WorkflowError(
            "Shopee upload did not return a confirmed result. Check Creator Center "
            "before retrying. Details: " + str(exc)
        ) from exc
    status = result.get("status", "DRAFT_SUBMITTED_UNCONFIRMED")
    upsert_platform_post(
        settings.database_path, job_id, "shopee", status,
        external_id=str((result.get("result") or {}).get("video_id") or "") or None,
    )
    update_job(settings.database_path, job_id, status=status, review_status=status,
               last_error=None)
    return result


def publish_facebook_reel_job(settings, job_id: str, confirmed: bool = False) -> dict:
    """Publish a reviewed Facebook Reel only after two explicit safety gates."""
    if not confirmed:
        raise WorkflowError("Facebook publishing requires --confirm; no action was taken.")
    if not settings.auto_publish:
        raise WorkflowError("AA_AUTO_PUBLISH is false; no Facebook request was sent.")
    if not settings.meta_enabled:
        raise WorkflowError("AA_META_ENABLED is false; configure the Facebook Page first.")
    if not settings.meta_page_access_token.get_secret_value():
        raise WorkflowError("Facebook Page Access Token is missing.")
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if not job.get("facebook_ready") or not job.get("facebook_output_path"):
        raise WorkflowError("This job has not passed Facebook Reels QA.")
    video_path = Path(job["facebook_output_path"])
    if not video_path.is_file():
        raise WorkflowError(f"Facebook review video is missing: {video_path}")
    _check_previous(settings, job_id, "facebook_reels")
    content = json.loads(job.get("content_json") or "{}")
    description = str(content.get("facebook_post") or content.get("facebook_description")
                      or content.get("caption") or "").strip()
    if not description:
        raise WorkflowError("Facebook description is missing from the review package.")
    from .publish_service import publish_facebook_reel

    try:
        result = publish_facebook_reel(settings, video_path, description=description,
                                       title=str(content.get("title") or ""))
    except Exception as exc:
        upsert_platform_post(settings.database_path, job_id, "facebook_reels",
                             "UPLOAD_NEEDS_RECONCILIATION", last_error=str(exc)[:1000])
        raise WorkflowError(
            "Facebook upload outcome may require reconciliation. Check the Page "
            "before retrying. Details: " + str(exc)
        ) from exc
    upsert_platform_post(
        settings.database_path, job_id, "facebook_reels", result.get("status", "SUBMITTED_UNCONFIRMED"),
        external_id=str(result.get("video_id") or "") or None,
        permalink=str(result.get("permalink") or "") or None,
    )
    return result


def publish_tiktok_job(settings, job_id: str, confirmed: bool = False) -> dict:
    if not confirmed:
        raise WorkflowError("TikTok upload requires confirmation; no action was taken.")
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    issues = publish_blockers(settings, job, "tiktok")
    if issues:
        raise WorkflowError("; ".join(issues))
    _check_previous(settings, job_id, "tiktok")
    content = json.loads(job.get("content_json") or "{}")
    timeline = json.loads(job.get("script_json") or "{}")
    is_aigc = any(s.get("generated") for s in timeline.get("scenes") or [])
    from .tiktok import TikTokPublisher

    try:
        result = TikTokPublisher(settings).upload(
            Path(job["output_path"]), content.get("tiktok_caption") or content.get("caption", ""),
            is_aigc=is_aigc)
    except Exception as exc:
        upsert_platform_post(settings.database_path, job_id, "tiktok",
                             "UPLOAD_NEEDS_RECONCILIATION", last_error=str(exc)[:1000])
        raise WorkflowError(f"TikTok upload needs reconciliation: {exc}") from exc
    upsert_platform_post(settings.database_path, job_id, "tiktok", result["status"],
                         external_id=result.get("publish_id"))
    return result


def publish_platform(settings, job_id: str, platform: str, action: str = "draft") -> dict:
    """Worker entry point for an approved upload."""
    if platform == "shopee":
        return upload_shopee_draft(settings, job_id, confirmed=True,
                                   action="publish" if action == "publish" else "draft")
    if platform == "facebook_reels":
        return publish_facebook_reel_job(settings, job_id, confirmed=True)
    if platform == "tiktok":
        return publish_tiktok_job(settings, job_id, confirmed=True)
    raise WorkflowError(f"Unsupported platform: {platform}")


def mark_post_reconciled(settings, job_id: str, platform: str, success: bool) -> None:
    """After checking the platform by hand, record the real outcome."""
    status = "RECONCILED_POSTED" if success else "UPLOAD_FAILED_CONFIRMED"
    upsert_platform_post(settings.database_path, job_id, platform, status, count_attempt=False)
    if not success:
        job = get_job(settings.database_path, job_id)
        if job and job["status"] in {"UPLOAD_NEEDS_RECONCILIATION", "APPROVED",
                                     "DRAFT_SUBMITTED_UNCONFIRMED", "SUBMITTED_UNCONFIRMED"}:
            update_job(settings.database_path, job_id, status="READY_FOR_REVIEW",
                       review_status="PENDING")
