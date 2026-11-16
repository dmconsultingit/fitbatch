"""Clean-up screen: bulk delete in Garmin Connect, always with a local backup first.

Workouts tab: list the workout library, select workouts, confirm, then for each workout the
backup (JSON + FIT) is saved before it is deleted. Activities tab: planned.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import flet as ft

from fitbatch.adapters.base import AdapterError, WorkoutAdapter
from fitbatch.core.cleanup import (
    BackupSaved,
    CleanupEvent,
    CleanupFailed,
    CleanupProgress,
    CleanupSummary,
    WorkoutCleaner,
    WorkoutDeleted,
    workout_backup_dir,
)
from fitbatch.core.export import Waiting
from fitbatch.core.models import Capability, Workout
from fitbatch.ui.logic import format_duration
from fitbatch.ui.theme import DISPLAY, MONO

if TYPE_CHECKING:
    from fitbatch.ui.app import FitBatchApp

log = logging.getLogger(__name__)

PAGE_SIZE = 50
MAX_LOG_LINES = 500
COLS = (44, None, 200, 110, 120, 120)  # checkbox, name, type, duration, changed, created


@dataclass(slots=True)
class CleanupJob:
    visible: bool = False
    running: bool = False
    cancelling: bool = False
    done: int = 0
    total: int = 0
    step: str = ""
    backed_up: int = 0
    deleted: int = 0
    failed: int = 0
    lines: list[tuple[str, str]] = field(default_factory=list)
    message: str = ""
    resumable: bool = False


def _day(value: Any) -> str:
    return value.astimezone().strftime("%Y-%m-%d") if value else "–"


class CleanupScreen:
    def __init__(self, app: FitBatchApp) -> None:
        self.app = app
        self.tab = "workouts"  # "activities" (planned) | "workouts"
        self.workouts: list[Workout] = []
        self.loaded_for: str | None = None  # account the list belongs to
        self.loading = False
        self.load_error = ""
        self.selected: set[str] = set()
        self.page_index = 0
        self.job = CleanupJob()
        self.cancel_event: threading.Event | None = None
        self.last_selection: list[Workout] = []
        self.backup_dir: Path | None = None
        self.delay_s = 1.0  # pause between requests to Garmin (N4)

    # --- helpers ---------------------------------------------------------------------------------

    @property
    def adapter(self) -> WorkoutAdapter | None:
        adapter = self.app.adapter
        if adapter is None or Capability.WORKOUTS not in adapter.capabilities:
            return None
        return adapter

    def _visible(self) -> bool:
        return self.app.screen == "cleanup" and self.tab == "workouts"

    def _base_folder(self) -> Path:
        return Path(self.app.folder or self.app.settings.download_dir).expanduser()

    def _chosen(self) -> list[Workout]:
        return [w for w in self.workouts if w.workout_id in self.selected]

    def _refresh(self, *, table: bool = False) -> None:
        if self._visible():
            self._fill_progress()
            self._fill_action()
            if table:
                self._fill_table()
        self.app.page.update()

    # --- view ------------------------------------------------------------------------------------

    def view(self) -> tuple[ft.Control, ft.Control | None]:
        k, tr = self.app.k, self.app.tr

        def select(tab: str) -> None:
            self.tab = tab
            self.app.render()

        tabs = ft.Row(
            [
                k.segmented(
                    [
                        ("activities", tr("cleanup.tab.activities")),
                        ("workouts", tr("cleanup.tab.workouts")),
                    ],
                    self.tab,
                    select,
                )
            ]
        )
        if self.tab == "activities":
            return ft.Column([tabs, self._activities_view()], spacing=16), None
        body, footer = self._workouts_view()
        return ft.Column([tabs, body], spacing=16), footer

    def _activities_view(self) -> ft.Control:
        k, tr = self.app.k, self.app.tr
        return k.card(
            ft.Column(
                [
                    ft.Row(
                        [
                            k.strong(tr("cleanup.activities.title"), 18),
                            k.pill(tr("common.soon")),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    k.text(tr("cleanup.activities.text"), 14),
                    k.text(tr("cleanup.safety"), 13, muted=True),
                ],
                spacing=10,
            )
        )

    def _workouts_view(self) -> tuple[ft.Control, ft.Control]:
        p, k, tr = self.app.p, self.app.k, self.app.tr
        account = self.adapter.account if self.adapter is not None else None
        if (
            account is not None
            and account != self.loaded_for
            and not self.loading
            and not self.job.running
        ):
            self._load()  # first visit, or another account

        warning = ft.Container(
            ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=p.warning, size=22),
                    ft.Column(
                        [
                            k.strong(tr("cleanup.workouts.warning_title"), 15),
                            k.text(
                                tr(
                                    "cleanup.workouts.warning",
                                    folder=str(workout_backup_dir(self._base_folder(), "garmin")),
                                ),
                                14,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=p.warning_soft,
            border=ft.Border.all(1, p.warning),
            border_radius=12,
            padding=16,
        )
        self.w_table = ft.Container()
        self.w_progress = ft.Container()
        self.w_action = ft.Container()
        self._fill_table()
        self._fill_progress()
        self._fill_action()
        body = ft.Column([warning, self.w_table, self.w_progress], spacing=16)
        return body, self.w_action

    # --- loading the workout list ----------------------------------------------------------------

    def _load(self) -> None:
        adapter = self.adapter
        if adapter is None:
            return
        self.loading = True
        self.load_error = ""
        self.app.page.run_thread(self._load_worker, adapter)

    def _load_worker(self, adapter: WorkoutAdapter) -> None:
        try:
            workouts = list(adapter.list_workouts())
            self.workouts = workouts
            self.loaded_for = adapter.account
            ids = {w.workout_id for w in workouts}
            self.selected &= ids
            self.page_index = 0
        except AdapterError as exc:
            log.warning("Workout list failed: %s", exc)
            self.load_error = self.app.tr("cleanup.workouts.load_failed", error=str(exc))
            self.loaded_for = adapter.account  # do not retry in a loop; "Refresh" retries
        except Exception:
            log.exception("Workout list crashed")
            self.load_error = self.app.tr("cleanup.workouts.load_failed", error="unexpected error")
            self.loaded_for = adapter.account
        finally:
            self.loading = False
            self.app._safe_persist()
        if self._visible():
            self.app.render()

    # --- table -----------------------------------------------------------------------------------

    def _fill_table(self) -> None:
        p, k, tr = self.app.p, self.app.k, self.app.tr
        if self.app.adapter is None:
            self.w_table.content = k.card(k.text(tr("export.need_connection"), 14, muted=True))
            return
        if self.adapter is None:
            self.w_table.content = k.card(k.text(tr("cleanup.workouts.unsupported"), 14))
            return
        if self.loading:
            self.w_table.content = k.card(
                ft.Row(
                    [
                        ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=p.primary),
                        k.text(tr("cleanup.workouts.loading"), 14),
                    ],
                    spacing=12,
                )
            )
            return
        busy = self.job.running

        def refresh(_: Any) -> None:
            self._load()
            self.app.render()

        def select_all(_: Any) -> None:
            self.selected = {w.workout_id for w in self.workouts}
            self._refresh(table=True)

        def select_none(_: Any) -> None:
            self.selected = set()
            self._refresh(table=True)

        all_button = k.quiet(tr("list.select_all"), select_all, ft.Icons.DONE_ALL)
        none_button = k.quiet(tr("list.select_none"), select_none, ft.Icons.REMOVE_DONE)
        refresh_button = k.quiet(tr("cleanup.workouts.refresh"), refresh, ft.Icons.REFRESH)
        all_button.disabled = busy or not self.workouts
        none_button.disabled = busy or not self.selected
        refresh_button.disabled = busy
        top = ft.Row(
            [
                k.text(
                    tr(
                        "cleanup.workouts.selected",
                        selected=len(self.selected),
                        total=len(self.workouts),
                    ),
                    14,
                    expand=True,
                ),
                all_button,
                none_button,
                refresh_button,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        extra: list[ft.Control] = []
        if self.load_error:
            extra.append(k.text(self.load_error, 13, color=p.danger))

        def cell(control: ft.Control, i: int, end: bool = False) -> ft.Container:
            return ft.Container(
                control,
                width=COLS[i],
                expand=COLS[i] is None,
                alignment=ft.Alignment.CENTER_RIGHT if end else None,
            )

        heads = [
            "",
            tr("cleanup.col.name"),
            tr("cleanup.col.type"),
            tr("cleanup.col.duration"),
            tr("cleanup.col.changed"),
            tr("cleanup.col.created"),
        ]
        rows: list[ft.Control] = [
            ft.Container(
                ft.Row([cell(k.overline(h), i) for i, h in enumerate(heads)], spacing=0),
                bgcolor=p.surface2,
                padding=ft.Padding.symmetric(vertical=10, horizontal=16),
            )
        ]
        pages = max(1, -(-len(self.workouts) // PAGE_SIZE))
        self.page_index = min(self.page_index, pages - 1)
        first = self.page_index * PAGE_SIZE
        for w in self.workouts[first : first + PAGE_SIZE]:
            rows.append(self._row(w, cell, busy))
        if not self.workouts:
            rows.append(
                ft.Container(k.text(tr("cleanup.workouts.empty"), 14, muted=True), padding=20)
            )
        pager: list[ft.Control] = []
        if pages > 1:

            def move(step: int) -> None:
                self.page_index = max(0, min(pages - 1, self.page_index + step))
                self._refresh(table=True)

            prev = ft.IconButton(
                ft.Icons.CHEVRON_LEFT,
                on_click=lambda _: move(-1),
                disabled=self.page_index == 0,
                tooltip=tr("list.prev"),
            )
            nxt = ft.IconButton(
                ft.Icons.CHEVRON_RIGHT,
                on_click=lambda _: move(1),
                disabled=self.page_index >= pages - 1,
                tooltip=tr("list.next"),
            )
            shown = len(self.workouts[first : first + PAGE_SIZE])
            label = tr("list.page", first=first + 1, last=first + shown, total=len(self.workouts))
            pager.append(
                ft.Row(
                    [k.text(label, 13, muted=True), prev, nxt],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=4,
                )
            )
        self.w_table.content = ft.Column(
            [
                k.card(top, padding=ft.Padding.symmetric(vertical=8, horizontal=16)),
                *extra,
                k.card(ft.Column(rows, spacing=0), padding=0),
                *pager,
            ],
            spacing=10,
        )

    def _row(self, w: Workout, cell: Any, busy: bool) -> ft.Container:
        p, k = self.app.p, self.app.k
        on = w.workout_id in self.selected

        def toggle(_: Any, workout_id: str = w.workout_id) -> None:
            if self.job.running:
                return
            if workout_id in self.selected:
                self.selected.discard(workout_id)
            else:
                self.selected.add(workout_id)
            self._refresh(table=True)

        checkbox = k.checkbox(on, toggle)
        checkbox.disabled = busy
        name = k.text(
            w.name or "–",
            14,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=w.name or None,
        )
        return ft.Container(
            ft.Row(
                [
                    cell(checkbox, 0),
                    cell(name, 1),
                    cell(k.type_badge(w.sport_key), 2),
                    cell(k.text(format_duration(w.duration_s), 14, muted=True), 3),
                    cell(k.text(_day(w.updated), 14, muted=True), 4),
                    cell(k.text(_day(w.created), 14, muted=True), 5),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            height=48,
            bgcolor=p.primary_soft if on else None,
            padding=ft.Padding.symmetric(vertical=0, horizontal=16),
            border=ft.Border.only(top=ft.BorderSide(1, p.border)),
            on_click=None if busy else toggle,
        )

    # --- action bar ------------------------------------------------------------------------------

    def _fill_action(self) -> None:
        p, k, tr, job = self.app.p, self.app.k, self.app.tr, self.job
        chosen = self._chosen()
        buttons: list[ft.Control] = []
        if job.running:
            cancel = k.secondary(tr("action.cancel"), self._cancel, ft.Icons.CLOSE)
            cancel.disabled = job.cancelling
            buttons.append(cancel)
        else:
            if self.backup_dir is not None:
                buttons.append(
                    k.secondary(tr("cleanup.open_backup"), self._open_backup, ft.Icons.FOLDER_OPEN)
                )
            start = k.danger(
                tr("cleanup.workouts.action", count=len(chosen)),
                lambda _: self._confirm(),
                ft.Icons.DELETE_SWEEP_OUTLINED,
            )
            start.disabled = not chosen or self.adapter is None or self.loading
            buttons.append(start)
        hint = k.text(
            tr("cleanup.backup_to", folder=str(workout_backup_dir(self._base_folder(), "garmin"))),
            13,
            muted=True,
            expand=True,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.w_action.content = ft.Container(
            ft.Row(
                [ft.Icon(ft.Icons.SAVE_OUTLINED, color=p.muted, size=18), hint, *buttons],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=p.surface,
            border=ft.Border.all(1, p.border),
            border_radius=16,
            padding=ft.Padding.symmetric(vertical=12, horizontal=16),
        )

    # --- confirmation ----------------------------------------------------------------------------

    def _confirm(self) -> None:
        k, tr = self.app.k, self.app.tr
        chosen = self._chosen()
        if not chosen or self.adapter is None:
            return
        folder = workout_backup_dir(self._base_folder(), "garmin")
        names = [f"• {w.name or w.workout_id}" for w in chosen[:8]]
        if len(chosen) > 8:
            names.append(tr("import.more", count=len(chosen) - 8))

        def go(_: Any) -> None:
            self.app.page.pop_dialog()
            self._start(chosen)

        self.app.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                bgcolor=self.app.p.surface,
                title=k.title(tr("cleanup.confirm.title", count=len(chosen)), 24),
                content=ft.Column(
                    [
                        k.text(tr("cleanup.confirm.text", folder=str(folder)), 14),
                        ft.Column([k.text(n, 13, muted=True) for n in names], spacing=2),
                        k.strong(tr("cleanup.confirm.undo"), 14),
                    ],
                    tight=True,
                    spacing=12,
                    width=520,
                ),
                actions=[
                    k.secondary(tr("common.cancel"), lambda _: self.app.page.pop_dialog()),
                    k.danger(
                        tr("cleanup.confirm.delete", count=len(chosen)),
                        go,
                        ft.Icons.DELETE_OUTLINE,
                    ),
                ],
            )
        )

    # --- running ---------------------------------------------------------------------------------

    def _start(self, chosen: list[Workout]) -> None:
        adapter = self.adapter
        if adapter is None or not chosen:
            return
        self.last_selection = chosen
        self.cancel_event = threading.Event()
        self.job = CleanupJob(visible=True, running=True, total=len(chosen))
        self.app.render()
        self.app._scroll_to_end()
        self.app.page.run_thread(
            self._worker, adapter, chosen, self._base_folder(), self.cancel_event
        )

    def _worker(
        self,
        adapter: WorkoutAdapter,
        chosen: list[Workout],
        out_dir: Path,
        cancel: threading.Event,
    ) -> None:
        summary: CleanupSummary | None = None
        try:
            cleaner = WorkoutCleaner(
                adapter, out_dir, on_event=self._on_event, delay_s=self.delay_s
            )
            self.backup_dir = cleaner.backup_dir
            summary = cleaner.run(chosen, cancel)
        except Exception as exc:  # report anything unexpected instead of freezing the UI
            log.exception("Workout clean-up crashed")
            self.job.message = self.app.tr("export.error", error=str(exc))
        finally:
            self.app._safe_persist()
        self.job.running = False
        self.job.cancelling = False
        if summary is not None:
            self._finish(summary)
        if self._visible():
            self.app.render()
            self.app._scroll_to_end()
        else:
            self.app.page.update()

    def _on_event(self, event: CleanupEvent | Waiting) -> None:
        job, tr = self.job, self.app.tr
        match event:
            case CleanupProgress(done, total, w):
                job.done, job.total = done, total
                job.step = tr("cleanup.now", name=w.name or w.workout_id)
                job.lines.append((f"[{done}/{total}] {w.name or w.workout_id}", "text"))
            case BackupSaved(_, paths, fit_missing):
                job.backed_up += 1
                files = ", ".join(p.name for p in paths)
                job.lines.append((f"    {tr('log.cleanup.backup', files=files)}", "success"))
                if fit_missing:
                    job.lines.append((f"    {tr('log.cleanup.no_fit')}", "muted"))
            case WorkoutDeleted(w):
                job.deleted += 1
                self.selected.discard(w.workout_id)
                job.lines.append((f"    {tr('log.cleanup.deleted')}", "success"))
            case CleanupFailed(_, stage, message):
                job.failed += 1
                key = "log.cleanup.failed_backup" if stage == "backup" else "log.cleanup.failed"
                job.lines.append((f"    {tr(key, error=message)}", "danger"))
            case Waiting(seconds, _):
                job.lines.append((f"    {tr('log.waiting', seconds=int(seconds))}", "muted"))
        del job.lines[:-MAX_LOG_LINES]
        self._refresh()

    def _finish(self, summary: CleanupSummary) -> None:
        tr, job = self.app.tr, self.job
        job.backed_up, job.deleted, job.failed = summary.backed_up, summary.deleted, summary.failed
        gone = set(summary.deleted_ids)
        self.workouts = [w for w in self.workouts if w.workout_id not in gone]
        self.selected -= gone
        if summary.cancelled:
            job.message, job.resumable = tr("cleanup.cancelled"), True
        elif summary.paused_reason:
            job.message, job.resumable = tr(f"progress.paused.{summary.paused_reason}"), True
            if summary.paused_reason == "authentication":
                self.app.adapter = None
        else:
            job.message = tr(
                "cleanup.result",
                deleted=summary.deleted,
                backed_up=summary.backed_up,
                folder=str(summary.backup_dir),
            )

    def _cancel(self, _: Any) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.job.cancelling = True
            self._refresh()

    def _open_backup(self, _: Any = None) -> None:
        from fitbatch.ui.app import open_in_explorer

        if self.backup_dir is not None:
            open_in_explorer(self.backup_dir)

    # --- progress --------------------------------------------------------------------------------

    def _fill_progress(self) -> None:
        p, k, tr, job = self.app.p, self.app.k, self.app.tr, self.job
        if not job.visible:
            self.w_progress.content = None
            return
        title = tr("cleanup.running") if job.running else tr("upload.done")
        if job.cancelling:
            title = tr("progress.cancelling")
        finished_ok = not job.running and not job.resumable and job.failed == 0 and job.total > 0
        bar_value: float | None = (
            None if job.running and job.done == 0 else (job.done / job.total if job.total else 0)
        )
        colors = {"text": p.text, "success": p.success, "muted": p.muted, "danger": p.danger}
        log_view = ft.ListView(
            [
                ft.Text(text, font_family=MONO, size=12.5, color=colors[kind], selectable=True)
                for text, kind in job.lines
            ],
            height=180,
            auto_scroll=True,
            spacing=2,
        )
        header: list[ft.Control] = []
        if job.running:
            header.append(ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=p.primary))
        header += [
            k.strong(title),
            ft.Text(
                tr("progress.count", done=job.done, total=job.total),
                font_family=DISPLAY,
                size=24,
                color=p.text,
            ),
            k.text(
                job.step if job.running else "",
                13,
                muted=True,
                expand=True,
                no_wrap=True,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
        ]
        message = (
            [k.text(job.message, 14, color=p.warning if job.resumable else p.success)]
            if job.message
            else []
        )
        self.w_progress.content = k.card(
            ft.Column(
                [
                    ft.Row(header, spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.ProgressBar(
                        value=1 if finished_ok else bar_value,
                        bar_height=10,
                        border_radius=999,
                        color=p.success if finished_ok else p.primary,
                        bgcolor=p.surface2,
                        semantics_label=title,
                    ),
                    ft.Row(
                        [
                            k.pill(tr("cleanup.count.backups", count=job.backed_up), "success"),
                            k.pill(tr("cleanup.count.deleted", count=job.deleted), "success"),
                            k.pill(
                                tr("progress.failed", count=job.failed),
                                "danger" if job.failed else "neutral",
                            ),
                        ],
                        spacing=8,
                        wrap=True,
                    ),
                    *message,
                    ft.Container(
                        log_view,
                        bgcolor=p.bg,
                        border=ft.Border.all(1, p.border),
                        border_radius=10,
                        padding=ft.Padding.symmetric(vertical=10, horizontal=14),
                    ),
                ],
                spacing=12,
            )
        )
