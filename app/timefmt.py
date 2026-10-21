"""
Display time zone for the whole app (default IST, UTC+05:30).

Graph and the database store times in UTC; everything shown to people (pages, MOM
documents, dates used as meeting keys) is converted here. Configure with
DISPLAY_TZ_NAME / DISPLAY_TZ_OFFSET_MINUTES in .env. A fixed offset is used on purpose:
IST has no daylight saving, and it needs no time-zone database (absent on Windows).
"""
from datetime import datetime, timedelta, timezone

from config import Config

DISPLAY_TZ = timezone(
    timedelta(minutes=Config.DISPLAY_TZ_OFFSET_MINUTES), Config.DISPLAY_TZ_NAME
)
TZ_NAME = Config.DISPLAY_TZ_NAME


def to_local(value) -> datetime | None:
    """UTC ISO string ('2026-10-21T10:30:00[.0000000][Z]') or naive-UTC datetime -> local."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TZ)


def local_date(value) -> str:
    """'YYYY-MM-DD' in the display zone (date-only strings pass through unchanged)."""
    if isinstance(value, str) and len(value) == 10:
        return value
    local = to_local(value)
    return local.strftime("%Y-%m-%d") if local else ""


def local_hm(value) -> str:
    """'HH:MM' in the display zone."""
    local = to_local(value)
    return local.strftime("%H:%M") if local else ""


def local_today() -> datetime:
    return datetime.now(DISPLAY_TZ)


def local_day_bounds_utc(day: datetime) -> tuple[datetime, datetime]:
    """Naive-UTC [start, end] of a local calendar day (for filtering UTC-stored rows)."""
    start_local = datetime(day.year, day.month, day.day, tzinfo=DISPLAY_TZ)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start, start + timedelta(days=1) - timedelta(seconds=1)
