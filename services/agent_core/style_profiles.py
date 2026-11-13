"""Visual style profiles for AI clips (Muse) and editing.

The default is ``ugc_phone``: footage that looks filmed on a phone by a real person, because
short-video viewers scroll past clips that look like a polished AI advert.
"""

PROFILES: dict[str, dict] = {
    "ugc_phone": {
        "label": "Quay điện thoại đời thường",
        "camera": "Filmed on a smartphone held in hand, slight natural handheld shake, eye level "
                  "or arm's-length selfie angle, imperfect casual framing, autofocus",
        "light": "natural window daylight or warm indoor bulbs, no added lighting",
        "look": "real skin texture, ordinary lived-in Vietnamese home or street, everyday clothes, "
                "no colour grading",
        "avoid": "no cinematic look, no studio lighting, no slow motion, no lens flare, no drone shot, "
                 "no on-screen text, no subtitles, no logos, no watermark",
        "transition": "cut",
    },
    "sitcom_bright": {
        "label": "Hài gia đình sáng sủa",
        "camera": "Phone on a tripod at chest height in a real home, occasional handheld follow, "
                  "natural framing like a family vlog",
        "light": "bright soft daylight from windows",
        "look": "warm everyday Vietnamese family home, playful expressive acting, real faces and fabric",
        "avoid": "no cinematic grading, no studio set, no on-screen text, no subtitles, no logos, "
                 "no watermark",
        "transition": "cut",
    },
    "cinematic": {
        "label": "Điện ảnh (chọn tay)",
        "camera": "Smooth stabilised camera movement, shallow depth of field",
        "light": "motivated soft key light",
        "look": "graded, polished",
        "avoid": "no on-screen text, no subtitles, no logos, no watermark",
        "transition": "fade",
    },
}

BRANCH_DEFAULT = {"viral_creator": "ugc_phone", "product_review": "ugc_phone",
                  "template_series": "sitcom_bright"}


def resolve(name: str | None, branch: str | None = None, override: str | None = None) -> str:
    """Owner override (settings) > storyboard choice > branch default."""
    for candidate in (override, name, BRANCH_DEFAULT.get(branch or ""), "ugc_phone"):
        if candidate and candidate in PROFILES:
            return candidate
    return "ugc_phone"


def get(name: str | None, branch: str | None = None, override: str | None = None) -> dict:
    return {"name": resolve(name, branch, override),
            **PROFILES[resolve(name, branch, override)]}
