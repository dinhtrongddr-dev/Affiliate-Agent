"""Explicit creation intents stored additively in a job's brief_json."""
import math
import re

# Target video length per branch (seconds); enforced by the storyboard checks and QA.
DURATION_TARGETS = {"viral_creator": (15.0, 30.0), "product_review": (20.0, 40.0),
                    "template_series": (20.0, 35.0)}

BRANCHES = {
    "product_review": "Review / trải nghiệm sản phẩm",
    "viral_creator": "Creator bắt trend, sáng tạo nội dung gốc",
    "template_series": "Series theo mẫu sáng tạo cố định",
}


def validate_branch(value) -> str:
    if not isinstance(value, str) or value not in BRANCHES:
        raise ValueError("Nhánh sáng tạo không hợp lệ")
    return value


def bounded_text(value, limit=1500) -> str:
    return " ".join(str(value or "").split())[:limit]


def reference_id(value) -> str:
    text = str(value or "")
    return text if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", text) else ""


def normalise_characters(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    result = []
    for index, raw in enumerate(value[:6]):
        if not isinstance(raw, dict):
            continue
        character = {key: bounded_text(raw[key], 800) for key in
                     ("name", "role", "gender", "age", "appearance", "wardrobe", "personality", "description", "voice") if key in raw}
        character["id"] = reference_id(raw.get("id")) or f"character_{index + 1}"
        if reference_id(raw.get("reference_id")):
            character["reference_id"] = reference_id(raw["reference_id"])
        result.append(character)
    return result


SURI_FAMILY = {
    "id": "suri-family", "version": 1, "title": "Gia đình Suri",
    "style": "Cute chibi 3D animation: big heads, small rounded bodies, huge expressive eyes, cosy "
             "Vietnamese family home; playful family comedy through expressions, timing and harmless "
             "exaggeration.",
    "characters": [
        {"id": "father", "name": "Bố", "role": "father", "gender": "male", "age": "35",
         "personality": "Warm, overconfident, affectionate; comic plans backfire harmlessly."},
        {"id": "mother", "name": "Mẹ", "role": "mother", "gender": "female", "age": "32",
         "personality": "Warm, observant, quick wit; loving equal partner."},
        {"id": "suri", "name": "Suri", "role": "daughter", "gender": "female", "age": "4",
         "personality": "Curious four-year-old girl; innocent literal thinking and playful surprises."},
    ],
    "rules": "Exactly father, mother and four-year-old daughter Suri. Age-appropriate daily family comedy; "
             "stable cast appearances and wardrobe, no dangerous child activities or humiliation.",
    "voice_mode": "dialogue",
}

# How a series is written: "dialogue" = the characters talk to each other on screen (one VieNeu
# voice per character, fitted to the mouth); "narration" = a narrator / first-person voice-over
# (knowledge and niche series). Every voice is VieNeu: Muse's own speech is discarded because it
# improvises its own words instead of the scripted line (measured Nov 2026: 0/8 lines matched).
VOICE_MODES = {"dialogue": "Hội thoại giữa nhân vật (mỗi nhân vật một giọng VieNeu)",
               "narration": "Thuyết minh / ngôi thứ nhất (giọng VieNeu)"}


def is_dialogue(brief: dict) -> bool:
    """A series written as character-to-character dialogue."""
    if (brief or {}).get("creation_branch") != "template_series":
        return False
    tpl = brief.get("template") or {}
    mode = (brief.get("voice_mode") or tpl.get("voice_mode")
            or ("dialogue" if tpl.get("id") == "suri-family" else ""))
    return mode == "dialogue"


def _context(value, depth=0):
    """Bound structured factual snapshots; never execute or resolve their values."""
    if depth > 5:
        return None
    if isinstance(value, dict):
        return {str(k)[:80]: _context(v, depth + 1) for k, v in list(value.items())[:60]}
    if isinstance(value, list):
        return [_context(v, depth + 1) for v in value[:30]]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    return bounded_text(value, 3000)


def normalise_brief(branch, *, experience=None, template=None, product_context=None) -> dict:
    branch = validate_branch(branch)
    experience = experience or {}
    if not isinstance(experience, dict):
        raise ValueError("Trải nghiệm phải là dữ liệu có cấu trúc")
    basis = experience.get("basis", "product_facts")
    if basis not in {"product_facts", "owner_experience"}:
        raise ValueError("Nguồn trải nghiệm không hợp lệ")
    result = {"creation_branch": branch, "creative_brief_version": 1,
              "experience": {"basis": basis, "notes": bounded_text(experience.get("notes"), 6000)}}
    if product_context is not None:
        if not isinstance(product_context, dict):
            raise ValueError("Thông tin sản phẩm phải là dữ liệu có cấu trúc")
        result["product_context"] = _context(product_context)
    if template is not None or branch == "template_series":
        raw = SURI_FAMILY if template is None else template
        if not isinstance(raw, dict):
            raise ValueError("Mẫu series phải là dữ liệu có cấu trúc")
        if isinstance(raw.get("characters"), list) and len(raw["characters"]) > 6:
            raise ValueError("Mẫu series hỗ trợ tối đa 6 nhân vật")
        version = raw.get("version", 1)
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= 10000:
            raise ValueError("Phiên bản mẫu không hợp lệ")
        result["template"] = {
            "id": reference_id(raw.get("id")) or "custom-series", "version": version,
            "title": bounded_text(raw.get("title"), 150),
            "label": bounded_text(raw.get("label") or raw.get("title"), 150),
            "setting": bounded_text(raw.get("setting"), 1500),
            "style": bounded_text(raw.get("style"), 1500),
            "rules": bounded_text(raw.get("rules"), 3000),
            "continuity_rules": _context(raw.get("continuity_rules", "")),
            "episode_idea": bounded_text(raw.get("episode_idea"), 3000),
            "characters": normalise_characters(raw.get("characters")),
            # the built-in family sitcom talks; a custom template narrates unless it says otherwise
            "voice_mode": raw.get("voice_mode") if raw.get("voice_mode") in VOICE_MODES else
                          ("dialogue" if template is None or raw.get("id") == "suri-family" else "narration"),
        }
    return result
