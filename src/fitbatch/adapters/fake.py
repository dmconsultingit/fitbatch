"""In-memory adapter for tests and offline development (N15: logic testable without internet)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from fitbatch.adapters.base import AdapterError
from fitbatch.core.models import Activity, Capability, ExportFormat


class FakeAdapter:
    """Serves a fixed list of activities and returns predictable file content."""

    def __init__(
        self,
        activities: Iterable[Activity] = (),
        *,
        capabilities: Iterable[Capability] = (Capability.LIST_ACTIVITIES, Capability.DOWNLOAD),
        remote_formats: Iterable[ExportFormat] = tuple(ExportFormat),
        account: str = "fake-account",
    ) -> None:
        self._activities = list(activities)
        self._capabilities = frozenset(capabilities)
        self._remote_formats = frozenset(remote_formats)
        self._account = account
        self.download_calls: list[tuple[str, ExportFormat]] = []

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

    def list_activities(self, start: datetime, end: datetime) -> Sequence[Activity]:
        return [a for a in self._activities if start <= a.start_time <= end]

    def download(self, activity: Activity, fmt: ExportFormat) -> bytes:
        if fmt not in self._remote_formats:
            raise AdapterError(f"Format {fmt.value} is not available remotely")
        self.download_calls.append((activity.activity_id, fmt))
        return f"{activity.activity_id}:{fmt.value}".encode()
