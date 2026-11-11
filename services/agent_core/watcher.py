import logging
import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .media import VIDEO_EXTENSIONS, register_video

log = logging.getLogger("affiliate_agent.watcher")


class VideoInboxHandler(FileSystemEventHandler):
    def __init__(self, settings, on_registered: Callable[[str, bool], None] | None = None):
        self.settings = settings
        self.on_registered = on_registered
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def _queue(self, raw_path: str) -> None:
        path = Path(raw_path)
        if path.suffix.lower() not in VIDEO_EXTENSIONS or not path.is_file():
            return
        key = str(path.resolve()).casefold()
        with self._lock:
            if key in self._active:
                return
            self._active.add(key)

        def worker():
            try:
                inserted, info = register_video(path, self.settings)
                log.info("Video scan complete inserted=%s info=%s", inserted, info)
                if self.on_registered and info.get("job_id"):
                    self.on_registered(info["job_id"], inserted)
            except Exception:
                log.exception("Failed to register video: %s", path)
            finally:
                with self._lock:
                    self._active.discard(key)

        threading.Thread(target=worker, daemon=True).start()

    def on_created(self, event):
        if not event.is_directory:
            self._queue(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._queue(event.dest_path)


def watch_inbox(settings) -> None:
    settings.inbox.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(VideoInboxHandler(settings), str(settings.inbox), recursive=True)
    observer.start()
    log.info("Watching video inbox: %s", settings.inbox)
    try:
        while True:
            observer.join(1)
    except KeyboardInterrupt:
        log.info("Stopping inbox watcher")
    finally:
        observer.stop()
        observer.join()
