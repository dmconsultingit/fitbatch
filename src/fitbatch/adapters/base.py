"""Common interface for platform adapters (N11)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from fitbatch.core.models import Activity, Capability, DownloadedFile, ExportFormat, Workout


class AdapterError(Exception):
    """Base error raised by adapters. Messages must never contain credentials (N2)."""


class AuthenticationError(AdapterError):
    """Login failed or the saved session can no longer be renewed."""


class RateLimitError(AdapterError):
    """The platform asked us to slow down (e.g. HTTP 429). Never retry in a tight loop (N2, N4)."""


class NoFileError(AdapterError):
    """The activity has no file in this format (e.g. added manually in Garmin Connect)."""


class DuplicateError(AdapterError):
    """Upload refused because the platform already has this activity (Garmin: HTTP 409)."""


class InvalidFileError(AdapterError):
    """Upload refused because the platform could not read the file."""


@runtime_checkable
class PlatformAdapter(Protocol):
    """One platform + one access method, e.g. Garmin Connect via email and password."""

    @property
    def platform_id(self) -> str:
        """Stable platform identifier, e.g. ``"garmin"``."""
        ...

    @property
    def method_id(self) -> str:
        """Stable access-method identifier, e.g. ``"email"`` or ``"api_key"``."""
        ...

    @property
    def capabilities(self) -> frozenset[Capability]:
        """What this adapter can do; the UI shows or greys out features based on it."""
        ...

    @property
    def remote_formats(self) -> frozenset[ExportFormat]:
        """Formats the platform delivers directly; others are converted locally from FIT."""
        ...

    @property
    def account(self) -> str:
        """Identifier of the connected account, used as part of the registry key (N14)."""
        ...

    def list_activities(
        self, start: datetime, end: datetime, categories: Sequence[str] | None = None
    ) -> Sequence[Activity]:
        """Activities that started between ``start`` and ``end`` (both timezone-aware).

        ``categories`` is a hint to narrow the list on the platform side; adapters may ignore it,
        so callers always apply their own filter as well.
        """
        ...

    def download(self, activity: Activity, fmt: ExportFormat) -> list[DownloadedFile]:
        """Files of one activity in one of ``remote_formats``.

        Raises ``NoFileError`` when there is nothing to download in that format.
        """
        ...


@runtime_checkable
class UploadAdapter(Protocol):
    """An adapter that can also upload activity files (``Capability.UPLOAD``, F18)."""

    @property
    def platform_id(self) -> str: ...

    @property
    def account(self) -> str: ...

    def upload(self, file_name: str, data: bytes) -> None:
        """Upload one activity file. ``file_name`` ends with .fit, .gpx or .tcx.

        Raises ``DuplicateError`` when the platform already has the activity, ``InvalidFileError``
        when it cannot read the file, ``RateLimitError``/``AuthenticationError`` as usual.
        """
        ...


@runtime_checkable
class WorkoutAdapter(Protocol):
    """An adapter that manages the workout library (``Capability.WORKOUTS``)."""

    @property
    def platform_id(self) -> str: ...

    @property
    def account(self) -> str: ...

    def list_workouts(self) -> Sequence[Workout]:
        """Every workout in the account's library, newest change first."""
        ...

    def workout_definition(self, workout: Workout) -> bytes:
        """The full workout as JSON: enough to create it again on the platform."""
        ...

    def workout_file(self, workout: Workout) -> bytes:
        """The workout as a FIT file. Raises ``NoFileError`` if the platform has none."""
        ...

    def delete_workout(self, workout: Workout) -> None:
        """Delete the workout from the library. Cannot be undone on the platform."""
        ...
