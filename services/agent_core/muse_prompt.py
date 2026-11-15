"""Compile one storyboard shot into a short, structured Muse prompt (<= MAX_CHARS).

The AI writes short shot fields; this module decides order and length, so every prompt has the
same shape: format -> camera -> who -> action -> setting -> light -> speech -> product -> avoid.
Internal identity lines (prefixed with INTERNAL) are kept in the stored task for idempotency but
stripped before the prompt is sent.
"""
from . import style_profiles

MAX_CHARS = 1500
INTERNAL = "§"


def _clip(text, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,.;:") + "…"


def _characters(plan: dict) -> dict[str, dict]:
    return {c["id"]: c for c in plan.get("characters") or [] if isinstance(c, dict) and c.get("id")}


def scene_cast(scene: dict, plan: dict) -> list[dict]:
    chars = _characters(plan)
    if "cast" not in scene:          # storyboard v1: no per-scene cast, keep the whole cast
        ids = list(chars)
    else:
        # an explicit empty cast means "nobody on screen" (the subject is the shot)
        ids = [cid for cid in scene.get("cast") or [] if cid in chars]
    return [chars[cid] for cid in ids[:3]]


ANIMALS = ("dog", "puppy", "cat", "kitten", "chó", "cún", "mèo", "pet", "bird", "chim", "rabbit", "thỏ")


def is_animal(c: dict) -> bool:
    text = f"{c.get('role') or ''} {c.get('species') or ''}".lower()
    return any(a in text for a in ANIMALS)


def _person(c: dict) -> str:
    bits = [c.get("gender"), f"age {c['age']}" if c.get("age") else ""]
    head = ", ".join(b for b in bits if b)
    look = "; ".join(b for b in (c.get("appearance"), ("wearing " + c["wardrobe"]) if c.get("wardrobe") else "")
                     if b)
    return _clip(f"{c.get('name') or c['id']}" + (f" ({head})" if head else "") + (f": {look}" if look else ""), 230)


def voice_hint(c: dict) -> str:
    """The same short voice description for a character in every clip, so Muse keeps one voice."""
    if c.get("voice_desc"):
        return _clip(c["voice_desc"], 60)
    try:
        age = int(str(c.get("age") or "").strip()[:3])
    except ValueError:
        age = None
    gender = str(c.get("gender") or "").lower()
    if not gender:   # templates saved without gender: infer from the role/name words
        who = f" {c.get('role') or ''} {c.get('name') or ''} {c.get('id') or ''} ".lower()
        if any(w in who for w in (" mẹ", "mother", " mom", " bà", "grandma", " chị", " cô ", "wife", "vợ")):
            gender = "female"
        elif any(w in who for w in (" bố", "father", " dad", " ba ", " ông", "grandpa", " anh", " chú", "husband", "chồng")):
            gender = "male"
    if age is not None and age <= 10:
        return "high bright child voice"
    if gender.startswith(("f", "nữ")):
        return "warm young woman's voice" if (age or 30) < 45 else "gentle older woman's voice"
    if gender.startswith(("m", "nam")):
        return "friendly young man's voice" if (age or 30) < 45 else "deep older man's voice"
    return "natural voice"


def compile_shot(scene: dict, plan: dict, references: list[dict] | None = None,
                 duration: int = 10, override_style: str | None = None) -> str:
    references = references or []
    shot = scene.get("shot") or {}
    style = style_profiles.get(plan.get("style_profile"), plan.get("creation_branch"), override_style)
    parts = [f"Vertical 9:16 video, one continuous {int(duration)}-second take."]
    # this shot's own size/angle/move first, the house style after it (trimmed if long)
    own = str(shot.get("camera") or "").strip()
    parts.append(_clip((f"Camera: {own}. Style: " if own else "Camera: ") + style["camera"], 320))

    cast = scene_cast(scene, plan)
    attach = {r["role"].split(":", 1)[1]: i for i, r in enumerate(references, 1)
              if str(r.get("role", "")).startswith("character:")}
    if cast:
        who = []
        for c in cast:
            ref = attach.get(c["id"])
            who.append(_person(c) + (f" (looks exactly like attachment {ref})" if ref else ""))
        parts.append("People: " + " | ".join(who) + ". Keep each face, hairstyle and outfit identical.")
    elif scene.get("hands_only"):
        parts.append("Only realistic hands are visible, no face.")
    elif "cast" in scene:
        parts.append("No people in frame: the subject of the action itself fills the frame.")

    action = shot.get("action") or scene.get("gen_prompt") or ""
    parts.append("Action: " + _clip(action, 320))
    if shot.get("setting"):
        parts.append("Setting: " + _clip(shot["setting"], 180))
    parts.append("Light: " + _clip(shot.get("lighting") or style["light"], 110))
    if shot.get("emotion"):
        parts.append("Mood: " + _clip(shot["emotion"], 70))
    parts.append("Look: " + style["look"] + ".")
    if plan.get("style"):
        parts.append("Style notes: " + _clip(plan["style"], 160))
    if plan.get("setting"):
        parts.append("Series setting: " + _clip(plan["setting"], 140))
    if plan.get("continuity_rules"):
        rules = plan["continuity_rules"]
        rules = "; ".join(map(str, rules)) if isinstance(rules, list) else str(rules)
        parts.append("Continuity: " + _clip(rules, 160))
    if references:
        parts.append("Attachments: " + "; ".join(
            f"{i} = {r['id']} ({r['role']})" for i, r in enumerate(references, 1)) + ".")

    lines = [ln for ln in scene.get("lines") or [] if ln.get("text")]
    names = {c["id"]: c.get("name") or c["id"] for c in cast}
    on_camera = [ln for ln in lines if ln.get("speaker") in names]
    if scene.get("talking") and on_camera and plan.get("voice_source") == "native":
        # dialogue series: this clip's own voices are kept, so the speech must be right here
        by_id = {c["id"]: c for c in cast}
        said = "; ".join(f'{names[ln["speaker"]]} ({voice_hint(by_id[ln["speaker"]])}) says in Vietnamese: '
                         f'"{_clip(ln["text"], 90)}"' for ln in on_camera[:2])
        parts.append("Dialogue: " + said + " They talk to each other, natural lip sync, clear standard "
                     "Vietnamese, lively emotion. Audio: only these voices and soft room sound, "
                     "no background music.")
    elif scene.get("talking") and on_camera:
        said = "; ".join(f'{names[ln["speaker"]]} says to camera: "{_clip(ln["text"], 90)}"'
                         for ln in on_camera[:2])
        parts.append("Speech: " + said + " Natural mouth movement, normal speaking pace.")
    else:
        parts.append("Nobody speaks; mouths stay closed or only smile, react and breathe.")

    product = next((i for i, r in enumerate(references, 1) if r.get("role") == "product"), None)
    if product and scene.get("product_visible", True):
        parts.append(f"The product is exactly the one in attachment {product}: same shape, colour "
                     "and packaging; never invent labels or brand names.")
    elif not scene.get("product_visible"):
        parts.append("Do not show any branded product or packaging.")
    parts.append("Avoid: " + style["avoid"] + ".")

    text = " ".join(p for p in parts if p)
    if len(text) > MAX_CHARS:  # trim the most verbose free-text fields first
        text = _clip(text, MAX_CHARS)
    return text


def strip_internal(prompt: str) -> str:
    return "\n".join(line for line in str(prompt).splitlines()
                     if not line.startswith(INTERNAL)).strip()


def character_sheet_prompt(character: dict, plan: dict, override_style: str | None = None) -> str:
    style = style_profiles.get(plan.get("style_profile"), plan.get("creation_branch"), override_style)
    if style_profiles.is_animated(style["name"]):
        kind = "chibi 3D" if style["name"] == "chibi_3d" else "3D animated"
        return _clip(
            f"Vertical {kind} character sheet: one cute stylised character, full body, front view, "
            "big head and small rounded body, expressive eyes, plain light background, soft colourful "
            f"light. {_person(character)}. Personality: {character.get('personality') or ''}. "
            f"{style['avoid']}.", MAX_CHARS) + f"\n{INTERNAL}cast:{character['id']}"
    if is_animal(character):
        return _clip(
            "Vertical photo of one real animal, whole body clearly visible, standing on a plain floor "
            "in natural daylight, taken with a smartphone, real fur texture. "
            f"{_person(character)}. Keep markings, colour and size exactly as described. "
            f"{style['avoid']}.", MAX_CHARS) + f"\n{INTERNAL}cast:{character['id']}"
    return _clip(
        "Vertical professional portrait, waist-up, one real Vietnamese person facing the camera, "
        "plain light backdrop, soft natural key light, sharp focus, "
        f"real skin texture. {_person(character)}. Personality: {character.get('personality') or ''}. "
        f"{style['avoid']}.", MAX_CHARS) + f"\n{INTERNAL}cast:{character['id']}"
