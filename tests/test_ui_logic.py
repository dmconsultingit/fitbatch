from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from fitbatch.core.export import FileFailed, FileSaved, FileSkipped, Waiting
from fitbatch.core.models import Activity, ExportFormat
from fitbatch.i18n import Translator, all_keys, missing_translations
from fitbatch.ui.logic import (
    EARLIEST,
    activity_row,
    format_distance,
    format_duration,
    format_event,
    local_day_bounds,
    parse_day,
    remember_recent,
    shortcut_range,
    split_phrases,
)

TODAY = date(2026, 11, 15)


@pytest.mark.parametrize(
    ("name", "days"),
    [("7d", 7), ("30d", 30), ("year", 365)],
)
def test_shortcuts_include_today(name: str, days: int) -> None:
    first, last = shortcut_range(name, TODAY)
    assert last == TODAY
    assert (last - first) == timedelta(days=days - 1)


def test_shortcut_all() -> None:
    assert shortcut_range("all", TODAY) == (EARLIEST, TODAY)


def test_parse_day() -> None:
    assert parse_day(" 2026-11-01 ") == date(2026, 11, 1)
    assert parse_day("01.11.2026") is None


def test_local_day_bounds_cover_whole_days() -> None:
    start, end = local_day_bounds(date(2026, 11, 1), date(2026, 11, 1))
    assert start.tzinfo is not None
    assert (start.hour, start.minute) == (0, 0)
    assert (end.hour, end.minute, end.second) == (23, 59, 59)


def test_split_phrases_keeps_spaces_inside() -> None:
    assert split_phrases("TFS, FTP Ramp ,, E1") == ["TFS", "FTP Ramp", "E1"]


def test_remember_recent() -> None:
    assert remember_recent(["Zwift", "E1"], ["zwift", "TFS"], limit=3) == ["zwift", "TFS", "E1"]


def test_formatting(activity: Activity) -> None:
    assert format_duration(3725) == "1:02:05"
    assert format_duration(None) == "–"
    assert format_distance(25300) == "25.30 km"
    row = activity_row(activity)
    assert "Enduro Zawody" in row and "mountain_biking" in row and "1:00:00" in row


def test_log_lines_are_translated(activity: Activity) -> None:
    pl, en = Translator("pl"), Translator("en")
    saved = FileSaved(activity, ExportFormat.FIT, Path("a.fit"))
    assert format_event(saved, pl) == "fit: zapisano a.fit"
    assert format_event(saved, en) == "fit: saved a.fit"
    skipped = FileSkipped(activity, ExportFormat.GPX, "no_gps")
    assert format_event(skipped, pl) == "gpx: pominięto (brak GPS)"
    assert "ERROR" in format_event(FileFailed(activity, ExportFormat.TCX, "x"), en)
    assert "60 s" in format_event(Waiting(60, "rate_limit"), pl)
    sub = FileSaved(replace(activity), ExportFormat.FIT, Path("a.gpx"), substitute=True)
    assert "oryginał" in format_event(sub, pl)


def test_every_text_has_both_languages() -> None:
    assert missing_translations() == []
    assert len(all_keys()) > 50


def test_every_skip_reason_is_translated() -> None:
    keys = set(all_keys())
    for reason in ("already_downloaded", "exists_on_disk", "no_gps", "no_file", "local_only"):
        assert f"reason.{reason}" in keys


def test_unknown_key_falls_back_to_key() -> None:
    assert Translator("pl")("nope.missing") == "nope.missing"
