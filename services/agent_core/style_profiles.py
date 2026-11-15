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
    # ---- trending art styles for character series (short-video trends 2025-2026) -------------
    "feature_3d": {
        "label": "Hoạt hình 3D điện ảnh", "trend": True, "stylized": True, "sheet": "3D animated feature",
        "desc": "Kiểu phim hoạt hình 3D chiếu rạp: da mềm, mắt biểu cảm, ánh sáng đẹp. Hợp hài gia đình, kể chuyện.",
        "camera": "Animated feature-film camera: smooth dolly, crane and orbit moves, cinematic framing, "
                  "expressive close-ups for reactions, wide shots to set the scene",
        "light": "rich global illumination, warm key light, soft rim light, gentle volumetric glow",
        "look": "high-end 3D animated feature film: appealing stylised characters with soft skin shading, "
                "large expressive eyes, detailed fabric and hair, warm detailed Vietnamese sets",
        "avoid": "no photorealistic humans, no live action, no on-screen text, no subtitles, no logos, "
                 "no watermark",
        "transition": "cut",
    },
    "anime_2d": {
        "label": "Anime 2D vẽ tay", "trend": True, "stylized": True, "sheet": "2D anime",
        "desc": "Anime vẽ tay, nền màu nước mềm, ánh nắng hoài niệm. Hợp chuyện cảm động, đời thường, tuổi thơ.",
        "camera": "Anime film staging: still layouts with slow pans, gentle parallax, dramatic low angles "
                  "and soft close-ups on faces and small details",
        "light": "warm nostalgic sunlight, soft glowing highlights, summer-afternoon atmosphere",
        "look": "hand-drawn 2D anime film: clean line art, cel shading, lush painted watercolour "
                "backgrounds, expressive eyes, everyday Vietnamese streets and homes",
        "avoid": "no 3D render look, no photorealistic humans, no on-screen text, no subtitles, no logos, "
                 "no watermark",
        "transition": "cut",
    },
    "clay_stopmotion": {
        "label": "Đất sét stop-motion", "trend": True, "stylized": True, "sheet": "claymation",
        "desc": "Nhân vật nặn đất sét, bối cảnh mô hình thủ công, chuyển động khựng nhẹ. Hài hước, độc lạ.",
        "camera": "Stop-motion miniature set photography: locked-off or slow motion-control moves, "
                  "macro lens, tabletop eye level",
        "light": "small practical studio lights on a miniature set, soft shadows",
        "look": "claymation: plasticine characters with visible fingerprints and tool marks, handmade "
                "miniature props and sets, slightly stepped stop-motion movement",
        "avoid": "no photorealistic humans, no smooth CGI look, no on-screen text, no subtitles, no logos, "
                 "no watermark",
        "transition": "cut",
    },
    "toy_figure": {
        "label": "Mô hình đồ chơi / blind box", "trend": True, "stylized": True, "sheet": "vinyl toy figure",
        "desc": "Nhân vật như figure vinyl sưu tầm trong sa bàn tí hon (trend blind box). Dễ thương, bắt mắt.",
        "camera": "Toy photography: macro lens, tilt-shift miniature effect, slow slider moves across a "
                  "diorama, low angles that make the figures feel alive",
        "light": "bright soft studio light, glossy highlights, colourful bokeh",
        "look": "collectible vinyl toy figurines with glossy plastic surfaces, chunky proportions and "
                "simple faces, living in a detailed miniature diorama of a Vietnamese home or street",
        "avoid": "no photorealistic humans, no brand logos, no on-screen text, no subtitles, no watermark",
        "transition": "cut",
    },
    "felt_craft": {
        "label": "Len / nỉ thủ công", "trend": True, "stylized": True, "sheet": "needle-felted wool",
        "desc": "Thế giới len móc, nỉ thủ công ấm áp. Hợp chuyện nhẹ nhàng, trẻ em, chữa lành.",
        "camera": "Gentle stop-motion style camera on a handmade set, slow push-ins, cosy close-ups",
        "light": "warm soft window light, cosy evening lamps",
        "look": "needle-felted wool and knitted yarn characters, fuzzy fibres, stitched details, "
                "handmade fabric world with felt furniture and props",
        "avoid": "no photorealistic humans, no plastic CGI shine, no on-screen text, no subtitles, no logos, "
                 "no watermark",
        "transition": "cut",
    },
    "storybook_2d": {
        "label": "Tranh truyện thiếu nhi", "trend": True, "stylized": True, "sheet": "picture-book illustration",
        "desc": "Như sách tranh thiếu nhi: màu nước, bút chì màu, chuyển động nhẹ. Hợp truyện bé, bài học.",
        "camera": "Animated picture-book pages: slow pans and gentle zooms across the illustration, "
                  "soft parallax between layers",
        "light": "soft even daylight, pastel palette",
        "look": "2D children's picture-book illustration in gouache and watercolour with coloured-pencil "
                "texture, simple rounded characters, warm Vietnamese everyday scenes",
        "avoid": "no 3D render look, no photorealistic humans, no on-screen text, no subtitles, no logos, "
                 "no watermark",
        "transition": "cut",
    },
    "cartoon_2d": {
        "label": "Hoạt hình 2D phẳng", "trend": True, "stylized": True, "sheet": "flat 2D cartoon",
        "desc": "Hoạt hình 2D nét đậm, màu tươi, biểu cảm cường điệu. Hợp hài nhanh, meme, giải thích vui.",
        "camera": "2D cartoon staging: snappy cuts, bold framing, smear frames and squash-and-stretch on "
                  "fast actions",
        "light": "flat bright colours with simple cel shadows",
        "look": "flat 2D cartoon with bold clean outlines, vibrant saturated colours, exaggerated "
                "expressive faces, simple graphic backgrounds",
        "avoid": "no 3D render look, no photorealistic humans, no on-screen text, no subtitles, no logos, "
                 "no watermark",
        "transition": "cut",
    },
    "retro_film": {
        "label": "Phim retro 90s (người thật)", "trend": True, "sheet": "",
        "desc": "Người thật, màu phim 35mm ấm, hạt film, hoài niệm thập niên 90. Hợp chuyện gia đình, kỷ niệm.",
        "camera": "Shot on 35mm film: steady tripod and gentle handheld, natural framing, slight zoom "
                  "pushes like a 90s family movie",
        "light": "warm tungsten interiors and soft afternoon sun",
        "look": "nostalgic 1990s Vietnamese film look: warm faded colours, visible film grain, soft halation, "
                "period-appropriate homes and clothes, real people",
        "avoid": "no modern smartphones unless asked, no on-screen text, no subtitles, no logos, no watermark",
        "transition": "cut",
    },
}
for _name in ("chibi_3d", "animated_3d"):
    PROFILES[_name]["stylized"] = True
