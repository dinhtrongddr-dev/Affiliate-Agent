"""Video generation providers behind one interface.

Backends:
- muse2api : a self-hosted OpenAI-style gateway in front of the user's own muse.ai account
             (e.g. github.com/czg86389-hub/muse2api, run separately like ChatGPT2API).
             POST /v1/videos, GET /v1/videos/{id}; POST /v1/images/tasks, GET /v1/images/tasks/{id}.
- http     : the older generic contract (POST {base}/generations, GET {base}/generations/{id}).

Only what a backend really exposes is implemented; everything else raises NotSupported.
The app never manages several Muse accounts, proxies or quota rotation."""
import base64
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

DONE = {"succeeded", "completed", "success", "done", "finished"}
FAILED = {"failed", "error", "cancelled", "canceled", "rejected", "expired"}


class ProviderError(RuntimeError):
    pass


class NotSupported(ProviderError):
    pass


@dataclass
class GenStatus:
    state: str  # queued | running | succeeded | failed
    url: str = ""
    progress: float | None = None
    error: str = ""
    raw: dict = field(default_factory=dict)


def _state(raw: str) -> str:
    value = str(raw or "").lower()
    if value in DONE:
        return "succeeded"
    if value in FAILED:
        return "failed"
    return "queued" if value in {"queued", "pending", "submitted", ""} else "running"


def data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode()}"


class VideoProvider:
    name = "none"
    capabilities: frozenset = frozenset()

    def __init__(self, settings, transport=None):
        self.settings = settings
        self.base = (settings.videogen_base_url or "").rstrip("/")
        self.key = settings.videogen_api_key.get_secret_value()
        self.transport = transport

    def _client(self, timeout: float = 60) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        return httpx.Client(base_url=self.base, headers=headers, timeout=timeout,
                            transport=self.transport, follow_redirects=True)

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def check_connection(self) -> dict:
        raise NotSupported(f"{self.name}: check_connection")

    def check_quota(self) -> dict:
        raise NotSupported(f"{self.name}: không có API đọc hạn mức")

    def submit_image(self, prompt: str, refs: list[Path], idem_key: str) -> str:
        raise NotSupported(f"{self.name}: tạo ảnh")

    def submit_video(self, prompt: str, refs: list[Path], duration: int, idem_key: str) -> str:
        raise NotSupported(f"{self.name}: tạo video")

    def status(self, kind: str, remote_id: str) -> GenStatus:
        raise NotSupported(f"{self.name}: trạng thái")

    def cancel(self, kind: str, remote_id: str) -> None:
        raise NotSupported(f"{self.name}: huỷ task")

    def download(self, url: str, out: Path) -> Path:
        """Gateway-hosted media gets the API key; other hosts get no credentials."""
        same_host = urlparse(url).netloc in ("", urlparse(self.base).netloc)
        target = url if urlparse(url).scheme else f"{self.base}/{url.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self.key}"} if (self.key and same_host) else {}
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".part")
        with httpx.Client(timeout=300, follow_redirects=True, transport=self.transport) as c:
            with c.stream("GET", target, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise ProviderError(f"Tải file lỗi HTTP {resp.status_code}")
                size = 0
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > 300 * 1024 * 1024:
                            raise ProviderError("File quá lớn (>300MB)")
                        fh.write(chunk)
        tmp.replace(out)
        return out


def _raise(resp: httpx.Response, what: str) -> None:
    if resp.status_code >= 400:
        try:
            body = resp.json()
            message = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) \
                else body.get("error") or body.get("detail") or body
        except Exception:  # noqa: BLE001
            message = resp.text[:300]
        raise ProviderError(f"{what}: HTTP {resp.status_code} {str(message)[:300]}")


class Muse2APIProvider(VideoProvider):
    name = "muse2api"
    capabilities = frozenset({"connection", "image", "video", "references"})
    DURATIONS = (5, 6, 8, 10)

    def check_connection(self) -> dict:
        with self._client(20) as c:
            resp = c.get("/v1/models")
        _raise(resp, "Kết nối muse2api")
        models = [m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict)]
        return {"ok": True, "models": models[:20]}

    def submit_image(self, prompt, refs, idem_key):
        body = {"prompt": prompt, "size": "9:16", "n": 1}
        if refs:
            body["images"] = [{"url": data_url(p)} for p in refs]
        with self._client(120) as c:
            resp = c.post("/v1/images/tasks", json=body, headers={"Idempotency-Key": idem_key})
        _raise(resp, "Gửi task ảnh")
        task_id = resp.json().get("id")
        if not task_id:
            raise ProviderError(f"muse2api không trả id: {resp.text[:200]}")
        return str(task_id)

    def submit_video(self, prompt, refs, duration, idem_key):
        duration = min(self.DURATIONS, key=lambda d: abs(d - duration))
        body = {"prompt": prompt, "duration": duration, "size": "9:16"}
        if refs:
            body["images"] = [{"url": data_url(p)} for p in refs]
        with self._client(120) as c:
            resp = c.post("/v1/videos", json=body, headers={"Idempotency-Key": idem_key})
        _raise(resp, "Gửi task video")
        task_id = resp.json().get("id")
        if not task_id:
            raise ProviderError(f"muse2api không trả id: {resp.text[:200]}")
        return str(task_id)

    def status(self, kind, remote_id):
        path = f"/v1/videos/{remote_id}" if kind == "video" else f"/v1/images/tasks/{remote_id}"
        with self._client(30) as c:
            resp = c.get(path)
        _raise(resp, "Đọc trạng thái")
        raw = resp.json()
        result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
        data = raw.get("data") if isinstance(raw.get("data"), list) else []
        url = (result.get("url") or raw.get("url") or raw.get("video_url")
               or (data[0].get("url") if data and isinstance(data[0], dict) else "") or "")
        error = raw.get("error")
        if isinstance(error, dict):
            error = error.get("message")
        return GenStatus(_state(raw.get("status")), str(url), raw.get("progress"),
                         str(error or ""), raw)


class HttpProvider(VideoProvider):
    """Legacy generic contract kept for existing configurations."""
    name = "http"
    capabilities = frozenset({"video"})

    def submit_video(self, prompt, refs, duration, idem_key):
        with self._client(60) as c:
            resp = c.post("/generations", json={
                "prompt": prompt, "aspect_ratio": "9:16", "duration": round(duration),
                "model": self.settings.videogen_model or None},
                headers={"Idempotency-Key": idem_key})
        _raise(resp, "Gửi task video")
        job_id = resp.json().get("id")
        if not job_id:
            raise ProviderError(f"Generator không trả id: {resp.text[:200]}")
        return str(job_id)

    def status(self, kind, remote_id):
        with self._client(30) as c:
            resp = c.get(f"/generations/{remote_id}")
        _raise(resp, "Đọc trạng thái")
        raw = resp.json()
        url = raw.get("video_url") or (raw.get("output") or {}).get("url") or ""
        return GenStatus(_state(raw.get("status")), str(url), raw.get("progress"),
                         str(raw.get("error") or ""), raw)


PROVIDERS = {"muse2api": Muse2APIProvider, "http": HttpProvider}


def get_provider(settings, transport=None) -> VideoProvider | None:
    name = (settings.videogen_provider or "none").lower()
    cls = PROVIDERS.get(name)
    if not cls or not settings.videogen_base_url:
        return None
    return cls(settings, transport=transport)
