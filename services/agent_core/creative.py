"""AI creative director: viral short-video scripts, self-review and timelines."""
import json
from typing import Any

HOOK_FORMULAS = """
- Gây tò mò: "Đừng mua X trước khi xem video này", "Cái này làm mình bất ngờ..."
- Nỗi đau trước: nêu vấn đề quen thuộc rồi đưa giải pháp ngay giây 2-3
- Kết quả trước: cho thấy kết quả/cảnh đẹp nhất đầu tiên, giải thích sau
- POV / tình huống đời thường: "POV: bạn vừa..."
- So sánh trước-sau, hoặc "3 lý do..." (đánh số giữ người xem)
""".strip()

RULES = """
Quy tắc nội dung (bắt buộc):
- Giây 0-3 phải có hook mạnh; câu thoại cảnh 1 chính là câu hook nói ra.
- Mỗi cảnh 1 ý, 1.8-3.5 giây, thoại 5-14 từ, văn nói tự nhiên, không đọc như quảng cáo.
- Tổng 15-35 giây, 5-9 cảnh, nhịp nhanh, đổi cảnh liên tục.
- text trên màn hình <= 6 từ, bổ sung ý chứ không lặp nguyên thoại.
- Cảnh cuối là kêu gọi hành động (bấm giỏ hàng / link), ngắn gọn.
- Giọng creator TikTok: tự nhiên, cảm xúc, nói về lợi ích/tình huống người xem gặp hằng ngày.
  TRÁNH câu rào đón kiểu "theo dữ liệu hiện có", "giá tham khảo", "sản phẩm được ghi thông tin";
  nếu không chắc một chi tiết thì đừng nhắc tới nó thay vì rào đón.
- Hook không mở đầu bằng tên + thông số sản phẩm; dùng tò mò, nỗi đau hoặc kết quả.
- KHÔNG bịa giá, thông số, công dụng, chứng nhận, xuất xứ, bảo hành, cảm nhận đã dùng
  nếu không có trong dữ liệu. Không cam kết kết quả, không giật tít sai sự thật.
- Không chèn URL vào caption (hệ thống tự thêm link + thông báo tiếp thị liên kết).
- Hashtag không dấu cách, 5-8 cái, có 1-2 hashtag rộng (#xuhuong #reviewdocongnghe...).
""".strip()

PLAN_SCHEMA = """
{
  "title": "tiêu đề ngắn",
  "hook": "chữ hook hiện trên màn hình 0-3s, <= 9 từ",
  "music_mood": "upbeat|chill|emotional|funny|epic",
  "scenes": [
    {SCENE_FIELDS
     "duration": 2.5,
     "voiceover": "câu thoại tiếng Việt",
     "text": "chữ trên màn hình <= 6 từ hoặc chuỗi rỗng",
     "motion": "zoom_in|zoom_out|pan_left|pan_right"}
  ],
  "cta": "câu kêu gọi hành động hiện cuối video, <= 10 từ",
  "caption": "caption tiếng Việt 2-4 câu, có hook ở câu đầu",
  "hashtags": ["#..."],
  "facebook_description": "mô tả ngắn cho Facebook Reels",
  "cover_ratio": 0.1
}
""".strip()

BASE_SCENE_FIELDS = '"segment": 0, "clip": null, "start_offset": 0.0,'
TREND_SCENE_FIELDS = (
    '"visual": "user_clip|product_image|card|stock|generate", "clip": null, "start_offset": 0.0, '
    '"stock_query": "english keywords for stock footage", '
    '"gen_prompt": "english prompt for a 9:16 AI video clip, product-accurate",'
)


def _clips_block(clips_text: str, first_image: int) -> str:
    if not clips_text:
        return ""
    return f"""
Clip người dùng tự thêm (ảnh đại diện đính kèm từ ảnh thứ {first_image}). Đây là chất liệu
ĐẮT GIÁ NHẤT (quay thật / AI chất lượng cao): ưu tiên dùng, đặc biệt cho cảnh hook và cảnh
khoe sản phẩm. Dùng bằng trường "clip": số thứ tự clip (start_offset = giây bắt đầu trong clip):
{clips_text}
"""


