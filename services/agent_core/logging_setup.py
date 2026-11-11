import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> None:
    """Console + rotating file log (logs/agent.log). Safe to call repeatedly."""
    global _configured
    if _configured:
        return
    logs_dir = Path(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = RotatingFileHandler(
        logs_dir / "agent.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file_handler)
    for noisy in ("httpx", "httpcore", "watchdog", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def tail_log(logs_dir: Path, lines: int = 200) -> str:
    path = Path(logs_dir) / "agent.log"
    if not path.is_file():
        return ""
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - 200_000))
        data = stream.read().decode("utf-8", "replace")
    return "\n".join(data.splitlines()[-lines:])
