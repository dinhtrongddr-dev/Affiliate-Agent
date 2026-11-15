"""App version, read-only new-version check, and the in-app update button."""
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_core import version_check as vc

ROOT = Path(__file__).resolve().parents[1]


def test_local_version_reads_the_running_commit():
    vc._local.clear()
    info = vc.local(ROOT)
    assert info["build"] > 10 and len(info["hash"]) >= 7 and info["date"][:2] == "20" and info["title"]


def test_github_repo_from_remote_url():
    for url in ("https://github.com/dinhtrongddr-dev/Affiliate-Agent.git", "git@github.com:dinhtrongddr-dev/Affiliate-Agent.git",
                "https://github.com/dinhtrongddr-dev/Affiliate-Agent"):
        with patch.object(vc, "_git", return_value=url):
            assert vc._repo(ROOT) == "dinhtrongddr-dev/Affiliate-Agent"


def test_check_never_writes_to_git_and_lists_changes():
    calls = []

    def fake_git(root, *args, timeout=30):
        calls.append(args)
        return {"ls-remote": "b" * 40 + "\trefs/heads/main", "rev-parse": "a" * 40,
                "config": "https://github.com/o/r.git"}.get(args[0], "")
    api = MagicMock(status_code=200)
    api.json.return_value = {"ahead_by": 2, "commits": [
        {"sha": "1" * 40, "commit": {"message": "feat: first\n\nbody", "committer": {"date": "2026-11-15T10:00:00Z"}}},
        {"sha": "2" * 40, "commit": {"message": "fix: second", "committer": {"date": "2026-11-15T11:00:00Z"}}}]}
    vc._local[str(ROOT)] = {"build": 500, "hash": "aaaaaaa", "date": "2026-11-15T09:00:00+07:00",
                            "title": "x", "branch": "main"}
    try:
        with patch.object(vc, "_git", side_effect=fake_git), patch.object(vc.httpx, "get", return_value=api):
            st = vc.check(ROOT)
    finally:
        vc._local.clear()
    assert st["behind"] == 2 and [c["title"] for c in st["changes"]] == ["fix: second", "feat: first"]
    assert not any(a[0] in ("fetch", "pull", "merge") for a in calls)        # read-only: no lock files


def test_start_update_is_detached_and_runs_once(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "update.ps1").write_text("# x")
    vc._state["updating"] = False
    with patch.object(vc.subprocess, "Popen") as popen:
        out = vc.start_update(tmp_path)
        with pytest.raises(RuntimeError):
            vc.start_update(tmp_path)                                           # second click
    vc._state["updating"] = False
    args = popen.call_args[0][0]
    assert args[0] == "powershell" and "Start-Process powershell" in args[-1] and "-LogFile" in args[-1]
    # no DETACHED_PROCESS: with it the intermediate powershell's Start-Process silently does nothing
    assert popen.call_args[1]["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert out["log"].endswith(".log")


def test_version_api_and_sidebar(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from agent_core.settings import Settings
    from agent_core.web import app as web
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                 media_root=tmp_path / "media", muse_embedded=False)
    monkeypatch.setattr(web, "get_settings", lambda: s)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "update-last.json").write_text(json.dumps(
        {"ok": False, "message": "pip install loi", "at": "2026-11-15T15:00:00Z"}), encoding="utf-8")
    with patch.object(web, "setup_logging"), TestClient(web.create_app(start_worker=False)) as c:
        v = c.get("/api/version").json()
        assert v["last_update"]["message"] == "pip install loi" and "local" in v and "remote" in v
        assert 'id="update-btn"' in c.get("/").text and 'id="version"' in c.get("/").text
        with patch.object(vc, "start_update", return_value={"started": True, "log": "x"}):
            r = c.post("/api/version/update", headers={"X-Requested-With": "affiliate-agent"})
        assert r.json()["started"]