def base_plan_prompt(analysis: dict, product: dict, segments: list[dict], meta: dict,
                     clips_text: str = "") -> str:
    seg_lines = "\n".join(
        f"- segment {s['index']}: bắt đầu {s['start']:.1f}s, dài {s['duration']:.1f}s (ảnh #{s['index']} đính kèm)"
        for s in segments
    )
    return f"""
Bạn là đạo diễn video ngắn viral (TikTok/Reels/Shopee Video) tại Việt Nam.
Dựng lại video gốc thành 1 video affiliate 9:16 hấp dẫn, giữ chân người xem tới cuối.

Phân tích video gốc: {json.dumps(analysis, ensure_ascii=False)}
Sản phẩm (dữ liệu thật từ Shopee): {json.dumps(_product_facts(product), ensure_ascii=False)}
Video gốc dài {meta.get('duration_sec', 0):.1f}s. Các đoạn có thể dùng (ảnh đại diện đính kèm theo thứ tự):
{seg_lines}

Cách dựng:
- Chọn và SẮP XẾP LẠI các segment: cảnh mở đầu là khung hình bắt mắt nhất.
- Có thể dùng lại 1 segment với start_offset khác (giây tính từ đầu segment).
- Bỏ đoạn mờ, rung, trống, không liên quan.
- Mỗi cảnh dùng HOẶC "segment" (video gốc) HOẶC "clip" (clip người dùng), cái còn lại = null.
{_clips_block(clips_text, len(segments) + 1)}
Công thức hook gợi ý:
{HOOK_FORMULAS}

{RULES}

Trả về DUY NHẤT 1 JSON object, không markdown:
{PLAN_SCHEMA.replace('SCENE_FIELDS', BASE_SCENE_FIELDS)}
""".strip()


def trend_plan_prompt(brief: dict, product: dict, can_generate: bool, has_stock: bool,
                      clips_text: str = "", first_clip_image: int = 1) -> str:
    visuals = []
    if clips_text:
        visuals.append('user_clip (clip người dùng thêm, chọn bằng trường "clip")')
    visuals += ["product_image (ảnh sản phẩm thật, có chuyển động zoom/pan)",
               "card (ảnh sản phẩm trên nền màu, hợp cảnh liệt kê/CTA)"]
    if has_stock:
        visuals.append("stock (video stock theo stock_query tiếng Anh: bối cảnh, con người, tình huống)")
    if can_generate:
        visuals.append("generate (AI tạo clip theo gen_prompt tiếng Anh; dùng cho 1-3 cảnh đắt giá nhất)")
    return f"""
Bạn là đạo diễn video ngắn viral (TikTok/Reels/Shopee Video) tại Việt Nam.
Tạo 1 video affiliate 9:16 hoàn toàn mới (không dùng video của người khác) dựa trên trend:

Trend / ý tưởng: {json.dumps(brief, ensure_ascii=False)}
Sản phẩm (dữ liệu thật từ Shopee): {json.dumps(_product_facts(product), ensure_ascii=False)}

Nguồn hình ảnh có sẵn cho mỗi cảnh (trường "visual"):
- {chr(10).join('- ' + v for v in visuals)[2:]}
Không lặp product_image quá 2 cảnh liền nhau; xen kẽ card/stock để đa dạng.
{_clips_block(clips_text, first_clip_image)}
Bắt trend: dùng đúng tinh thần/format của trend (câu nói, tình huống, nhịp), nhưng nội dung là của mình.

Công thức hook gợi ý:
{HOOK_FORMULAS}

{RULES}

Trả về DUY NHẤT 1 JSON object, không markdown:
{PLAN_SCHEMA.replace('SCENE_FIELDS', TREND_SCENE_FIELDS)}
""".strip()


def ideation_prompt(brief: dict) -> str:
    return f"""
Bạn là chuyên gia affiliate Shopee Việt Nam. Từ trend sau, đề xuất sản phẩm phù hợp để bán:
{json.dumps(brief, ensure_ascii=False)}

Trả về DUY NHẤT JSON:
{{
  "topic": "chủ đề video ngắn",
  "angle": "góc tiếp cận bắt trend, 1 câu",
  "product_category": "loại sản phẩm",
  "product_search_keywords": ["2-4 cụm từ tìm kiếm Shopee tiếng Việt ngắn"],
  "visual_summary": "mô tả ngắn video sẽ làm"
}}
Nếu trend không liên quan mua sắm (tin chính trị, tai nạn, tang lễ...), trả product_category rỗng.
""".strip()


