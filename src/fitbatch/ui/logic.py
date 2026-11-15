"""Interface logic without Flet: date ranges, input parsing, list and log formatting.

Kept separate from the widgets so it can be tested without a window (N15).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fitbatch.core.export import (
    ExportEvent,
    FileFailed,
    FileSaved,
    FileSkipped,
    Waiting,
)
from fitbatch.core.models import Activity
from fitbatch.i18n import Translator

EARLIEST = date(1990, 1, 1)
SHORTCUTS = ("7d", "30d", "year", "all")


def shortcut_range(name: str, today: date) -> tuple[date, date]:
    """F6 shortcuts; every range ends today."""
    if name == "7d":
        return today - timedelta(days=6), today
    if name == "30d":
        return today - timedelta(days=29), today
    if name == "year":
        return today - timedelta(days=364), today
    if name == "all":
        return EARLIEST, today
    raise ValueError(f"Unknown shortcut: {name}")


def parse_day(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def local_day_bounds(first: date, last: date) -> tuple[datetime, datetime]:
    """Whole days in this computer's time zone, from 00:00 to 23:59:59.999999 (F6, D8)."""
    start = datetime.combine(first, time.min).astimezone()
    end = datetime.combine(last, time.max).astimezone()
    return start, end


def split_phrases(text: str) -> list[str]:
    """Comma-separated phrases; spaces inside a phrase are kept ("FTP Ramp, E1")."""
    return [p.strip() for p in text.split(",") if p.strip()]


def remember_recent(recent: list[str], phrases: list[str], limit: int = 10) -> list[str]:
    """Most recent first, without duplicates (case-insensitive), at most ``limit`` (F7)."""
    result: list[str] = []
    for phrase in [*phrases, *recent]:
        if phrase.lower() not in {r.lower() for r in result}:
            result.append(phrase)
    return result[:limit]


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "–"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def format_distance(meters: float | None) -> str:
    if not meters:
        return "–"
    return f"{meters / 1000:.2f} km"


def activity_row(activity: Activity) -> str:
    """F8 list columns: date, name, type, time, distance."""
    when = activity.start_time.astimezone().strftime("%Y-%m-%d %H:%M")
    return (
        f"{when} · {activity.name or '–'} · {activity.type_key} · "
        f"{format_duration(activity.duration_s)} · {format_distance(activity.distance_m)}"
    )


def format_event(event: ExportEvent, tr: Translator) -> str | None:
    """One line of the live log (F14), or ``None`` for events shown elsewhere."""
    match event:
        case FileSaved(_, fmt, path, substitute):
            key = "log.saved_substitute" if substitute else "log.saved"
            return tr(key, fmt=fmt.value, file=path.name)
        case FileSkipped(_, fmt, reason):
            return tr("log.skipped", fmt=fmt.value, reason=tr(f"reason.{reason}"))
        case FileFailed(_, fmt, message):
            return tr("log.failed", fmt=fmt.value, error=message)
        case Waiting(seconds, _):
            return tr("log.waiting", seconds=int(seconds))
    return None
