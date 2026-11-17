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
        hidden: bool = False,
    ) -> dict:
        """hidden=True uploads the Reel as an unpublished draft on the Page (nobody sees it until
        the owner publishes it in Meta Business Suite); otherwise it is published."""
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
            # Meta refuses chunked transfer: send the bytes with an explicit Content-Length
            upload = client.post(upload_url, headers={**upload_headers, "Content-Length": str(file_size)},
                                 content=video_path.read_bytes())
            self._raise_for_meta(upload, "transfer_video")

            finish = client.post(
                edge,
                data={
                    "video_id": video_id,
                    "upload_phase": "finish",
                    "video_state": "DRAFT" if hidden else "PUBLISHED",
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
                processing = str((status.get("processing_phase") or {}).get("status") or "").lower()
                if hidden and (video_status == "ready" or processing in {"complete", "completed"}):
                    return {
                        "status": "DRAFT_SAVED_CONFIRMED",
                        "video_id": video_id,
                        "permalink": last_status.get("permalink_url"),
                        "meta_finish": finish_data,
                        "meta_status": last_status,
                        "hidden": True,
                    }
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


def exchange_for_page_token(graph_version: str, app_id: str, app_secret: str,
                            short_user_or_page_token: str, page_id: str,
                            transport: httpx.BaseTransport | None = None) -> str:
    """Short-lived Explorer token -> long-lived user token -> non-expiring Page token.

    Page tokens obtained from a long-lived user token do not expire (Meta docs:
    "Long-Lived Access Tokens"). Nothing is logged or printed.
    """
    base = f"https://graph.facebook.com/{graph_version}"
    with httpx.Client(timeout=30, transport=transport) as client:
        resp = client.get(f"{base}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": app_id,
            "client_secret": app_secret, "fb_exchange_token": short_user_or_page_token,
        })
        MetaReelsPublisher._raise_for_meta(resp, "exchange_token")
        long_user = resp.json().get("access_token")
        if not long_user:
            raise MetaReelsError("Meta returned no long-lived token")
        auth = {"Authorization": f"Bearer {long_user}"}
        me = client.get(f"{base}/me", params={"fields": "id"}, headers=auth)
        MetaReelsPublisher._raise_for_meta(me, "whoami")
        if str(me.json().get("id")) == str(page_id):
            # A Page token was pasted: the exchanged token is already the Page's.
            return long_user
        resp = client.get(f"{base}/me/accounts", params={"fields": "id,name,access_token",
                                                         "limit": 100},
                          headers={"Authorization": f"Bearer {long_user}"})
        MetaReelsPublisher._raise_for_meta(resp, "list_pages")
        for page in resp.json().get("data") or []:
            if str(page.get("id")) == str(page_id) and page.get("access_token"):
                return page["access_token"]
    raise MetaReelsError(
        "Page not found for this token. Generate the token as the *user* "
        "(Người dùng) in Graph API Explorer, not as the Page, and make sure the Page was granted.")
