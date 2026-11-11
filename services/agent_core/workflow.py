import base64
import html
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .ai_client import AIClient, AIClientError
from .affiliate_client import ShopeeAffiliateClient, ShopeeAffiliateError
from .db import (
    get_job,
    list_platform_posts,
    update_job,
    upsert_platform_post,
)
from .media import extract_keyframes, probe_video
from .product_matcher import rank_products
from .quality import validate_facebook_reel, validate_shopee_video
from .shopee_video import upload_shopee_video


class WorkflowError(RuntimeError):
    pass


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
            raise WorkflowError("AI response did not contain a JSON object")
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
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        if protocol.lower() == "anthropic-messages":
            parts.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": encoded,
                },
            })
        else:
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            })
    return [{"role": "user", "content": parts}]


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


def _content_prompt(analysis: dict, product: dict, affiliate_link: str) -> str:
    return f"""
Viết nội dung affiliate bằng tiếng Việt tự nhiên, ngắn gọn, đúng văn trao đổi.
Dữ liệu quan sát video: {json.dumps(analysis, ensure_ascii=False)}
Dữ liệu sản phẩm Shopee trả về: {json.dumps(product, ensure_ascii=False)}
Link affiliate: {affiliate_link}

Trả về DUY NHẤT JSON object:
{{
  "title": "tiêu đề ngắn",
  "caption": "caption tiếng Việt",
  "hashtags": ["5-8 hashtag"],
  "cover_ratio": 0.15,
  "facebook_description": "mô tả ngắn cho Facebook Reels"
}}

Quy tắc:
- Không bịa giá, thông số, bảo hành, xuất xứ, chứng nhận hoặc tác dụng không có trong dữ liệu.
- Không khẳng định đã dùng/thử sản phẩm nếu video không chứng minh.
- Không dùng câu giật gân sai sự thật hoặc cam kết kết quả.
- Caption dễ đọc, không viết hoa toàn bộ, không nhồi từ khóa.
- Không tự chèn URL; hệ thống sẽ thêm link và thông báo liên kết tiếp thị.
- Hashtag không có dấu cách; không tạo hashtag thương hiệu nếu không có căn cứ.
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


def _affiliate_configured(affiliate: ShopeeAffiliateClient) -> bool:
    credentials = affiliate.credentials_status()
    return bool(
        credentials.get("sections", {})
        .get("shopee_affiliate", {})
        .get("configured", False)
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


def _generate_content(
    client: AIClient, analysis: dict, product: dict, affiliate_link: str
) -> dict:
    reply = client.chat(
        [{"role": "user", "content": _content_prompt(analysis, product, affiliate_link)}],
        temperature=0.35,
        max_tokens=1000,
    )
    content = _parse_json_reply(reply)
    content["hashtags"] = _normalise_hashtags(content.get("hashtags"))
    content["cover_ratio"] = max(0.0, min(1.0, float(content.get("cover_ratio", 0.15))))
    return content


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
video{{width:100%;max-height:70vh;background:#111;border-radius:10px}}
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
<section><h2>Nội dung Shopee</h2><pre>{esc(content.get("shopee_caption"))}</pre>
<p>{esc(hashtags)}</p></section>
<section><h2>Khung hình xem trước</h2><div class="grid">{''.join(frames)}</div></section>
<section><h2>Kiểm tra chất lượng</h2>
<p><span class="status">Shopee: {"PASS" if shopee.get("passed") else "CHECK"}</span>
<span class="status">Facebook Reels: {"PASS" if facebook.get("passed") else "CHECK"}</span></p>
<p class="muted">Shopee: {esc("; ".join(shopee.get("reasons") or [])) or "Basic file integrity passed"}</p>
<p class="muted">Facebook: {esc("; ".join(facebook.get("reasons") or [])) or "Existing 9:16 QA passed"}</p></section>
<section><h2>Bước tiếp theo</h2>
<p>Kiểm tra video, sản phẩm, caption và link trước khi gửi bản nháp.</p>
<pre>python -m agent_core.cli show {esc(manifest.get("job_id"))}
python -m agent_core.cli shopee-draft {esc(manifest.get("job_id"))} --confirm</pre>
<p class="muted">Lệnh thứ hai chỉ tải lên dạng bản nháp; không đăng công khai.</p>
</section></main></body></html>
"""
    target = output_dir / "review.html"
    target.write_text(page, encoding="utf-8")
    return target


