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
from .assets import pexels_video, product_image
from .videogen import engine as gen_engine
from .videogen.providers import ProviderError, get_provider
from .creation_branches import normalise_brief
from .clips import clip_dir, describe_for_ai, is_allowed_source, list_clips
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
from .quality import (
    evaluate_creative_quality,
    inspect_rendered_video,
    validate_facebook_reel,
    validate_rendered_video,
    validate_shopee_video,
)
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
        data = Path(path).read_bytes()
        mime = ("image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else
                "image/webp" if data.startswith(b"RIFF") and data[8:12] == b"WEBP" else
                "image/jpeg")
        encoded = base64.b64encode(data).decode("ascii")
        if protocol.lower() == "anthropic-messages":
            parts.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": encoded},
            })
        else:
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{encoded}"},
            })
    return [{"role": "user", "content": parts}]


def _repair_json_locally(raw: str) -> dict | None:
    """Common model slips: markdown fences, trailing commas, text after the object,
    a reply cut off before the closing braces."""
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw.strip(), flags=re.IGNORECASE)
    start = text.find("{")
    if start < 0:
        return None
    text = re.sub(r",\s*([}\]])", r"\1", text[start:])
    candidates = [text[:text.rfind("}") + 1]] if "}" in text else []
    # Close a truncated object: balance open brackets outside strings.
    stack, in_str, esc = [], False, False
    for ch in text:
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    tail = text + ('"' if in_str else "")
    tail = re.sub(r",\s*$", "", tail)
    candidates.append(tail + "".join(reversed(stack)))
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _transient_ai_error(exc: Exception) -> bool:
    text = str(exc)
    return any(k in text for k in ("timed out", "unavailable", "HTTP 500", "HTTP 502", "HTTP 503",
                                   "HTTP 504", "stream error", "UPSTREAM", "total time limit"))


def _ask_json(client: AIClient, settings, prompt: str, images: list[Path] | None = None,
              temperature: float = 0.4, max_tokens: int = 2400) -> dict:
    messages = (_image_messages(prompt, images, settings.ai_protocol) if images
                else [{"role": "user", "content": prompt}])
    # Long storyboard replies with extended thinking need more than the default timeout.
    base_timeout = getattr(client, "timeout", None)
    if isinstance(base_timeout, (int, float)) and max_tokens >= 4000:
        client.timeout = max(base_timeout, 600)   # fallback when the gateway does not stream
    try:
        try:
            reply = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
        except AIClientError as exc:
            # Long replies with extended thinking are the ones the upstream web session
            # drops (timeouts, 5xx, broken SSE). Retry once right away with light thinking.
            effort = getattr(client, "thinking_effort", "")
            if max_tokens < 4000 or not effort or not _transient_ai_error(exc):
                raise
            log.warning("Long AI call failed (%s); retrying once without extended thinking", exc)
            client.thinking_effort = ""
            try:
                reply = client.chat(messages, temperature=temperature, max_tokens=max_tokens)
            finally:
                client.thinking_effort = effort
    finally:
        if isinstance(base_timeout, (int, float)):
            client.timeout = base_timeout
    try:
        return _parse_json_reply(reply)
    except WorkflowError as first_error:
        raw = _reply_text(reply)
        repaired = _repair_json_locally(raw)
        if repaired is not None:
            log.info("AI JSON repaired locally")
            return repaired
        if not raw.strip():
            raise
        log.warning("AI JSON malformed (%s); asking for a corrected copy", first_error)
        fix = client.chat([{"role": "user", "content": (
            "Đoạn sau là JSON bị lỗi cú pháp hoặc bị cắt. Trả về DUY NHẤT JSON object hợp lệ, "
            "giữ nguyên nội dung và các trường, không markdown:\n" + raw[:30000])}],
            temperature=0, max_tokens=max_tokens)
        try:
            return _parse_json_reply(fix)
        except WorkflowError:
            repaired = _repair_json_locally(_reply_text(fix))
            if repaired is not None:
                return repaired
            raise first_error from None


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


# Internal names and generic English tags read as machine-made to a Vietnamese audience.
_BAD_TAG = re.compile(
    r"^(viralcreator|productreview|templateseries|creationbranch|ugc|aigenerated|"
    r"dog\w*|cat\w*|countryside\w*|life\w*|running|funny\w*|cute\w*|family\w*|love\w*|"
    r"happy\w*|kitchen\w*|homedecor|fyp|foryou\w*|viral|trending|shorts|reels?)$", re.I)


def _normalise_hashtags(values: Any, limit: int = 5) -> list[str]:
    if not isinstance(values, list):
        return []
    result = []
    for value in values:
        tag = re.sub(r"[^\wÀ-ỹ]", "", str(value).replace("#", ""), flags=re.UNICODE)
        if tag and not _BAD_TAG.match(tag):
            result.append("#" + tag[:40])
    unique = list(dict.fromkeys(t.lower() for t in result))
    return [next(t for t in result if t.lower() == u) for u in unique][:limit]


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
        "tiktok_link": offer.get("tiktokLink") or offer.get("tiktok_link") or "",
        "catalog_id": offer.get("catalogId") or offer.get("catalog_id"),
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
                     url: str = "", *, creation_branch: str | None = None,
                     experience: dict | None = None, template: dict | None = None,
                     product_context: dict | None = None) -> str:
    topic = " ".join((topic or "").split())
    if not topic and not offer:
        raise WorkflowError("A trend topic or a product is required")
    job_id = "t" + uuid.uuid4().hex[:23]
    brief = {"topic": topic or (offer or {}).get("productName", ""), "notes": notes,
             "keywords": keywords or [], "trend_id": trend_id, "reference_url": url}
    if offer:
        brief["offer"] = offer
    creation_branch = creation_branch or ("product_review" if offer else "viral_creator")
    if creation_branch:
        if creation_branch == "product_review" and not offer:
            raise WorkflowError("Nhánh review cần chọn sản phẩm trước khi tạo job.")
        if creation_branch == "template_series":
            from .creative_series import get_template
            # Snapshot owner-managed references, never paths supplied by a caller/model.
            template = get_template(settings, (template or {}).get("id", "suri-family"))
            brief["template_references"] = template.get("reference_assets") or []
        brief.update(normalise_brief(creation_branch, experience=experience,
                                    template=template, product_context=product_context))
    link = str((offer or {}).get("affiliateLink") or "").strip()
    if link and not _is_affiliate_link(link):
        raise WorkflowError("Link affiliate phải là link Shopee hoặc TikTok")
    register_job(settings.database_path, {
        "id": job_id, "source_path": "", "sha256": f"trend:{job_id}",
        "file_name": (brief["topic"] or "trend")[:120], "mode": "trend", "brief": brief,
    })
    if offer and (link or creation_branch == "product_review"):
        product = offer_to_product(offer)
        update_job(settings.database_path, job_id, product_item_id=product["item_id"],
                   product_name=product["name"], affiliate_link=link,
                   product_candidates_json=json.dumps([product], ensure_ascii=False))
    return job_id


def _is_affiliate_link(url: str) -> bool:
    from .affiliate_client import _is_shopee_vn
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    tiktok = parsed.scheme == "https" and (host == "tiktok.com" or host.endswith(".tiktok.com"))
    return _is_shopee_vn(url) or tiktok


# ---------------------------------------------------------------- pipeline