PROFILES["chibi_3d"].update(trend=True, sheet="chibi 3D",
                            desc="Chibi 3D đầu to thân nhỏ, mắt to. Mặc định của series gia đình.")
PROFILES["animated_3d"]["sheet"] = "3D animated"
for _name, _desc in {
        "pro_social": "Người thật, quay chắc tay như studio nhỏ, góc máy đa dạng. Mặc định của review và viral.",
        "ugc_phone": "Người thật, tự quay bằng điện thoại, chân thật đời thường (rung tay nhẹ).",
        "sitcom_bright": "Người thật, hài gia đình sáng sủa kiểu vlog, điện thoại đặt chân máy.",
        "documentary": "Kiểu phim tài liệu: macro, góc thấp, chủ thể là ngôi sao. Hợp kiến thức, thiên nhiên.",
        "animated_3d": "Hoạt hình 3D đơn giản, nhân vật dễ thương, nền gọn.",
        "cinematic": "Người thật, chuyển cảnh mượt, xoá phông, màu điện ảnh."}.items():
    PROFILES[_name].setdefault("desc", _desc)

BRANCH_DEFAULT = {"viral_creator": "pro_social", "product_review": "pro_social",
                  "template_series": "chibi_3d"}
ANIMATED = {name for name, p in PROFILES.items() if p.get("stylized")}


def is_animated(name: str | None) -> bool:
    return str(name or "") in ANIMATED


def catalog() -> list[dict]:
    """Styles for the series editor: trending ones first."""
    rows = [{"id": k, "label": p["label"], "desc": p.get("desc", ""), "trend": bool(p.get("trend")),
             "animated": bool(p.get("stylized"))} for k, p in PROFILES.items()]
    return sorted(rows, key=lambda r: (not r["trend"], r["label"]))


def resolve(name: str | None, branch: str | None = None, override: str | None = None) -> str:
    """Owner override (settings) > storyboard choice > branch default."""
    for candidate in (override, name, BRANCH_DEFAULT.get(branch or ""), "pro_social"):
        if candidate and candidate in PROFILES:
            return candidate
    return "pro_social"


def get(name: str | None, branch: str | None = None, override: str | None = None) -> dict:
    return {"name": resolve(name, branch, override),
            **PROFILES[resolve(name, branch, override)]}
