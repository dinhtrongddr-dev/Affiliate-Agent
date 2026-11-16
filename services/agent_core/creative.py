"""AI creative director: viral short-video scripts, self-review and timelines 2.0."""
import json
from typing import Any

from .creation_branches import DURATION_TARGETS, normalise_characters, reference_id, validate_branch
from .prompts import (HOOK_FORMULAS, INTENTS as BRANCH_INTENTS, WORDS_PER_SECOND,  # noqa: F401
                      branch_issues, build_fix_prompt, build_plan_prompt, target_words)
from .prompts.common import clips_block as _clips_block, product_facts as _product_facts  # noqa: F401
from .prompts.common import prompt_brief as _prompt_brief  # noqa: F401
from .prompts.common import MISSIONS
from .prompts.series import DIALOGUE_RULES  # noqa: F401

RULES = """
Quy tắc sáng tạo & sản xuất nội dung 2.0 (bắt buộc):
- Giây 0-3 (Hook Intelligence): Khung hình mở đầu phải có hành động thị giác ngay lập tức. Câu thoại cảnh 1 chính là hook nói ra. Text trên màn hình <= 7 từ, ngắn gọn, hiển thị rõ ràng, không che chủ thể. Tuyệt đối không chào hỏi dài dòng ("Xin chào các bạn...", "Hôm nay mình...").
- Storytelling & Cấu trúc có chủ đích:
  + Hook (0-3s): Thu hút dừng lướt.
  + Setup (3-8s): Bối cảnh & nhân vật thân thuộc.
  + Tension / Mâu thuẫn (8-16s): Vấn đề, câu hỏi gợi mở, hoặc sự tò mò tăng dần.
  + Build-up / Cao trào (16-24s): Tình tiết đẩy lên đỉnh điểm.
  + Payoff / Cảm xúc (24-32s): Lời giải bất ngờ, thỏa mãn hoặc chạm đáy cảm xúc người xem.
  + Outro / Kết thúc: Trọn vẹn câu chuyện, dư âm sâu lắng hoặc nụ cười ấm áp.
- Thoại tự nhiên: Lời thoại đời thường như người thật nói chuyện hàng ngày. Loại bỏ câu sáo rỗng, câu rào đón ("theo dữ liệu hiện có", "được ghi nhận", "giá tham khảo").
- CTA & Content Mode:
  + Chế độ viral (Viral Content): Ưu tiên cảm xúc, giải trí, tò mò, chia sẻ. CTA ưu tiên tương tác, bình luận, theo dõi; hoặc ĐỂ RỖNG NẾU LÀ CÂU CHUYỆN CẢM XÚC để giữ trọn vẹn cảm xúc.
  + Chế độ value (Value Content): Ưu tiên kiến thức, mẹo hay. CTA lưu lại, chia sẻ, thảo luận.
  + Chế độ affiliate (Affiliate Content): Giải quyết vấn đề của người xem, sản phẩm xuất hiện tự nhiên, CTA rõ ràng.
- Quy tắc khóa đồng nhất nhân vật và nối cảnh liền mạch (Continuity Lock):
  + Khóa cố định nhận diện nhân vật (Character Bible): Nếu có nhân vật xuất hiện, tên và ngoại hình phải giữ nguyên 100% từ cảnh 1 đến cảnh cuối. TUYỆT ĐỐI CẤM viết tắt "trang phục như cũ" hay "như trước"; trong mọi prompt cảnh đều phải lặp lại mỏ neo ngoại hình (độ tuổi, kiểu tóc, màu áo, màu quần).
  + Nối tiếp chuyển động mượt mà: Hành động và tư thế kết thúc của cảnh N phải nối tiếp tự nhiên sang mở đầu cảnh N+1 để các mối ghép không bị nhảy giật.
  + Hậu cảnh sạch không chữ (Clean background): Tuyệt đối không sinh chữ rác, áp phích chữ hay bảng hiệu lộn xộn ở phông nền.
- KHÔNG bịa giá, thông số, công dụng nếu không có trong dữ liệu.
- Không chèn URL vào caption (hệ thống tự thêm link + thông báo tiếp thị liên kết).
- Hashtag tiếng Việt không dấu cách, 3-5 cái đúng chủ đề; không hashtag tiếng Anh, không tên hệ thống.
""".strip()