def process_job(settings, job_id: str, force: bool = False) -> dict:
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if (job.get("mode") or "") == "long":   # a long video is driven by its outline and parts
        enqueue_task(settings.database_path, "long_outline", job_id)
        return {"job_id": job_id, "status": job["status"], "message": "Video dài: chạy lại dàn ý/các phần"}
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

    if mode == "trend" and not brief.get("creation_branch"):
        # One prompt system: older trend jobs run through the creation branches too.
        brief.update(normalise_brief("product_review" if brief.get("offer") else "viral_creator"))
        update_job(db, job_id, brief_json=json.dumps(brief, ensure_ascii=False))
        job = get_job(db, job_id)
    if brief.get("creation_branch"):
        return _process_creation(settings, job, brief, client)

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
            _mark_sub_id(settings, job_id)
        except Exception as exc:  # noqa: BLE001
            _stop(settings, job_id, "WAITING_FOR_SHOPEE_API", exc)
        update_job(db, job_id, product_item_id=product["item_id"], product_name=product["name"],
                   affiliate_link=affiliate_link)

    return _create_and_render(settings, get_job(db, job_id), client, analysis, product,
                              affiliate_link, frame_paths, metadata)


def _process_creation(settings, job, brief, client) -> dict:
    """Explicit branches keep content-first creation independent of product search."""
    branch = brief["creation_branch"]
    if brief.get("shared_cast_from"):
        from . import shared_cast
        if not shared_cast.ready(settings, brief["shared_cast_from"]):
            _stop(settings, job["id"], shared_cast.WAIT_STATUS,
                  "Chờ phần/tập đầu tạo xong ảnh nhân vật để dùng chung khuôn mặt.")
    _mark(settings, job["id"], "script_start")
    analysis = json.loads(job.get("analysis_json") or "null")
    product, affiliate_link = {}, str(job.get("affiliate_link") or "")
    if branch == "product_review":
        if not brief.get("offer"):
            _stop(settings, job["id"], "NEEDS_PRODUCT_REVIEW", "Chọn sản phẩm để viết review.")
        product = offer_to_product(brief["offer"])
        if not brief.get("product_context"):
            from .creation_context import build_product_context
            brief["product_context"] = build_product_context(
                settings, product["item_id"], experience=brief.get("experience"))
            update_job(settings.database_path, job["id"],
                       brief_json=json.dumps(brief, ensure_ascii=False))
        if not isinstance(analysis, dict):
            analysis = {"topic": brief.get("topic") or product["name"],
                        "visual_summary": product["name"], "angle": brief.get("notes", ""),
                        "product_search_keywords": [product["name"]]}
    elif not isinstance(analysis, dict):
        # The storyboard call does the ideation itself (one AI call instead of two).
        topic = str(brief.get("topic") or "")
        analysis = {"topic": topic, "angle": brief.get("notes", ""), "visual_summary": topic,
                    "product_search_keywords": brief.get("keywords") or []}
    update_job(settings.database_path, job["id"], status="ANALYZED", ai_generated=1,
               last_error=None, analysis_json=json.dumps(analysis, ensure_ascii=False))
    return _create_and_render(settings, get_job(settings.database_path, job["id"]), client,
                              analysis, product, affiliate_link, [], {})


def _viewer_test(client, settings, plan: dict) -> dict:
    """S2.3: a stranger reads only the words; mismatch or missing context triggers one fix."""
    if not creative.spoken_script(plan).strip():
        return {}
    try:
        raw = _ask_json(client, settings, creative.viewer_test_prompt(plan), temperature=0.2, max_tokens=700)
    except AIClientError:
        raise
    except Exception as exc:  # noqa: BLE001 - the test is advice; the code checks still run
        log.warning("viewer test skipped: %s", exc)
        return {}
    flow = raw.get("flow")
    try:
        flow = max(0.0, min(10.0, float(flow))) if flow is not None else None
    except (TypeError, ValueError):
        flow = None
    return {"summary": str(raw.get("summary") or "")[:300],
            "hook_answered": raw.get("hook_answered") if isinstance(raw.get("hook_answered"), bool) else None,
            "matches_takeaway": raw.get("matches_takeaway") if isinstance(raw.get("matches_takeaway"), bool) else None,
            "unclear_lines": [str(x)[:160] for x in (raw.get("unclear_lines") or [])[:5]], "flow": flow}


def _brief_plan_fields(brief: dict) -> dict:
    """Content Director choices that travel from the brief into the storyboard and QA."""
    out = {k: brief[k] for k in ("format", "niche", "goal", "duration_range") if brief.get(k)}
    if brief.get("fixed_characters"):
        out["characters"] = brief["fixed_characters"]
    tpl_style = (brief.get("template") or {}).get("style_profile")
    if brief.get("fixed_style") or tpl_style:   # long-video parts, or the series' chosen art style
        out["style_profile"] = brief.get("fixed_style") or tpl_style
    if brief.get("fixed_music_mood"):
        out["music_mood"] = brief["fixed_music_mood"]
    from .creation_branches import is_dialogue
    if is_dialogue(brief):   # series written as character dialogue (VieNeu voice per character)
        out["dialogue"] = True
    source = brief.get("trend_source") or {}
    texts = [source.get("title_vi"), source.get("summary_vi"), source.get("title_zh")]
    if any(texts):
        out["source_texts"] = [t for t in texts if t]
    return out


def _recent_hooks(settings, exclude: str = "", limit: int = 15) -> list[str]:
    """Hooks of the latest videos, so a new storyboard does not open the same way."""
    from .db import connect
    hooks = []
    with connect(settings.database_path) as conn:
        for row in conn.execute("SELECT id, content_json FROM jobs WHERE content_json IS NOT NULL "
                                "ORDER BY updated_at DESC LIMIT 60").fetchall():
            if row["id"] == exclude:
                continue
            try:
                hook = (json.loads(row["content_json"]) or {}).get("hook")
            except (TypeError, ValueError):
                continue
            if hook and hook not in hooks:
                hooks.append(str(hook)[:80])
            if len(hooks) >= limit:
                break
    return hooks


def _channel_learning(settings) -> str:
    try:
        from .learning import prompt_block
        return prompt_block(settings.database_path)
    except Exception as exc:  # noqa: BLE001 - learning is advice, never a blocker
        log.warning("channel learning unavailable: %s", exc)
        return ""


def _cast_plan_voices(settings, plan: dict, brief: dict) -> None:
    from .style_profiles import resolve
    from .voice_cast import cast_voices
    plan["style_profile"] = resolve(plan.get("style_profile"), plan.get("creation_branch"),
                                    getattr(settings, "style_profile", "") or None)
    pinned = {c["id"]: c["voice"] for c in (brief.get("template") or {}).get("characters") or []
              if isinstance(c, dict) and c.get("voice")}
    chars = [c for c in plan.get("characters") or [] if isinstance(c, dict) and c.get("id")]
    dialogue = plan.get("creation_branch") == "template_series" or (
        plan.get("creation_branch") == "viral_creator" and plan.get("format") in (None, "", "story"))
    if not dialogue:
        # S1.4: explainers, documentaries, process and product videos have ONE narrator voice:
        # the on-screen creator if there is one, otherwise the narrator.
        host = next((c["id"] for c in chars if str(c.get("role") or "").lower() in ("creator", "host")), None)
        speaker = host or "narrator"
        for scene in plan.get("scenes") or []:
            for line in scene.get("lines") or []:
                line["speaker"] = speaker
    if plan.get("creation_branch") == "product_review" and len(chars) == 1:
        # one reviewer: the "narrator" is that same person, so one voice for the whole video
        for scene in plan.get("scenes") or []:
            for line in scene.get("lines") or []:
                if line.get("speaker") == "narrator":
                    line["speaker"] = chars[0]["id"]
    plan["voice_cast"] = cast_voices(plan, settings.tts_voice, pinned)


def _related_affiliates(settings, plan: dict, analysis: dict) -> list[dict]:
    from .trend_intel.matcher import match_products
    keywords = plan.get("affiliate_keywords") or []
    if not keywords:
        return []
    topic = {"key": "job:" + str(plan.get("title") or analysis.get("topic") or "")[:80],
             "keyword": str(plan.get("title") or analysis.get("topic") or ""),
             "intent": {"product_keywords": keywords}}
    found = match_products(settings, topic, limit=8)
    if not found:
        return []
    from .trend_intel.matcher import verify_matches
    try:   # the story's suggestions get the same AI check as trend matches
        return verify_matches(settings, [(topic, found)]).get(topic["key"], [])
    except Exception as exc:  # noqa: BLE001 - unverified suggestions are not offered
        log.warning("affiliate suggestion check failed: %s", exc)
        return []


