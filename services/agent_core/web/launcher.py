"""Reserve the UI port before creating any application workers."""
import errno
import socket
import threading
import webbrowser

import httpx
import uvicorn


def _is_existing_app(url):
    try:
        response = httpx.get(url + '/api/status', timeout=2, trust_env=False)
        data = response.json() if response.status_code == 200 else None
        return isinstance(data, dict) and isinstance(data.get('worker'), bool) and 'task' in data
    except (httpx.HTTPError, ValueError):
        return False


def serve(app_factory, host, port, open_browser=True):
    url = f'http://{host}:{port}'
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind((host, port))
        except OSError as exc:
            if (exc.errno in {errno.EADDRINUSE, errno.EACCES} or getattr(exc, 'winerror', None) in {10048, 10013}) and _is_existing_app(url):
                if open_browser:
                    webbrowser.open(url)
                return
            raise RuntimeError(f'Không mở được app tại {host}:{port}; kiểm tra ứng dụng đang dùng cổng này.') from exc
        app = app_factory()
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level='warning'))
        timer = None
        if open_browser:
            timer = threading.Timer(1.5, lambda: webbrowser.open(url))
            timer.daemon = True
            timer.start()
        try:
            server.run(sockets=[listener])
        finally:
            if timer:
                timer.cancel()
    finally:
        listener.close()
