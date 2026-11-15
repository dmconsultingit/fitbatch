from datetime import datetime

import pytest

from fitbatch.core.models import Activity, ExportFormat


def test_activity_requires_timezone_aware_start() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Activity("fake", "acc", "1", "Run", "running", datetime(2026, 1, 1))  # noqa: DTZ001


def test_gps_only_formats() -> None:
    assert ExportFormat.GPX.needs_gps
    assert ExportFormat.KML.needs_gps
    assert not ExportFormat.FIT.needs_gps
    assert not ExportFormat.SPLITS_CSV.needs_gps
