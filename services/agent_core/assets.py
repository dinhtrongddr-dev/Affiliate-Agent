"""Visual assets: product images, licensed stock footage and AI-generated clips."""
import logging
import time
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


# ---------------------------------------------------------------- AI video generation

class VideoGenerator:
    """Pluggable text/image-to-video adapter (e.g. Muse or any HTTP service).

    'http' provider contract (configure AA_VIDEOGEN_BASE_URL / _API_KEY / _MODEL):
      POST {base}/generations  {"prompt", "aspect_ratio": "9:16", "duration", "model",
                                "image_url"?}            -> {"id": "..."}
      GET  {base}/generations/{id}                       -> {"status": "queued|running|
                                                             succeeded|failed", "video_url": "..."}
    Adapt this class if your provider uses another schema.
    """

    def __init__(self, settings):
        self.settings = settings
        self.provider = (settings.videogen_provider or "none").lower()
        self.base = settings.videogen_base_url.rstrip("/")
        self.key = settings.videogen_api_key.get_secret_value()

    @property
    def available(self) -> bool:
        return self.provider == "http" and bool(self.base)

    def generate(self, prompt: str, out: Path, duration: float = 4.0) -> Path | None:
        if not self.available or not prompt:
            return None
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        try:
            with httpx.Client(timeout=60, headers=headers) as client:
                resp = client.post(f"{self.base}/generations", json={
                    "prompt": prompt, "aspect_ratio": "9:16",
                    "duration": round(duration), "model": self.settings.videogen_model or None,
                })
                resp.raise_for_status()
                job_id = resp.json().get("id")
                if not job_id:
                    raise AssetError(f"Video generator returned no id: {resp.text[:200]}")
                deadline = time.monotonic() + self.settings.videogen_timeout_seconds
                while time.monotonic() < deadline:
                    status = client.get(f"{self.base}/generations/{job_id}").json()
                    state = str(status.get("status", "")).lower()
                    if state in {"succeeded", "completed", "success", "done"}:
                        url = status.get("video_url") or (status.get("output") or {}).get("url")
                        if not url:
                            raise AssetError("Generator finished without a video URL")
                        return download(url, out, None, headers=None, timeout=300)
                    if state in {"failed", "error", "cancelled"}:
                        raise AssetError(f"Generation failed: {status}")
                    time.sleep(5)
        except Exception as exc:  # noqa: BLE001
            log.warning("AI video generation failed: %s", exc)
        return None