def attach_affiliate(settings, job_id: str, catalog_id: str) -> dict:
    """Attach an owner catalog link after the story; never enqueue or rewrite media."""
    from . import catalog
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError("Không tìm thấy job.")
    if job["status"] in FINISHED_STATUSES - {"READY_FOR_REVIEW"}:
        raise WorkflowError("Job đã được duyệt/đăng; không đổi link sau khi duyệt.")
    brief = json.loads(job.get("brief_json") or "{}")
    branch = brief.get("creation_branch")
    if not branch:
        raise WorkflowError("Gắn affiliate sau nội dung chỉ áp dụng cho nhánh sáng tạo mới.")
    if job["status"] not in {"READY_FOR_REVIEW", "NEEDS_MEDIA_REVIEW"}:
        raise WorkflowError("Chờ job dựng xong hoặc dừng để kiểm tra trước khi gắn affiliate.")
    product = catalog.get_product(settings.database_path, catalog_id)
    if not product:
        raise WorkflowError("Sản phẩm không có trong danh mục của bạn.")
    if branch in {"viral_creator", "template_series"} and catalog_id not in {
            s.get("product_id") for s in brief.get("affiliate_suggestions") or []}:
        raise WorkflowError("Sản phẩm chưa được xác định liên quan với storyboard này.")
    if branch == "product_review" and str(product["item_id"]) != str(job.get("product_item_id")):
        raise WorkflowError("Link phải thuộc đúng sản phẩm đang review.")
    link = str(product.get("affiliate_link") or product.get("tiktok_link") or "")
    if not link or not _is_affiliate_link(link):
        raise WorkflowError("Sản phẩm chưa có link affiliate Shopee/TikTok hợp lệ của bạn.")
    content = json.loads(job.get("content_json") or "{}")
    if content:
        content["tiktok_link"] = product.get("tiktok_link") or ""
        finalise_content(content, link)
    brief["attached_affiliate"] = {"catalog_id": catalog_id, "item_id": product["item_id"]}
    update_job(settings.database_path, job_id, product_item_id=product["item_id"],
               product_name=product["name"], affiliate_link=link,
               brief_json=json.dumps(brief, ensure_ascii=False),
               content_json=json.dumps(content, ensure_ascii=False))
    state_path = _plan_state_path(settings, job_id)
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["affiliate_link"] = link
        # The media product remains the storyboard's original product (or empty).
        state["attached_affiliate"] = {"catalog_id": catalog_id, "item_id": product["item_id"]}
        _save_plan_state(settings, job_id, state)
    output = Path(job.get("output_path") or "")
    manifest_path = output.parent / "manifest.json"
    if output.is_file() and manifest_path.is_file() and content:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(affiliate_link=link, content=content,
                        affiliate_product={"item_id": product["item_id"], "name": product["name"]})
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (output.parent / "caption.txt").write_text(content["shopee_caption"], encoding="utf-8")
        _write_review_page(output.parent, manifest)
    return {"job_id": job_id, "catalog_id": catalog_id, "affiliate_attached": True,
            "regenerated": False}