def _save_review_package(
    settings,
    job: dict,
    metadata: dict,
    analysis: dict,
    product: dict,
    content: dict,
    affiliate_link: str,
    frame_paths: list[Path],
) -> tuple[Path, bool, bool, dict]:
    source = Path(job["source_path"])
    output_dir = settings.shopee_output / job["id"]
    preview_dir = output_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    video_out = output_dir / source.name
    if source.resolve() != video_out.resolve():
        shutil.copy2(source, video_out)

    saved_frames = []
    for frame in frame_paths:
        target = preview_dir / frame.name
        if frame.resolve() != target.resolve():
            shutil.copy2(frame, target)
        saved_frames.append(str(target))

    hashtags = _normalise_hashtags(content.get("hashtags"))
    caption = str(content.get("caption") or "").strip()
    if not caption:
        raise WorkflowError("AI did not generate a usable caption")
    shopee_caption = (
        f"{caption}\n\nLink sản phẩm: {affiliate_link}\n"
        "Thông báo: Đây là liên kết tiếp thị liên kết; tôi có thể nhận hoa hồng "
        "nếu bạn mua hàng qua liên kết này."
    )
    content["hashtags"] = hashtags
    content["caption"] = caption
    content["shopee_caption"] = shopee_caption
    content["affiliate_link"] = affiliate_link

    shopee_qa = validate_shopee_video(video_out, metadata)
    facebook_qa = validate_facebook_reel(metadata, settings)
    facebook_out = None
    if facebook_qa.passed:
        fb_dir = settings.facebook_output / job["id"]
        fb_dir.mkdir(parents=True, exist_ok=True)
        facebook_out = fb_dir / source.name
        if video_out.resolve() != facebook_out.resolve():
            shutil.copy2(video_out, facebook_out)

    manifest = {
        "job_id": job["id"],
        "source": str(source),
        "video": str(video_out),
        "metadata": metadata,
        "analysis": analysis,
        "product": product,
        "affiliate_link": affiliate_link,
        "content": content,
        "keyframes": saved_frames,
        "quality": {
            "shopee": {
                "passed": shopee_qa.passed,
                "reasons": shopee_qa.reasons,
            },
            "facebook_reels": {
                "passed": facebook_qa.passed,
                "reasons": facebook_qa.reasons,
                "output": str(facebook_out) if facebook_out else None,
            },
        },
        "publishing": "manual_review_required",
    }
    (output_dir / "caption.txt").write_text(
        shopee_caption + "\n\n" + " ".join(hashtags) + "\n",
        encoding="utf-8",
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    review_page = _write_review_page(output_dir, manifest)
    manifest["review_page"] = str(review_page)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return video_out, shopee_qa.passed, facebook_qa.passed, manifest


def process_job(settings, job_id: str) -> dict:
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if job["status"] in {"READY_FOR_REVIEW", "DRAFT_SUBMITTED_UNCONFIRMED"}:
        return {
            "job_id": job_id,
            "status": job["status"],
            "message": "Already processed; refusing to regenerate or duplicate work.",
            "output_path": job.get("output_path"),
        }

    source = Path(job["source_path"])
    if not source.is_file():
        update_job(
            settings.database_path, job_id,
            status="SOURCE_MISSING", last_error="Source video file no longer exists.",
        )
        return {"job_id": job_id, "status": "SOURCE_MISSING"}

    metadata = probe_video(source, settings.ffprobe_path)
    try:
        frame_paths = _load_or_extract_keyframes(settings, job, source)
    except Exception as exc:
        update_job(
            settings.database_path, job_id,
            status="KEYFRAME_EXTRACTION_FAILED", last_error=str(exc)[:1000],
        )
        return {"job_id": job_id, "status": "KEYFRAME_EXTRACTION_FAILED", "error": str(exc)}

    update_job(
        settings.database_path, job_id,
        keyframes_json=json.dumps([str(path) for path in frame_paths], ensure_ascii=False),
        last_error=None,
    )

    try:
        client = _client(settings)
    except AIClientError as exc:
        message = str(exc)
        update_job(
            settings.database_path, job_id,
            status="WAITING_FOR_AI_CREDENTIALS", last_error=message[:1000],
        )
        return {
            "job_id": job_id, "status": "WAITING_FOR_AI_CREDENTIALS",
            "keyframes": [str(path) for path in frame_paths], "error": message,
        }

    analysis = None
    if job.get("analysis_json"):
        try:
            analysis = json.loads(job["analysis_json"])
            if not isinstance(analysis, dict):
                analysis = None
        except (TypeError, json.JSONDecodeError):
            analysis = None

    if analysis is None:
        update_job(settings.database_path, job_id, status="ANALYZING")
        try:
            analysis_reply = client.chat(
                _image_messages(
                    _analyse_prompt(job, metadata), frame_paths, settings.ai_protocol
                ),
                temperature=0.1,
                max_tokens=1400,
            )
            analysis = _parse_json_reply(analysis_reply)
        except AIClientError as exc:
            message = str(exc)
            status = (
                "WAITING_FOR_AI_CREDENTIALS"
                if "401" in message or "API key" in message
                else "WAITING_FOR_AI_SERVICE"
            )
            update_job(
                settings.database_path, job_id, status=status, last_error=message[:1000]
            )
            return {
                "job_id": job_id, "status": status,
                "keyframes": [str(path) for path in frame_paths],
                "error": message,
            }
        except Exception as exc:
            update_job(
                settings.database_path, job_id,
                status="AI_ANALYSIS_FAILED", last_error=str(exc)[:1000],
            )
            return {"job_id": job_id, "status": "AI_ANALYSIS_FAILED", "error": str(exc)}
        update_job(
            settings.database_path, job_id,
            status="ANALYZED",
            ai_generated=1,
            analysis_json=json.dumps(analysis, ensure_ascii=False),
            last_error=None,
        )
    else:
        update_job(
            settings.database_path, job_id,
            status="ANALYZED", ai_generated=1, last_error=None,
        )

    keywords = analysis.get("product_search_keywords")
    if not isinstance(keywords, list):
        keywords = []
    keywords = [str(value).strip() for value in keywords if str(value).strip()][:4]
    if not analysis.get("product_category") or not keywords:
        update_job(
            settings.database_path, job_id,
            status="NEEDS_PRODUCT_REVIEW",
            review_status="NEEDS_PRODUCT",
            last_error="AI could not identify a product confidently from the video.",
        )
        return {"job_id": job_id, "status": "NEEDS_PRODUCT_REVIEW", "analysis": analysis}

    if not settings.shopee_affiliate_enabled:
        update_job(
            settings.database_path, job_id,
            status="WAITING_FOR_SHOPEE_CREDENTIALS",
            last_error="Enable Shopee Affiliate API after adding App ID and Secret.",
        )
        return {
            "job_id": job_id,
            "status": "WAITING_FOR_SHOPEE_CREDENTIALS",
            "analysis": analysis,
        }

    affiliate = ShopeeAffiliateClient(settings)
    try:
        if not _affiliate_configured(affiliate):
            update_job(
                settings.database_path, job_id,
                status="WAITING_FOR_SHOPEE_CREDENTIALS",
                last_error="Shopee Affiliate App ID or Secret is missing.",
            )
            return {"job_id": job_id, "status": "WAITING_FOR_SHOPEE_CREDENTIALS"}
    except Exception as exc:
        update_job(
            settings.database_path, job_id,
            status="WAITING_FOR_SHOPEE_CREDENTIALS", last_error=str(exc)[:1000],
        )
        return {
            "job_id": job_id,
            "status": "WAITING_FOR_SHOPEE_CREDENTIALS",
            "error": str(exc),
        }

    product = None
    affiliate_link = str(job.get("affiliate_link") or "")
    if job.get("product_item_id") and affiliate_link:
        try:
            saved_candidates = json.loads(job.get("product_candidates_json") or "[]")
        except json.JSONDecodeError:
            saved_candidates = []
        product = next(
            (
                candidate for candidate in saved_candidates
                if str(candidate.get("item_id")) == str(job["product_item_id"])
            ),
            None,
        )
        if product is None:
            product = {
                "item_id": str(job["product_item_id"]),
                "name": str(job.get("product_name") or ""),
                "offer_link": "",
            }

    if product is None:
        offers_by_id: dict[str, dict] = {}
        try:
            for keyword in keywords[:3]:
                for offer in affiliate.search_offers(keyword, limit=20):
                    item_id = str(offer.get("itemId") or "")
                    if item_id:
                        offers_by_id[item_id] = offer
        except ShopeeAffiliateError as exc:
            update_job(
                settings.database_path, job_id,
                status="WAITING_FOR_SHOPEE_API", last_error=str(exc)[:1000],
            )
            return {"job_id": job_id, "status": "WAITING_FOR_SHOPEE_API", "error": str(exc)}

        context = " ".join([
            str(analysis.get("product_category") or ""),
            str(analysis.get("visual_summary") or ""),
            " ".join(keywords),
            str(analysis.get("visible_brand") or ""),
        ])
        match = rank_products(
            context, list(offers_by_id.values()), min_score=68, min_margin=10
        )
        update_job(
            settings.database_path, job_id,
            product_candidates_json=json.dumps(match.get("candidates", []), ensure_ascii=False),
        )
        if match.get("status") != "MATCHED":
            update_job(
                settings.database_path, job_id,
                status="NEEDS_PRODUCT_REVIEW",
                review_status="NEEDS_PRODUCT",
                last_error=f"No safe automatic product match: {match.get('status')}",
            )
            return {"job_id": job_id, "status": "NEEDS_PRODUCT_REVIEW", "match": match}

        product = match["product"]
        offer_url = str(product.get("offer_link") or "")
        if not offer_url:
            update_job(
                settings.database_path, job_id,
                status="NEEDS_PRODUCT_REVIEW",
                review_status="NEEDS_PRODUCT",
                last_error="Matched offer did not contain a Shopee product URL.",
            )
            return {"job_id": job_id, "status": "NEEDS_PRODUCT_REVIEW", "match": match}

        try:
            affiliate_link = affiliate.create_short_link(offer_url, sub_id=job_id)
        except Exception as exc:
            update_job(
                settings.database_path, job_id,
                status="WAITING_FOR_SHOPEE_API", last_error=str(exc)[:1000],
            )
            return {"job_id": job_id, "status": "WAITING_FOR_SHOPEE_API", "error": str(exc)}

        update_job(
            settings.database_path, job_id,
            product_item_id=product["item_id"],
            product_name=product["name"],
            affiliate_link=affiliate_link,
        )

    try:
        content = _generate_content(client, analysis, product, affiliate_link)
    except Exception as exc:
        update_job(
            settings.database_path, job_id,
            status="CONTENT_GENERATION_FAILED", last_error=str(exc)[:1000],
            product_item_id=product["item_id"],
            product_name=product["name"],
            affiliate_link=affiliate_link,
        )
        return {"job_id": job_id, "status": "CONTENT_GENERATION_FAILED", "error": str(exc)}

    try:
        video_out, shopee_ready, facebook_ready, manifest = _save_review_package(
            settings, job, metadata, analysis, product, content,
            affiliate_link, frame_paths,
        )
    except Exception as exc:
        update_job(
            settings.database_path, job_id,
            status="REVIEW_PACKAGE_FAILED", last_error=str(exc)[:1000],
            product_item_id=product["item_id"],
            product_name=product["name"],
            affiliate_link=affiliate_link,
            content_json=json.dumps(content, ensure_ascii=False),
        )
        return {"job_id": job_id, "status": "REVIEW_PACKAGE_FAILED", "error": str(exc)}

    status = "READY_FOR_REVIEW" if shopee_ready else "NEEDS_MEDIA_REVIEW"
    update_job(
        settings.database_path, job_id,
        status=status,
        review_status="PENDING",
        product_item_id=product["item_id"],
        product_name=product["name"],
        affiliate_link=affiliate_link,
        output_path=str(video_out),
        facebook_output_path=manifest["quality"]["facebook_reels"]["output"],
        analysis_json=json.dumps(analysis, ensure_ascii=False),
        content_json=json.dumps(content, ensure_ascii=False),
        shopee_ready=int(shopee_ready),
        facebook_ready=int(facebook_ready),
        last_error=None if shopee_ready else "Shopee media QA failed.",
    )
    return {
        "job_id": job_id,
        "status": status,
        "output_path": str(video_out),
        "shopee_ready": shopee_ready,
        "facebook_ready": facebook_ready,
        "manifest": manifest,
    }


def upload_shopee_draft(settings, job_id: str, confirmed: bool = False) -> dict:
    if not confirmed:
        raise WorkflowError("Draft upload requires --confirm; no action was taken.")
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if job["status"] != "READY_FOR_REVIEW" or not job.get("shopee_ready"):
        raise WorkflowError("Job is not READY_FOR_REVIEW for Shopee.")
    existing = list_platform_posts(settings.database_path, job_id)
    previous = next((post for post in existing if post["platform"] == "shopee"), None)
    if previous and previous["status"] not in {"UPLOAD_FAILED_CONFIRMED"}:
        raise WorkflowError(
            f"Shopee already has an upload attempt ({previous['status']}); "
            "reconcile it before retrying to avoid duplicate drafts."
        )

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
            action="draft",
        )
    except Exception as exc:
        upsert_platform_post(
            settings.database_path, job_id, "shopee",
            "UPLOAD_NEEDS_RECONCILIATION", last_error=str(exc)[:1000],
        )
        update_job(
            settings.database_path, job_id,
            status="UPLOAD_NEEDS_RECONCILIATION",
            review_status="UPLOAD_NEEDS_RECONCILIATION",
            last_error=str(exc)[:1000],
        )
        raise WorkflowError(
            "Shopee upload did not return a confirmed result. Check Creator Center "
            "before retrying. Details: " + str(exc)
        ) from exc

    status = result.get("status", "DRAFT_SUBMITTED_UNCONFIRMED")
    upsert_platform_post(
        settings.database_path, job_id, "shopee", status,
        external_id=str((result.get("result") or {}).get("video_id") or "") or None,
    )
    update_job(
        settings.database_path, job_id,
        status=status,
        review_status=status,
        last_error=None,
    )
    return result


