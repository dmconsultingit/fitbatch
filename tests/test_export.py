import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fitbatch.adapters.base import AuthenticationError, NoFileError, RateLimitError
from fitbatch.adapters.fake import FakeAdapter
from fitbatch.core.export import (
    Exporter,
    ExportEvent,
    ExportRequest,
    FileSaved,
    FileSkipped,
    Waiting,
    default_file_name,
    export_root,
    format_dir,
)
from fitbatch.core.filters import ActivityFilter
from fitbatch.core.models import Activity, ExportFormat
from fitbatch.core.registry import DownloadRegistry

START = datetime(2026, 9, 1, tzinfo=UTC)
END = datetime(2026, 9, 30, tzinfo=UTC)


@pytest.fixture
def registry(tmp_path: Path) -> DownloadRegistry:
    with DownloadRegistry(tmp_path / "registry.sqlite3") as reg:
        yield reg


def request_for(tmp_path: Path, *formats: ExportFormat, **kwargs: object) -> ExportRequest:
    return ExportRequest(
        START, END, list(formats) or [ExportFormat.FIT], tmp_path / "out", **kwargs
    )


def make_exporter(adapter: FakeAdapter, registry: DownloadRegistry):
    events: list[ExportEvent] = []
    sleeps: list[float] = []
    exporter = Exporter(adapter, registry, on_event=events.append, delay_s=0, sleep=sleeps.append)
    return exporter, events, sleeps


def test_saves_files_and_registers_them(tmp_path: Path, registry, activity: Activity) -> None:
    exporter, events, _ = make_exporter(FakeAdapter([activity]), registry)
    req = request_for(tmp_path, ExportFormat.FIT, ExportFormat.SPLITS_CSV)
    summary = exporter.run(req, exporter.plan(req))
    assert summary.saved == 2
    day = activity.start_time.astimezone().date().isoformat()
    root = req.out_dir / "Fake" / "export"
    assert summary.out_dir == root == export_root(req.out_dir, "fake")
    assert sorted(p.name for p in (root / "fit").iterdir()) == [f"{day}_mountain_biking_123.fit"]
    assert sorted(p.name for p in (root / "csv").iterdir()) == [
        f"{day}_mountain_biking_123.splits.csv"
    ]
    assert registry.is_downloaded(activity, ExportFormat.FIT)
    assert sum(isinstance(e, FileSaved) for e in events) == 2


def test_second_run_skips_already_downloaded(tmp_path: Path, registry, activity: Activity) -> None:
    adapter = FakeAdapter([activity])
    exporter, events, _ = make_exporter(adapter, registry)
    req = request_for(tmp_path)
    exporter.run(req, [activity])
    summary = exporter.run(req, [activity])
    assert summary.saved == 0
    assert summary.skipped_by_reason == {"already_downloaded": 1}
    assert len(adapter.download_calls) == 1


def test_overwrite_downloads_again(tmp_path: Path, registry, activity: Activity) -> None:
    adapter = FakeAdapter([activity])
    exporter, _, _ = make_exporter(adapter, registry)
    exporter.run(request_for(tmp_path), [activity])
    exporter.run(request_for(tmp_path, overwrite=True), [activity])
    assert len(adapter.download_calls) == 2


def test_existing_file_on_disk_is_skipped_and_registered(
    tmp_path: Path, registry, activity: Activity
) -> None:
    req = request_for(tmp_path)
    folder = format_dir(req.out_dir, "fake", ExportFormat.FIT)
    folder.mkdir(parents=True)
    (folder / default_file_name(activity, ".fit")).write_bytes(b"old")
    exporter, _, _ = make_exporter(FakeAdapter([activity]), registry)
    summary = exporter.run(req, [activity])
    assert summary.skipped_by_reason == {"exists_on_disk": 1}
    assert registry.is_downloaded(activity, ExportFormat.FIT)


def test_gps_formats_skipped_without_gps(tmp_path: Path, registry, activity: Activity) -> None:
    gym = replace(activity, has_gps=False, type_key="strength_training")
    exporter, _, _ = make_exporter(FakeAdapter([gym]), registry)
    summary = exporter.run(request_for(tmp_path, ExportFormat.GPX, ExportFormat.FIT), [gym])
    assert summary.skipped_by_reason == {"no_gps": 1}
    assert summary.saved == 1


def test_missing_file_is_reported_not_failed(tmp_path: Path, registry, activity: Activity) -> None:
    def no_file(_a: Activity, _f: ExportFormat) -> None:
        raise NoFileError("manual activity")

    exporter, events, _ = make_exporter(FakeAdapter([activity], fail_with=no_file), registry)
    summary = exporter.run(request_for(tmp_path), [activity])
    assert summary.failed == 0
    assert summary.skipped_by_reason == {"no_file": 1}
    assert any(isinstance(e, FileSkipped) and e.reason == "no_file" for e in events)


