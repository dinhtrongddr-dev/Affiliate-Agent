"""Voice casting: every speaker in a storyboard gets one fixed VieNeu voice.

Rules (enforced in code, not left to the model):
- one speaker keeps one voice for the whole video;
- two speakers never share a voice in the same video;
- the voice's gender matches the speaker; children use a pitched-up female voice;
- the review narrator follows the product's audience (women's product -> female voice ...);
- a series character can pin a voice in the template so it never changes between episodes.
"""
from .editor.tts import VOICES

FEMALE = [v for v, label in VOICES.items() if label.startswith("Nữ")]
MALE = [v for v, label in VOICES.items() if label.startswith("Nam")]

# Ordered preferences per use: first free voice wins.
NARRATOR = {
    ("product_review", "female"): ["Ái Hân", "Trúc Ly", "Mỹ Duyên"],
    ("product_review", "male"): ["Anh Khôi", "Adam", "Minh Quân"],
    ("product_review", "unisex"): ["Trúc Ly", "Anh Khôi"],
    ("viral_creator", "story"): ["Xuân Tiên", "Đức Trí", "Mỹ Duyên"],
    ("viral_creator", "funny"): ["Anh Khôi", "Xuân Tiên", "Minh Quân"],
    ("viral_creator", "emotional"): ["Mỹ Duyên", "Đức Trí", "Xuân Tiên"],
    ("template_series", "story"): ["Xuân Tiên", "Đức Trí"],
}
ROLE = {
    "father": ["Mạnh Dũng", "Đức Trí", "Hữu Quân"],
    "mother": ["Mỹ Duyên", "Mai Anh", "Trúc Ly"],
    "grandmother": ["Mai Anh", "Mỹ Duyên"],
    "grandfather": ["Đức Trí", "Mạnh Dũng"],
}
CHILD_PITCH = 1.22   # semitone-ish lift via asetrate, speed restored with atempo
CHILD_VOICES = ["Mỹ Duyên", "Ái Hân", "Xuân Tiên"]


def _gender(value) -> str:
    v = str(value or "").lower()
    if v.startswith(("f", "nữ", "nu", "woman", "girl")) or v in {"mother", "daughter", "grandmother"}:
        return "female"
    if v.startswith(("m", "nam", "man", "boy")) or v in {"father", "son", "grandfather"}:
        return "male"
    return ""


def _is_child(character: dict) -> bool:
    try:
        return int(str(character.get("age") or "").split()[0]) < 13
    except (ValueError, IndexError):
        return str(character.get("role") or "").lower() in {"daughter", "son", "child", "kid"} and \
            "adult" not in str(character.get("age") or "")


def _mood_key(plan: dict) -> str:
    mood = str(plan.get("music_mood") or "").lower()
    if any(k in mood for k in ("funny", "hài", "bouncy", "playful")):
        return "funny"
    if any(k in mood for k in ("emotional", "warm", "cảm", "sad", "calm")):
        return "emotional"
    return "story"


def _pick(prefs, gender, used) -> str:
    pool = list(prefs) + (FEMALE if gender == "female" else MALE if gender == "male" else FEMALE + MALE)
    for voice in pool:
        if voice in VOICES and voice not in used:
            return voice
    return pool[0] if pool else "Mai Anh"


def speakers(plan: dict) -> list[str]:
    found = []
    for scene in plan.get("scenes") or []:
        for line in scene.get("lines") or []:
            if line.get("speaker") and line["speaker"] not in found:
                found.append(line["speaker"])
    return found


def cast_voices(plan: dict, default_voice: str = "", pinned: dict | None = None) -> dict:
    """Return {speaker: {"voice", "pitch", "gender", "label"}} for every speaker in the plan."""
    pinned = pinned or {}
    branch = plan.get("creation_branch") or ""
    chars = {c["id"]: c for c in plan.get("characters") or [] if isinstance(c, dict) and c.get("id")}
    previous = plan.get("voice_cast") or {}
    result, used = {}, set()

    def assign(sid, voice, pitch=1.0, gender="", label=""):
        result[sid] = {"voice": voice, "pitch": pitch, "gender": gender, "label": label or sid}
        used.add(voice)

    # 1) owner choices and pinned series voices are kept as they are
    for sid in speakers(plan):
        chosen = (previous.get(sid) or {}).get("voice") if (previous.get(sid) or {}).get("locked") else None
        chosen = chosen or pinned.get(sid) or (chars.get(sid) or {}).get("voice")
        if chosen in VOICES:
            c = chars.get(sid, {})
            assign(sid, chosen, CHILD_PITCH if c and _is_child(c) else 1.0,
                   _gender(c.get("gender") or c.get("role")), c.get("name") or sid)
            if (previous.get(sid) or {}).get("locked"):
                result[sid]["locked"] = True
    # 2) characters
    for sid in speakers(plan):
        if sid in result or sid not in chars:
            continue
        c = chars[sid]
        gender = _gender(c.get("gender") or c.get("role"))
        if _is_child(c):
            assign(sid, _pick(CHILD_VOICES, "female", used), CHILD_PITCH, gender or "female",
                   c.get("name") or sid)
            continue
        assign(sid, _pick(ROLE.get(str(c.get("role") or "").lower(), []), gender, used), 1.0,
               gender, c.get("name") or sid)
    # 3) narrator / creator voice
    for sid in speakers(plan):
        if sid in result:
            continue
        if branch == "product_review":
            audience = str(plan.get("audience_gender") or "unisex").lower()
            audience = audience if audience in {"female", "male"} else "unisex"
            prefs = NARRATOR[(branch, audience)]
            gender = audience if audience != "unisex" else ""
        else:
            prefs = NARRATOR.get((branch, _mood_key(plan))) or NARRATOR[("viral_creator", "story")]
            gender = ""
        if default_voice in VOICES and branch != "product_review" and default_voice not in used:
            prefs = [default_voice, *prefs]
        voice = _pick(prefs, gender, used)
        assign(sid, voice, 1.0, gender or _gender(VOICES[voice][:3]), "Người dẫn" if sid == "narrator" else sid)
    return result


def problems(plan: dict, cast: dict) -> list[str]:
    """Hard checks on a voice cast (used by tests and QA)."""
    issues = []
    voices = [v["voice"] for v in cast.values()]
    if len(voices) != len(set(voices)):
        issues.append("Hai người nói dùng chung một giọng")
    chars = {c["id"]: c for c in plan.get("characters") or [] if isinstance(c, dict) and c.get("id")}
    for sid, info in cast.items():
        want = _gender((chars.get(sid) or {}).get("gender") or (chars.get(sid) or {}).get("role"))
        have = "female" if info["voice"] in FEMALE else "male"
        if want and want != have and not (chars.get(sid) and _is_child(chars[sid])):
            issues.append(f"Giọng {info['voice']} không khớp giới tính của {sid}")
    return issues
