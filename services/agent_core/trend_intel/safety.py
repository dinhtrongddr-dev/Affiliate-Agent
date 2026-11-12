"""Rule-based filter for trends that must never be tied to a product: tragedies, gambling,
crime, politics, sensitive health topics and adult content. The AI intent step adds its own
`sensitive` verdict on top; either one blocks the topic."""
import re

from .store import strip_accents

# Matched on the accented, lowercased text: accent-free forms are ambiguous in Vietnamese
# ("bát" bowl vs "bắt" arrest, "bão giá" sale vs "bão" storm).
ACCENTED = {
    "Cờ bạc / xổ số": ["xổ số", "vietlott", "cá độ", "soi kèo", "nhà cái", "casino", "lô đề",
                       "kết quả xs", "xs "],
    "Tai nạn / thiên tai / qua đời": [
        "tử vong", "qua đời", "thiệt mạng", "tai nạn", "hỏa hoạn", "vụ cháy", "cháy nhà",
        "cháy rừng", "lũ lụt", "lũ quét", "bão số", "cơn bão", "siêu bão", "áp thấp nhiệt đới",
        "động đất", "sạt lở", "sập cầu", "sập nhà", "đắm tàu", "rơi máy bay", "tang lễ",
        "viếng", "chết"],
    "Tội phạm / pháp lý": [
        "bị bắt", "bắt giữ", "bắt tạm giam", "khởi tố", "án mạng", "giết", "cướp", "lừa đảo",
        "ma túy", "hiếp", "tử hình", "xét xử", "truy nã", "bạo hành"],
    "Chính trị": [
        "bầu cử", "quốc hội", "tổng bí thư", "thủ tướng", "chủ tịch nước", "bộ chính trị",
        "chiến tranh", "biểu tình", "xung đột vũ trang"],
    "Sức khoẻ nhạy cảm": ["ung thư", "dịch bệnh", "covid", "vắc xin", "vaccine", "chữa khỏi",
                          "trị dứt điểm"],
    "Người lớn": ["khiêu dâm", "sex", "18+"],
}
ACCENT_FREE = {
    "Cờ bạc / xổ số": [r"\bxo so\b", r"\bxsmn\b", r"\bxsmb\b", r"\bxsmt\b", r"\bxskt\b",
                       r"\bvietlott\b", r"\bsoi keo\b"],
    "Tai nạn / thiên tai / qua đời": [r"\btu vong\b", r"\bqua doi\b", r"\bdong dat\b",
                                      r"\bsat lo\b"],
}


def check(keyword: str, notes: str = "") -> tuple[bool, str]:
    """(blocked, reason)."""
    text = f" {keyword} {notes} ".lower()
    for category, words in ACCENTED.items():
        for word in words:
            if word in text:
                return True, f"{category}: '{word.strip()}'"
    plain = strip_accents(text)
    for category, patterns in ACCENT_FREE.items():
        for pattern in patterns:
            if re.search(pattern, plain):
                return True, category
    return False, ""
