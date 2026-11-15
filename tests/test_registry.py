from dataclasses import replace
from pathlib import Path

from fitbatch.core.models import Activity, ExportFormat
from fitbatch.core.registry import DownloadRegistry


def test_marks_and_detects_downloads(tmp_path: Path, activity: Activity) -> None:
    with DownloadRegistry(tmp_path / "r.sqlite3") as registry:
        assert not registry.is_downloaded(activity, ExportFormat.FIT)
        registry.mark_downloaded(activity, ExportFormat.FIT, tmp_path / "a.fit")
        assert registry.is_downloaded(activity, ExportFormat.FIT)
        assert not registry.is_downloaded(activity, ExportFormat.GPX)


def test_key_includes_platform_and_account(tmp_path: Path, activity: Activity) -> None:
    with DownloadRegistry(tmp_path / "r.sqlite3") as registry:
        registry.mark_downloaded(activity, ExportFormat.FIT, tmp_path / "a.fit")
        assert not registry.is_downloaded(replace(activity, account="other"), ExportFormat.FIT)
        assert not registry.is_downloaded(replace(activity, platform="suunto"), ExportFormat.FIT)


def test_persists_between_sessions(tmp_path: Path, activity: Activity) -> None:
    db = tmp_path / "r.sqlite3"
    with DownloadRegistry(db) as registry:
        registry.mark_downloaded(activity, ExportFormat.FIT, tmp_path / "a.fit")
        registry.mark_downloaded(activity, ExportFormat.FIT, tmp_path / "a.fit")  # overwrite
    with DownloadRegistry(db) as registry:
        assert registry.is_downloaded(activity, ExportFormat.FIT)
        assert registry.count() == 1