def select_product_for_job(
    settings, job_id: str, item_id: str, confirmed: bool = False
) -> dict:
    """Use an explicitly user-selected candidate when automatic matching is uncertain."""
    if not confirmed:
        raise WorkflowError("Product selection requires --confirm; no action was taken.")
    job = get_job(settings.database_path, job_id)
    if not job:
        raise WorkflowError(f"Job not found: {job_id}")
    if job["status"] not in {"NEEDS_PRODUCT_REVIEW", "WAITING_FOR_SHOPEE_CREDENTIALS", "WAITING_FOR_SHOPEE_API"}:
        raise WorkflowError("Job is not waiting for product selection.")
    if not settings.shopee_affiliate_enabled:
        raise WorkflowError("Shopee Affiliate API is disabled in local settings.")

    try:
        candidates = json.loads(job.get("product_candidates_json") or "[]")
        analysis = json.loads(job.get("analysis_json") or "{}")
    except json.JSONDecodeError as exc:
        raise WorkflowError("Saved analysis or product candidates are invalid.") from exc
    product = next(
        (candidate for candidate in candidates if str(candidate.get("item_id")) == str(item_id)),
        None,
    )
    if not product:
        raise WorkflowError(
            f"Product ID {item_id} is not in this job's candidate list. "
            "Run process again or inspect the current candidate IDs."
        )
    if not product.get("offer_link"):
        raise WorkflowError("Selected product has no Shopee offer URL.")

    affiliate = ShopeeAffiliateClient(settings)
    if not _affiliate_configured(affiliate):
        update_job(
            settings.database_path, job_id,
            status="WAITING_FOR_SHOPEE_CREDENTIALS",
            last_error="Shopee Affiliate App ID or Secret is missing.",
        )
        raise WorkflowError("Shopee Affiliate App ID or Secret is missing.")

    affiliate_link = ""
    try:
        affiliate_link = affiliate.create_short_link(
            str(product["offer_link"]), sub_id=job_id
        )
        client = _client(settings)
        content = _generate_content(client, analysis, product, affiliate_link)
        source = Path(job["source_path"])
        metadata = probe_video(source, settings.ffprobe_path)
        frame_paths = _load_or_extract_keyframes(settings, job, source)
        video_out, shopee_ready, facebook_ready, manifest = _save_review_package(
            settings, job, metadata, analysis, product, content,
            affiliate_link, frame_paths,
        )
    except Exception as exc:
        message = str(exc)
        waiting_status = (
            "WAITING_FOR_AI_CREDENTIALS"
            if "API key" in message or "401" in message
            else "PRODUCT_SELECTION_FAILED"
        )
        update_job(
            settings.database_path, job_id,
            status=waiting_status,
            product_item_id=str(product.get("item_id") or item_id),
            product_name=str(product.get("name") or ""),
            affiliate_link=affiliate_link or job.get("affiliate_link"),
            last_error=message[:1000],
        )
        raise WorkflowError(f"Could not prepare selected product: {exc}") from exc

    status = "READY_FOR_REVIEW" if shopee_ready else "NEEDS_MEDIA_REVIEW"
    update_job(
        settings.database_path, job_id,
        status=status,
        review_status="PENDING",
        product_item_id=str(product["item_id"]),
        product_name=str(product["name"]),
        affiliate_link=affiliate_link,
        output_path=str(video_out),
        facebook_output_path=manifest["quality"]["facebook_reels"]["output"],
        content_json=json.dumps(content, ensure_ascii=False),
        shopee_ready=int(shopee_ready),
        facebook_ready=int(facebook_ready),
        last_error=None if shopee_ready else "Shopee media QA failed.",
    )
    return {
        "job_id": job_id,
        "selected_product": product,
        "status": status,
        "output_path": str(video_out),
        "shopee_ready": shopee_ready,
        "facebook_ready": facebook_ready,
        "manifest": manifest,
    }


