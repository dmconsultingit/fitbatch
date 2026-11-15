from datetime import UTC, datetime

import pytest

from fitbatch.adapters import AdapterError, PlatformAdapter
from fitbatch.adapters.fake import FakeAdapter
from fitbatch.core.models import Activity, Capability, DownloadedFile, ExportFormat


def test_fake_adapter_satisfies_interface() -> None:
    assert isinstance(FakeAdapter(), PlatformAdapter)


def test_list_activities_filters_by_range(activity: Activity) -> None:
    adapter = FakeAdapter([activity])
    inside = adapter.list_activities(
        datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)
    )
    outside = adapter.list_activities(
        datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)
    )
    assert inside == [activity]
    assert outside == []


def test_download_respects_declared_formats(activity: Activity) -> None:
    adapter = FakeAdapter([activity], remote_formats=[ExportFormat.FIT])
    assert adapter.download(activity, ExportFormat.FIT) == [DownloadedFile(".fit", b"123:fit")]
    with pytest.raises(AdapterError):
        adapter.download(activity, ExportFormat.GPX)


def test_capabilities_are_declared() -> None:
    adapter = FakeAdapter(capabilities=[Capability.LIST_ACTIVITIES])
    assert Capability.DOWNLOAD not in adapter.capabilities
