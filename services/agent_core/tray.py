"""Run Affiliate Agent in the background with an icon in the Windows tray (no console window).

Started by the Desktop shortcut with pythonw.exe:  pythonw -m agent_core.tray  (cwd = services)
- starts the app (web UI + worker) as a hidden child process and opens the browser when ready;
- tray menu: open the app, video folder, log, restart, quit;
- if the app stops by itself, the icon says so (with the log) and offers a restart;
- a second click on the shortcut only opens the browser (one app at a time).
scripts\\start-app.cmd still runs the app in a console window, for troubleshooting.
"""
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[1]
ROOT = SERVICES.parent
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
if sys.stdout is None:   # pythonw: no console; keep prints and tracebacks in a file
    sys.stdout = sys.stderr = open(LOGS / "tray.log", "a", encoding="utf-8", buffering=1)

PORT = 8765
URL = f"http://127.0.0.1:{PORT}"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _python() -> str:
    """Console-less python for the child would hide crashes from nothing; python.exe with
    CREATE_NO_WINDOW keeps the window hidden and the exit code readable."""
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv if venv.is_file() else Path(sys.executable).with_name("python.exe"))


def _env() -> dict:
    env = dict(os.environ)
    tmp = ROOT / "cache" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env.update(PYTHONPATH=str(SERVICES), PYTHONUTF8="1", TEMP=str(tmp), TMP=str(tmp),
               PLAYWRIGHT_BROWSERS_PATH=str(ROOT / "cache" / "playwright"))
    return env


def app_running(timeout: float = 2) -> bool:
    import httpx
    try:
        r = httpx.get(URL + "/api/status", timeout=timeout, trust_env=False)
        return r.status_code == 200 and "worker" in r.json()
    except Exception:  # noqa: BLE001
        return False


def message(title: str, text: str, error: bool = False) -> None:
    """A Windows message box (works without a console)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 if error else 0x40)
    except Exception:  # noqa: BLE001
        print(f"{title}: {text}")


class App:
    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.quitting = False
        self.icon = None
        self.state = "Đang khởi động…"

    # ---------------------------------------------------------------- child process
    def start(self, open_browser: bool = True) -> None:
        log = open(LOGS / "app-console.log", "a", encoding="utf-8")
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start =====\n")
        log.flush()
        self.proc = subprocess.Popen([_python(), "-m", "agent_core.cli", "ui", "--no-browser"],
                                     cwd=str(ROOT), env=_env(), stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW)
        self._set_state("Đang khởi động…")
        threading.Thread(target=self._wait_ready, args=(open_browser,), daemon=True).start()
        threading.Thread(target=self._watch, args=(self.proc,), daemon=True).start()

    def _wait_ready(self, open_browser: bool) -> None:
        for _ in range(240):                      # up to 2 minutes (first start loads the voice model)
            if self.proc is None or self.proc.poll() is not None:
                return
            if app_running(1):
                self._set_state("Đang chạy")
                if open_browser:
                    webbrowser.open(URL)
                return
            time.sleep(0.5)
        self._set_state("Khởi động chậm, xem nhật ký")

    def _watch(self, proc: subprocess.Popen) -> None:
        code = proc.wait()
        if self.quitting or proc is not self.proc:
            return
        self._set_state(f"Đã dừng (mã {code})")
        tail = self.log_tail()
        if self.icon is not None:
            try:
                self.icon.notify("App đã dừng. Bấm biểu tượng → Khởi động lại, hoặc xem Nhật ký.",
                                 "Affiliate Agent")
            except Exception:  # noqa: BLE001
                pass
        message("Affiliate Agent đã dừng",
                f"App dừng bất ngờ (mã {code}).\n\nDòng cuối nhật ký:\n{tail}\n\n"
                "Bấm biểu tượng Affiliate Agent ở khay hệ thống → Khởi động lại.\n"
                "Nếu lặp lại: chạy scripts\\setup.ps1 để sửa cài đặt.", error=True)

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()

    @staticmethod
    def log_tail(lines: int = 6) -> str:
        path = LOGS / "app-console.log"
        try:
            return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])[-900:]
        except OSError:
            return "(chưa có nhật ký)"

    # ---------------------------------------------------------------- tray
    def _set_state(self, text: str) -> None:
        self.state = text
        if self.icon is not None:
            self.icon.title = f"Affiliate Agent - {text}"
            try:
                self.icon.update_menu()
            except Exception:  # noqa: BLE001
                pass

    def restart(self, *_):
        self.stop()
        self.start(open_browser=False)

    def quit(self, *_):
        self.quitting = True
        self.stop()
        if self.icon is not None:
            self.icon.stop()

    def run(self) -> None:
        import pystray
        from PIL import Image
        picture = ROOT / "assets" / "app.png"
        image = Image.open(picture) if picture.is_file() else Image.new("RGB", (64, 64), (238, 77, 45))

        def open_folder(path: Path):
            return lambda *_: os.startfile(str(path)) if path.exists() else None  # noqa: S606

        media = self._media_root()
        menu = pystray.Menu(
            pystray.MenuItem("Mở Affiliate Agent", lambda *_: webbrowser.open(URL), default=True),
            pystray.MenuItem(lambda _: f"Trạng thái: {self.state}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Thư mục video", open_folder(media / "outbox" if media else ROOT)),
            pystray.MenuItem("Nhật ký", open_folder(LOGS / "app-console.log")),
            pystray.MenuItem("Khởi động lại", self.restart),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Thoát (tắt app)", self.quit),
        )
        self.icon = pystray.Icon("affiliate-agent", image, "Affiliate Agent - Đang khởi động…", menu)
        self.start(open_browser=True)
        self.icon.run()

    @staticmethod
    def _media_root() -> Path | None:
        env = ROOT / ".env"
        try:
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.startswith("AA_MEDIA_ROOT="):
                    return Path(line.split("=", 1)[1].strip().strip('"'))
        except OSError:
            pass
        return None


def main() -> int:
    if not (ROOT / ".venv" / "Scripts" / "python.exe").is_file():
        message("Affiliate Agent", "Chưa cài xong (thiếu .venv).\nChạy lại lệnh cài đặt: scripts\\setup.ps1",
                error=True)
        return 1
    if app_running():               # already open (tray or console): just show it
        webbrowser.open(URL)
        return 0
    check = subprocess.run([_python(), "-c", "import agent_core.web.app"], cwd=str(ROOT), env=_env(),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW)
    if check.returncode != 0:
        detail = (check.stderr or check.stdout).strip().splitlines()[-3:]
        message("Affiliate Agent - thiếu thư viện",
                "Thư viện chưa đủ hoặc bị lỗi:\n" + "\n".join(detail) +
                "\n\nSửa bằng: powershell -ExecutionPolicy Bypass -File scripts\\setup.ps1", error=True)
        return 1
    try:
        import pystray  # noqa: F401
    except ImportError:              # old install without the tray library: run without a tray
        message("Affiliate Agent", "Thiếu thư viện pystray: app vẫn chạy nhưng không có biểu tượng khay.\n"
                "Chạy scripts\\update.cmd để cài đủ.")
        app = App()
        app.start(open_browser=True)
        app.proc.wait()
        return 0
    App().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
