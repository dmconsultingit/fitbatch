"""Bulk clean-up of the platform's workout library, always with a local backup first.

For every chosen workout:
1. the full definition is saved as JSON (enough to create the workout again),
2. the FIT file is saved too when the platform has one,
3. only then the workout is deleted on the platform.

If the backup cannot be saved, the workout is not deleted. Deleting cannot be undone on the
platform, so the backup is not optional.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TypeVar

from fitbatch.adapters.base import (
    AdapterError,
    AuthenticationError,
    NoFileError,
    RateLimitError,
    WorkoutAdapter,
)
from fitbatch.core.export import PLATFORM_FOLDERS, RATE_LIMIT_WAITS_S, Waiting
from fitbatch.core.models import Workout
from fitbatch.storage.files import atomic_write, safe_filename

log = logging.getLogger(__name__)

T = TypeVar("T")


def workout_backup_dir(out_dir: Path, platform_id: str) -> Path:
    """``<target>/Garmin/backup/workouts``: next to the exported activities."""
    folder = PLATFORM_FOLDERS.get(platform_id, safe_filename(platform_id.capitalize()))
    return out_dir / folder / "backup" / "workouts"


def backup_file_name(workout: Workout, extension: str) -> str:
    """``<deletion date>_<workout id>_<name>.json``: sorts by date, never collides."""
    stem = safe_filename(f"{date.today().isoformat()}_{workout.workout_id}_{workout.name}")
    return f"{stem[:150]}{extension}"


# --- events --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CleanupEvent:
    pass


@dataclass(frozen=True, slots=True)
class CleanupProgress(CleanupEvent):
    done: int
    total: int
    workout: Workout


@dataclass(frozen=True, slots=True)
class BackupSaved(CleanupEvent):
    workout: Workout
    paths: tuple[Path, ...]
    fit_missing: bool  # the platform had no FIT file; the JSON definition is saved anyway


@dataclass(frozen=True, slots=True)
class WorkoutDeleted(CleanupEvent):
    workout: Workout


@dataclass(frozen=True, slots=True)
class CleanupFailed(CleanupEvent):
    workout: Workout
    stage: str  # "backup": nothing deleted | "delete": backup saved, workout still there
    message: str


@dataclass(slots=True)
class CleanupSummary:
    backed_up: int = 0
    deleted: int = 0
    failed: int = 0
    cancelled: bool = False
    paused_reason: str | None = None  # "rate_limit" | "authentication"
    backup_dir: Path | None = None
    deleted_ids: list[str] = field(default_factory=list)


class _JobPaused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class WorkoutCleaner:
    def __init__(
        self,
        adapter: WorkoutAdapter,
        out_dir: Path,
        *,
        on_event: Callable[[CleanupEvent | Waiting], None] = lambda _event: None,
        delay_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = adapter
        self._backup_dir = workout_backup_dir(out_dir, adapter.platform_id)
        self._emit = on_event
        self._delay_s = delay_s
        self._sleep = sleep

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    def run(
        self, workouts: Sequence[Workout], cancel: threading.Event | None = None
    ) -> CleanupSummary:
        summary = CleanupSummary(backup_dir=self._backup_dir)
        total = len(workouts)
        log.info("Workout clean-up started: %d workouts", total)
        try:
            for done, workout in enumerate(workouts, 1):
                if cancel is not None and cancel.is_set():
                    summary.cancelled = True
                    break
                self._emit(CleanupProgress(done, total, workout))
                self._clean_one(workout, summary)
        except _JobPaused as paused:
            summary.paused_reason = paused.reason
        log.info(
            "Workout clean-up finished: backed up %d, deleted %d, failed %d, cancelled %s, "
            "paused %s",
            summary.backed_up,
            summary.deleted,
            summary.failed,
            summary.cancelled,
            summary.paused_reason,
        )
        return summary

    def _fail(self, summary: CleanupSummary, workout: Workout, stage: str, message: str) -> None:
        summary.failed += 1
        self._emit(CleanupFailed(workout, stage, message))

    def _clean_one(self, workout: Workout, summary: CleanupSummary) -> None:
        # 1. Backup. Without it, nothing is deleted.
        try:
            paths, fit_missing = self._backup(workout)
        except AuthenticationError as exc:
            self._fail(summary, workout, "backup", str(exc))
            raise _JobPaused("authentication") from exc
        except (AdapterError, OSError, ValueError) as exc:
            self._fail(summary, workout, "backup", str(exc))
            return
        summary.backed_up += 1
        self._emit(BackupSaved(workout, paths, fit_missing))

        # 2. Delete.
        try:
            self._call(lambda: self._adapter.delete_workout(workout))
        except AuthenticationError as exc:
            self._fail(summary, workout, "delete", str(exc))
            raise _JobPaused("authentication") from exc
        except AdapterError as exc:
            self._fail(summary, workout, "delete", str(exc))
            return
        summary.deleted += 1
        summary.deleted_ids.append(workout.workout_id)
        self._emit(WorkoutDeleted(workout))

    def _backup(self, workout: Workout) -> tuple[tuple[Path, ...], bool]:
        definition = self._call(lambda: self._adapter.workout_definition(workout))
        parsed = json.loads(definition)  # never keep a broken backup
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("Garmin returned an empty workout definition")
        json_path = self._backup_dir / backup_file_name(workout, ".json")
        atomic_write(json_path, definition)
        paths = [json_path]
        fit_missing = False
        try:
            fit = self._call(lambda: self._adapter.workout_file(workout))
        except NoFileError:
            fit_missing = True
        except (RateLimitError, AuthenticationError):
            raise
        except AdapterError as exc:
            log.info("Workout %s: FIT backup not available (%s)", workout.workout_id, exc)
            fit_missing = True
        else:
            fit_path = self._backup_dir / backup_file_name(workout, ".fit")
            atomic_write(fit_path, fit)
            paths.append(fit_path)
        return tuple(paths), fit_missing

    def _call(self, request: Callable[[], T]) -> T:
        """One network request: moderate pace, waits on rate limits, then pauses the job."""
        for wait_s in (*RATE_LIMIT_WAITS_S, None):
            try:
                return request()
            except RateLimitError as exc:
                if wait_s is None:
                    raise _JobPaused("rate_limit") from exc
                self._emit(Waiting(wait_s, "rate_limit"))
                self._sleep(wait_s)
            finally:
                self._sleep(self._delay_s)  # N4
        raise AssertionError("unreachable")  # pragma: no cover
