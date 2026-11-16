"""What every branch's storyboard prompt shares: field contract, writing method, shot language.

Nothing here may name one branch's rules (review honesty, series dialogue, viral formulas...):
those live in review.py / viral.py / series.py so the three prompts never mix.
"""
import json

from ..creation_branches import validate_branch

# Rendered speed incl. the gaps between lines (VieNeu nano, Nov 2026): 79 words -> 22.0 s,
# 66 -> 18.2 s, 97 -> 26.5 s = ~3.6 words/s. 3.5 keeps a little margin so videos reach the target.
WORDS_PER_SECOND = 3.5

HOOK_FORMULAS = """
1. Khoảng trống tò mò: câu hỏi hoặc mâu thuẫn buộc người xem xem tiếp để có lời giải.
2. Phá vỡ kỳ vọng: hành động bất ngờ ngay khung hình đầu; tương phản giữa vẻ ngoài và bản chất.
3. Chạm nỗi đau / đồng cảm: tình huống đời thường ai cũng từng gặp.
4. POV / trực diện: đặt người xem vào giữa sự việc, không chào hỏi.
5. Trước - sau: khác biệt rõ ràng khi có giải pháp.
Tự nghĩ câu hook mới cho đúng nội dung; KHÔNG mở đầu bằng "Ủa", "Khoan", "Xin chào", "Hôm nay mình".
""".strip()

# Visual tells of cheap AI video (UGC research 2026-11-15). Every branch avoids them.
AI_SLOP = ("người cười đứng yên nhìn camera không làm gì", "tay/ngón biến dạng cầm đồ vật",
           "chữ/bảng hiệu/nhãn giả trên nền", "máy bay lượn vô cớ (drone) không theo hành động",
           "nhiều hành động dồn trong một cảnh", "đồ vật tự bay/tự biến hình", "ánh sáng lung linh vô nghĩa",
           "đám đông mặt giống nhau")

MISSIONS = {
    "proof": "chứng minh là thật (cận vật, kết cấu, kết quả thấy được)",
    "mechanism": "cho thấy nó hoạt động thế nào",
    "consequence": "vì sao điều đó quan trọng với người xem",
    "action": "người xem nên làm gì tiếp",
    "story": "đẩy câu chuyện đi tiếp (tình huống, phản ứng, cao trào, payoff)",
}


def target_words(low: float, high: float, rate: float = 0.0) -> tuple[int, int, int]:
    """(min, aim, max) spoken words for a video of low-high seconds."""
    rate = rate or WORDS_PER_SECOND
    mid = (low + high) / 2
    return int(low * rate), int(mid * rate), int(high * rate)


def prompt_brief(value):
    """Trusted local reference assets are transported as attachments, never prompt paths."""
    if isinstance(value, dict):
        return {key: prompt_brief(item) for key, item in value.items()
                if key not in {"reference_assets", "reference_manifest", "path", "local_path"}}
    if isinstance(value, list):
        return [prompt_brief(item) for item in value]
    return value


def product_facts(product: dict) -> dict:
    keys = ("name", "price_min", "price_max", "commission_rate", "sales", "rating_star", "shop_name")
    return {k: product.get(k) for k in keys if product.get(k) not in (None, "")}


def clips_block(clips_text: str, first_image: int) -> str:
    if not clips_text:
        return ""
    return f"""
Clip người dùng tự thêm (ảnh đại diện đính kèm từ ảnh thứ {first_image}). Đây là chất liệu
ĐẮT GIÁ NHẤT (quay thật / AI chất lượng cao): ưu tiên dùng, đặc biệt cho cảnh hook và cảnh
khoe sản phẩm. Dùng bằng trường "clip": số thứ tự clip (start_offset = giây bắt đầu trong clip):
{clips_text}
"""


