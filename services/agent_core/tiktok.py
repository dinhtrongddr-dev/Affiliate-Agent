"""TikTok Content Posting API (official).

Modes:
  inbox  - upload to the creator's TikTok inbox/drafts; the user finishes the post
           in the TikTok app (add trending sound there). Default, safest.
  direct - Direct Post. Unaudited apps can only post SELF_ONLY (private).
Requires an access token with video.upload (inbox) or video.publish (direct).
"""
import math
import time
from pathlib import Path

import httpx

API = "https://open.tiktokapis.com"
MIN_CHUNK = 5 * 1024 * 1024
MAX_SINGLE = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024


class TikTokError(RuntimeError):
    pass


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunks) following TikTok's FILE_UPLOAD rules."""
    if size <= MAX_SINGLE:
        return size, 1
    total = math.floor(size / CHUNK)
    return CHUNK, total


class TikTokPublisher:
    def __init__(self, settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.token = settings.tiktok_access_token.get_secret_value()
        if not self.token:
            raise TikTokError("TikTok access token is missing")
        self.mode = settings.tiktok_mode if settings.tiktok_mode in {"inbox", "direct"} else "inbox"
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(120, connect=30), transport=self.transport,
                            headers={"Authorization": f"Bearer {self.token}",
                                     "Content-Type": "application/json; charset=UTF-8"})

    @staticmethod
    def _check(resp: httpx.Response, stage: str) -> dict:
        try:
            data = resp.json()
        except ValueError:
            data = {"body": resp.text[:300]}
        code = ((data.get("error") or {}).get("code")) if isinstance(data, dict) else None
        if resp.is_error or (code and code != "ok"):
            raise TikTokError(f"{stage} failed (HTTP {resp.status_code}): {data}")
        return data.get("data") or {}

    def upload(self, video: Path, caption: str, is_aigc: bool = False,
               poll_seconds: int = 180) -> dict:
        video = Path(video)
        size = video.stat().st_size
        chunk_size, total = chunk_plan(size)
        source_info = {"source": "FILE_UPLOAD", "video_size": size,
                       "chunk_size": chunk_size, "total_chunk_count": total}
        with self._client() as client:
            if self.mode == "direct":
                body = {"post_info": {
                    "title": caption[:2200],
                    "privacy_level": self.settings.tiktok_privacy_level or "SELF_ONLY",
                    "disable_duet": False, "disable_comment": False, "disable_stitch": False,
                    "is_aigc": bool(is_aigc),
                }, "source_info": source_info}
                init = client.post(f"{API}/v2/post/publish/video/init/", json=body)
            else:
                init = client.post(f"{API}/v2/post/publish/inbox/video/init/",
                                   json={"source_info": source_info})
            data = self._check(init, "init")
            upload_url, publish_id = data.get("upload_url"), data.get("publish_id")
            if not upload_url or not publish_id:
                raise TikTokError(f"TikTok init returned no upload URL: {data}")

            with video.open("rb") as stream:
                for index in range(total):
                    start = index * chunk_size
                    length = size - start if index == total - 1 else chunk_size
                    stream.seek(start)
                    payload = stream.read(length)
                    put = httpx.put(upload_url, content=payload, timeout=300, headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes {start}-{start + length - 1}/{size}",
                    }, transport=None)
                    if put.status_code not in (200, 201, 206):
                        raise TikTokError(f"Chunk {index} upload failed: HTTP {put.status_code}")

            deadline = time.monotonic() + poll_seconds
            last = {}
            while time.monotonic() < deadline:
                status = client.post(f"{API}/v2/post/publish/status/fetch/",
                                     json={"publish_id": publish_id})
                last = self._check(status, "status")
                state = str(last.get("status", ""))
                if state in {"SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"}:
                    return {"status": "TIKTOK_INBOX_CONFIRMED" if self.mode == "inbox"
                            else "PUBLISHED_CONFIRMED", "publish_id": publish_id, "raw": last}
                if state == "FAILED":
                    raise TikTokError(f"TikTok processing failed: {last}")
                time.sleep(5)
        return {"status": "SUBMITTED_UNCONFIRMED", "publish_id": publish_id, "raw": last}
