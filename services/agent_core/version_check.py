"""App version, new-version check and the in-app update button.

- local(): the running version (build number = commits on main, short hash, date, title).
- check(): asks GitHub what is new WITHOUT writing to .git (git ls-remote + the public GitHub API).
  The old check ran `git fetch` inside the app; the updater stops the app, and a fetch killed midway
  left .git/refs/remotes/origin/main.lock behind, so the next update failed.
- start_update(): runs scripts/update.ps1 hidden and detached from the app (the updater stops the
  app, so it must not be its child), logging to logs/update-*.log; update.ps1 reopens the app and
  writes logs/update-last.json, which the page shows after the restart.
"""
import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger("affiliate_agent.version")

CHECK_EVERY = 3 * 3600
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_state: dict = {"behind": 0, "checked": 0.0, "current": "", "error": "", "latest": "", "changes": [],
                "updating": False}
_lock = threading.Lock()
_local: dict = {}


def _git(root: Path, *args: str, timeout: int = 30) -> str:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=NO_WINDOW).stdout.strip()


def local(root: Path) -> dict:
    """The version this app runs (read once: it only changes with a restart)."""
    key = str(root)
    if key not in _local:
        info = {"build": 0, "hash": "", "date": "", "title": "", "branch": "main"}
        try:
            if (Path(root) / ".git").exists():
                info["build"] = int(_git(root, "rev-list", "--count", "HEAD") or 0)
                line = _git(root, "log", "-1", "--format=%h%x1f%cI%x1f%s")
                info["hash"], info["date"], info["title"] = (line.split("\x1f") + ["", "", ""])[:3]
                info["branch"] = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "main"
        except Exception as exc:  # noqa: BLE001
            log.debug("version info: %s", exc)
        _local[key] = info
    return dict(_local[key])


def _repo(root: Path) -> str:
    """owner/name of the GitHub remote, '' when it is not GitHub."""
    url = _git(root, "config", "--get", "remote.origin.url")
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url.strip())
    return m.group(1) if m else ""


def check(root: Path) -> dict:
    root = Path(root)
    try:
        if not (root / ".git").exists():
            raise RuntimeError("không phải bản git clone")
        mine = local(root)
        line = _git(root, "ls-remote", "origin", f"refs/heads/{mine['branch']}", timeout=30)
        latest = line.split()[0] if line else ""
        if not latest:
            raise RuntimeError("không đọc được bản mới nhất trên GitHub (mạng?)")
        head = _git(root, "rev-parse", "HEAD")
        behind, changes = 0, []
        if latest != head:
            repo = _repo(root)
            if repo:
                r = httpx.get(f"https://api.github.com/repos/{repo}/compare/{head}...{latest}",
                              headers={"Accept": "application/vnd.github+json"}, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    behind = int(data.get("ahead_by") or 0)
                    changes = [{"hash": c["sha"][:7], "title": c["commit"]["message"].splitlines()[0][:160],
                                "date": c["commit"]["committer"]["date"]}
                               for c in reversed(data.get("commits") or [])][:15]
            if not behind:
                # GitHub API unreachable or rate-limited: the commit is known locally only if fetched
                count = _git(root, "rev-list", "--count", f"HEAD..{latest}")
                behind = int(count) if count.isdigit() else 1
        with _lock:
            _state.update(behind=behind, latest=latest[:7], changes=changes, checked=time.time(), error="",
                          current=f"{mine['hash']} {mine['date'][:10]}")
    except Exception as exc:  # noqa: BLE001 - offline: stay quiet, try again later
        with _lock:
            _state.update(checked=time.time(), error=str(exc)[:200])
    return status()


def status() -> dict:
    with _lock:
        return json.loads(json.dumps(_state))


def last_result(root: Path) -> dict | None:
    path = Path(root) / "logs" / "update-last.json"
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def start_update(root: Path) -> dict:
    """Start scripts/update.ps1 hidden, outside the app's process tree."""
    root = Path(root)
    script = root / "scripts" / "update.ps1"
    if not script.is_file():
        raise RuntimeError("Thiếu scripts/update.ps1")
    with _lock:
        if _state.get("updating"):
            raise RuntimeError("Đang cập nhật rồi")
        _state["updating"] = True
    logfile = root / "logs" / f"update-{time.strftime('%Y%m%d-%H%M%S')}.log"
    logfile.parent.mkdir(parents=True, exist_ok=True)
    ps = (f"Start-Process powershell -WindowStyle Hidden -ArgumentList "
          f"'-NoProfile','-ExecutionPolicy','Bypass','-File','\"{script}\"','-LogFile','\"{logfile}\"'")
    # an intermediate powershell starts the updater and exits at once: the updater's parent is then
    # gone, so the app's "taskkill /T" during the update does not kill the updater itself
    try:
        _spawn(ps, root)
    except Exception:
        with _lock:
            _state["updating"] = False
        raise
    log.info("update started, log %s", logfile)
    return {"started": True, "log": str(logfile)}


def _spawn(ps: str, root: Path) -> None:
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                     cwd=str(root), creationflags=NO_WINDOW,   # (DETACHED_PROCESS stops Start-Process)
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True)


def start_background(root: Path) -> None:
    def loop():
        time.sleep(20)          # let the app finish starting first
        while True:
            check(root)
            time.sleep(CHECK_EVERY)
    threading.Thread(target=loop, name="version-check", daemon=True).start()
