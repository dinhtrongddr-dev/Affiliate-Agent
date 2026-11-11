"""Read/update the local .env without printing or logging secret values."""
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not Path(path).is_file():
        return values
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def update_env(path: Path, updates: dict[str, str]) -> None:
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    pending = dict(updates)
    for index, line in enumerate(lines):
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in pending:
                lines[index] = f"{key}={_clean(pending.pop(key))}"
    for key, value in pending.items():
        lines.append(f"{key}={_clean(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _clean(value) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").strip()
