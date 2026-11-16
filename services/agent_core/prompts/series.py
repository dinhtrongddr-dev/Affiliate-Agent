"""Series theo mẫu: the saved template decides cast, look, setting and tone; one episode at a time.

Two voice modes, each with its own block: "dialogue" (characters talk to each other on screen)
and "narration" (one narrator / first person). Nothing here assumes the Suri family.
"""
import json

BRANCH = "template_series"

INTENT = ("Tạo MỘT tập của series theo đúng template snapshot/version: template snapshot là nguồn quyết "
          "định cast, tuổi, phong cách hình, bối cảnh, giọng kể và quy tắc nhất quán. Giữ nguyên ngoại hình "
          "và tính cách nhân vật qua các tập. Không ép gắn sản phẩm; chọn affiliate liên quan sau nội dung.")

DIALOGUE_RULES = """SERIES HỘI THOẠI (mỗi nhân vật có giọng riêng, KHÔNG có người dẫn):
- Toàn bộ lời là THOẠI giữa các nhân vật nói với nhau trên màn hình; KHÔNG dùng speaker "narrator",
  không độc thoại kể chuyện, không lời bình ngoài hình.
- Mỗi nhịp có lời = MỘT cảnh "visual": "generate", "talking": true; người nói phải có trong "cast" của nhịp đó.
  Mỗi nhịp đúng MỘT câu thoại của MỘT người nói, tối đa 12 từ: câu ngắn, khẩu ngữ tự nhiên, có cảm xúc
  (ngạc nhiên, trêu, dỗi, cười), xưng hô đúng quan hệ (bố/mẹ/con, anh/em...). Câu ngắn để nhân vật nói
  đúng từng chữ và khớp môi.
- Câu chuyện đi bằng qua lại hỏi - đáp - phản ứng, có tình huống, cao trào nhỏ và câu chốt hài/ấm áp.
- Nhịp không lời (phản ứng, hành động) để "lines": [] và "talking": false.
- Không giới hạn số cảnh talking; tối đa {limit} cảnh generate: ưu tiên cảnh thoại."""

NARRATION_RULES = """SERIES THUYẾT MINH (một giọng kể duy nhất):
- Toàn bộ lời do MỘT người kể (speaker "narrator", hoặc id nhân vật chính nếu template kể ở ngôi thứ nhất);
  không đổi người đọc giữa chừng.
- "talking": true chỉ khi người kể xuất hiện nói vào camera; tối đa 2 cảnh như vậy.
- Hình minh hoạ đúng điều đang kể; cảnh vật/quy trình/kiến thức thì "cast": []."""


def schema_extra(schema: dict, template: dict) -> None:
    if template.get("style_profile"):
        schema["style_profile"] = template["style_profile"]
    chars = template.get("characters") or []
    if chars:
        schema["characters"] = [{"id": c.get("id"), "name": c.get("name") or "", "role": c.get("role") or "",
                                 "gender": c.get("gender") or "", "age": str(c.get("age") or ""),
                                 "appearance": "English, giữ đúng template", "wardrobe": "English, giữ đúng template",
                                 "personality": c.get("personality") or ""} for c in chars[:6]]
        schema["scenes"][0]["cast"] = [chars[0].get("id")]
        schema["scenes"][0]["mission"] = "story"


def rules(brief: dict, limit: int, dialogue: bool) -> str:
    tpl = brief.get("template") or {}
    parts = ["SERIES THEO MẪU:"]
    if tpl.get("style"):
        parts.append(f"- Phong cách hình của series (giữ nguyên mọi tập): {tpl['style']}")
    if tpl.get("setting"):
        parts.append(f"- Bối cảnh chung: {tpl['setting']}")
    if tpl.get("script_notes"):
        parts.append(f"- Giọng điệu / cách kể chung của series (chủ kênh đặt): {tpl['script_notes']}")
    persons = [c for c in tpl.get("characters") or [] if c.get("personality")]
    if persons:
        parts.append("- Tính cách nhân vật (giữ đúng trong lời thoại và hành động): " + "; ".join(
            f"{c.get('name') or c.get('id')}: {c['personality']}" for c in persons))
    rules_list = tpl.get("continuity_rules") or ([tpl["rules"]] if tpl.get("rules") else [])
    if rules_list:
        parts.append("- Quy tắc nhất quán: " + " ".join(str(r) for r in rules_list))
    parts.append("- \"characters\" chép đúng id, tên, tuổi, giới tính từ template; appearance/wardrobe bằng tiếng "
                 "Anh, cụ thể và GIỐNG HỆT giữa các cảnh và các tập. Không thêm nhân vật chính mới.")
    parts.append("- style_profile theo template; mỗi nhịp \"mission\" thường là \"story\".")
    parts.append("- Kết bằng payoff/câu chốt trọn vẹn của tập, \"cta\" ĐỂ RỖNG.")
    episode = brief.get("episode") or {}
    if episode:
        parts.append(f"- Đây là TẬP {episode.get('number')} của series. Các tập trước: "
                     + json.dumps(episode.get("previous") or [], ensure_ascii=False)
                     + ". Tình huống mới, không lặp tập trước; giữ tính cách và quan hệ nhân vật.")
    if brief.get("desired_script"):
        parts.append("- KỊCH BẢN MONG MUỐN của chủ kênh (bám sát diễn biến, nhân vật và câu thoại chính; "
                     "được trau chuốt lời cho tự nhiên và đúng thời lượng, không đổi ý): "
                     + str(brief["desired_script"])[:3000])
    parts.append(DIALOGUE_RULES.format(limit=limit) if dialogue else NARRATION_RULES)
    return "\n".join(parts)


def fix_rules(dialogue: bool) -> str:
    voice = ("mọi nhịp có lời là thoại nhân vật, talking=true, không có narrator, mỗi nhịp một câu <= 12 từ "
             "(KHÔNG giới hạn số cảnh talking)" if dialogue else
             "một giọng kể duy nhất, tối đa 2 cảnh nói vào camera")
    return f"""Kiểm riêng series: đúng cast/tuổi/phong cách của template, không thêm nhân vật chính; {voice};
tình huống mới so với các tập trước; kết trọn vẹn, cta rỗng. Không trừ điểm vì không có CTA bán hàng."""


def issues(plan: dict, dialogue: bool) -> list[str]:
    out = []
    if not dialogue:
        return out
    narrated = [ln.get("text") for sc in plan.get("scenes") or [] for ln in sc.get("lines") or []
                if ln.get("speaker") == "narrator"]
    if narrated:
        out.append("Series hội thoại: không có người dẫn; chuyển thành thoại nhân vật: "
                   + json.dumps(narrated[:3], ensure_ascii=False))
    silent_talk = [i for i, sc in enumerate(plan.get("scenes") or []) if sc.get("lines") and not sc.get("talking")]
    if silent_talk:
        out.append(f"Series hội thoại: nhịp có lời phải talking=true (nhịp {silent_talk[:5]})")
    long_lines = [ln.get("text") for sc in plan.get("scenes") or [] for ln in sc.get("lines") or []
                  if len(str(ln.get("text") or "").split()) > 14]
    if long_lines:
        out.append("Series hội thoại: câu thoại dài quá 12 từ, khó nói đúng từng chữ: "
                   + json.dumps(long_lines[:3], ensure_ascii=False))
    return out