def base_schema(branch: str, low: float, high: float, has_stock: bool) -> dict:
    validate_branch(branch)
    schema = {
        "storyboard_version": 2, "creation_branch": branch, "title": "",
        "hook": "chữ hook 0-2 giây, <= 7 từ, viết thường có hoa đầu câu, không emoji",
        "hook_variants": ["hook kiểu khác 1 (<= 7 từ)", "hook kiểu khác 2 (<= 7 từ)"],
        "content_mode": "viral",
        "style_profile": "pro_social|documentary|chibi_3d|ugc_phone", "music_mood": "funny|emotional|chill|upbeat",
        "audience_gender": "unisex",
        "duration_target_seconds": int((low + high) / 2),
        "characters": [{"id": "me", "name": "", "role": "", "gender": "female|male", "age": "28",
                        "appearance": "English, cụ thể", "wardrobe": "English, màu sắc cụ thể",
                        "personality": ""}],
        "takeaway": "MỘT câu người xem nhớ sau khi xem",
        "script": "toàn bộ lời viết liền như một đoạn văn nói, đúng thứ tự các nhịp",
        "scenes": [{"visual": "generate", "clip": None, "duration": 4, "group": "seq1_kitchen",
                    "mission": "|".join(MISSIONS),
                    "cast": ["me"], "talking": False, "product_visible": False, "hands_only": False,
                    "lines": [{"speaker": "narrator", "text": "câu tiếng Việt đời thường"}],
                    "shot": {"action": "English, đúng 1 hành động", "setting": "English, bối cảnh Việt Nam cụ thể",
                             "camera": "English: shot size + angle + ONE move, e.g. 'macro close-up, slow push-in'",
                             "lighting": "", "emotion": "", "sound": ""},
                    "reference_ids": [], "pause": False, "reuse_of": None}],
        "cta": "", "caption": "", "hashtags": ["#..."], "facebook_description": "", "cover_ratio": .1,
        "affiliate_keywords": [], "self_review": {"score": 8.0, "issues": []},
    }
    if has_stock:
        schema["scenes"][0]["stock_query"] = "english keywords"
    return schema


def writing_method(words: tuple[int, int, int], rate: float) -> str:
    return f"""CÁCH VIẾT (bắt buộc theo thứ tự):
1) Viết "script" TRƯỚC: lời liền mạch như một người kể cho bạn nghe, {words[0]}-{words[2]} từ,
   NHẮM KHOẢNG {words[1]} TỪ (giọng đọc ~{rate:g} từ/giây; ít lời hơn thì video hụt thời lượng).
   Câu nối ý ("vì", "nên", "nhưng", "hoá ra", "thế là", "rồi"); payoff TRẢ LỜI đúng hook; chỉ giữ 3-4 ý
   phục vụ "takeaway"; không liệt kê sự kiện rời rạc.
2) Chia script thành NHỊP (mỗi phần tử "scenes" là một nhịp): ghép các "lines" lại phải ra đúng script.
   Mỗi nhịp tối đa khoảng 12 từ (~3 giây hình): video giữ người xem bằng nhịp cắt <= 3 giây.
   Thời lượng do app tính theo giọng (trường "duration" chỉ dùng cho nhịp không lời).
   "pause": true khi cần nghỉ lâu hơn cho khoảnh khắc cảm xúc (hiếm).
3) Mỗi nhịp có hình MINH HOẠ ĐÚNG điều đang được nói ("nói gì cho xem nấy")."""