def publish_facebook_reel_job(
    settings, job_id: str, confirmed: bool = False
) -> dict:
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

    existing = list_platform_posts(settings.database_path, job_id)
    previous = next(
        (post for post in existing if post["platform"] == "facebook_reels"),
        None,
    )
    if previous and previous["status"] not in {"UPLOAD_FAILED_CONFIRMED"}:
        raise WorkflowError(
            f"Facebook already has an upload attempt ({previous['status']}); "
            "reconcile it before retrying to avoid duplicate Reels."
        )

    content = json.loads(job.get("content_json") or "{}")
    description = str(
        content.get("facebook_description") or content.get("caption") or ""
    ).strip()
    if not description:
        raise WorkflowError("Facebook description is missing from the review package.")

    from .publish_service import publish_facebook_reel

    try:
        result = publish_facebook_reel(
            settings,
            video_path,
            description=description,
            title=str(content.get("title") or ""),
        )
    except Exception as exc:
        upsert_platform_post(
            settings.database_path,
            job_id,
            "facebook_reels",
            "UPLOAD_NEEDS_RECONCILIATION",
            last_error=str(exc)[:1000],
        )
        raise WorkflowError(
            "Facebook upload outcome may require reconciliation. Check the Page "
            "before retrying. Details: " + str(exc)
        ) from exc

    status = result.get("status", "SUBMITTED_UNCONFIRMED")
    upsert_platform_post(
        settings.database_path,
        job_id,
        "facebook_reels",
        status,
        external_id=str(result.get("video_id") or "") or None,
        permalink=str(result.get("permalink") or "") or None,
    )
    return result
