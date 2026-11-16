"""Interface logic without Flet: date ranges, input parsing, list and log formatting.

Kept separate from the widgets so it can be tested without a window (N15).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

from fitbatch.core.activity_types import display_name
from fitbatch.core.export import (
    ExportEvent,
    FileFailed,
    FileSaved,
    FileSkipped,
    Waiting,
)
from fitbatch.core.models import Activity, ExportFormat
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


def local_time(activity: Activity) -> str:
    """Start time in this computer's time zone (D8)."""
    return activity.start_time.astimezone().strftime("%Y-%m-%d %H:%M")


def activity_row(activity: Activity) -> str:
    """One-line summary for the log: date, name, type, time, distance (F8)."""
    return (
        f"{local_time(activity)} · {activity.name or '–'} · {display_name(activity.type_key)} · "
        f"{format_duration(activity.duration_s)} · {format_distance(activity.distance_m)}"
    )


def total_distance_km(activities: Sequence[Activity]) -> str:
    meters = sum(a.distance_m or 0 for a in activities)
    return f"{meters / 1000:,.1f}"


def files_to_download(activities: Sequence[Activity], formats: Sequence[ExportFormat]) -> int:
    """Upper bound shown before the download: GPX/KML only for activities with GPS."""
    return sum(1 for a in activities for f in formats if a.has_gps or not f.needs_gps)


def filter_summary(
    include: list[str], exclude: list[str], chosen_types: int, all_types: int, tr: Translator
) -> str:
    """Collapsed filter card header, e.g. "name has “E1” · without “Zwift” · 4 types"."""
    parts: list[str] = []
    if include:
        parts.append(tr("filters.summary.include", value=", ".join(include)))
    if exclude:
        parts.append(tr("filters.summary.exclude", value=", ".join(exclude)))
    if all_types and chosen_types < all_types:
        parts.append(tr("filters.summary.types", count=chosen_types))
    return " · ".join(parts) or tr("filters.none_active")


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