def review_prompt(plan: dict, product: dict, min_score: float) -> str:
    return f"""
Bạn là biên tập viên khó tính chuyên video viral. Chấm kịch bản sau thang 0-10 theo:
hook 0-3s (trọng số cao nhất), nhịp & giữ chân, rõ lợi ích, CTA, trung thực (không bịa so với dữ liệu sản phẩm).

Sản phẩm: {json.dumps(_product_facts(product), ensure_ascii=False)}
Kịch bản: {json.dumps(plan, ensure_ascii=False)}

Nếu điểm < {min_score}, BẮT BUỘC viết lại toàn bộ kịch bản tốt hơn trong "revised" (hook mạnh hơn, bỏ câu rào đón, nhịp nhanh hơn) (giữ nguyên cấu trúc JSON,
giữ nguyên các trường segment/clip/visual/stock_query/gen_prompt hợp lệ). Nếu đạt, "revised" = null.
Trả về DUY NHẤT JSON: {{"score": 8.0, "issues": ["..."], "revised": null}}
""".strip()


def _product_facts(product: dict) -> dict:
    keys = ("name", "price_min", "price_max", "commission_rate", "sales", "rating_star", "shop_name")
    return {k: product.get(k) for k in keys if product.get(k) not in (None, "")}


def normalise_plan(plan: dict, mode: str, segments: list[dict] | None = None,
                   n_clips: int = 0) -> dict:
    """Validate/clean AI output so rendering never trusts raw model JSON."""
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a JSON object")
    seg_by_index = {s["index"]: s for s in segments or []}
    scenes = []
    for raw in plan.get("scenes") or []:
        if not isinstance(raw, dict):
            continue
        scene: dict[str, Any] = {
            "duration": _num(raw.get("duration"), 2.5, 1.2, 6.0),
            "voiceover": _text(raw.get("voiceover"), 160),
            "text": _text(raw.get("text"), 48),
            "motion": raw.get("motion") if raw.get("motion") in
            {"zoom_in", "zoom_out", "pan_left", "pan_right"} else None,
        }
        clip = _int(raw.get("clip"))
        if clip is not None and 0 <= clip < n_clips:
            scene.update({"clip": clip, "visual": "user_clip",
                          "start_offset": _num(raw.get("start_offset"), 0.0, 0.0, 600.0)})
        elif mode == "base":
            try:
                index = int(raw.get("segment"))
            except (TypeError, ValueError):
                continue
            if index not in seg_by_index:
                continue
            seg = seg_by_index[index]
            offset = _num(raw.get("start_offset"), 0.0, 0.0, max(0.0, seg["duration"] - 0.5))
            scene.update({"kind": "video", "segment": index, "start": round(seg["start"] + offset, 3)})
        else:
            visual = raw.get("visual") if raw.get("visual") in {
                "product_image", "card", "stock", "generate"} else "product_image"
            scene.update({
                "visual": visual,
                "stock_query": _text(raw.get("stock_query"), 80),
                "gen_prompt": _text(raw.get("gen_prompt"), 600),
            })
        scenes.append(scene)
    if not scenes:
        raise ValueError("Plan contains no usable scenes")
    scenes = scenes[:10]
    return {
        "title": _text(plan.get("title"), 90),
        "hook": _text(plan.get("hook"), 70),
        "music_mood": _text(plan.get("music_mood"), 20) or "upbeat",
        "scenes": scenes,
        "cta": _text(plan.get("cta"), 60),
        "caption": _text(plan.get("caption"), 600),
        "hashtags": plan.get("hashtags") if isinstance(plan.get("hashtags"), list) else [],
        "facebook_description": _text(plan.get("facebook_description"), 600),
        "cover_ratio": _num(plan.get("cover_ratio"), 0.1, 0.0, 1.0),
    }


def fallback_base_plan(analysis: dict, segments: list[dict]) -> dict:
    """Deterministic edit used when the AI plan is unusable."""
    hook = analysis.get("video_hook") or analysis.get("topic") or ""
    picks = segments[: min(6, len(segments))]
    scenes = [{"segment": s["index"], "start_offset": 0, "duration": min(3.0, s["duration"]),
               "voiceover": hook if i == 0 else "", "text": ""} for i, s in enumerate(picks)]
    return {"hook": hook[:60], "scenes": scenes, "cta": "", "caption": hook,
            "hashtags": [], "music_mood": "upbeat"}


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _num(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(low, min(high, number)), 3)
