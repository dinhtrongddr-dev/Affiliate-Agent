from pathlib import Path
from time import monotonic, sleep
from urllib.parse import urlparse

import httpx


class MetaReelsError(RuntimeError):
    pass


class MetaReelsPublisher:
    """Facebook Page Reels uploader. Requires a Page token and pages_manage_posts."""

    def __init__(self, graph_version: str, page_id: str, page_access_token: str):
        if not graph_version or not page_id or not page_access_token:
            raise ValueError("Meta Graph version, Page ID and Page Access Token are required")
        self.base = f"https://graph.facebook.com/{graph_version}"
        self.page_id = page_id
        self.token = page_access_token

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def check_page(self) -> dict:
        with httpx.Client(timeout=30) as client:
            response = client.get(
                f"{self.base}/{self.page_id}",
                params={"fields": "id,name"},
                headers=self.auth_headers,
            )
            self._raise_for_meta(response, "check_page")
            return response.json()

    @staticmethod
    def _raise_for_meta(response: httpx.Response, stage: str) -> None:
        try:
            payload = response.json()
        except Exception:
            payload = {"body": response.text[:500]}
        if response.is_error or (isinstance(payload, dict) and payload.get("error")):
            raise MetaReelsError(f"{stage} failed (HTTP {response.status_code}): {payload}")

    def publish_reel(
        self,
        video_path: Path,
        description: str,
        title: str = "",
        poll_seconds: int = 300,
        poll_interval: int = 5,
    ) -> dict:
        video_path = Path(video_path)
        if not video_path.is_file():
            raise FileNotFoundError(video_path)
        file_size = video_path.stat().st_size
        if file_size <= 0:
            raise ValueError("Video file is empty")

        edge = f"{self.base}/{self.page_id}/video_reels"
        with httpx.Client(timeout=httpx.Timeout(120, connect=30)) as client:
            start = client.post(
                edge,
                data={"upload_phase": "start"},
                headers=self.auth_headers,
            )
            self._raise_for_meta(start, "start_upload")
            start_data = start.json()
            video_id = start_data.get("video_id")
            upload_url = start_data.get("upload_url")
            if not video_id or not upload_url:
                raise MetaReelsError(f"Meta start response missing video_id/upload_url: {start_data}")

            parsed = urlparse(upload_url)
            if parsed.scheme != "https" or parsed.hostname != "rupload.facebook.com":
                raise MetaReelsError("Meta returned an unexpected upload host; refusing to send the video")

            upload_headers = {
                "Authorization": f"OAuth {self.token}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream",
            }
            with video_path.open("rb") as stream:
                upload = client.post(
                    upload_url,
                    headers=upload_headers,
                    content=iter(lambda: stream.read(1024 * 1024), b""),
                )
            self._raise_for_meta(upload, "transfer_video")

            finish = client.post(
                edge,
                data={
                    "video_id": video_id,
                    "upload_phase": "finish",
                    "video_state": "PUBLISHED",
                    "description": description,
                    **({"title": title} if title else {}),
                },
                headers=self.auth_headers,
            )
            self._raise_for_meta(finish, "finish_publish")
            finish_data = finish.json()

            deadline = monotonic() + poll_seconds
            last_status = {}
            while monotonic() < deadline:
                status_response = client.get(
                    f"{self.base}/{video_id}",
                    params={"fields": "status,permalink_url"},
                    headers=self.auth_headers,
                )
                self._raise_for_meta(status_response, "poll_status")
                last_status = status_response.json()
                status = last_status.get("status") or {}
                video_status = str(status.get("video_status") or "").lower()
                publishing = status.get("publishing_phase") or {}
                publishing_status = str(publishing.get("status") or "").lower()

                if video_status in {"error", "upload_failed", "expired"}:
                    raise MetaReelsError(f"Meta processing failed for video {video_id}: {last_status}")
                if publishing_status in {"complete", "completed", "published", "success"}:
                    return {
                        "status": "PUBLISHED_CONFIRMED",
                        "video_id": video_id,
                        "permalink": last_status.get("permalink_url"),
                        "meta_finish": finish_data,
                        "meta_status": last_status,
                    }
                if last_status.get("permalink_url") and video_status in {"ready", "published"}:
                    return {
                        "status": "PUBLISHED_CONFIRMED",
                        "video_id": video_id,
                        "permalink": last_status.get("permalink_url"),
                        "meta_finish": finish_data,
                        "meta_status": last_status,
                    }
                sleep(poll_interval)

            return {
                "status": "SUBMITTED_UNCONFIRMED",
                "video_id": video_id,
                "meta_finish": finish_data,
                "meta_status": last_status,
            }
