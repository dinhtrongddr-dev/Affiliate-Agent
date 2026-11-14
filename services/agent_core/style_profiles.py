"""Visual style profiles for AI clips (Muse) and editing.

Default is ``pro_social``: deliberate, stable, well-composed shots that change size and angle
from shot to shot (the owner found the shaky phone look tiring). ``ugc_phone`` stays available
as an explicit choice. Series default to cute chibi 3D animation.
"""

PROFILES: dict[str, dict] = {
    "pro_social": {
        "label": "Quay chuyên nghiệp (mặc định)",
        "camera": "Professional short-video cinematography on a gimbal, slider or tripod: deliberate "
                  "shot size for this beat, slow purposeful move (push-in, slide, orbit or tilt), "
                  "rule-of-thirds composition, shallow depth of field on details, rock-steady frame",
        "light": "soft natural key light with gentle contrast and clean highlights",
        "look": "crisp true-to-life colours, realistic textures, tidy modern Vietnamese setting, "
                "polished but believable, not a plastic AI look",
        "avoid": "no shaky handheld, no selfie angle, no phone-camera look, no on-screen text, "
                 "no subtitles, no logos, no watermark, no warped hands or faces",
        "transition": "cut",
    },
    "chibi_3d": {
        "label": "Hoạt hình chibi 3D",
        "camera": "Animated 3D film camera: smooth dolly and crane moves, varied shot sizes "
                  "(wide establishing, medium, expressive close-up), clean staging",
        "light": "soft global illumination, warm rim light, vibrant pastel colours",
        "look": "cute chibi 3D animation like a modern animated feature: big heads, small rounded "
                "bodies, huge expressive eyes, smooth stylised materials, cosy detailed Vietnamese "
                "home and street sets, characters identical in every shot",
        "avoid": "no photorealistic humans, no live action, no on-screen text, no subtitles, no logos, "
                 "no watermark",
        "transition": "cut",
    },
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
    "documentary": {
        "label": "Phim tài liệu / kiến thức",
        "camera": "Nature and science documentary cinematography: stabilised slow dolly, top-down and "
                  "low angles, macro lens on the subject's details, cutaway or cross-section views "
                  "when explaining how something works; the subject fills the frame",
        "light": "beautiful natural light of the real place, golden hour when outdoors",
        "look": "rich realistic detail like a BBC-style documentary, true colours, the topic itself is "
                "the hero of every shot",
        "avoid": "no shaky handheld, no on-screen text, no subtitles, no logos, no watermark, "
                 "no people unless the shot is about them",
        "transition": "cut",
    },
    "animated_3d": {
        "label": "Hoạt hình 3D",
        "camera": "Stylised 3D animation, smooth virtual camera, clear silhouettes",
        "light": "soft colourful animated lighting",
        "look": "cute consistent 3D characters and props, rich but simple backgrounds",
        "avoid": "no on-screen text, no subtitles, no logos, no watermark, no photoreal humans",
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

BRANCH_DEFAULT = {"viral_creator": "pro_social", "product_review": "pro_social",
                  "template_series": "chibi_3d"}
ANIMATED = {"chibi_3d", "animated_3d"}


def is_animated(name: str | None) -> bool:
    return str(name or "") in ANIMATED


def resolve(name: str | None, branch: str | None = None, override: str | None = None) -> str:
    """Owner override (settings) > storyboard choice > branch default."""
    for candidate in (override, name, BRANCH_DEFAULT.get(branch or ""), "pro_social"):
        if candidate and candidate in PROFILES:
            return candidate
    return "pro_social"


def get(name: str | None, branch: str | None = None, override: str | None = None) -> dict:
    return {"name": resolve(name, branch, override),
            **PROFILES[resolve(name, branch, override)]}
