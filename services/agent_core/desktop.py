"""The app's own window (Edge WebView2 through pywebview) instead of a tab in Chrome.

Run:  pythonw -m agent_core.desktop  (the tray starts it; cwd = repo root, PYTHONPATH = services)
- one window at a time: a second start asks the open window to come to the front and exits;
- closing the window only closes the window; the app keeps running in the tray, and the
  window's WebView2 memory is freed until it is opened again;
- without pywebview / WebView2 it falls back to the default browser.
"""
import socket
import sys
import threading
import webbrowser

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
FOCUS_PORT = 8766          # local-only: "show" brings the open window to the front
TITLE = "Affiliate Agent"


def focus_existing(timeout: float = 0.5, path: str = "") -> bool:
    """True when a window is already open (and was asked to come to the front, at `path`)."""
    try:
        with socket.create_connection(("127.0.0.1", FOCUS_PORT), timeout=timeout) as s:
            s.sendall(b"show" + (b" " + path.encode("utf-8") if path else b""))
            return s.recv(8) == b"ok"
    except OSError:
        return False


def _serve_focus(window, listener: socket.socket) -> None:
    while True:
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            try:
                msg = conn.recv(512).decode("utf-8", "replace")
                if msg.startswith("show"):
                    path = msg[4:].strip()
                    if path.startswith("/") and not path.startswith("//"):
                        window.load_url(URL + path)
                    window.restore()
                    window.show()
                    window.on_top = True      # reliable way to raise it on Windows
                    window.on_top = False
                conn.sendall(b"ok")
            except Exception:  # noqa: BLE001
                pass


def _app_icon() -> str | None:
    from pathlib import Path
    ico = Path(__file__).resolve().parents[2] / "assets" / "app.ico"
    return str(ico) if ico.is_file() else None


def _own_taskbar_entry() -> None:
    """Group the window under Affiliate Agent's icon in the taskbar, not python.exe's."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AffiliateAgent.App")
    except Exception:  # noqa: BLE001 - not Windows
        pass


def main(url: str = URL) -> int:
    path = url[len(URL):] if url.startswith(URL) else ""
    if focus_existing(path=path):
        return 0
    _own_taskbar_entry()
    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        return 0
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", FOCUS_PORT))
    except OSError:            # another window won the race
        return 0 if focus_existing(2) else _fallback(url)
    listener.listen(4)
    webview.settings["ALLOW_DOWNLOADS"] = True   # "Tải video" links save to Downloads (off by default)
    window = webview.create_window(TITLE, url, width=1280, height=860, min_size=(900, 600),
                                   text_select=True)
    threading.Thread(target=_serve_focus, args=(window, listener), daemon=True).start()
    try:
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=_storage_path(), icon=_app_icon())
    except Exception as exc:  # noqa: BLE001  - no WebView2 runtime, broken install, ...
        print("Desktop window failed, using the browser:", exc, file=sys.stderr)
        listener.close()
        return _fallback(url)
    listener.close()
    return 0


def _storage_path() -> str:
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "cache" / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def _fallback(url: str) -> int:
    webbrowser.open(url)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else URL))