def shot_language(has_image: bool, has_stock: bool, allow_reuse: bool) -> str:
    missions = "; ".join(f"{k} = {v}" for k, v in MISSIONS.items())
    reuse = ("""- MỘT TAKE CHO 2 NHỊP: clip AI dài 10 giây. Nhịp kế tiếp cùng chuỗi có thể đặt "reuse_of": số thứ tự
  (0-based) của nhịp generate ngay trước nó để lấy ĐOẠN TIẾP THEO của cùng take; khi đó shot.action của nhịp
  generate mô tả cả hai hành động theo thứ tự ("first ..., then ..."). Video 30 giây thường 3-5 take."""
             if allow_reuse else "- Không dùng \"reuse_of\".")
    return f"""Hình ảnh:
- Mỗi nhịp có "mission" (nhiệm vụ hình ảnh): {missions}. Nhịp không có nhiệm vụ là cảnh độn: bỏ đi.
- shot.* viết tiếng Anh ngắn gọn. Mỗi cảnh ĐÚNG MỘT hành động và ĐÚNG MỘT chuyển động máy.
  shot.camera ghi cỡ cảnh + góc + chuyển động (wide establishing, medium, close-up, macro, top-down,
  low angle, slow push-in, slide, orbit...). Hai cảnh liền nhau KHÔNG cùng cỡ cảnh.
- Bối cảnh Việt Nam đời thường cụ thể. Không viết "cinematic", "photorealistic", "studio", "slow motion":
  phong cách hình do style_profile quyết định, app tự thêm.
- Tránh dấu hiệu video AI rẻ tiền: {"; ".join(AI_SLOP)}.
- "product_visible": true chỉ khi {"có ảnh sản phẩm để Muse bám theo" if has_image else "KHÔNG có ảnh sản phẩm: luôn false, cảnh sản phẩm phải là product_image/card hoặc tay cầm không thấy nhãn"}.
- Nguồn hình: user_clip nếu có clip; product_image/card chỉ khi có ảnh sản phẩm ({has_image});
  stock chỉ khi có Pexels ({has_stock}); generate khi ngân sách > 0.
- reference_ids: tối đa 6 ID từ manifest được cung cấp; không tạo đường dẫn/URL.
- "characters": mỗi người VÀ con vật chính xuất hiện (con vật: role "dog"/"cat"..., appearance gồm giống,
  màu lông, vết đốm, kích cỡ); gender, age, appearance, wardrobe cụ thể và giữ nguyên mọi cảnh.
  Mỗi nhịp: "cast" = id người có mặt. Trẻ em có age thật (vd "4"); không cho trẻ làm việc nguy hiểm.
- CHUỖI CẢNH: "group" = id chuỗi cảnh (cùng bối cảnh, cùng nhân vật, liên tiếp); video có 2-4 chuỗi.
  Nhịp đầu mỗi chuỗi là cảnh toàn giới thiệu bối cảnh. Trong một chuỗi, hành động nối tiếp liên tục
  (app nối khung cuối clip trước vào đầu clip sau); cùng phía máy quay, cùng giờ trong ngày, cùng thời tiết.
{reuse}"""


def on_screen_text(hook_note: str = "") -> str:
    return f"""Chữ trên màn hình: chỉ "hook" (0-2 giây) và phụ đề do app tạo từ lines; app tự thêm lớp chữ
riêng của nhánh (nếu có). Không tự thêm chữ khác vào hình, không emoji.
"hook_variants": 2 hook KHÁC KIỂU với "hook" (vd tò mò / đồng cảm / bất ngờ), cùng khớp hình mở đầu,
để app dựng 3 bản A/B chỉ khác chữ 2 giây đầu. {hook_note}
Hook: {HOOK_FORMULAS}"""


def output_block(schema: dict) -> str:
    return f"""Hashtag 3-5 cái tiếng Việt đúng chủ đề. Caption không chèn URL.
"self_review": tự chấm 0-10 và nêu vấn đề còn lại.
JSON mẫu là hợp đồng trường (tên/nhân vật lấy từ brief thật, không chép ví dụ):
{json.dumps(schema, ensure_ascii=False)}
Trả DUY NHẤT một JSON object, không markdown."""


def fix_common(min_score: float) -> str:
    return f"""Nếu điểm < {min_score}, viết lại toàn bộ trong revised, giữ nguyên creation_branch/storyboard_version,
characters, reference_ids hợp lệ, giới hạn cảnh generate và đủ các trường như storyboard gốc.
Không tạo đường dẫn/URL hay reference_manifest.
Trả DUY NHẤT JSON {{"score":8.5,"issues":[],"revised":null}}."""
