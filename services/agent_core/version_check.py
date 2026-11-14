"""Tell the owner when GitHub has a newer version (checked in the background, never applied
automatically: updating is scripts\\update.cmd, which backs up and restarts safely)."""
import logging
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("affiliate_agent.version")

CHECK_EVERY = 6 * 3600
_state: dict = {"behind": 0, "checked": 0.0, "current": "", "error": ""}
_lock = threading.Lock()


def _git(root: Path, *args: str, timeout: int = 60) -> str:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()


def check(root: Path) -> dict:
    try:
        if not (Path(root) / ".git").exists():
            raise RuntimeError("not a git clone")
        branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "main"
        _git(root, "fetch", "--quiet", "origin", branch)
        behind = int(_git(root, "rev-list", "--count", f"HEAD..origin/{branch}") or 0)
        current = _git(root, "log", "-1", "--format=%h %cd", "--date=format:%d/%m %H:%M")
        with _lock:
            _state.update(behind=behind, checked=time.time(), current=current, error="")
    except Exception as exc:  # noqa: BLE001 - offline or no git: just stay silent
        with _lock:
            _state.update(checked=time.time(), error=str(exc)[:200])
    return status()


def status() -> dict:
    with _lock:
        return dict(_state)


def start_background(root: Path) -> None:
    def loop():
        while True:
            check(root)
            time.sleep(CHECK_EVERY)
    threading.Thread(target=loop, name="version-check", daemon=True).start()