PLAN_SCHEMA = """
{
  "title": "tiêu đề ngắn thu hút",
  "hook": "chữ hook hiện trên màn hình 0-3s, <= 8 từ",
  "content_mode": "viral|value|affiliate",
  "music_mood": "upbeat|chill|emotional|funny|epic",
  "hook_options": [
    {"type": "curiosity|surprise|empathy", "text_hook": "chữ trên màn hình <= 7 từ", "voice_hook": "câu thoại mở đầu 0-3s", "visual_action": "hành động thị giác ngay frame 0"}
  ],
  "scenes": [
    {SCENE_FIELDS
     "duration": 2.5,
     "voiceover": "câu thoại tiếng Việt tự nhiên",
     "text": "chữ trên màn hình <= 6 từ hoặc chuỗi rỗng",
     "motion": "zoom_in|zoom_out|pan_left|pan_right"}
  ],
  "cta": "câu kêu gọi hành động hoặc để rỗng nếu là câu chuyện cảm xúc",
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


def review_prompt(plan: dict, product: dict, min_score: float) -> str:
    return f"""
Bạn là biên tập viên khó tính chuyên video viral ngắn (Reels, Shopee Video, TikTok). Chấm kịch bản thang 0-10:
- Hook 0-3s (trọng số cao nhất): Có chữ gọn trên màn hình? Thoại ngắn gọn? Có hành động thị giác ngay giây 0?
- Cấu trúc & Storytelling: Có thắt nút, cao trào và payoff thỏa mãn không? Lời thoại có tự nhiên như người thật giao tiếp không?
- Cảm xúc & Ending: Ending có trọn vẹn không? Có bị cắt câu hoặc ép CTA quảng cáo vô duyên không?
- Trung thực: Không bịa giá, công dụng so với dữ liệu sản phẩm.

Sản phẩm: {json.dumps(_product_facts(product), ensure_ascii=False)}
Kịch bản: {json.dumps(plan, ensure_ascii=False)}

Nếu điểm < {min_score}, BẮT BUỘC viết lại toàn bộ kịch bản tốt hơn trong "revised" (hook mạnh hơn, bỏ câu rào đón, nhịp nhanh hơn, kết trọn vẹn). Nếu đạt, "revised" = null.
Trả về DUY NHẤT JSON: {{"score": 8.5, "issues": ["..."], "revised": null}}
""".strip()


def branch_plan_prompt(brief: dict, product: dict, can_generate: bool, has_stock: bool,
                       clips_text: str = "", first_clip_image: int = 1, gen_limit: int = 3,
                       recent_hooks: list[str] | None = None, learning: str = "") -> str:
    """Storyboard prompt of the job's own branch (prompts/review|viral|series.py)."""
    return build_plan_prompt(brief, product, can_generate, has_stock, clips_text, first_clip_image,
                             gen_limit, recent_hooks, learning)


def branch_review_prompt(plan: dict, product: dict, min_score: float, brief: dict) -> str:
    """Editor/fix prompt of the job's own branch."""
    return build_fix_prompt(plan, product, min_score, brief)


