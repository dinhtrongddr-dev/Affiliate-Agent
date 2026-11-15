"""Show times in the owner's time zone (default Asia/Ho_Chi_Minh, UTC+7).

Times are stored as UTC ISO strings (db.now_iso); everything a person reads - pages and the
log file - is converted here, whatever time zone the Windows machine itself is set to.
"""
from datetime import datetime, timedelta, timezone, tzinfo

VIETNAM = timezone(timedelta(hours=7), "ICT")   # no daylight saving: a fixed offset is exact


def zone(name: str | None = None) -> tzinfo:
    if not name:
        try:
            from .settings import get_settings
            name = get_settings().timezone
        except Exception:  # noqa: BLE001
            name = "Asia/Ho_Chi_Minh"
    if name in ("Asia/Ho_Chi_Minh", "Asia/Saigon", "", None):
        return VIETNAM
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown name or no tz database on Windows
        return VIETNAM


def to_local(value, name: str | None = None) -> datetime | None:
    """ISO string / datetime / epoch seconds -> aware datetime in the owner's zone.
    A naive value is taken as UTC (that is how the app stores times)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            moment = datetime.fromtimestamp(float(value), timezone.utc)
        elif isinstance(value, datetime):
            moment = value
        else:
            moment = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone(name))


def fmt(value, pattern: str = "%d/%m/%Y %H:%M", name: str | None = None) -> str:
    """Jinja filter `localtime`: '' for empty, the raw text when it is not a time."""
    moment = to_local(value, name)
    if moment is None:
        return "" if value in (None, "") else str(value)
    return moment.strftime(pattern)
