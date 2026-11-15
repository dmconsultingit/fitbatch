"""Common interface for platform adapters (N11)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from fitbatch.core.models import Activity, Capability, DownloadedFile, ExportFormat


class AdapterError(Exception):
    """Base error raised by adapters. Messages must never contain credentials (N2)."""


class AuthenticationError(AdapterError):
    """Login failed or the saved session can no longer be renewed."""


class RateLimitError(AdapterError):
    """The platform asked us to slow down (e.g. HTTP 429). Never retry in a tight loop (N2, N4)."""


class NoFileError(AdapterError):
    """The activity has no file in this format (e.g. added manually in Garmin Connect)."""


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

    def list_activities(self, start: datetime, end: datetime) -> Sequence[Activity]:
        """Activities that started between ``start`` and ``end`` (both timezone-aware)."""
        ...

    def download(self, activity: Activity, fmt: ExportFormat) -> list[DownloadedFile]:
        """Files of one activity in one of ``remote_formats``.

        Raises ``NoFileError`` when there is nothing to download in that format.
        """
        ...