def normalise_plan(plan: dict, mode: str, segments: list[dict] | None = None,
                   n_clips: int = 0) -> dict:
    """Validate/clean AI output so rendering never trusts raw model JSON."""
    if not isinstance(plan, dict):
        raise ValueError("Plan must be a JSON object")
    explicit = bool(plan.get("creation_branch"))
    if explicit:
        validate_branch(plan["creation_branch"])
        if not isinstance(plan.get("scenes"), list) or not plan["scenes"]:
            raise ValueError("Storyboard scenes must be a nonempty list")
    if (plan.get("creation_branch") and isinstance(plan.get("characters"), list)
            and len(plan["characters"]) > 6):
        raise ValueError("Storyboard exceeds 6 characters")
    seg_by_index = {s["index"]: s for s in segments or []}
    scenes = []
    for raw in plan.get("scenes") or []:
        if not isinstance(raw, dict):
            if explicit:
                raise ValueError("Every storyboard scene must be an object")
            continue
        if explicit:
            if "reference_ids" in raw:
                refs = raw["reference_ids"]
                if (not isinstance(refs, list) or len(refs) > 6
                        or any(not isinstance(v, str) or not reference_id(v) for v in refs)
                        or len(set(refs)) != len(refs)):
                    raise ValueError("Storyboard reference IDs must be valid unique IDs (maximum 6)")
            if "shot" in raw and not isinstance(raw["shot"], dict):
                raise ValueError("Storyboard shot must be an object")
            if mode != "base":
                clip = _int(raw.get("clip"))
                valid_clip = clip is not None and 0 <= clip < n_clips
                if not valid_clip and raw.get("visual") not in {"product_image", "card", "stock", "generate",
                                                                 "reuse"}:
                    raise ValueError("Storyboard scene must have a valid visual source")
        branch_generated = (bool(plan.get("creation_branch")) and mode != "base"
                            and raw.get("visual") == "generate")
        scene: dict[str, Any] = {
            # AI clips come back ~10 s long; the edit keeps the best 2-6 s of each.
            "duration": _num(raw.get("duration"), 4.0 if branch_generated else 2.5,
                             2.0 if branch_generated else 1.2,
                             8.0 if raw.get("visual") == "generate" and mode != "base" else 6.0),
            "voiceover": _text(raw.get("voiceover"), 160),
            "text": "" if explicit else _text(raw.get("text"), 48),
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
        if explicit:
            _storyboard_v2_fields(scene, raw)
            reuse = _int(raw.get("reuse_of"))
            if reuse is not None and mode != "base":
                scene["reuse_of"] = reuse   # validated against the final scene list below
        if isinstance(raw.get("shot"), dict):
            scene["shot"] = {key: _text(raw["shot"].get(key), 600) for key in
                             ("action", "setting", "camera", "lighting", "emotion", "continuity",
                              "dialogue", "sound", "transition") if key in raw["shot"]}
        if isinstance(raw.get("reference_ids"), list):
            scene["reference_ids"] = list(dict.fromkeys(
                ref for ref in (reference_id(v) for v in raw["reference_ids"][:6]) if ref))
        scenes.append(scene)
    if not scenes:
        raise ValueError("Plan contains no usable scenes")
    if plan.get("creation_branch") and len(scenes) > 10:
        raise ValueError("Storyboard exceeds 10 scenes; no scenes were silently removed")
    scenes = scenes[:10]
    for i, scene in enumerate(scenes):   # "reuse" = another cut of an earlier generated clip
        ref = scene.pop("reuse_of", None)
        if ref is not None and 0 <= ref < i and scenes[ref].get("visual") == "generate":
            scene["visual"], scene["reuse_of"] = "reuse", ref
    if explicit and not plan.get("dialogue"):  # max two talking shots, the rest voice-over
        talking = [s for s in scenes if s.get("talking")]
        for extra in talking[2:]:
            extra["talking"] = False
    result = {
        "title": _text(plan.get("title"), 120),
        "hook": short_hook(plan.get("hook")) if explicit else _text(plan.get("hook"), 70),
        "music_mood": _text(plan.get("music_mood"), 60) or "upbeat",
        "scenes": scenes,
        "cta": _text(plan.get("cta"), 100),
        "caption": _text(plan.get("caption"), 1000),
        "hashtags": plan.get("hashtags") if isinstance(plan.get("hashtags"), list) else [],
        "facebook_description": _text(plan.get("facebook_description"), 1000),
        "cover_ratio": _num(plan.get("cover_ratio"), 0.1, 0.0, 1.0),
        "character": _text(plan.get("character"), 500),
        "content_mode": str(plan.get("content_mode") or ("affiliate" if plan.get("creation_branch") == "product_review" else "viral")).strip().lower(),
    }
    if "hook_options" in plan and isinstance(plan["hook_options"], list):
        result["hook_options"] = [
            {
                "type": _text(h.get("type"), 40),
                "text_hook": _text(h.get("text_hook"), 60),
                "voice_hook": _text(h.get("voice_hook"), 120),
                "visual_action": _text(h.get("visual_action"), 200),
            }
            for h in plan["hook_options"][:5] if isinstance(h, dict)
        ]
    if "creation_branch" in plan:
        result["creation_branch"] = validate_branch(plan["creation_branch"])
    if "storyboard_version" in plan:
        if plan["storyboard_version"] not in (1, 2) or isinstance(plan["storyboard_version"], bool):
            raise ValueError("Unsupported storyboard version")
        result["storyboard_version"] = plan["storyboard_version"]
    if explicit and isinstance(plan.get("hook_variants"), list):
        variants = [short_hook(h) for h in plan["hook_variants"][:3] if str(h or "").strip()]
        result["hook_variants"] = [h for h in dict.fromkeys(variants) if h and h != result["hook"]][:2]
    for key, limit in (("style_profile", 30), ("audience_gender", 10)):
        if plan.get(key):
            result[key] = _text(plan[key], limit).lower()
    if plan.get("duration_target_seconds") is not None:
        result["duration_target_seconds"] = _num(plan["duration_target_seconds"], 25, 8, 90)
    if isinstance(plan.get("voice_cast"), dict):
        result["voice_cast"] = plan["voice_cast"]
    for key, limit in (("takeaway", 200), ("script", 2500)):
        if plan.get(key):
            result[key] = _text(plan[key], limit)
    if isinstance(plan.get("viewer_test"), dict):
        result["viewer_test"] = plan["viewer_test"]
    if isinstance(plan.get("facts"), list):
        result["facts"] = [{"claim": _text(f.get("claim"), 200), "confidence": _num(f.get("confidence"), 50, 0, 100)}
                           for f in plan["facts"][:12] if isinstance(f, dict) and f.get("claim")]
    for key in ("format", "niche", "goal"):
        if plan.get(key):
            result[key] = _text(plan[key], 40)
    if plan.get("dialogue"):
        result["dialogue"] = True
    if isinstance(plan.get("duration_range"), (list, tuple)) and len(plan["duration_range"]) == 2:
        result["duration_range"] = [float(plan["duration_range"][0]), float(plan["duration_range"][1])]
    if isinstance(plan.get("source_texts"), list):
        result["source_texts"] = [_text(t, 300) for t in plan["source_texts"][:4]]
    review = plan.get("self_review") if isinstance(plan.get("self_review"), dict) else {}
    if review.get("score") is not None:
        result["self_review"] = {"score": _num(review.get("score"), 7.0, 0.0, 10.0),
                                 "issues": [_text(i, 200) for i in (review.get("issues") or [])[:6]]}
    if "style" in plan:
        result["style"] = _text(plan.get("style"), 1500)
    if "characters" in plan:
        result["characters"] = normalise_characters(plan["characters"])
    truth = plan.get("product_truth")
    if isinstance(truth, dict):
        result["product_truth"] = {
            "what": _text(truth.get("what"), 200), "for_whom": _text(truth.get("for_whom"), 200),
            "features": [{"feature": _text(f.get("feature"), 160), "scene": _int(f.get("scene")),
                          "proof": _text(f.get("proof"), 300)}
                         for f in (truth.get("features") or [])[:4] if isinstance(f, dict) and f.get("feature")]}
    if "affiliate_keywords" in plan:
        result["affiliate_keywords"] = (list(dict.fromkeys(
            text for text in (_text(v, 100) for v in plan["affiliate_keywords"][:8]) if text))
            if isinstance(plan["affiliate_keywords"], list) else [])
    return result


def fix_visual_sources(plan: dict, has_stock: bool, can_generate: bool, has_image: bool) -> int:
    """The model sometimes picks a source this machine does not have (stock without Pexels).
    Re-map instead of failing the job later. Returns how many scenes changed."""
    changed = 0
    for scene in plan.get("scenes") or []:
        visual = scene.get("visual")
        want = visual
        if visual == "stock" and not has_stock:
            want = "generate" if can_generate else ("product_image" if has_image else "card")
        elif visual in {"product_image", "card"} and not has_image:
            want = "generate" if can_generate else visual
        if want != visual:
            scene["visual"] = want
            if want == "generate" and not (scene.get("shot") or {}).get("action"):
                scene.setdefault("shot", {})["action"] = scene.get("stock_query") or scene.get("voiceover") or ""
            changed += 1
    return changed


def storyboard_issues(plan: dict) -> list[str]:
    """Hard rules checked in code; only when one fails is the AI asked for a fix."""
    from . import formats
    issues = []
    branch = plan.get("creation_branch")
    if branch in DURATION_TARGETS:
        low, high = formats.duration_range(plan)
        # the voice decides the length now: estimate it from the words (silent beats add theirs)
        silent = sum(float(sc.get("duration") or 2) for sc in plan.get("scenes") or [] if not sc.get("lines"))
        total = len(spoken_script(plan).split()) / WORDS_PER_SECOND + silent
        if total < low * 0.9 or total > high * 1.25:
            issues.append(f"Thời lượng ước theo lời {total:.0f}s ngoài mục tiêu {low:.0f}-{high:.0f}s")
    hits = meta_word_lines(plan)
    if hits:
        issues.append("Câu nghe như báo cáo/rào đón (tối đa 1 câu rào đón): " + json.dumps(hits[:6], ensure_ascii=False))
    if formats.needs_facts(plan):
        if not plan.get("facts"):
            issues.append("Video kiến thức: thiếu danh sách facts (khẳng định + độ chắc chắn)")
        weak = [f["claim"] for f in plan.get("facts") or [] if f.get("confidence", 0) < 70]
        if weak:
            issues.append("Khẳng định chưa chắc (< 70): bỏ hoặc nói mềm hơn: " + json.dumps(weak[:5], ensure_ascii=False))
    issues += coherence_issues(plan)
    issues += mission_issues(plan)
    issues += branch_issues(plan)
    copied = originality_issues(plan)
    if copied:
        issues.append("Giống nội dung trend gốc, cần viết mới: " + json.dumps(copied[:4], ensure_ascii=False))
    return issues


CONNECTORS = ("vì", "nên", "nhưng", "hoá ra", "hóa ra", "thế là", "rồi", "bởi", "để", "khi", "nhờ",
              "thì", "vậy", "sau đó", "cuối cùng", "mà")


def mission_issues(plan: dict) -> list[str]:
    """UGC rule: every beat has a visual mission; most beats without one means filler shots."""
    scenes = plan.get("scenes") or []
    if not plan.get("creation_branch") or len(scenes) < 3:
        return []
    missing = [i for i, sc in enumerate(scenes) if not sc.get("mission")]
    if len(missing) > len(scenes) // 2:
        return [f"Nhịp thiếu nhiệm vụ hình ảnh (mission): {missing[:6]}; nhịp không có nhiệm vụ là cảnh độn"]
    return []


def spoken_script(plan: dict) -> str:
    return " ".join(ln.get("text") or "" for sc in plan.get("scenes") or [] for ln in sc.get("lines") or [])


def coherence_issues(plan: dict) -> list[str]:
    """A story a stranger can follow: one takeaway, enough words for the length, joined ideas,
    no strings of tiny fragments."""
    if not plan.get("creation_branch"):
        return []
    from . import formats
    issues = []
    if not plan.get("takeaway"):
        issues.append("Thiếu takeaway: video phải để lại MỘT ý rõ ràng")
    text = spoken_script(plan)
    words = len(text.split())
    low, high = formats.duration_range(plan)
    rate = WORDS_PER_SECOND
    if words and words < low * rate * 0.9:
        least, aim, most = target_words(low, high, rate)
        issues.append(f"Lời quá ít ({words} từ) cho {low:.0f}-{high:.0f}s: video sẽ hụt thời lượng; "
                      f"cần {least}-{most} từ liền mạch (nhắm {aim}), thêm ý/chi tiết thật, không độn chữ")
    if words > high * rate * 1.2:
        issues.append(f"Lời quá dài ({words} từ) cho {high:.0f}s: rút gọn, giữ 3-4 ý")
    lines = [ln.get("text") or "" for sc in plan.get("scenes") or [] for ln in sc.get("lines") or []]
    tiny = [ln for ln in lines if 0 < len(ln.split()) < 5]
    if lines and len(tiny) > max(1, len(lines) // 3):
        issues.append("Quá nhiều câu cụt rời rạc: gộp thành câu đầy đủ có câu nối: " + json.dumps(tiny[:4], ensure_ascii=False))
    low_text = f" {text.lower()} "
    joins = sum(low_text.count(f" {c} ") for c in CONNECTORS)
    if words >= 30 and joins < 2:
        issues.append("Các câu chưa nối ý (thiếu 'vì/nên/nhưng/hoá ra/thế là...'): người xem phải tự liên kết")
    test = plan.get("viewer_test") or {}
    if test and (test.get("hook_answered") is False or test.get("matches_takeaway") is False):
        issues.append("Người xem lạ hiểu sai/không thấy câu trả lời: " + str(test.get("summary") or "")[:200])
    if test.get("unclear_lines"):
        issues.append("Câu thiếu ngữ cảnh với người xem lạ: " + json.dumps(test["unclear_lines"][:4], ensure_ascii=False))
    return issues


def viewer_test_prompt(plan: dict) -> str:
    """Cold viewer: only the words, no pictures, no brief."""
    return f"""Bạn là người lướt Facebook Reels, lần đầu nghe đoạn lời dưới đây (không thấy hình, không biết gì trước).
Hook trên màn hình: "{plan.get('hook') or ''}"
Lời: "{spoken_script(plan)}"
Trả lời thật lòng:
- summary: video nói gì, trong MỘT câu.
- hook_answered: cuối video có trả lời đúng điều hook gợi tò mò không (true/false).
- matches_takeaway: summary của bạn có cùng ý với: "{plan.get('takeaway') or ''}" (true/false).
- unclear_lines: các câu bạn không hiểu vì thiếu ngữ cảnh hoặc nhảy ý (trích nguyên văn, có thể rỗng).
- flow: 0-10 mức liền mạch, dễ theo dõi.
Trả DUY NHẤT JSON: {{"summary":"","hook_answered":true,"matches_takeaway":true,"unclear_lines":[],"flow":8}}"""


def originality_issues(plan: dict, threshold: int = 85) -> list[str]:
    """Hook/lines that are near-copies of the source trend's title or summary (translated)."""
    from rapidfuzz import fuzz
    sources = [t for t in plan.get("source_texts") or [] if t and len(t) >= 12]
    if not sources:
        return []
    ours = [plan.get("hook") or "", plan.get("title") or ""] + [
        ln.get("text") or "" for s in plan.get("scenes") or [] for ln in s.get("lines") or []]
    return [o for o in ours if len(o) >= 12 and any(
        fuzz.token_set_ratio(o.lower(), src.lower()) >= threshold for src in sources)]


def short_hook(value, max_words: int = 8) -> str:
    """On-screen hook stays readable in 2 s: the first clause, or the first words."""
    text = _text(value, 70)
    words = text.split()
    if len(words) <= max_words:
        return text
    import re as _re
    first = _re.split(r"[,;:—–\-]\s", text)[0].strip()
    if 3 <= len(first.split()) <= max_words:
        return first.rstrip(".!?…")
    return " ".join(words[:max_words]).rstrip(",.;:") + "…"


# System words that must never be spoken in a video (they make it sound like a report).
META_WORDS = ("dữ liệu", "listing", "brief", "ghi nhận", "hiển thị", "thông số đối chiếu",
              "theo thông tin", "trường dữ liệu")
# Hedges are fine once (honesty), but a video made of them sells nothing.
HEDGE_WORDS = ("chưa xác minh", "chưa xác nhận", "chưa kết luận", "trang bán", "ảnh từ trang",
               "chưa có ảnh", "chưa có hình", "chưa thể")


def meta_word_lines(plan: dict) -> list[str]:
    """Spoken lines that sound like a report: system words, or more than one hedge."""
    hits = []
    for scene in plan.get("scenes") or []:
        for line in scene.get("lines") or []:
            low = str(line.get("text") or "").lower()
            if any(w in low for w in META_WORDS):
                hits.append(line["text"])
    hedges = [line["text"] for scene in plan.get("scenes") or [] for line in scene.get("lines") or []
              if any(w in str(line.get("text") or "").lower() for w in HEDGE_WORDS)]
    if len(hedges) > 1:
        hits.extend(hedges)
    for key in ("hook", "caption", "title"):
        if any(w in str(plan.get(key) or "").lower() for w in META_WORDS):
            hits.append(str(plan[key]))
    return hits


def _storyboard_v2_fields(scene: dict, raw: dict) -> None:
    """Speakers per line, cast/group per scene and talking/product flags (storyboard v2)."""
    lines = []
    for line in raw.get("lines") or []:
        if isinstance(line, dict) and str(line.get("text") or "").strip():
            speaker = "narrator" if str(line.get("speaker") or "narrator") == "narrator" else                 reference_id(line.get("speaker")) or "narrator"
            lines.append({"speaker": speaker, "text": _text(line.get("text"), 140)})
    if not lines and scene.get("voiceover"):
        lines = [{"speaker": "narrator", "text": scene["voiceover"]}]
    scene["lines"] = lines[:4]
    scene["voiceover"] = _text(" ".join(ln["text"] for ln in scene["lines"]), 400)
    scene["cast"] = list(dict.fromkeys(
        c for c in (reference_id(v) for v in (raw.get("cast") or [])[:3]) if c))
    scene["group"] = _text(raw.get("group"), 40) or ""
    scene["talking"] = bool(raw.get("talking")) and any(
        ln["speaker"] in scene["cast"] for ln in scene["lines"])
    scene["product_visible"] = bool(raw.get("product_visible"))
    scene["continuous"] = bool(raw.get("continuous"))
    scene["pause"] = bool(raw.get("pause"))
    scene["hands_only"] = bool(raw.get("hands_only"))
    mission = str(raw.get("mission") or "").strip().lower()
    if mission in MISSIONS:
        scene["mission"] = mission


def fallback_base_plan(analysis: dict, segments: list[dict]) -> dict:
    """Deterministic edit used when the AI plan is unusable."""
    hook = analysis.get("video_hook") or analysis.get("topic") or ""
    picks = segments[: min(6, len(segments))]
    scenes = [{"segment": s["index"], "start_offset": 0, "duration": min(3.0, s["duration"]),
               "voiceover": hook if i == 0 else "", "text": ""} for i, s in enumerate(picks)]
    return {"hook": _smart_truncate(hook, 60), "scenes": scenes, "cta": "", "caption": hook,
            "hashtags": [], "music_mood": "upbeat"}


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _smart_truncate(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    truncated = cleaned[:limit]
    last_space = truncated.rfind(" ")
    if last_space > limit // 2:
        truncated = truncated[:last_space].rstrip(" ,.-;:!?")
    return truncated


def _text(value, limit: int) -> str:
    return _smart_truncate(value, limit)


def _num(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(low, min(high, number)), 3)
