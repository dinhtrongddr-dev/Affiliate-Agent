"""Visual assets: product images, licensed stock footage and AI-generated clips."""
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("affiliate_agent.assets")

PRODUCT_IMAGE_HOSTS = ("susercontent.com", "shopee.vn", "shopee.com")
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class AssetError(RuntimeError):
    pass


def _host_allowed(url: str, suffixes: tuple[str, ...]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == s or host.endswith("." + s) for s in suffixes)


def download(url: str, target: Path, allowed_hosts: tuple[str, ...] | None = None,
             headers: dict | None = None, timeout: float = 120) -> Path:
    if allowed_hosts and not _host_allowed(url, allowed_hosts):
        raise AssetError(f"Refusing download from unexpected host: {urlparse(url).hostname}")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    size = 0
    with httpx.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as stream:
            for chunk in resp.iter_bytes(1024 * 256):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise AssetError("Download exceeds size limit")
                stream.write(chunk)
    tmp.replace(target)
    return target


def product_image(product: dict, out_dir: Path) -> Path | None:
    url = str(product.get("image_url") or "")
    if not url:
        return None
    target = Path(out_dir) / f"product_{product.get('item_id') or 'x'}.jpg"
    if target.is_file() and target.stat().st_size > 0:
        return target
    try:
        return download(url, target, PRODUCT_IMAGE_HOSTS, timeout=30)
    except Exception as exc:  # noqa: BLE001 - visual fallback continues
        log.warning("Product image download failed: %s", exc)
        return None


# ---------------------------------------------------------------- Pexels stock

def pexels_video(settings, query: str, out_dir: Path, used: set[str]) -> Path | None:
    """Portrait stock clip from Pexels (free licence). Needs AA_PEXELS_API_KEY."""
    key = settings.pexels_api_key.get_secret_value()
    if not key or not query:
        return None
    try:
        resp = httpx.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "orientation": "portrait", "per_page": 10, "size": "medium"},
            headers={"Authorization": key}, timeout=30,
        )
        resp.raise_for_status()
        for video in resp.json().get("videos") or []:
            vid = str(video.get("id"))
            if vid in used or not (3 <= float(video.get("duration") or 0) <= 60):
                continue
            files = [f for f in video.get("video_files") or []
                     if f.get("file_type") == "video/mp4" and (f.get("height") or 0) >= 960]
            if not files:
                continue
            best = min(files, key=lambda f: abs((f.get("height") or 0) - 1920))
            used.add(vid)
            return download(best["link"], Path(out_dir) / f"stock_{vid}.mp4",
                            ("pexels.com", "vimeocdn.com", "vimeo.com"))
    except Exception as exc:  # noqa: BLE001
        log.warning("Pexels search failed for %r: %s", query, exc)
    return None
