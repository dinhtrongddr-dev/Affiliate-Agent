"""Embedded muse2api gateway: started, watched and stopped by AffiliateAgent itself.

The gateway code lives in services/muse_gateway (MIT, see UPSTREAM.md). It runs as a child
process of the app on 127.0.0.1 only. Its data (account session, tasks, media, Chrome
profile) is kept in state/muse2api, which is git-ignored."""
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("affiliate_agent.muse_gateway")

GATEWAY_DIR = Path(__file__).resolve().parents[2] / "muse_gateway"
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def enabled(settings) -> bool:
    return (settings.videogen_provider or "").lower() == "muse2api" and settings.muse_embedded


def base_url(settings) -> str:
    return f"http://127.0.0.1:{settings.muse_gateway_port}"


def data_dir(settings) -> Path:
    return settings.project_root / "state" / "muse2api"


def find_chrome() -> str:
    for path in CHROME_CANDIDATES:
        if path and Path(path).is_file():
            return path
    return shutil.which("chrome") or shutil.which("msedge") or ""


def ensure_config(settings) -> None:
    """Embedded mode: base URL points at the child process; create an API key once."""
    from ..env_file import update_env
    from pydantic import SecretStr

    updates = {}
    url = base_url(settings)
    if settings.videogen_base_url != url:
        settings.videogen_base_url = url
        updates["AA_VIDEOGEN_BASE_URL"] = url
    if not settings.videogen_api_key.get_secret_value():
        key = "m2a_" + secrets.token_hex(24)
        settings.videogen_api_key = SecretStr(key)
        updates["AA_VIDEOGEN_API_KEY"] = key
    if updates:
        update_env(settings.project_root / ".env", updates)


def healthy(settings, timeout: float = 3.0) -> bool:
    try:
        resp = httpx.get(f"{base_url(settings)}/v1/models", timeout=timeout, headers={
            "Authorization": f"Bearer {settings.videogen_api_key.get_secret_value()}"})
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


class GatewayProcess:
    def __init__(self, settings):
        self.settings = settings
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self.last_start = 0.0
        self.log_file = None
        self.error = ""

    def _env(self) -> dict:
        s = self.settings
        home = data_dir(s)
        (home / "profiles").mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("MUSE2API_")}
        env.update({
            "PYTHONUTF8": "1",
            "MUSE2API_HOME": str(home),
            "MUSE2API_PROFILE_ROOT": str(home / "profiles"),
            "MUSE2API_HOST": "127.0.0.1",
            "MUSE2API_PORT": str(s.muse_gateway_port),
            "MUSE2API_CDP_PORT": str(s.muse_cdp_port),
            "MUSE2API_KEY": s.videogen_api_key.get_secret_value(),
            "MUSE2API_CONCURRENCY": "1",
            "MUSE2API_CORS_ORIGINS": "",
            "MUSE2API_REF_IMAGE_LOCAL_FILES": "0",
            "MUSE2API_REF_IMAGE_PRIVATE_FETCH": "0",
        })
        chrome = find_chrome()
        if chrome:
            env["MUSE2API_CHROMIUM"] = chrome
        return env

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, wait: float = 40.0) -> bool:
        """Start (or adopt an already healthy gateway on the port). Returns health."""
        with self.lock:
            if not enabled(self.settings):
                return False
            ensure_config(self.settings)
            if self.running() or healthy(self.settings):
                return True
            if not (GATEWAY_DIR / "app.py").is_file():
                self.error = f"Thiếu mã gateway ở {GATEWAY_DIR}"
                log.error(self.error)
                return False
            self.last_start = time.monotonic()
            self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
            self.log_file = open(self.settings.logs_dir / "muse-gateway.log", "ab")  # noqa: SIM115
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP \
                if sys.platform == "win32" else 0
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
                 "--port", str(self.settings.muse_gateway_port), "--log-level", "warning"],
                cwd=str(GATEWAY_DIR), env=self._env(), stdout=self.log_file,
                stderr=subprocess.STDOUT, creationflags=flags)
            log.info("Muse gateway starting (pid %s) on %s", self.proc.pid, base_url(self.settings))
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not self.running():
                self.error = "Gateway thoát ngay khi khởi động (xem logs/muse-gateway.log)"
                log.error(self.error)
                return False
            if healthy(self.settings):
                self.error = ""
                sync_session(self.settings)
                return True
            time.sleep(0.5)
        self.error = "Gateway chưa phản hồi sau khi khởi động"
        return False

    def ensure_alive(self) -> None:
        """Called by the worker loop: restart a crashed gateway (at most once a minute)."""
        if not enabled(self.settings) or self.running():
            return
        if time.monotonic() - self.last_start < 60:
            return
        if self.proc is not None:
            log.warning("Muse gateway exited (code %s); restarting", self.proc.returncode)
        self.start(wait=20)

    def stop(self) -> None:
        with self.lock:
            proc, self.proc = self.proc, None
            if proc and proc.poll() is None:
                if sys.platform == "win32":
                    # Kill the whole tree: the gateway's headless Chrome must go too.
                    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                   capture_output=True, check=False)
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if self.log_file:
                self.log_file.close()
                self.log_file = None


def sync_session(settings) -> dict | None:
    """Restore saved profile routes, or the older single Muse session."""
    from . import muse_profiles, muse_session
    try:
        if muse_profiles.load(settings):
            return muse_profiles.push_to_gateway(settings)
        if not muse_session.load(settings):
            return None
        resp = httpx.get(f"{base_url(settings)}/admin/accounts", timeout=10, headers={
            "Authorization": f"Bearer {settings.videogen_api_key.get_secret_value()}"})
        if resp.status_code == 200 and resp.json().get("accounts"):
            return None
        return muse_session.push_to_gateway(settings)
    except Exception as exc:  # noqa: BLE001 - never block start-up on this
        log.warning("Could not push the Muse session to the gateway: %s", exc)
        return None


def account_status(settings) -> dict:
    """Safe account summary from the gateway (no cookie values)."""
    try:
        url = settings.videogen_base_url.rstrip("/") or base_url(settings)
        resp = httpx.get(f"{url}/admin/accounts", timeout=5, headers={
            "Authorization": f"Bearer {settings.videogen_api_key.get_secret_value()}"})
    except httpx.HTTPError as exc:
        return {"reachable": False, "error": str(exc)[:200]}
    if resp.status_code != 200:
        return {"reachable": True, "error": f"HTTP {resp.status_code}"}
    body = resp.json()
    accounts = []
    for account in body.get("accounts") or []:
        item = {k: account.get(k) for k in ("id", "label", "enabled", "status", "healthy", "error",
                                           "last_error", "expires_at", "profile_id", "profile_revision",
                                           "ok", "note", "checked_at", "cookies_exp", "proxy_status")}
        route = account.get("proxy") or {}
        item["proxy_server"] = route.get("server")
        item["proxy_authenticated"] = bool(route.get("username") or route.get("has_password"))
        accounts.append(item)
    return {"reachable": True, "accounts": accounts, "stats": body.get("stats") or {}}


_INSTANCE: GatewayProcess | None = None


def instance(settings) -> GatewayProcess:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = GatewayProcess(settings)
    _INSTANCE.settings = settings
    return _INSTANCE
