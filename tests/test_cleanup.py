import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fitbatch.adapters.base import AdapterError, AuthenticationError, NoFileError, RateLimitError
from fitbatch.adapters.fake import FakeAdapter
from fitbatch.core.cleanup import (
    BackupSaved,
    CleanupFailed,
    WorkoutCleaner,
    WorkoutDeleted,
    workout_backup_dir,
)
from fitbatch.core.export import Waiting
from fitbatch.core.models import Capability, Workout

T0 = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)


def workout(n: int, name: str = "") -> Workout:
    return Workout("fake", "fake-account", f"w{n}", name or f"Workout {n}", "cycling",
                   T0, T0 + timedelta(days=n))  # fmt: skip


def cleaner_for(tmp_path: Path, workouts: list[Workout], **kw) -> tuple:
    adapter = FakeAdapter(capabilities=(Capability.WORKOUTS,), workouts=workouts, **kw)
    events: list = []
    cleaner = WorkoutCleaner(adapter, tmp_path, on_event=events.append, sleep=lambda _s: None)
    return cleaner, adapter, events


def test_backup_is_saved_before_delete(tmp_path: Path) -> None:
    order: list[str] = []
    w = workout(1, "E2 vo2max week4 - deload")

    def track(step: str, _w: Workout) -> None:
        order.append(step)

    cleaner, adapter, events = cleaner_for(tmp_path, [w], workout_fail_with=track)
    summary = cleaner.run([w])
    assert order == ["definition", "file", "delete"]
    assert (summary.backed_up, summary.deleted, summary.failed) == (1, 1, 0)
    assert adapter.deleted_workouts == ["w1"] and adapter.workouts == []
    saved = next(e for e in events if isinstance(e, BackupSaved))
    assert [p.suffix for p in saved.paths] == [".json", ".fit"]
    assert all(p.parent == workout_backup_dir(tmp_path, "fake") for p in saved.paths)
    assert json.loads(saved.paths[0].read_text(encoding="utf-8"))["workoutId"] == "w1"
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("BackupSaved") < kinds.index("WorkoutDeleted")


def test_backup_folder_is_next_to_the_export(tmp_path: Path) -> None:
    assert workout_backup_dir(tmp_path, "garmin") == tmp_path / "Garmin" / "backup" / "workouts"


def test_no_backup_means_no_delete(tmp_path: Path) -> None:
    def broken(step: str, _w: Workout) -> None:
        if step == "definition":
            raise AdapterError("connection problem")

    w1, w2 = workout(1), workout(2)
    cleaner, adapter, events = cleaner_for(tmp_path, [w1, w2], workout_fail_with=broken)
    summary = cleaner.run([w1, w2])
    assert adapter.deleted_workouts == []
    assert summary.failed == 2 and summary.deleted == 0
    assert all(e.stage == "backup" for e in events if isinstance(e, CleanupFailed))


def test_missing_fit_still_backs_up_json_and_deletes(tmp_path: Path) -> None:
    w = workout(1)
    cleaner, adapter, events = cleaner_for(tmp_path, [w], workouts_without_fit=["w1"])
    summary = cleaner.run([w])
    saved = next(e for e in events if isinstance(e, BackupSaved))
    assert saved.fit_missing and [p.suffix for p in saved.paths] == [".json"]
    assert summary.deleted == 1


def test_failed_delete_keeps_the_backup_and_goes_on(tmp_path: Path) -> None:
    def refuse_first(step: str, w: Workout) -> None:
        if step == "delete" and w.workout_id == "w1":
            raise NoFileError("already deleted?")

    w1, w2 = workout(1), workout(2)
    cleaner, adapter, events = cleaner_for(tmp_path, [w1, w2], workout_fail_with=refuse_first)
    summary = cleaner.run([w1, w2])
    assert (summary.backed_up, summary.deleted, summary.failed) == (2, 1, 1)
    assert adapter.deleted_workouts == ["w2"]
    failed = next(e for e in events if isinstance(e, CleanupFailed))
    assert failed.stage == "delete"


def test_rate_limit_waits_then_pauses_without_deleting(tmp_path: Path) -> None:
    def limited(step: str, _w: Workout) -> None:
        if step == "delete":
            raise RateLimitError("429")

    w = workout(1)
    cleaner, adapter, events = cleaner_for(tmp_path, [w], workout_fail_with=limited)
    summary = cleaner.run([w])
    assert summary.paused_reason == "rate_limit"
    assert [e.seconds for e in events if isinstance(e, Waiting)] == [30.0, 60.0]
    assert adapter.deleted_workouts == []
    assert summary.backed_up == 1  # the backup is there for the next attempt


