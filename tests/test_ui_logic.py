from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from fitbatch.core.export import FileFailed, FileSaved, FileSkipped, Waiting
from fitbatch.core.models import Activity, ExportFormat
from fitbatch.i18n import DEFAULT_LANGUAGE, Translator, all_keys, missing_translations
from fitbatch.ui.logic import (
    EARLIEST,
    activity_row,
    files_to_download,
    filter_summary,
    format_distance,
    format_duration,
    format_event,
    local_day_bounds,
    parse_day,
    remember_recent,
    shortcut_range,
    split_phrases,
    total_distance_km,
)

TODAY = date(2026, 11, 15)
tr = Translator()


@pytest.mark.parametrize(("name", "days"), [("7d", 7), ("30d", 30), ("year", 365)])
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
    assert "Enduro Zawody" in row and "Mountain biking" in row and "1:00:00" in row


def test_kpis(activity: Activity) -> None:
    gym = replace(activity, activity_id="2", has_gps=False, distance_m=None)
    assert total_distance_km([activity, gym]) == "25.0"
    formats = [ExportFormat.FIT, ExportFormat.GPX]
    assert files_to_download([activity, gym], formats) == 3  # no GPX for the gym session


def test_filter_summary() -> None:
    assert filter_summary([], [], 3, 3, tr) == "no filters"
    text = filter_summary(["E1"], ["Zwift"], 2, 5, tr)
    assert text == "name has “E1” · without “Zwift” · 2 types"


def test_log_lines(activity: Activity) -> None:
    saved = FileSaved(activity, ExportFormat.FIT, Path("a.fit"))
    assert format_event(saved, tr) == "fit: saved a.fit"
    skipped = FileSkipped(activity, ExportFormat.GPX, "no_gps")
    assert format_event(skipped, tr) == "gpx: skipped (no GPS)"
    assert "ERROR" in format_event(FileFailed(activity, ExportFormat.TCX, "x"), tr)
    assert "60 s" in format_event(Waiting(60, "rate_limit"), tr)
    sub = FileSaved(activity, ExportFormat.FIT, Path("a.gpx"), substitute=True)
    assert "original upload" in format_event(sub, tr)


def test_english_is_default_and_complete() -> None:
    assert DEFAULT_LANGUAGE == "en"
    assert missing_translations() == []
    assert len(all_keys()) > 100


def test_every_skip_reason_has_text() -> None:
    keys = set(all_keys())
    for reason in ("already_downloaded", "exists_on_disk", "no_gps", "no_file", "local_only"):
        assert f"reason.{reason}" in keys


def test_unknown_key_and_language_fall_back() -> None:
    assert Translator("en")("nope.missing") == "nope.missing"
    assert Translator("xx").language == "en"