def _create_and_render(settings, job, client, analysis, product, affiliate_link,
                       frame_paths, metadata) -> dict:
    job_id, mode = job["id"], job.get("mode") or "base"
    db = settings.database_path
    brief = json.loads(job.get("brief_json") or "{}")
    branch = brief.get("creation_branch")
    asset_dir = settings.work_dir / job_id / "assets"
    product_img = product_image(product, asset_dir)
    user_clips = list_clips(settings, job_id)[:8]
    clip_thumbs = [t for t in (_clip_thumb(settings, job_id, c) for c in user_clips) if t]
    clips_text = describe_for_ai(user_clips)
    n_clips = len(user_clips)
    provider = get_provider(settings) if mode == "trend" else None
    gen_limit = (min(settings.videogen_max_clips, gen_engine.remaining_today(settings))
                 if provider and provider.supports("video") else 0)
    if brief.get("fast_mode"):
        gen_limit = min(gen_limit, 3)   # fast mode: 3 AI clips, the rest cuts/photos
    if (not branch and mode == "trend" and not product_img and not user_clips and not gen_limit
            and not settings.pexels_api_key.get_secret_value()):
        # Nothing to show on screen: ask for visuals before spending AI calls.
        _stop(settings, job_id, "NEEDS_MEDIA_REVIEW",
              "Chưa có hình ảnh: thêm ảnh/clip ở tab Clip & hình (hoặc link ảnh sản phẩm), "
              "rồi bấm \"AI dựng video với các clip này\".")

    # 4) creative plan ---------------------------------------------------------
    update_job(db, job_id, status="SCRIPTING")
    segments: list[dict] = []
    references = []
    try:
        if branch:
            from .storyboard_support import prepare_references, reference_legend
            references = prepare_references(settings, job_id, brief, product, product_img, user_clips)
            prompt_brief = {**brief, "analysis": analysis,
                            "available_references": [{"id": r["id"], "role": r["role"]}
                                                     for r in references],
                            "has_product_image": bool(product_img)}
            prompt = creative.branch_plan_prompt(
                prompt_brief, product, bool(gen_limit), bool(settings.pexels_api_key.get_secret_value()),
                clips_text, 1, gen_limit, recent_hooks=_recent_hooks(settings, job_id),
                learning=_channel_learning(settings)
            ) + "\n" + reference_legend(references)
            # Clip indexes refer to the library, while reference IDs refer to ordered attachments.
            raw = _ask_json(client, settings, prompt,
                            [Path(r["path"]) for r in references] or None,
                            temperature=0.7, max_tokens=5000)
            plan = creative.normalise_plan({**raw, "creation_branch": branch,
                                            "storyboard_version": 1, **_brief_plan_fields(brief)},
                                           "trend", None, n_clips)
            if brief.get("no_hook"):          # part 2+ of a long video: no new opening hook
                plan["hook"], plan["hook_variants"] = "", []
        elif mode == "base":
            source = Path(job["source_path"])
            cuts = detect_cuts(settings.ffmpeg_path, source)
            segments = build_segments(cuts, float(metadata["duration_sec"]))
            thumbs = segment_thumbnails(settings.ffmpeg_path, source, segments,
                                        settings.work_dir / job_id / "segments")
            prompt = creative.base_plan_prompt(analysis, product, segments, metadata, clips_text)
            try:
                raw = _ask_json(client, settings, prompt, [*thumbs, *clip_thumbs],
                                temperature=0.6)
                plan = creative.normalise_plan(raw, "base", segments, n_clips)
            except AIClientError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("AI edit plan unusable (%s); using fallback cut", exc)
                plan = creative.normalise_plan(
                    creative.fallback_base_plan(analysis, segments), "base", segments)
        else:
            raise WorkflowError("Job trend cần nhánh sáng tạo; chạy lại để chuyển nhánh.")
        score = None
        if branch:
            _mark(settings, job_id, "script_draft")
            creative.fix_visual_sources(plan, bool(settings.pexels_api_key.get_secret_value()),
                                        bool(gen_limit), bool(product_img))
            score = (plan.get("self_review") or {}).get("score")
            plan["viewer_test"] = _viewer_test(client, settings, plan)
            issues = creative.storyboard_issues(plan)
            if issues and settings.viral_review:
                try:   # one targeted fix only when a hard rule fails
                    fix_prompt = creative.branch_review_prompt(plan, product, 10, prompt_brief) + (
                        "\nBẮT BUỘC sửa trong revised, giữ nguyên phần không lỗi: "
                        + json.dumps(issues, ensure_ascii=False))
                    fixed = _ask_json(client, settings, fix_prompt, temperature=0.4, max_tokens=5000)
                    if isinstance(fixed.get("revised"), dict):
                        candidate = creative.normalise_plan(
                            {**fixed["revised"], "creation_branch": branch,
                             "storyboard_version": plan.get("storyboard_version", 2),
                             **_brief_plan_fields(brief)},
                            mode, segments, n_clips)
                        creative.fix_visual_sources(candidate, bool(settings.pexels_api_key.get_secret_value()),
                                                    bool(gen_limit), bool(product_img))
                        candidate["viewer_test"] = _viewer_test(client, settings, candidate)
                        if len(creative.storyboard_issues(candidate)) < len(issues):
                            plan = candidate
                            plan["revised_after_review"] = issues
                        if fixed.get("score") is not None:
                            score = float(fixed["score"])
                except AIClientError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    log.warning("Storyboard fix skipped: %s", exc)
        elif settings.viral_review:
            try:
                review_prompt = (creative.branch_review_prompt(
                    plan, product, settings.viral_min_score, prompt_brief) if branch else
                    creative.review_prompt(plan, product, settings.viral_min_score))
                review = _ask_json(client, settings, review_prompt, temperature=0.3,
                                   max_tokens=7000 if branch else 2400)
                score = float(review.get("score")) if review.get("score") is not None else None
                if review.get("revised") and (score is None or score < settings.viral_min_score):
                    revised = review["revised"]
                    if branch:
                        revised = {**revised, "creation_branch": branch, "storyboard_version": 1}
                    plan = creative.normalise_plan(revised, mode, segments, n_clips)
                    plan["revised_after_review"] = review.get("issues") or True
                    try:  # score the rewrite so the UI shows the final script's score
                        again_prompt = (creative.branch_review_prompt(plan, product, 0, prompt_brief)
                                        if branch else creative.review_prompt(plan, product, 0))
                        again = _ask_json(client, settings, again_prompt, temperature=0.2,
                                          max_tokens=1500)
                        score = float(again.get("score", score))
                    except Exception:  # noqa: BLE001
                        pass
                hits = creative.meta_word_lines(plan) if branch else []
                if hits:  # sounds like a report: one forced rewrite of the spoken lines
                    fix_prompt = creative.branch_review_prompt(plan, product, 10, prompt_brief) + (
                        "\nBẮT BUỘC viết lại (revised): các câu sau nghe như báo cáo, cấm từ hệ thống "
                        + json.dumps(list(creative.META_WORDS), ensure_ascii=False)
                        + "; lời rào đón (" + json.dumps(list(creative.HEDGE_WORDS), ensure_ascii=False)
                        + ") tối đa MỘT câu cả video; thay bằng tình huống dùng và cách dùng: "
                        + json.dumps(hits[:8], ensure_ascii=False))
                    fixed = _ask_json(client, settings, fix_prompt, temperature=0.4, max_tokens=7000)
                    if isinstance(fixed.get("revised"), dict):
                        candidate = creative.normalise_plan(
                            {**fixed["revised"], "creation_branch": branch,
                             "storyboard_version": plan.get("storyboard_version", 2)},
                            mode, segments, n_clips)
                        if len(creative.meta_word_lines(candidate)) < len(hits):
                            plan = candidate
                            plan["revised_after_review"] = ["lời như báo cáo"]
            except AIClientError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Viral review skipped: %s", exc)
    except AIClientError as exc:
        _stop(settings, job_id, _ai_status(exc), exc)
    except Exception as exc:  # noqa: BLE001
        _stop(settings, job_id, "CONTENT_GENERATION_FAILED", exc)

    # 5) AI clips (Muse): submit and wait without blocking the worker -------
    reference_issues = []
    if branch:
        _cast_plan_voices(settings, plan, brief)
        from .storyboard_support import attach_references, reference_errors
        try:
            attach_references(plan, brief, references)
            # Missing cast photos are generated by Muse first unless that is switched off.
            if not settings.videogen_character:
                reference_issues = reference_errors(plan, brief, references)
        except ProviderError as exc:
            reference_issues = [str(exc)]
        if branch in {"viral_creator", "template_series"}:
            # product suggestions need an AI check; it runs beside Muse instead of before it
            enqueue_task(db, "affiliate_suggest", job_id)
    if branch:
        _mark(settings, job_id, "script_done")
    _save_plan_state(settings, job_id, {"plan": plan, "score": score, "product": product,
                                        "affiliate_link": affiliate_link, "analysis": analysis,
                                        "clip_ids": [c["id"] for c in user_clips]})
    if reference_issues and any(s.get("visual") == "generate" for s in plan["scenes"]):
        _stop(settings, job_id, "NEEDS_MEDIA_REVIEW", "; ".join(reference_issues), viral_score=score)
    can_plan = bool(provider and provider.supports("video"))
    if branch and any(s.get("visual") == "generate" for s in plan["scenes"]) and not can_plan:
        _stop(settings, job_id, "NEEDS_MEDIA_REVIEW",
              "Storyboard đã lưu; cần bật Muse và đủ ngân sách cho toàn bộ cảnh generate.", viral_score=score)
    if gen_limit or (branch and can_plan):
        try:
            waiting = gen_engine.plan_job(settings, job_id, plan, product_img, provider)
        except ProviderError as exc:
            _stop(settings, job_id, "NEEDS_MEDIA_REVIEW", exc, viral_score=score)
        if waiting:
            _mark(settings, job_id, "muse_start")
            if settings.tts_enabled:   # voices are made while Muse renders the clips
                enqueue_task(db, "prepare_audio", job_id)
            _stop(settings, job_id, gen_engine.WAIT_STATUS, None, viral_score=score,
                  generation_tasks=waiting)
    else:
        gen_engine.cancel_job_tasks(db, job_id)
    return _finish_timeline(settings, job, plan, score, product, affiliate_link, analysis,
                            product_img, asset_dir, user_clips, {}, frame_paths)


def _mark(settings, job_id: str, stage: str) -> None:
    """Stage timestamps (job page shows how long each step took)."""
    path = settings.work_dir / job_id / "timings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data[stage] = now_iso()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError):
        pass


def stage_times(settings, job_id: str) -> list[tuple[str, float]]:
    path = settings.work_dir / job_id / "timings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    order = [("script_start", "script_draft", "AI viết kịch bản"), ("script_draft", "script_done", "Kiểm và sửa kịch bản"),
             ("muse_start", "muse_done", "Muse tạo clip"), ("render_start", "render_done", "Dựng video + QA")]
    out = []
    for a, b, label in order:
        if data.get(a) and data.get(b):
            out.append((label, round((datetime.fromisoformat(data[b]) - datetime.fromisoformat(data[a])).total_seconds())))
    return out


def suggest_affiliates(settings, job_id: str) -> dict:
    """Viral/series product suggestions (AI-checked), filled in while Muse works."""
    db = settings.database_path
    job = get_job(db, job_id)
    state_path = _plan_state_path(settings, job_id)
    if not job or not state_path.is_file():
        return {"suggestions": 0}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    brief = json.loads(job.get("brief_json") or "{}")
    brief["affiliate_suggestions"] = _related_affiliates(settings, state.get("plan") or {},
                                                         state.get("analysis") or {})
    attached = brief.get("attached_affiliate", {}).get("catalog_id")
    fields = {}
    if attached and attached not in {s["product_id"] for s in brief["affiliate_suggestions"]}:
        brief.pop("attached_affiliate", None)
        fields.update(product_item_id=None, product_name=None, affiliate_link="")
        state["affiliate_link"] = ""   # the render reads the link from the plan state too
        state.pop("attached_affiliate", None)
        _save_plan_state(settings, job_id, state)
    update_job(db, job_id, brief_json=json.dumps(brief, ensure_ascii=False), **fields)
    return {"suggestions": len(brief["affiliate_suggestions"])}


