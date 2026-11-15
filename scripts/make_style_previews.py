"""Make the style preview pictures shown in the series editor (one per art style).

Every style draws the SAME scene, so the pictures compare styles, not subjects.
Run once (or after adding a style):  python scripts/make_style_previews.py [style_id ...]
Uses the configured ChatGPT2API image model; pictures go to web/static/styles/<style>.jpg.
"""
import base64
import io
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image

from agent_core.settings import get_settings
from agent_core.style_profiles import PROFILES

SCENE = ("A young Vietnamese mother and her 4-year-old daughter laughing while folding colourful laundry "
         "on a sofa in a cosy living room; the father peeks in from the doorway holding a cup of tea.")
OUT = Path(__file__).resolve().parents[1] / "services" / "agent_core" / "web" / "static" / "styles"
MODEL = "gpt-image-2"


def prompt(style_id: str) -> str:
    p = PROFILES[style_id]
    return (f"Vertical 3:4 still frame. {SCENE} Visual style: {p['look']}. Light: {p['light']}. "
            f"Camera: {p['camera'][:160]}. {p['avoid']}.")


def make(style_id: str) -> str:
    s = get_settings()
    r = httpx.post(s.ai_base_url.rstrip("/") + "/images/generations", timeout=600,
                   headers={"Authorization": f"Bearer {s.ai_api_key.get_secret_value()}"},
                   json={"model": MODEL, "prompt": prompt(style_id), "n": 1, "size": "1024x1536",
                         "response_format": "b64_json"})
    r.raise_for_status()
    item = r.json()["data"][0]
    raw = base64.b64decode(item["b64_json"]) if item.get("b64_json") else httpx.get(item["url"], timeout=120).content
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail((480, 640))
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / f"{style_id}.jpg", quality=82, optimize=True, progressive=True)
    return f"{style_id}: ok {img.size}"


def _safe(style_id):
    try:
        return make(style_id)
    except Exception as exc:  # noqa: BLE001
        return f"{style_id}: FAILED {exc}"


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(PROFILES)
    with ThreadPoolExecutor(3) as ex:
        for res in ex.map(_safe, wanted):
            print(res, flush=True)
