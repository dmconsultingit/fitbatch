"""Layout rules Flutter enforces only when drawing (a broken rule shows a grey error box).

Builds every screen without a window and walks the control tree: a wrapping Row must not
contain children with ``expand`` — Flutter cannot size them and draws a grey box instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from fitbatch.adapters.fake import FakeAdapter
from fitbatch.core.models import Activity
from fitbatch.services import AppServices
from fitbatch.ui.app import FitBatchApp


class _Page(SimpleNamespace):
    def update(self, *_: Any) -> None:
        pass

    def run_thread(self, fn: Any, *args: Any) -> None:
        fn(*args)

    def run_task(self, *_: Any, **__: Any) -> None:
        pass


class _Adapter(FakeAdapter):
    account_label = "Test"

    def persist_session(self) -> None:
        pass


def _children(control: Any) -> list[Any]:
    found: list[Any] = []
    for name in ("controls", "content", "actions", "title"):
        value = getattr(control, name, None)
        if isinstance(value, ft.Control):
            found.append(value)
        elif isinstance(value, list):
            found.extend(v for v in value if isinstance(v, ft.Control))
    return found


def _problems(control: Any, path: str = "") -> list[str]:
    issues: list[str] = []
    here = f"{path}/{type(control).__name__}"
    kids = _children(control)
    if isinstance(control, ft.Row) and control.wrap:
        issues += [
            f"{here}: expand child in wrapping Row" for c in kids if getattr(c, "expand", None)
        ]
    for child in kids:
        issues += _problems(child, here)
    return issues


@pytest.fixture
def app(tmp_path: Path) -> FitBatchApp:
    from fitbatch.services import create_services
    from fitbatch.storage.paths import AppPaths

    services: AppServices = create_services(AppPaths(tmp_path / "data"))
    page = _Page(
        platform_brightness=ft.Brightness.DARK,
        controls=[],
        window=SimpleNamespace(),
    )
    page.add = lambda *c: page.controls.extend(c)
    application = FitBatchApp(page, services)  # type: ignore[arg-type]
    application.folder = str(tmp_path / "out")
    now = datetime.now(UTC)
    acts = [
        Activity("fake", "acc", str(i), f"Ride {i}", key, now - timedelta(days=i), 60, 1000, True)
        for i, key in enumerate(["cycling", "mountain_biking", "strength_training", "walking"], 1)
    ]
    application.adapter = _Adapter(acts)
    return application


@pytest.mark.parametrize(
    "screen", ["connect", "export", "files", "import", "cleanup", "accounts", "settings"]
)
def test_screens_have_no_layout_errors(app: FitBatchApp, screen: str) -> None:
    app.go(screen)
    assert _problems(app.page.controls[0]) == []


def test_export_with_types_open_and_list(app: FitBatchApp) -> None:
    app.go("export")
    app._start_listing()  # runs synchronously with the fake page
    app.types_open = True
    app.expanded_categories = {"cycling"}
    app.render()
    assert len(app.listed) == 4
    assert _problems(app.page.controls[0]) == []
    app._set_type_query("bik")
    assert _problems(app.page.controls[0]) == []


def test_import_with_files_and_progress(app: FitBatchApp, tmp_path: Path) -> None:
    from fitbatch.core.upload import Uploaded, UploadProgress, UploadSkipped

    fit = bytes([14, 16, 0, 0, 0, 0, 0, 0]) + b".FIT" + b"\x00\x00data"
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "ride.fit").write_bytes(fit)
    (tmp_path / "in" / "notes.gpx").write_bytes(b"not a gpx")
    screen = app.import_screen
    app.go("import")
    screen._scan_worker([tmp_path / "in"], True)  # synchronously, as the worker thread would
    assert len(screen.files) == 2
    screen.job.visible = screen.job.running = True
    screen._on_event(UploadProgress(1, 2, screen.files[0]))
    screen._on_event(Uploaded(screen.files[0]))
    screen._on_event(UploadSkipped(screen.files[1], "wrong_content"))
    app.render()
    assert _problems(app.page.controls[0]) == []
    assert screen.job.uploaded == 1


def test_import_tabs(app: FitBatchApp) -> None:
    app.go("import")
    assert app.import_screen.tab == "activities"
    app.import_screen.tab = "workouts"
    app.render()
    assert _problems(app.page.controls[0]) == []
    app.import_screen.tab = "activities"


def test_cleanup_tabs(app: FitBatchApp) -> None:
    app.go("cleanup")
    for tab in ("activities", "workouts"):
        app.cleanup_screen.tab = tab
        app.render()
        assert _problems(app.page.controls[0]) == []
    app.cleanup_screen.tab = "activities"


def test_cleanup_workouts_flow(app: FitBatchApp, tmp_path: Path) -> None:
    """List, select, back up and delete through the screen (fake page runs threads inline)."""
    from fitbatch.core.models import Capability, Workout

    now = datetime.now(UTC)
    workouts = [
        Workout("fake", "acc", f"w{i}", f"VO2max week {i}", "cycling", now, now) for i in range(3)
    ]
    app.adapter = _Adapter(
        capabilities=(Capability.LIST_ACTIVITIES, Capability.WORKOUTS), workouts=workouts
    )
    screen = app.cleanup_screen
    screen.tab = "workouts"
    screen.delay_s = 0
    app.go("cleanup")  # loads the list
    assert len(screen.workouts) == 3
    assert _problems(app.page.controls[0]) == []
    screen.selected = {"w0", "w2"}
    app.page.show_dialog = lambda _dialog: None
    screen._confirm()  # dialog only; nothing deleted yet
    assert app.adapter.deleted_workouts == []
    screen._start(screen._chosen())
    assert app.adapter.deleted_workouts == ["w0", "w2"]
    assert [w.workout_id for w in screen.workouts] == ["w1"]
    backups = sorted(
        p.suffix for p in (tmp_path / "out" / "Fake" / "backup" / "workouts").iterdir()
    )
    assert backups == [".fit", ".fit", ".json", ".json"]
    assert screen.job.deleted == 2 and screen.job.backed_up == 2
    assert _problems(app.page.controls[0]) == []


def test_scrolling_columns_have_no_expand_children(app: FitBatchApp) -> None:
    """Flutter cannot size an expand child inside a scrolling column (grey box)."""

    def walk(control: Any) -> list[str]:
        issues: list[str] = []
        kids = _children(control)
        if isinstance(control, ft.Column) and control.scroll:
            issues += [type(c).__name__ for c in kids if getattr(c, "expand", None)]
        for child in kids:
            issues += walk(child)
        return issues

    for screen in ("connect", "export", "files", "import", "cleanup", "accounts", "settings"):
        app.go(screen)
        assert walk(app.page.controls[0]) == [], screen


def test_sidebar_groups_platforms(app: FitBatchApp) -> None:
    app.go("export")
    texts: list[str] = []

    def collect(control: Any) -> None:
        value = getattr(control, "value", None)
        if isinstance(value, str):
            texts.append(value)
        for child in _children(control):
            collect(child)

    collect(app.page.controls[0].controls[0])  # the sidebar
    assert [t for t in texts if t in {"GARMIN", "SUUNTO", "WAHOO"}] == ["GARMIN", "SUUNTO", "WAHOO"]
    assert texts.count("soon") == 2  # Suunto, Wahoo: one wording everywhere
    assert "coming soon" not in texts


def test_sidebar_logo_has_no_platform_slogan(app: FitBatchApp) -> None:
    app.go("export")
    texts: list[str] = []

    def collect(control: Any) -> None:
        value = getattr(control, "value", None)
        if isinstance(value, str):
            texts.append(value)
        for child in _children(control):
            collect(child)

    collect(app.page.controls[0].controls[0])
    assert not any("·" in t or "Train" in t for t in texts[:3])  # no slogan by the logo
    assert not any("Garmin Connect" in t for t in texts)
