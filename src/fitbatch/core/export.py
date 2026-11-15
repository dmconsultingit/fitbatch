"""Bulk export of activities (F5–F16) on top of any ``PlatformAdapter``.

Progress is reported as events (N5) so the CLI and the GUI can show it without knowing the
details. Re-running an interrupted export resumes it: the registry skips finished files (F15).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from fitbatch.adapters.base import (
    AdapterError,
    AuthenticationError,
    NoFileError,
    PlatformAdapter,
    RateLimitError,
)
from fitbatch.core.filters import ActivityFilter
from fitbatch.core.models import Activity, DownloadedFile, ExportFormat
from fitbatch.core.registry import DownloadRegistry
from fitbatch.storage.files import atomic_write, safe_filename

log = logging.getLogger(__name__)

RATE_LIMIT_WAITS_S = (30.0, 60.0)  # D15: at most two waits, then pause the job


# --- events ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExportEvent:
    pass


@dataclass(frozen=True, slots=True)
class FileSaved(ExportEvent):
    activity: Activity
    fmt: ExportFormat
    path: Path
    substitute: bool = False  # e.g. no FIT exists, the uploaded GPX was saved instead


@dataclass(frozen=True, slots=True)
class FileSkipped(ExportEvent):
    activity: Activity
    fmt: ExportFormat
    reason: str  # "already_downloaded" | "exists_on_disk" | "no_gps" | "no_file" | "local_only"


@dataclass(frozen=True, slots=True)
class FileFailed(ExportEvent):
    activity: Activity
    fmt: ExportFormat
    message: str


@dataclass(frozen=True, slots=True)
class Waiting(ExportEvent):
    seconds: float
    reason: str


@dataclass(frozen=True, slots=True)
class Progress(ExportEvent):
    done: int
    total: int
    activity: Activity


@dataclass(slots=True)
class ExportSummary:
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    paused_reason: str | None = None  # set when the job stopped and can be resumed later
    out_dir: Path | None = None
    skipped_by_reason: dict[str, int] = field(default_factory=dict)


# --- request -------------------------------------------------------------------------------------


@dataclass(slots=True)
class ExportRequest:
    start: datetime  # timezone-aware
    end: datetime  # timezone-aware
    formats: Sequence[ExportFormat]
    out_dir: Path
    activity_filter: ActivityFilter = field(default_factory=ActivityFilter)
    overwrite: bool = False

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Export range must be timezone-aware")
        if self.start > self.end:
            raise ValueError("Export range: start must not be after end")
        if not self.formats:
            raise ValueError("Choose at least one format")


class _JobPaused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def default_file_name(activity: Activity, extension: str, index: int = 0) -> str:
    """F10 default: RRRR-MM-DD_type_ID.ext (local date, D8), Windows-safe."""
    local_day = activity.start_time.astimezone().date().isoformat()
    suffix = f"_{index}" if index else ""
    stem = safe_filename(f"{local_day}_{activity.type_key}_{activity.activity_id}{suffix}")
    return f"{stem}{extension}"


class Exporter:
    def __init__(
        self,
        adapter: PlatformAdapter,
        registry: DownloadRegistry,
        *,
        on_event: Callable[[ExportEvent], None] = lambda _event: None,
        delay_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = adapter
        self._registry = registry
        self._emit = on_event
        self._delay_s = delay_s
        self._sleep = sleep

    def plan(self, request: ExportRequest) -> list[Activity]:
        """Activities in the range that pass the filters (F7, F8): nothing is downloaded yet."""
        listed = self._adapter.list_activities(request.start, request.end)
        return request.activity_filter.apply(listed)

    def run(
        self,
        request: ExportRequest,
        activities: Sequence[Activity],
        cancel: threading.Event | None = None,
    ) -> ExportSummary:
        summary = ExportSummary(out_dir=request.out_dir)
        request.out_dir.mkdir(parents=True, exist_ok=True)
        total = len(activities)
        try:
            for done, activity in enumerate(activities, 1):
                if cancel is not None and cancel.is_set():
                    summary.cancelled = True
                    break
                self._emit(Progress(done, total, activity))
                for fmt in request.formats:
                    self._export_one(request, activity, fmt, summary)
        except _JobPaused as paused:
            summary.paused_reason = paused.reason
        return summary

    # --- one activity in one format -------------------------------------------------------------

    def _skip(
        self, summary: ExportSummary, activity: Activity, fmt: ExportFormat, why: str
    ) -> None:
        summary.skipped += 1
        summary.skipped_by_reason[why] = summary.skipped_by_reason.get(why, 0) + 1
        self._emit(FileSkipped(activity, fmt, why))

    def _export_one(
        self, request: ExportRequest, activity: Activity, fmt: ExportFormat, summary: ExportSummary
    ) -> None:
        if fmt.needs_gps and not activity.has_gps:
            return self._skip(summary, activity, fmt, "no_gps")
        if fmt not in self._adapter.remote_formats:
            # Local conversion from FIT comes in a later stage.
            return self._skip(summary, activity, fmt, "local_only")
        if not request.overwrite and self._registry.is_downloaded(activity, fmt):
            return self._skip(summary, activity, fmt, "already_downloaded")
        target = request.out_dir / default_file_name(activity, f".{fmt.value}")
        if not request.overwrite and target.exists():
            self._registry.mark_downloaded(activity, fmt, target)
            return self._skip(summary, activity, fmt, "exists_on_disk")

        try:
            files = self._download_with_rate_limit(activity, fmt)
        except NoFileError:
            return self._skip(summary, activity, fmt, "no_file")
        except AuthenticationError as exc:
            summary.failed += 1
            self._emit(FileFailed(activity, fmt, str(exc)))
            raise _JobPaused("authentication") from exc
        except AdapterError as exc:
            summary.failed += 1
            self._emit(FileFailed(activity, fmt, str(exc)))
            return None
        finally:
            self._sleep(self._delay_s)  # N4: moderate request rate

        paths = self._save(request.out_dir, activity, fmt, files)
        self._registry.mark_downloaded(activity, fmt, paths[0])
        summary.saved += 1
        for path, file in zip(paths, files, strict=True):
            self._emit(FileSaved(activity, fmt, path, file.is_substitute))
        return None

    def _download_with_rate_limit(
        self, activity: Activity, fmt: ExportFormat
    ) -> list[DownloadedFile]:
        for wait_s in (*RATE_LIMIT_WAITS_S, None):
            try:
                return self._adapter.download(activity, fmt)
            except RateLimitError as exc:
                if wait_s is None:
                    raise _JobPaused("rate_limit") from exc
                self._emit(Waiting(wait_s, "rate_limit"))
                self._sleep(wait_s)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _save(
        out_dir: Path, activity: Activity, fmt: ExportFormat, files: list[DownloadedFile]
    ) -> list[Path]:
        paths: list[Path] = []
        for index, file in enumerate(files):
            extension = f".{fmt.value}" if fmt is not ExportFormat.FIT else file.extension
            path = out_dir / default_file_name(activity, extension, index)
            atomic_write(path, file.data)  # N3: no half-written files
            paths.append(path)
        return paths