def test_rate_limit_waits_twice_then_succeeds(tmp_path: Path, registry, activity: Activity) -> None:
    attempts = {"n": 0}

    def flaky(_a: Activity, _f: ExportFormat) -> None:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise RateLimitError("429")

    exporter, events, sleeps = make_exporter(FakeAdapter([activity], fail_with=flaky), registry)
    summary = exporter.run(request_for(tmp_path), [activity])
    assert summary.saved == 1
    assert [e.seconds for e in events if isinstance(e, Waiting)] == [30.0, 60.0]
    assert 30.0 in sleeps and 60.0 in sleeps


def test_rate_limit_pauses_job_after_two_waits(
    tmp_path: Path, registry, activity: Activity
) -> None:
    second = replace(activity, activity_id="456")

    def always_429(_a: Activity, _f: ExportFormat) -> None:
        raise RateLimitError("429")

    adapter = FakeAdapter([activity, second], fail_with=always_429)
    exporter, _, _ = make_exporter(adapter, registry)
    summary = exporter.run(request_for(tmp_path), [activity, second])
    assert summary.paused_reason == "rate_limit"
    assert len(adapter.download_calls) == 3  # first try + two retries, second activity untouched


def test_authentication_error_pauses_job(tmp_path: Path, registry, activity: Activity) -> None:
    def expired(_a: Activity, _f: ExportFormat) -> None:
        raise AuthenticationError("expired")

    exporter, _, _ = make_exporter(FakeAdapter([activity], fail_with=expired), registry)
    summary = exporter.run(request_for(tmp_path), [activity])
    assert summary.paused_reason == "authentication"


def test_cancel_stops_before_next_activity(tmp_path: Path, registry, activity: Activity) -> None:
    cancel = threading.Event()
    second = replace(activity, activity_id="456")
    adapter = FakeAdapter([activity, second])
    exporter = Exporter(
        adapter, registry, on_event=lambda e: cancel.set(), delay_s=0, sleep=lambda s: None
    )
    summary = exporter.run(request_for(tmp_path), [activity, second], cancel)
    assert summary.cancelled
    assert len(adapter.download_calls) == 1


def test_plan_applies_filters(tmp_path: Path, registry, activity: Activity) -> None:
    zwift = replace(activity, activity_id="2", name="Zwift E1")
    exporter, _, _ = make_exporter(FakeAdapter([activity, zwift]), registry)
    req = request_for(tmp_path, activity_filter=ActivityFilter(exclude_names=["Zwift"]))
    assert exporter.plan(req) == [activity]


def test_request_validation(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="start"):
        ExportRequest(END, START, [ExportFormat.FIT], tmp_path)
    with pytest.raises(ValueError, match="format"):
        ExportRequest(START, END, [], tmp_path)


def test_new_folder_downloads_again(tmp_path: Path, registry, activity: Activity) -> None:
    adapter = FakeAdapter([activity])
    exporter, _, _ = make_exporter(adapter, registry)
    exporter.run(ExportRequest(START, END, [ExportFormat.FIT], tmp_path / "a"), [activity])
    summary = exporter.run(
        ExportRequest(START, END, [ExportFormat.FIT], tmp_path / "b"), [activity]
    )
    assert summary.saved == 1
    assert len(adapter.download_calls) == 2
    folder = format_dir(tmp_path / "b", "fake", ExportFormat.FIT)
    assert (folder / default_file_name(activity, ".fit")).exists()


def test_deleted_file_is_downloaded_again(tmp_path: Path, registry, activity: Activity) -> None:
    adapter = FakeAdapter([activity])
    exporter, _, _ = make_exporter(adapter, registry)
    req = request_for(tmp_path)
    exporter.run(req, [activity])
    (
        format_dir(req.out_dir, "fake", ExportFormat.FIT) / default_file_name(activity, ".fit")
    ).unlink()
    summary = exporter.run(req, [activity])
    assert summary.saved == 1


def test_back_to_first_folder_with_file_present_is_skipped(
    tmp_path: Path, registry, activity: Activity
) -> None:
    exporter, _, _ = make_exporter(FakeAdapter([activity]), registry)
    first = ExportRequest(START, END, [ExportFormat.FIT], tmp_path / "a")
    exporter.run(first, [activity])
    exporter.run(ExportRequest(START, END, [ExportFormat.FIT], tmp_path / "b"), [activity])
    summary = exporter.run(first, [activity])
    assert summary.saved == 0
    assert summary.skipped_by_reason == {"exists_on_disk": 1}


def test_downloading_event_comes_before_save(tmp_path: Path, registry, activity: Activity) -> None:
    from fitbatch.core.export import Downloading

    exporter, events, _ = make_exporter(FakeAdapter([activity]), registry)
    exporter.run(request_for(tmp_path, ExportFormat.FIT), [activity])
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("Downloading") < kinds.index("FileSaved")
    assert isinstance(events[kinds.index("Downloading")], Downloading)


def test_folder_layout_per_platform_and_format(tmp_path: Path) -> None:
    assert (
        format_dir(tmp_path, "garmin", ExportFormat.FIT) == tmp_path / "Garmin" / "export" / "fit"
    )
    assert format_dir(tmp_path, "garmin", ExportFormat.SPLITS_CSV).name == "csv"
    assert export_root(tmp_path, "suunto") == tmp_path / "Suunto" / "export"
