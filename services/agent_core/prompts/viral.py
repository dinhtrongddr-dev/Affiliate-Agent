"""Video viral: an original creator story worth watching on its own; products only if they fit."""

BRANCH = "viral_creator"

INTENT = ("Sáng tạo câu chuyện creator gốc từ tín hiệu trend: premise, hook, diễn biến, payoff có giá trị "
          "xem độc lập. Không sao chép video người khác. Sản phẩm affiliate chỉ gợi ý sau câu chuyện khi "
          "thực sự liên quan; không ép bán hàng hoặc CTA mua hàng.")


def schema_extra(schema: dict) -> None:
    schema["characters"][0]["role"] = "creator|friend|mother|father|..."


def rules(limit: int, multi_voice: bool) -> str:
    voices = ("Định dạng kể chuyện có thoại nhân vật: mỗi câu đúng speaker là id nhân vật; người kể (narrator) "
              "chỉ khi thật cần." if multi_voice else
              "MỘT người dẫn duy nhất (speaker \"narrator\", hoặc id creator nếu họ xuất hiện), không đổi người "
              "đọc giữa chừng.")
    return f"""VIDEO VIRAL:
- Cấu trúc: hook đặt câu hỏi/lời hứa -> bối cảnh -> diễn biến có câu nối -> payoff TRẢ LỜI đúng hook ->
  câu chốt. Câu chuyện tự đứng được khi không có sản phẩm.
- Giọng: {voices}
- "talking": true CHỈ khi nhân vật nói thẳng vào camera; tối đa 2 cảnh talking (hook hoặc câu then chốt).
  Các cảnh khác là hành động/phản ứng/tay/góc nhìn thứ nhất, lời đặt trên hình.
- Chủ đề là vật, cây, con vật, món ăn, quy trình hay kiến thức: cảnh là CHÍNH THỨ ĐÓ (toàn -> cận ->
  macro -> mặt cắt/sơ đồ động khi giải thích cơ chế), "cast": []; người dẫn chỉ ở hook/kết.
- style_profile: pro_social (mặc định), documentary (kiến thức/thiên nhiên), ugc_phone chỉ khi brief yêu cầu.
- Kết bằng payoff trọn vẹn, "cta" ĐỂ RỖNG (không kêu gọi mua hàng).
- affiliate_keywords: tối đa 8 cụm từ vật dụng thật sự xuất hiện trong câu chuyện, không có thì []."""


def formula_rule() -> str:
    return ("Brief có \"formula\" (công thức viral rút từ trend Trung Quốc): dùng CƠ CHẾ hấp dẫn đó cho câu "
            "chuyện/nội dung MỚI của Việt Nam. KHÔNG dịch lại tiêu đề, câu chữ, cảnh hay nhân vật của video gốc.")


def fix_rules() -> str:
    return """Kiểm riêng viral: câu chuyện gốc xem được độc lập; tối đa 2 cảnh talking; một người dẫn trừ khi là
định dạng kể chuyện có thoại; payoff trả lời đúng hook; cta để rỗng. Tuyệt đối không trừ điểm chỉ vì
không có CTA bán hàng; không ép gắn sản phẩm."""


def issues(plan: dict) -> list[str]:
    out = []
    if str(plan.get("cta") or "").strip() and any(w in str(plan["cta"]).lower()
                                                  for w in ("mua", "giỏ hàng", "link", "đặt hàng")):
        out.append("Viral: không kết bằng CTA bán hàng; để cta rỗng, kết bằng payoff")
    return out