def prepare_audio(settings, job_id: str) -> dict:
    """Pre-make the voice lines of a storyboard that is waiting for Muse (T1.4)."""
    state_path = _plan_state_path(settings, job_id)
    if not state_path.is_file():
        return {"prepared": 0}
    plan = json.loads(state_path.read_text(encoding="utf-8")).get("plan") or {}
    from .editor.voice_track import prepare_voices
    made = prepare_voices(settings, plan, settings.work_dir / job_id / "prep")
    return {"prepared": made}


def regenerate_scene(settings, job_id: str, index: int, action: str | None = None,
                     camera: str | None = None) -> dict:
    """Make one AI shot again (optionally with an edited action/camera) and re-render after it."""
    state_path = _plan_state_path(settings, job_id)
    if not state_path.is_file():
        raise WorkflowError("Video này chưa có storyboard để tạo lại cảnh")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    plan = state.get("plan") or {}
    scenes = plan.get("scenes") or []
    if not 0 <= index < len(scenes) or scenes[index].get("visual") != "generate":
        raise WorkflowError("Chỉ tạo lại được cảnh do AI tạo (visual=generate)")
    shot = scenes[index].setdefault("shot", {})
    if action is not None and str(action).strip():
        shot["action"] = str(action).strip()[:600]
    if camera is not None and str(camera).strip():
        shot["camera"] = str(camera).strip()[:200]
    provider = get_provider(settings)
    if provider is None:
        raise WorkflowError("Chưa cấu hình Muse")
    compiled = {i: (prompt, refs, dur) for i, prompt, refs, dur in gen_engine._compile_storyboard(settings, plan, provider)}
    prompt, refs, duration = compiled[index]
    db = settings.database_path
    gen_engine.ensure_schema(db)
    same = [t for t in gen_engine.list_tasks(db, job_id, 10000)
            if t["kind"] == "video" and t["scene_index"] == index and t["prompt"] == prompt]
    attempt = max((t["attempt"] for t in same), default=0) + 1
    gen_engine._insert(db, job_id=job_id, scene_index=index, kind="video", provider=provider.name,
                       prompt=prompt, refs=refs, duration=duration, attempt=attempt)
    _save_plan_state(settings, job_id, state)
    update_job(db, job_id, status=gen_engine.WAIT_STATUS, last_error=None)
    return {"job_id": job_id, "scene": index, "attempt": attempt}


def _plan_state_path(settings, job_id: str) -> Path:
    return settings.work_dir / job_id / "plan_state.json"


