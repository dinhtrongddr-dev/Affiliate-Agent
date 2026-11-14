"""Video formats (Content Director layer above the creation branches).

A format decides the length range, the visual style and how strictly facts are checked.
Durations follow the owner's guide; long formats cost many Muse clips, so they stay capped.
"""
from .creation_branches import DURATION_TARGETS

FORMATS = {
    "explainer": {"label": "Giải thích kiến thức", "duration": (30, 60), "style": "documentary",
                  "facts": True, "voice": "narrator",
                  "rule": "Một câu hỏi tò mò -> giải thích từng bước bằng hình ảnh trực quan -> điều bất ngờ cuối."},
    "story": {"label": "Kể chuyện", "duration": (20, 45), "style": "pro_social", "facts": False,
              "voice": "characters", "rule": "Nhân vật, tình huống, nút thắt, cao trào, kết trọn vẹn hoặc twist."},
    "mini_doc": {"label": "Phim tài liệu ngắn", "duration": (40, 90), "style": "documentary", "facts": True,
                 "voice": "narrator", "rule": "Người/nghề/nơi chốn thật kiểu phóng sự; giọng dẫn trầm, cảnh quan sát."},
    "process": {"label": "Quy trình trước/sau", "duration": (15, 35), "style": "pro_social", "facts": False,
                "voice": "narrator", "rule": "Kết quả gây tò mò ở giây 0 -> các bước nhanh, cận tay -> trước/sau."},
    "product": {"label": "Giới thiệu sản phẩm", "duration": (20, 40), "style": "pro_social", "facts": False,
                "voice": "creator", "rule": "Vấn đề đời thường -> món đồ giải quyết -> cách dùng -> giá."},
    "animated": {"label": "Hoạt hình chibi 3D", "duration": (20, 60), "style": "chibi_3d", "facts": False,
                 "voice": "narrator", "rule": "Thế giới hoạt hình 3D dễ thương, nhân vật nhất quán, nhịp nhanh."},
}
MAX_SECONDS = 120


def get(name: str | None) -> dict | None:
    return FORMATS.get(str(name or ""))


def duration_range(brief_or_plan: dict) -> tuple[float, float]:
    """Explicit range > format > branch default."""
    rng = brief_or_plan.get("duration_range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        try:
            low, high = float(rng[0]), min(float(rng[1]), MAX_SECONDS)
            if 5 <= low < high:
                return low, high
        except (TypeError, ValueError):
            pass
    fmt = get(brief_or_plan.get("format"))
    if fmt:
        return float(fmt["duration"][0]), float(fmt["duration"][1])
    return DURATION_TARGETS.get(brief_or_plan.get("creation_branch") or "", (15.0, 45.0))


def needs_facts(brief_or_plan: dict) -> bool:
    fmt = get(brief_or_plan.get("format"))
    return bool((fmt and fmt["facts"]) or brief_or_plan.get("niche") == "kien_thuc")
