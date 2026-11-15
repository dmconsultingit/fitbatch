from datetime import UTC, datetime
from pathlib import Path

import pytest

from fitbatch.core.models import Activity


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Never touch the real %APPDATA%\\FitBatch during tests."""
    data_dir = tmp_path / "appdata"
    monkeypatch.setenv("FITBATCH_DATA_DIR", str(data_dir))
    return data_dir


@pytest.fixture
def activity() -> Activity:
    return Activity(
        platform="fake",
        account="acc-1",
        activity_id="123",
        name="Enduro Zawody",
        type_key="mountain_biking",
        start_time=datetime(2026, 9, 5, 8, 30, tzinfo=UTC),
        duration_s=3600.0,
        distance_m=25000.0,
    )