def _save_plan_state(settings, job_id: str, state: dict) -> None:
    path = _plan_state_path(settings, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")


def _finish_timeline(settings, job, plan, score, product, affiliate_link, analysis, product_img,
                     asset_dir, user_clips, generated, frame_paths=None) -> dict:
    job_id = job["id"]
    try:
        timeline = build_timeline(settings, job, plan, product_img, generated, asset_dir,
                                  user_clips)
    except WorkflowError as exc:
        _stop(settings, job_id, "NEEDS_MEDIA_REVIEW", exc)
    tiktok_link = str(product.get("tiktok_link") or "")
    current_job = get_job(settings.database_path, job_id) or job
    attached = json.loads(current_job.get("brief_json") or "{}").get("attached_affiliate", {})
    if attached.get("catalog_id"):
        from . import catalog
        affiliate_product = catalog.get_product(settings.database_path, attached["catalog_id"])
        tiktok_link = str((affiliate_product or {}).get("tiktok_link") or "")
    content = _content_from_plan(plan, affiliate_link, tiktok_link)
    update_job(settings.database_path, job_id,
               script_json=json.dumps(timeline, ensure_ascii=False),
               content_json=json.dumps(content, ensure_ascii=False), viral_score=score)
    return render_job(settings, job_id, analysis=analysis, product=product,
                      frame_paths=frame_paths)


def resume_after_generation(settings, job_id: str) -> dict:
    _mark(settings, job_id, "muse_done")
    return _resume_after_generation(settings, job_id)


def _resume_after_generation(settings, job_id: str) -> dict:
    """Continue a job once its AI clips are downloaded (or failed: those scenes fall back)."""
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    path = _plan_state_path(settings, job_id)
    if not path.is_file():
        raise WorkflowError("Không còn kịch bản đã lưu: bấm Viết lại kịch bản")
    state = json.loads(path.read_text(encoding="utf-8"))
    asset_dir = settings.work_dir / job_id / "assets"
    product = state.get("product") or {}
    generated = gen_engine.results_for_job(settings.database_path, job_id)
    # Same clips, same order as when the plan was written (plan "clip" fields are indexes).
    by_id = {c["id"]: c for c in list_clips(settings, job_id)}
    user_clips = [by_id[i] for i in state.get("clip_ids") or [] if i in by_id]
    if state["plan"].get("creation_branch"):
        missing = [i for i in state.get("clip_ids") or [] if i not in by_id]
        if missing:
            update_job(settings.database_path, job_id, status="NEEDS_MEDIA_REVIEW",
                       last_error="Clip của storyboard đã bị xóa; cần bổ sung hoặc viết lại storyboard.")
            return {"job_id": job_id, "status": "NEEDS_MEDIA_REVIEW"}
        try:
            generated = gen_engine.results_for_storyboard(settings, job_id, state["plan"])
        except ProviderError as exc:
            update_job(settings.database_path, job_id, status="NEEDS_MEDIA_REVIEW",
                       last_error=str(exc)[:1000])
            return {"job_id": job_id, "status": "NEEDS_MEDIA_REVIEW", "error": str(exc)}
    update_job(settings.database_path, job_id, status="RENDERING")
    try:
        return _finish_timeline(settings, job, state["plan"], state.get("score"), product,
                                state.get("affiliate_link") or job.get("affiliate_link") or "",
                                state.get("analysis") or {}, product_image(product, asset_dir),
                                asset_dir, user_clips, generated)
    except _Stop as stop:
        return stop.result


def _clip_thumb(settings, job_id: str, clip: dict) -> Path | None:
    base = clip_dir(settings, job_id)
    if clip.get("thumb") and (base / clip["thumb"]).is_file():
        return base / clip["thumb"]
    if clip["kind"] == "image":
        return base / clip["file"]
    return None


def _clip_scene(settings, job_id: str, clip: dict, start: float = 0.0) -> dict:
    path = clip_dir(settings, job_id) / clip["file"]
    is_video = clip["kind"] == "video"
    if is_video:
        start = max(0.0, min(float(start or 0), max(0.0, float(clip["duration"] or 0) - 0.5)))
    return {"kind": "video" if is_video else "image", "source": str(path),
            "start": round(start, 3) if is_video else 0.0, "clip_id": clip["id"],
            "visual": "user_clip", "generated": bool(clip.get("ai_generated"))}


def build_timeline(settings, job: dict, plan: dict, product_img: Path | None,
                   generated_clips: dict[int, Path] | None, asset_dir: Path,
                   user_clips: list[dict] | None = None) -> dict:
    """generated_clips: scene index -> downloaded AI clip (see videogen.engine)."""
    generated_clips = generated_clips or {}
    mode = job.get("mode") or "base"
    user_clips = user_clips or []
    strict = bool(plan.get("creation_branch"))
    scenes: list[dict] = []
    used_stock: set[str] = set()
    used_clips = {s["clip"] for s in plan["scenes"] if s.get("clip") is not None}
    spare_clips = [i for i in range(len(user_clips)) if i not in used_clips]
    for i, scene in enumerate(plan["scenes"]):
        item = {k: scene.get(k) for k in ("duration", "voiceover", "text", "motion")}
        if strict:
            item.update({k: scene[k] for k in ("shot", "reference_ids", "gen_prompt", "lines", "cast",
                                               "talking", "group", "product_visible", "hands_only",
                                               "pause", "reuse_of")
                         if k in scene})
            item["beat"] = i
        if scene.get("clip") is not None and scene["clip"] < len(user_clips):
            item.update(_clip_scene(settings, job["id"], user_clips[scene["clip"]],
                                    scene.get("start_offset") or 0))
        elif mode == "base":
            item.update(kind="video", source=job["source_path"], start=scene["start"],
                        segment=scene.get("segment"))
        else:
            visual = scene.get("visual")
            path, kind = None, "image"
            if visual == "generate" and generated_clips.get(i):
                path, kind = generated_clips[i], "video"
            if visual == "reuse" and generated_clips.get(scene.get("reuse_of")):
                path, kind = generated_clips[scene["reuse_of"]], "video"   # another cut, same clip
                visual = "generate"
            if strict and visual == "generate" and (path is None or not Path(path).is_file()):
                raise WorkflowError(f"Cảnh {i + 1} chưa có clip Muse; storyboard được giữ nguyên để kiểm tra.")
            if strict and visual == "user_clip":
                raise WorkflowError(f"Cảnh {i + 1} thiếu clip đã chọn; thêm lại clip trước khi dựng.")
            if not strict and path is None and visual in {"stock", "generate"} and spare_clips:
                # Hand-added clips beat stock footage for "stock"/"generate" scenes.
                item.update(_clip_scene(settings, job["id"], user_clips[spare_clips.pop(0)]))
                scenes.append(item)
                continue
            if path is None and visual in {"stock", "generate"}:
                path = pexels_video(settings, scene.get("stock_query") or "", asset_dir, used_stock)
                kind = "video" if path else kind
                if strict and path is None:
                    raise WorkflowError(f"Cảnh {i + 1} chưa có stock hợp lệ; cần bổ sung nguồn hình.")
            if path is None:
                path = product_img
                kind = "card" if visual == "card" else "image"
                # Same still image twice in a row looks static: alternate framing.
                prev = scenes[-1] if scenes else None
                if prev and prev.get("source") == str(path) and prev.get("kind") == kind:
                    kind = "card" if kind == "image" else "image"
                    item["motion"] = ROTATE_MOTION.get(prev.get("motion") or "", "zoom_out")
            if path is None:
                if strict:
                    raise WorkflowError(f"Cảnh {i + 1} thiếu ảnh sản phẩm; cần bổ sung nguồn hình.")
                continue
            item.update(kind=kind, source=str(path), start=0.0, visual=visual,
                        generated=kind == "video" and visual == "generate")
        scenes.append(item)
    if strict:   # which takes continue from the previous take's last frame (S3)
        links = gen_engine.chain_map(plan["scenes"], settings)
        by_beat = {sc.get("beat"): sc for sc in scenes}
        for idx, prev in links.items():
            if idx in by_beat and prev in by_beat and by_beat[idx].get("generated"):
                by_beat[idx]["chain_in"] = True
                # the take feeding it: the last scene that uses the previous take's clip
                feeders = [sc for sc in scenes if sc.get("source") == by_beat[prev].get("source")]
                feeders[-1]["chain_out"] = True
    if mode == "base" and product_img and scenes:
        scenes.append({"kind": "card", "source": str(product_img), "duration": 2.4,
                       "voiceover": "", "text": "", "motion": "zoom_in"})
    if not scenes:
        raise WorkflowError("Chưa có hình ảnh để dựng: thêm clip/ảnh ở tab Kịch bản, "
                            "link ảnh sản phẩm, hoặc key Pexels/video AI.")
    jbrief = json.loads(job.get("brief_json") or "{}")
    if jbrief.get("fast_mode"):
        plan = {**plan, "fast_mode": True}
    for flag in ("no_music", "no_variants", "no_hook"):   # parts of a long video
        if jbrief.get(flag):
            plan = {**plan, flag: True}
    result = {"hook": plan.get("hook", ""),
              "cta": plan.get("cta", "") if strict else plan.get("cta") or settings.cta_text,
              "music_mood": plan.get("music_mood", "upbeat"), "scenes": scenes}
    if strict:
        result.update({k: plan[k] for k in ("creation_branch", "storyboard_version", "style", "characters",
                                            "voice_cast", "style_profile", "audience_gender",
                                            "hook_variants", "format", "niche", "goal",
                                            "duration_range", "facts", "takeaway", "script",
                                            "viewer_test", "fast_mode", "no_music", "no_variants",
                                            "no_hook", "dialogue",
                                            "duration_target_seconds", "content_mode")
                       if k in plan})
    return result


def _quality_gate(settings, job_id: str, manifest: dict, status: str) -> str | None:
    """PASS keeps the video; FIX regenerates the scenes the frame check flagged (once);
    REJECT parks the job for a person. Returns a new status when it changed one."""
    if not getattr(settings, "qa_autofix", True):
        return None
    job = get_job(settings.database_path, job_id)
    brief = json.loads((job or {}).get("brief_json") or "{}")
    if not brief.get("creation_branch"):
        return None
    from .qa_video import gate_decision
    qa = manifest.get("quality", {}).get("creative_qa", {})
    decision, scenes, reason = gate_decision(qa, int(brief.get("qa_fix_attempts") or 0))
    brief.setdefault("qa_history", []).append({"decision": decision, "scenes": scenes, "reason": reason,
                                               "score": qa.get("total_score"), "at": now_iso()})
    if decision == "FIX":
        brief["qa_fix_attempts"] = int(brief.get("qa_fix_attempts") or 0) + 1
        update_job(settings.database_path, job_id, brief_json=json.dumps(brief, ensure_ascii=False))
        made = gen_engine.regenerate_scenes(settings, job_id, scenes)
        if made:
            log.info("quality gate: regenerating %s scene(s) of %s", made, job_id)
            update_job(settings.database_path, job_id, last_error="QA: " + reason)
            return gen_engine.WAIT_STATUS
        return None
    update_job(settings.database_path, job_id, brief_json=json.dumps(brief, ensure_ascii=False))
    if decision == "REJECT" and status == "READY_FOR_REVIEW":
        update_job(settings.database_path, job_id, status="NEEDS_MEDIA_REVIEW",
                   last_error="QA không đạt: " + reason)
        return "NEEDS_MEDIA_REVIEW"
    return None


def _content_from_plan(plan: dict, affiliate_link: str, tiktok_link: str = "") -> dict:
    caption = str(plan.get("caption") or plan.get("title") or plan.get("hook") or "").strip()
    content = {
        "title": plan.get("title") or plan.get("hook") or "",
        "hook": plan.get("hook", ""),
        "caption": caption,
        "hashtags": _normalise_hashtags(plan.get("hashtags")),
        "cover_ratio": float(plan.get("cover_ratio") or 0.1),
        "facebook_description": plan.get("facebook_description") or caption,
        "tiktok_link": tiktok_link,
    }
    return finalise_content(content, affiliate_link)


def finalise_content(content: dict, affiliate_link: str) -> dict:
    caption = str(content.get("caption") or "").strip()
    if not affiliate_link:
        content["shopee_caption"] = caption
        content["facebook_post"] = (str(content.get("facebook_description") or caption).strip()
                                    + "\n" + " ".join(content.get("hashtags") or [])).strip()
        content["tiktok_caption"] = (caption + " " + " ".join(content.get("hashtags") or [])).strip()[:2200]
        content["affiliate_link"] = ""
        return content
    content["shopee_caption"] = f"{caption}\n\nLink sản phẩm: {affiliate_link}\n{DISCLOSURE}"
    content["facebook_post"] = (
        f"{str(content.get('facebook_description') or caption).strip()}\n\n"
        f"👉 Link sản phẩm: {affiliate_link}\n{DISCLOSURE}\n{' '.join(content.get('hashtags') or [])}"
    )
    # TikTok: no external (Shopee) link in the caption; the product is attached
    # from the TikTok Shop showcase (tiktok_link) when finishing the post in the app.
    where = "giỏ hàng (TikTok Shop)" if content.get("tiktok_link") else "bio"
    content["tiktok_caption"] = (
        f"{caption} {' '.join(content.get('hashtags') or [])}\n"
        f"Sản phẩm ở {where}. #affiliate #lienkettiepthi"
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
    _mark(settings, job_id, "render_start")
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
            "scenes": result.scenes, "music_metadata": getattr(result, "music_metadata", None)}}, ensure_ascii=False),
        shopee_ready=int(shopee_ready), facebook_ready=int(facebook_ready),
        duration_sec=meta["duration_sec"], width=meta["width"], height=meta["height"],
        qa_score=manifest["quality"].get("creative_qa", {}).get("total_score"),
        last_error=None if shopee_ready else "; ".join(manifest["quality"]["shopee"]["reasons"]),
    )
    _mark(settings, job_id, "render_done")
    try:   # what this video is made of, for the channel learning loop
        from .learning import features_from_timeline, save_features
        save_features(db, job_id, features_from_timeline(
            timeline, result.duration, json.loads(job.get("brief_json") or "{}")))
    except Exception as exc:  # noqa: BLE001
        log.warning("feature record failed: %s", exc)
    gate = _quality_gate(settings, job_id, manifest, status)
    if gate:
        status = gate
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
    t_qa = quality.get("technical_qa") or {}
    c_qa = quality.get("creative_qa") or {}
    hashtags = " ".join(content.get("hashtags") or [])
    music_meta = manifest.get("music_metadata") or {}
    music_html = ""
    if music_meta:
        reasons_text = esc("; ".join(music_meta.get("reasons") or []))
        music_html = f"""<section><h2>Bản quyền âm nhạc (AI Background Music - Mixkit)</h2>
<p><strong>{esc(music_meta.get("title"))}</strong> — Tác giả: <b>{esc(music_meta.get("author"))}</b></p>
<p>Nguồn tải: <a href="{esc(music_meta.get("source_url"))}" target="_blank" rel="noopener noreferrer">{esc(music_meta.get("source_url"))}</a> (ID: {esc(music_meta.get("track_id"))})</p>
<p>Giấy phép: <a href="{esc(music_meta.get("license_url"))}" target="_blank" rel="noopener noreferrer">{esc(music_meta.get("license"))}</a> (Miễn phí thương mại &amp; mạng xã hội)</p>
<p>Điểm thẩm định: <b>{music_meta.get("score")}/100</b> <span class="status">{esc(music_meta.get("decision"))}</span></p>
<p class="muted">Lý do chọn: {reasons_text}</p>
<p class="muted"><i>Dọn dẹp tự động: File nhạc tạm đã được xóa ngay sau khi render xong để giải phóng dung lượng ổ cứng.</i></p></section>"""

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
<section><h2>QA video (xem hình + đo thật)</h2>
<p><b>Tổng điểm: {c_qa.get('total_score', 0)} / 100</b> — <span class="status">{c_qa.get('status', 'PENDING')}</span>
{"" if c_qa.get('visual_checked') else '<span class="status">CHƯA KIỂM HÌNH</span>'}</p>
<p class="muted">Thành phần: {esc(json.dumps(c_qa.get('breakdown') or {}, ensure_ascii=False))}
 · Hình: {esc(json.dumps({k: v for k, v in (c_qa.get('visual') or {}).items() if k != 'issues'}, ensure_ascii=False))}</p>
