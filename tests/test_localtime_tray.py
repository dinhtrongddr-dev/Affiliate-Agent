"""Vietnam time on every page and in the log; the tray launcher (no console window)."""
import logging
from unittest.mock import patch

from agent_core import localtime, tray


def test_utc_storage_is_shown_in_vietnam_time():
    assert localtime.fmt("2026-11-15T17:05:00+00:00") == "16/11/2026 00:05"     # crosses midnight
    assert localtime.fmt("2026-11-15T17:05:00") == "16/11/2026 00:05"           # naive = stored UTC
    assert localtime.fmt("2026-11-15T10:00:00+07:00", "%H:%M") == "10:00"       # already local
    assert localtime.fmt(None) == "" and localtime.fmt("không phải giờ") == "không phải giờ"
    assert localtime.to_local("2026-11-15T00:00:00Z").utcoffset().total_seconds() == 7 * 3600


def test_pages_use_the_localtime_filter(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from agent_core import db
    from agent_core.settings import Settings
    from agent_core.web import app as web
    s = Settings(_env_file=None, project_root=tmp_path, database_path=tmp_path / "t.sqlite3",
                 media_root=tmp_path / "media", muse_embedded=False)
    monkeypatch.setattr(web, "get_settings", lambda: s)
    db.init_db(s.database_path)
    tid = db.enqueue_task(s.database_path, "scan", None)
    with db.connect(s.database_path) as conn:
        conn.execute("UPDATE tasks SET updated_at='2026-11-15T17:05:09+00:00' WHERE id=?", (tid,))
    with patch.object(web, "setup_logging"), TestClient(web.create_app(start_worker=False)) as c:
        html = c.get("/tasks").text
    assert "16/11/2026 00:05:09" in html and "2026-11-15 17:05" not in html


def test_log_lines_use_vietnam_time(tmp_path):
    from agent_core import logging_setup
    logging_setup._configured = False
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logging_setup.setup_logging(tmp_path)
        handler = next(h for h in root.handlers if h not in before)
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
        record.created = 1794762309.0          # 2026-11-15 17:05:09 UTC
        assert handler.formatter.format(record).startswith("2026-11-16 00:05:09")
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
        logging_setup._configured = False


def test_tray_child_environment_and_single_instance():
    env = tray._env()
    assert env["PYTHONPATH"].endswith("services") and env["PYTHONUTF8"] == "1"
    assert env["PLAYWRIGHT_BROWSERS_PATH"].endswith("playwright")
    with patch.object(tray, "app_running", return_value=True), \
            patch.object(tray.webbrowser, "open") as browser, patch.object(tray.subprocess, "Popen") as popen:
        assert tray.main() == 0
    browser.assert_called_once_with(tray.URL)     # second click: only show the open app
    popen.assert_not_called()


def test_tray_reports_an_unexpected_stop():
    app = tray.App()
    shown = []

    class Proc:
        def wait(self):
            return 3221225477
    proc = Proc()
    app.proc = proc
    with patch.object(tray, "message", side_effect=lambda t, m, error=False: shown.append((t, m, error))):
        app._watch(proc)
    assert "Đã dừng" in app.state and shown and shown[0][2] and "Khởi động lại" in shown[0][1]
    app.quitting, shown[:] = True, []
    with patch.object(tray, "message", side_effect=lambda *a, **k: shown.append(a)):
        app._watch(proc)                          # quitting on purpose: no alarm
    assert not shown
