"""In-memory adapter for tests and offline development (N15: logic testable without internet)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from fitbatch.adapters.base import AdapterError
from fitbatch.core.activity_types import describe
from fitbatch.core.models import Activity, Capability, DownloadedFile, ExportFormat

FailureHook = Callable[[Activity, ExportFormat], None]


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
    ) -> None:
        self._activities = list(activities)
        self._capabilities = frozenset(capabilities)
        self._remote_formats = frozenset(remote_formats)
        self._account = account
        self._fail_with = fail_with
        self.download_calls: list[tuple[str, ExportFormat]] = []
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