<p><b>Cần sửa:</b> {esc("; ".join(c_qa.get('improvements') or []) or "—")}</p>
</section>
<section><h2>Kiểm tra kỹ thuật file video thực tế (Technical QA)</h2>
<p><span class="status">Kỹ thuật video: {"PASS" if t_qa.get("passed") else "CHECK"}</span>
<span class="status">Shopee: {"PASS" if shopee.get("passed") else "CHECK"}</span>
<span class="status">Facebook Reels: {"PASS" if facebook.get("passed") else "CHECK"}</span></p>
<p class="muted">Âm thanh: Max {t_qa.get('audio_metrics', {}).get('max_volume_db', 'N/A')} dB, Mean {t_qa.get('audio_metrics', {}).get('mean_volume_db', 'N/A')} dB | Khoảng im lặng: {t_qa.get('audio_metrics', {}).get('silence_intervals', 0)} ({t_qa.get('audio_metrics', {}).get('total_silence_sec', 0)}s)</p>
<p class="muted">Phụ đề: {t_qa.get('subtitle_metrics', {}).get('total_subtitles', 0)} dòng, Hook: {t_qa.get('subtitle_metrics', {}).get('has_hook', False)}, CTA: {t_qa.get('subtitle_metrics', {}).get('has_cta', False)}</p>
<p class="muted">Shopee: {esc("; ".join(shopee.get("reasons") or [])) or "Basic file integrity passed"}</p>
<p class="muted">Facebook: {esc("; ".join(facebook.get("reasons") or [])) or "Existing 9:16 QA passed"}</p></section>
{music_html}
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

    # The AI look at the frames (~90 s) runs while the technical checks and copies happen.
    from concurrent.futures import ThreadPoolExecutor

    from .qa_video import score_video, visual_check
    product_ref = next(iter(sorted((settings.work_dir / job["id"] / "assets").glob("product_*.jpg"))), None)         if (settings.work_dir / job["id"] / "assets").is_dir() else None
    pool = ThreadPoolExecutor(max_workers=1)
    visual_future = pool.submit(visual_check, settings, video_out, timeline, result.scenes, product_ref)

    # 2.0: Technical QA on the rendered video
    tech_qa = inspect_rendered_video(video_out, settings, timeline=timeline)
    if tech_qa.critical_errors:
        shopee_qa.reasons.extend(tech_qa.critical_errors)
        shopee_qa.passed = False
        facebook_qa.reasons.extend(tech_qa.critical_errors)
        facebook_qa.passed = False

    # 2.0: Creative Quality Score (100-point rubric)
    brief = json.loads(job.get("brief_json") or "{}") if isinstance(job, dict) else {}
    creative_qa = evaluate_creative_quality(
        timeline,
        content=content,
        mode=job.get("mode") or "viral",
        branch=brief.get("creation_branch") or "",
        rendered_duration=float(metadata.get("duration_sec") or 0) or None,
        music_used=bool(getattr(result, "music", None)),
    )

    try:
        visual = visual_future.result()
    except Exception as exc:  # noqa: BLE001 - never a pass: scored as "not checked"
        log.warning("visual QA failed: %s", exc)
        visual = None
    pool.shutdown(wait=False)
    from .qa_video import seam_similarity
    real_qa = score_video(timeline, result.scenes, float(metadata.get("duration_sec") or 0),
                          bool(getattr(result, "music", None)), visual, job.get("viral_score"),
                          getattr(result, "subtitles", None), tech_qa.passed,
                          voice_segments=getattr(result, "voice_segments", None),
                          seam_ssim=seam_similarity(settings, video_out, getattr(result, "seams", None)))
    if "wrong_product" in real_qa["gates"]:
        shopee_qa.reasons.append("QA: sản phẩm trong video không khớp sản phẩm thật")
        shopee_qa.passed = False

    facebook_out = None
    if facebook_qa.passed:
        fb_dir = settings.facebook_output / job["id"]
        fb_dir.mkdir(parents=True, exist_ok=True)
        facebook_out = fb_dir / video_out.name
        shutil.copy2(video_out, facebook_out)

    warnings = list(tech_qa.warnings)
    if not result.music:
        warnings.append(f"Chưa có nhạc nền riêng: dùng ambient floor thay thế ({settings.music})")
    if not result.voice:
        warnings.append("Không tạo được giọng đọc (TTS); video chỉ có phụ đề.")
    manifest = {
        "job_id": job["id"], "mode": job.get("mode") or "base",
        "source": job.get("source_path"), "video": str(video_out), "cover": str(cover),
        "metadata": metadata, "analysis": analysis, "product": product,
        "affiliate_link": affiliate_link, "content": content, "timeline": timeline,
        "keyframes": saved_frames, "warnings": warnings,
        "hook_variants": [{"index": 1, "hook": timeline.get("hook", ""), "path": str(video_out)}]
        + list(getattr(result, "variants", None) or []),
        "music_metadata": getattr(result, "music_metadata", None),
        "quality": {
            "shopee": {"passed": shopee_qa.passed, "reasons": shopee_qa.reasons},
            "facebook_reels": {"passed": facebook_qa.passed, "reasons": facebook_qa.reasons,
                               "output": str(facebook_out) if facebook_out else None},
            "technical_qa": {
                "passed": tech_qa.passed,
                "critical_errors": tech_qa.critical_errors,
                "warnings": tech_qa.warnings,
                "audio_metrics": tech_qa.audio_metrics,
                "video_metrics": tech_qa.video_metrics,
                "subtitle_metrics": tech_qa.subtitle_metrics,
            },
            "creative_qa": {
                **real_qa,
                "rubric_note": "Điểm thành phần dưới đây là gợi ý theo kịch bản, không cộng vào tổng.",
                "hook": creative_qa.hook,
                "storytelling": creative_qa.storytelling,
                "emotion": creative_qa.emotion,
                "creativity": creative_qa.creativity,
                "storyboard": creative_qa.storyboard,
                "voice_audio": creative_qa.voice_audio,
                "editing": creative_qa.editing,
                "cta_fit": creative_qa.cta_fit,
                "strengths": creative_qa.strengths,
                "improvements": real_qa["improvements"] + creative_qa.improvements,
            },
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
        clips = {c["id"]: c for c in list_clips(settings, job_id)}
        # Sources the pipeline already chose stay valid; anything new must be a clip
        # or one of this job's own files.
        known = {str(sc.get("source")) for sc in
                 json.loads(job.get("script_json") or "{}").get("scenes") or []}
        scenes = []
        for scene in timeline["scenes"]:
            if not isinstance(scene, dict):
                continue
            clip_id = scene.get("clip_id")
            if clip_id and clip_id not in clips and str(scene.get("source")) in known:
                scene = {k: v for k, v in scene.items() if k != "clip_id"}  # clip deleted
                clip_id = None
            if clip_id:
                if clip_id not in clips:
                    raise WorkflowError(f"Clip không tồn tại: {clip_id}")
                scene = {**scene, **_clip_scene(settings, job_id, clips[clip_id],
                                                scene.get("start") or 0)}
            elif (str(scene.get("source")) not in known
                  and not is_allowed_source(settings, job, str(scene.get("source") or ""))):
                raise WorkflowError("Nguồn hình của cảnh không hợp lệ")
            scenes.append(scene)
        if not scenes:
            raise WorkflowError("Script must contain scenes")
        fields["script_json"] = json.dumps({**timeline, "scenes": scenes}, ensure_ascii=False)
    if content_updates:
        content = json.loads(job.get("content_json") or "{}")
        for key in ("title", "caption", "facebook_description", "tiktok_link"):
            if key in content_updates:
                content[key] = str(content_updates[key]).strip()
        if content.get("tiktok_link") and not _is_affiliate_link(content["tiktok_link"]):
            raise WorkflowError("Link TikTok phải là https://...tiktok.com/...")
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
    _mark_sub_id(settings, job_id)
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


def _mark_sub_id(settings, job_id: str) -> None:
    """The app built this link with sub_id = job id, so orders can be traced to the video."""
    job = get_job(settings.database_path, job_id)
    if job:
        brief = json.loads(job.get("brief_json") or "{}")
        brief["link_has_sub_id"] = True
        update_job(settings.database_path, job_id, brief_json=json.dumps(brief, ensure_ascii=False))


def use_hook_variant(settings, job_id: str, index: int) -> dict:
    """Make hook variant N the video that gets approved/published (A/B pick by the owner)."""
    job = get_job(settings.database_path, job_id)
    if not job or not job.get("output_path"):
        raise WorkflowError("Video chưa dựng xong")
    if job["status"] not in {"READY_FOR_REVIEW", "NEEDS_MEDIA_REVIEW"}:
        raise WorkflowError("Chỉ đổi bản hook trước khi duyệt")
    out = Path(job["output_path"])
    manifest = json.loads((out.parent / "manifest.json").read_text(encoding="utf-8"))
    variants = {int(v["index"]): v for v in manifest.get("hook_variants") or []}
    if index not in variants or index == 1:
        raise WorkflowError("Không có bản hook này")
    chosen = Path(variants[index]["path"])
    if not chosen.is_file() or chosen == out:
        raise WorkflowError("File bản hook không còn")
    # swap the two files' contents; paths stay fixed, so switching back and forth is safe
    swap = out.with_name(out.stem + ".swap" + out.suffix)
    shutil.copy2(out, swap)
    shutil.copy2(chosen, out)
    shutil.move(str(swap), chosen)
    fb = Path(job.get("facebook_output_path") or "")
    if fb.is_file():
        shutil.copy2(out, fb)
    variants[1]["hook"], variants[index]["hook"] = variants[index]["hook"], variants[1]["hook"]
    manifest["hook_variants"] = [variants[k] for k in sorted(variants)]
    timeline = json.loads(job.get("script_json") or "{}")
    timeline["hook_variants"] = [variants[k]["hook"] for k in sorted(variants) if k != 1]
    timeline["hook"] = variants[1]["hook"]
    manifest["timeline"]["hook"] = timeline["hook"]
    (out.parent / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2,
                                                         default=str), encoding="utf-8")
    content = json.loads(job.get("content_json") or "{}")
    content["hook"] = timeline["hook"]
    update_job(settings.database_path, job_id, script_json=json.dumps(timeline, ensure_ascii=False),
               content_json=json.dumps(content, ensure_ascii=False))
    from .learning import features_from_timeline, save_features
    save_features(settings.database_path, job_id, {
        **features_from_timeline(timeline, float(job.get("duration_sec") or 0)), "hook_variant": index})
    return {"hook": timeline["hook"]}


def link_check(settings, job: dict) -> dict:
    """Is the product link right for this video? Blockers stop approval, notes only inform."""
    brief = json.loads(job.get("brief_json") or "{}")
    branch = brief.get("creation_branch")
    link = str(job.get("affiliate_link") or "")
    blockers, notes = [], []
    if branch == "product_review" and not link:
        blockers.append("Video review chưa có link affiliate của sản phẩm đang review")
    if link and not _is_affiliate_link(link):
        blockers.append("Link gắn kèm không phải link Shopee/TikTok hợp lệ")
    if branch == "product_review" and link and job.get("product_item_id"):
        offer = brief.get("offer") or {}
        reviewed = str(offer.get("itemId") or offer.get("item_id") or "")
        if reviewed and reviewed != str(job["product_item_id"]):
            blockers.append("Link đang gắn thuộc sản phẩm khác với sản phẩm trong video")
    if link and not brief.get("link_has_sub_id"):
        notes.append(f"Link lấy từ kho (CSV/API data) chưa gắn Sub_id: đơn hàng không tách được theo "
                     f"video. Muốn đo theo video, tạo Custom Link có Sub_id = {job['id']}.")
    timeline = json.loads(job.get("script_json") or "{}")
    facts = timeline.get("facts") or []
    if facts:
        notes.append("Video kiến thức: kiểm tra trước khi duyệt các khẳng định: "
                     + "; ".join(f"{f['claim']} ({f.get('confidence', 0):.0f}%)" for f in facts[:6]))
    if branch in {"viral_creator", "template_series"} and not link:
        notes.append("Video viral chưa gắn sản phẩm: chọn ở mục \"Gợi ý sản phẩm phù hợp\" (đã được AI xác nhận).")
    return {"blockers": blockers, "notes": notes}


def publish_blockers(settings, job: dict, platform: str, action: str = "draft") -> list[str]:
    issues = list(link_check(settings, job)["blockers"])
    if platform == "shopee":
        if "tiktok" in str(job.get("affiliate_link") or ""):
            issues.append("Sản phẩm TikTok Shop: không đăng lên Shopee Video")
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