def test_expired_session_pauses(tmp_path: Path) -> None:
    def expired(_step: str, _w: Workout) -> None:
        raise AuthenticationError("expired")

    cleaner, adapter, _events = cleaner_for(tmp_path, [workout(1), workout(2)],
                                            workout_fail_with=expired)  # fmt: skip
    summary = cleaner.run(adapter.workouts)
    assert summary.paused_reason == "authentication" and summary.failed == 1


def test_cancel_stops_before_the_next_workout(tmp_path: Path) -> None:
    cancel = threading.Event()

    def stop(step: str, _w: Workout) -> None:
        if step == "delete":
            cancel.set()

    w1, w2 = workout(1), workout(2)
    cleaner, adapter, events = cleaner_for(tmp_path, [w1, w2], workout_fail_with=stop)
    summary = cleaner.run([w1, w2], cancel)
    assert summary.cancelled and adapter.deleted_workouts == ["w1"]
    assert sum(isinstance(e, WorkoutDeleted) for e in events) == 1


def test_backup_names_are_safe(tmp_path: Path) -> None:
    w = workout(7, 'Intervals 4×8 min: "hard" / easy?')
    cleaner, _adapter, events = cleaner_for(tmp_path, [w])
    cleaner.run([w])
    saved = next(e for e in events if isinstance(e, BackupSaved))
    assert all(p.exists() for p in saved.paths)
    assert "w7" in saved.paths[0].name


# --- Garmin adapter ------------------------------------------------------------------------------


class _Client:
    def __init__(self, pages: list[list[dict]], delete_error: Exception | None = None) -> None:
        self.pages = pages
        self.delete_error = delete_error
        self.deleted: list[str] = []
        self.client = self

    def dumps(self) -> str:
        return "{}"

    def get_workouts(self, start: int, limit: int) -> list[dict]:
        return self.pages[start // limit] if start // limit < len(self.pages) else []

    def get_workout_by_id(self, workout_id: str) -> dict:
        return {"workoutId": int(workout_id), "workoutSegments": []}

    def download_workout(self, workout_id: str) -> bytes:
        return bytes([14, 16, 0, 0, 0, 0, 0, 0]) + b".FIT" + bytes(2)

    def delete_workout(self, workout_id: str) -> None:
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(workout_id)


def _raw(n: int, updated: str) -> dict:
    return {
        "workoutId": n,
        "workoutName": f"W{n}",
        "sportType": {"sportTypeKey": "cycling"},
        "createdDate": "2026-05-01T10:00:00.0",
        "updatedDate": updated,
        "estimatedDurationInSecs": 2700,
    }


def test_garmin_lists_all_pages_newest_first() -> None:
    from fitbatch.adapters.garmin_email import GarminEmailAdapter

    first = [_raw(i, "2026-05-02T10:00:00.0") for i in range(100)]
    second = [_raw(500, "2026-11-14T08:00:00.0")]
    adapter = GarminEmailAdapter(_Client([first, second]), on_session_change=lambda _t: None)
    workouts = adapter.list_workouts()
    assert len(workouts) == 101
    assert workouts[0].workout_id == "500" and workouts[0].sport_key == "cycling"
    assert Capability.WORKOUTS in adapter.capabilities


def test_garmin_backup_and_delete() -> None:
    from fitbatch.adapters.garmin_email import GarminEmailAdapter

    client = _Client([[_raw(1, "2026-05-02T10:00:00.0")]])
    adapter = GarminEmailAdapter(client, on_session_change=lambda _t: None)
    (w,) = adapter.list_workouts()
    assert json.loads(adapter.workout_definition(w))["workoutId"] == 1
    assert adapter.workout_file(w)[8:12] == b".FIT"
    adapter.delete_workout(w)
    assert client.deleted == ["1"]


@pytest.mark.parametrize(("status", "expected"), [(429, RateLimitError), (404, NoFileError)])
def test_garmin_delete_errors_are_mapped(status: int, expected: type) -> None:
    from garminconnect import GarminConnectConnectionError

    from fitbatch.adapters.garmin_email import GarminEmailAdapter

    class _Response:
        status_code = status

    error = GarminConnectConnectionError(f"API Error {status}")
    error.response = _Response()
    client = _Client([[_raw(1, "2026-05-02T10:00:00.0")]], delete_error=error)
    adapter = GarminEmailAdapter(client, on_session_change=lambda _t: None)
    (w,) = adapter.list_workouts()
    with pytest.raises(expected):
        adapter.delete_workout(w)
