"""In-memory adapter for tests and offline development (N15: logic testable without internet)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from fitbatch.adapters.base import AdapterError, NoFileError
from fitbatch.core.activity_types import describe
from fitbatch.core.models import Activity, Capability, DownloadedFile, ExportFormat, Workout

FailureHook = Callable[[Activity, ExportFormat], None]
UploadHook = Callable[[str, bytes], None]
WorkoutHook = Callable[[str, Workout], None]  # (step, workout); step: definition|file|delete


class FakeAdapter:
    """Serves a fixed list of activities and returns predictable file content.

    ``fail_with`` lets tests raise adapter errors (rate limit, missing file, ...) on demand.
    """

    def __init__(
        self,
        activities: Iterable[Activity] = (),
        *,
        capabilities: Iterable[Capability] = (Capability.LIST_ACTIVITIES, Capability.DOWNLOAD),
        remote_formats: Iterable[ExportFormat] = tuple(ExportFormat),
        account: str = "fake-account",
        fail_with: FailureHook | None = None,
        upload_fail_with: UploadHook | None = None,
        workouts: Iterable[Workout] = (),
        workout_fail_with: WorkoutHook | None = None,
        workouts_without_fit: Iterable[str] = (),
    ) -> None:
        self._activities = list(activities)
        self._capabilities = frozenset(capabilities)
        self._remote_formats = frozenset(remote_formats)
        self._account = account
        self._fail_with = fail_with
        self._upload_fail_with = upload_fail_with
        self.workouts = list(workouts)
        self._workout_fail_with = workout_fail_with
        self._no_fit = set(workouts_without_fit)
        self.deleted_workouts: list[str] = []
        self.download_calls: list[tuple[str, ExportFormat]] = []
        self.uploads: list[tuple[str, bytes]] = []
        self.list_calls: list[list[str] | None] = []

    @property
    def platform_id(self) -> str:
        return "fake"

    @property
    def method_id(self) -> str:
        return "memory"

    @property
    def capabilities(self) -> frozenset[Capability]:
        return self._capabilities

    @property
    def remote_formats(self) -> frozenset[ExportFormat]:
        return self._remote_formats

    @property
    def account(self) -> str:
        return self._account

    def list_activities(
        self, start: datetime, end: datetime, categories: Sequence[str] | None = None
    ) -> Sequence[Activity]:
        self.list_calls.append(list(categories) if categories else None)
        wanted = set(categories or [])
        return [
            a
            for a in self._activities
            if start <= a.start_time <= end
            and (not wanted or describe(a.type_key).category in wanted)
        ]

    def download(self, activity: Activity, fmt: ExportFormat) -> list[DownloadedFile]:
        if fmt not in self._remote_formats:
            raise AdapterError(f"Format {fmt.value} is not available remotely")
        self.download_calls.append((activity.activity_id, fmt))
        if self._fail_with is not None:
            self._fail_with(activity, fmt)
        return [DownloadedFile(f".{fmt.value}", f"{activity.activity_id}:{fmt.value}".encode())]

    def upload(self, file_name: str, data: bytes) -> None:
        """Records the upload; ``upload_fail_with`` can raise (duplicate, rate limit, ...)."""
        if Capability.UPLOAD not in self._capabilities:
            raise AdapterError("Upload is not supported")
        if self._upload_fail_with is not None:
            self._upload_fail_with(file_name, data)
        self.uploads.append((file_name, data))

    # --- workout library ----------------------------------------------------------------------

    def _workout_step(self, step: str, workout: Workout) -> None:
        if Capability.WORKOUTS not in self._capabilities:
            raise AdapterError("Workouts are not supported")
        if self._workout_fail_with is not None:
            self._workout_fail_with(step, workout)

    def list_workouts(self) -> Sequence[Workout]:
        if Capability.WORKOUTS not in self._capabilities:
            raise AdapterError("Workouts are not supported")
        return list(self.workouts)

    def workout_definition(self, workout: Workout) -> bytes:
        self._workout_step("definition", workout)
        return json.dumps({"workoutId": workout.workout_id, "workoutName": workout.name}).encode()

    def workout_file(self, workout: Workout) -> bytes:
        self._workout_step("file", workout)
        if workout.workout_id in self._no_fit:
            raise NoFileError("No FIT for this workout")
        header = bytes([14, 16, 0, 0, 0, 0, 0, 0]) + b".FIT" + bytes(2)
        return header + workout.workout_id.encode()

    def delete_workout(self, workout: Workout) -> None:
        self._workout_step("delete", workout)
        self.workouts = [w for w in self.workouts if w.workout_id != workout.workout_id]
        self.deleted_workouts.append(workout.workout_id)
