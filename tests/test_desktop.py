import builtins
import socket

from agent_core import desktop


def test_focus_existing_false_when_no_window(monkeypatch):
    monkeypatch.setattr(desktop, "FOCUS_PORT", _free_port())
    assert desktop.focus_existing(0.2) is False


def test_without_pywebview_falls_back_to_browser(monkeypatch):
    monkeypatch.setattr(desktop, "FOCUS_PORT", _free_port())
    opened = []
    monkeypatch.setattr(desktop.webbrowser, "open", opened.append)
    real_import = builtins.__import__

    def no_webview(name, *args, **kwargs):
        if name == "webview":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_webview)
    assert desktop.main("http://127.0.0.1:1/") == 0
    assert opened == ["http://127.0.0.1:1/"]


def test_second_start_only_focuses(monkeypatch):
    port = _free_port()
    monkeypatch.setattr(desktop, "FOCUS_PORT", port)
    server = socket.socket()
    server.bind(("127.0.0.1", port))
    server.listen(1)
    import threading

    def answer():
        conn, _ = server.accept()
        with conn:
            assert conn.recv(16) == b"show"
            conn.sendall(b"ok")

    t = threading.Thread(target=answer)
    t.start()
    try:
        assert desktop.focus_existing(2) is True
    finally:
        t.join(5)
        server.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
