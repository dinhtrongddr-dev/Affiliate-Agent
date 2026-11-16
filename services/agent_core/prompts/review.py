"""Review sản phẩm: one honest reviewer, product truth first, every feature shown as proof."""
import json

BRANCH = "product_review"

INTENT = ("Review sản phẩm dựa trên dữ liệu và hình thật. Tách bằng chứng sản phẩm khỏi trải nghiệm "
          "chủ kênh cung cấp. Chỉ kể đã dùng khi experience.basis=owner_experience và notes nêu rõ trải "
          "nghiệm đó; ảnh AI minh hoạ không phải bằng chứng đã dùng.")


def schema_extra(schema: dict) -> None:
    schema["content_mode"] = "affiliate"
    schema["audience_gender"] = "female|male|unisex (người dùng sản phẩm)"
    schema["characters"][0]["role"] = "creator"
    schema["product_truth"] = {"what": "sản phẩm là gì (1 câu)", "for_whom": "dành cho ai",
                               "features": [{"feature": "tính năng có trong dữ liệu", "scene": 2,
                                             "proof": "English: shot that proves it"}]}
    schema["cta"] = "câu CTA có lời, mềm, vd 'link mình để ở giỏ hàng nha'"


def rules(limit: int) -> str:
    return """REVIEW SẢN PHẨM:
- TRƯỚC khi viết, điền "product_truth": sản phẩm là gì, cho ai, 2-4 tính năng SẼ NÓI (chỉ lấy từ dữ liệu
  sản phẩm/ghi chú chủ kênh, cấm bịa tính năng), mỗi tính năng ghi "scene" (số thứ tự nhịp, 0-based)
  và "proof" = cảnh quay chứng minh nó. Mỗi tính năng = một nhịp "mission": "proof" hoặc "mechanism".
- Nhịp video (giọng người thật, ngôi thứ nhất, nói một hơi):
  hook <= 1,5 giây (phản ứng thật / nỗi đau / khẳng định táo bạo; cấm "Giới thiệu sản phẩm X")
  -> nỗi phiền trước đây -> "thế là mình thử..." -> cho nó làm việc (2-4 tính năng, mỗi tính năng
  một cảnh bằng chứng) -> kết quả -> CTA có lời 6-8 giây (mission "action"). Không kết bằng khung im lặng.
- MỘT người review duy nhất (nhân vật "creator") xưng "mình", nói như giới thiệu món đồ cho bạn bè.
  Dùng dữ kiện thật (tên, công dụng ghi trên sản phẩm, giá, lượt bán, sao) nhưng nói tự nhiên
  ("hơn 14 nghìn người mua rồi", "chưa tới 100 nghìn"). TUYỆT ĐỐI KHÔNG nói các từ hệ thống:
  "dữ liệu", "listing", "brief", "ghi nhận", "hiển thị", "thông số đối chiếu".
- Trung thực: không nói đã dùng/đã thử trừ khi experience.basis=owner_experience. Khi chưa dùng: tối đa
  MỘT câu ngắn nói thật điều đó (thường ở cuối, vd "mình chưa xài lâu nên ai dùng rồi cmt giúp nha");
  các cảnh còn lại cho thấy món đồ trong tình huống thật và cách dùng theo đúng công dụng ghi trên sản
  phẩm, nói "món này giúp…", "kiểu này tiện ở chỗ…"; không khẳng định độ bền/hiệu quả.
  product_context.missing_info và constraints là GHI CHÚ NỘI BỘ, KHÔNG đọc ra thành lời
  (không nói "chưa xác minh", "chưa xác nhận", "ảnh từ trang bán", "chưa kết luận").
- "talking": true CHỈ khi người review nói thẳng vào camera; tối đa 2 cảnh talking (hook hoặc câu then
  chốt). Các cảnh khác là tay cầm, cận sản phẩm, góc nhìn thứ nhất, giọng đặt trên hình.
- audience_gender theo người dùng sản phẩm (đồ nữ=female, đồ nam=male, đồ chung=unisex).
- style_profile: pro_social (mặc định) hoặc ugc_phone khi brief yêu cầu quay kiểu điện thoại.
- affiliate_keywords: tên/loại sản phẩm đang review (tối đa 8)."""


def fix_rules() -> str:
    return """Kiểm riêng review: product_truth có 2-4 tính năng từ dữ liệu thật, mỗi tính năng có nhịp bằng chứng;
hook <= 1,5 giây không giới thiệu suông; một người review xưng "mình"; tối đa 2 cảnh talking;
không nói đã dùng nếu chưa có trải nghiệm thật; tối đa một câu nói thật là chưa dùng lâu;
không đọc từ hệ thống/ghi chú nội bộ; có CTA có lời ở cuối (giỏ hàng)."""


def issues(plan: dict) -> list[str]:
    out = []
    truth = plan.get("product_truth") if isinstance(plan.get("product_truth"), dict) else {}
    features = [f for f in truth.get("features") or [] if isinstance(f, dict) and f.get("feature")]
    n = len(plan.get("scenes") or [])
    if len(features) < 2:
        out.append("Review: product_truth cần 2-4 tính năng lấy từ dữ liệu thật, mỗi tính năng một cảnh bằng chứng")
    unmapped = [f["feature"] for f in features if not isinstance(f.get("scene"), int) or not 0 <= f["scene"] < n]
    if unmapped:
        out.append("Review: tính năng chưa có nhịp bằng chứng hợp lệ: " + json.dumps(unmapped[:4], ensure_ascii=False))
    if not str(plan.get("cta") or "").strip():
        out.append("Review: thiếu CTA có lời ở cuối (vd 'link ở giỏ hàng')")
    speakers = {ln.get("speaker") for sc in plan.get("scenes") or [] for ln in sc.get("lines") or []}
    if len(speakers - {"narrator"}) > 1 or (len(speakers) > 1 and "narrator" in speakers and len(speakers) > 2):
        out.append("Review: chỉ MỘT người review nói, không đổi giọng giữa chừng")
    return out
