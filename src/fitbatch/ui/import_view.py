"""Import screen (F18): upload FIT, GPX and TCX files from disk to Garmin Connect.

The screen keeps its own state, so switching to another screen and back keeps the file list and
a running upload. Checking files (reading and hashing them) and uploading run in worker threads.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import flet as ft

from fitbatch.adapters.base import UploadAdapter
from fitbatch.core.export import Waiting
from fitbatch.core.models import Capability
from fitbatch.core.upload import (
    Uploaded,
    Uploader,
    UploadEvent,
    UploadFailed,
    UploadFile,
    Uploading,
    UploadProgress,
    UploadSkipped,
    UploadSummary,
    scan_upload_paths,
)
from fitbatch.ui.theme import DISPLAY, MONO

if TYPE_CHECKING:
    from fitbatch.ui.app import FitBatchApp

log = logging.getLogger(__name__)

MAX_ROWS = 200  # rows shown in the file table; the rest is counted (keeps the window fast)
MAX_LOG_LINES = 500
COLS = (None, 90, 240)  # file, format, status


@dataclass(slots=True)
class UploadJob:
    visible: bool = False
    running: bool = False
    cancelling: bool = False
    done: int = 0
    total: int = 0
    step: str = ""
    uploaded: int = 0
    duplicates: int = 0
    skipped: int = 0
    failed: int = 0
    lines: list[tuple[str, str]] = field(default_factory=list)
    message: str = ""
    resumable: bool = False


class ImportScreen:
    def __init__(self, app: FitBatchApp) -> None:
        self.app = app
        self.files: list[UploadFile] = []
        self.pending_hashes: set[str] = set()  # files that would really be sent
        self.status: dict[Path, tuple[str, str]] = {}  # path -> (text, pill kind) after upload
        self.subfolders = True
        self.scanning = False
        self.job = UploadJob()
        self.cancel_event: threading.Event | None = None
        self.notice = ""
        self.tab = "activities"  # "activities" (files) | "workouts" (planned)
        self._pending_for: str | None = None  # account the pending list was computed for

    # --- helpers ---------------------------------------------------------------------------------

    @property
    def adapter(self) -> UploadAdapter | None:
        adapter = self.app.adapter
        if adapter is None or Capability.UPLOAD not in adapter.capabilities:
            return None
        return adapter

    def _visible(self) -> bool:
        return self.app.screen == "import"

    def _refresh(self, *, table: bool = False) -> None:
        if self._visible():
            self._fill_progress()
            self._fill_action()
            if table:
                self._fill_files()
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
                        ("activities", tr("import.tab.activities")),
                        ("workouts", tr("import.tab.workouts")),
                    ],
                    self.tab,
                    select,
                )
            ]
        )
        if self.tab == "workouts":
            return ft.Column([tabs, self._workouts_view()], spacing=16), None
        body, footer = self._activities_view()
        return ft.Column([tabs, body], spacing=16), footer

    def _workouts_view(self) -> ft.Control:
        k, tr = self.app.k, self.app.tr
        return k.card(
            ft.Column(
                [
                    ft.Row(
                        [k.strong(tr("import.workouts.title"), 18), k.pill(tr("common.soon"))],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    k.text(tr("import.workouts.text"), 14),
                    k.text(tr("import.workouts.note"), 13, muted=True),
                ],
                spacing=10,
            )
        )

    def _activities_view(self) -> tuple[ft.Control, ft.Control]:
        k, tr = self.app.k, self.app.tr
        busy = self.scanning or self.job.running
        if self.files and not busy and self._account() != self._pending_for:
            self._compute_pending()  # logged in or out since the files were checked

        async def add_files(_: Any) -> None:
            picked = await self.app.folder_picker.pick_files(
                dialog_title=tr("import.dialog.files"),
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["fit", "gpx", "tcx", "gz"],
                allow_multiple=True,
            )
            paths = [Path(f.path) for f in picked or [] if f.path]
            if paths:
                self._add(paths)

        async def add_folder(_: Any) -> None:
            path = await self.app.folder_picker.get_directory_path(
                dialog_title=tr("import.dialog.folder")
            )
            if path:
                self._add([Path(path)])

        def set_subfolders(e: Any) -> None:
            self.subfolders = bool(e.control.value)

        def clear(_: Any) -> None:
            self.files, self.pending_hashes, self.status = [], set(), {}
            self.job = UploadJob()
            self.notice = ""
            self.app.render()

        add_files_button = k.secondary(tr("import.add_files"), add_files, ft.Icons.UPLOAD_FILE)
        add_folder_button = k.secondary(tr("import.add_folder"), add_folder, ft.Icons.FOLDER_OPEN)
        clear_button = k.quiet(tr("import.clear"), clear, ft.Icons.CLEAR_ALL)
        for button in (add_files_button, add_folder_button):
            button.disabled = busy
        clear_button.disabled = busy or not self.files

        intro = k.card(
            ft.Column(
                [
                    k.strong(tr("import.title"), 18),
                    k.text(tr("import.intro"), 14),
                    k.text(tr("import.how"), 13, muted=True),
                    ft.Row(
                        [
                            add_files_button,
                            add_folder_button,
                            k.checkbox(self.subfolders, set_subfolders, tr("import.subfolders")),
                            ft.Container(expand=True),
                            clear_button,
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=10,
            )
        )
        self.w_files = ft.Container()
        self.w_progress = ft.Container()
        self.w_action = ft.Container()
        self._fill_files()
        self._fill_progress()
        self._fill_action()
        body = ft.Column([intro, self.w_files, self.w_progress], spacing=16)
        return body, self.w_action

    # --- choosing and checking files -------------------------------------------------------------

    def _add(self, paths: list[Path]) -> None:
        self.scanning = True
        self.notice = ""
        self.app.render()
        self.app.page.run_thread(self._scan_worker, paths, self.subfolders)

    def _scan_worker(self, paths: list[Path], recursive: bool) -> None:
        try:
            known = {f.path.resolve() for f in self.files}
            added = [
                f
                for f in scan_upload_paths(paths, recursive=recursive)
                if f.path.resolve() not in known
            ]
            self.files = self.files + added
            self._compute_pending()
        except Exception:
            log.exception("Checking files failed")
        finally:
            self.scanning = False
        if self._visible():
            self.app.render()

    def _account(self) -> str | None:
        adapter = self.adapter
        return adapter.account if adapter is not None else None

    def _compute_pending(self) -> None:
        adapter = self.adapter
        self._pending_for = self._account()
        if adapter is None:
            self.pending_hashes = {f.sha256 for f in self.files if not f.problem}
            return
        with self.app.services.open_upload_registry() as registry:
            pending = Uploader(adapter, registry).pending(self.files)
        self.pending_hashes = {f.sha256 for f in pending}

    def _file_status(self, f: UploadFile) -> tuple[str, str]:
        tr = self.app.tr
        if f.path in self.status:
            return self.status[f.path]
        if f.problem:
            return tr(f"reason.{f.problem}"), "warning"
        if f.sha256 in self.pending_hashes:
            return tr("import.status.new"), "accent"
        if self.adapter is None:
            return tr("import.status.new"), "accent"
        return tr("reason.uploaded_before"), "neutral"

    def _fill_files(self) -> None:
        p, k, tr = self.app.p, self.app.k, self.app.tr
        if self.scanning:
            self.w_files.content = k.card(
                ft.Row(
                    [
                        ft.ProgressRing(width=18, height=18, stroke_width=2.5, color=p.primary),
                        k.text(tr("import.checking"), 14),
                    ],
                    spacing=12,
                )
            )
            return
        if not self.files:
            self.w_files.content = k.card(k.text(tr("import.empty"), 14, muted=True))
            return
        problems = sum(1 for f in self.files if f.problem)
        new = len(self.pending_hashes)
        before = len(self.files) - problems - new if self.adapter is not None else 0
        pills: list[ft.Control] = [
            k.pill(tr("import.count.files", count=len(self.files))),
            k.pill(tr("import.count.new", count=new), "accent"),
        ]
        if before > 0:
            pills.append(k.pill(tr("import.count.before", count=before)))
        if problems:
            pills.append(k.pill(tr("import.count.problems", count=problems), "warning"))
        extra: list[ft.Control] = []
        if self.app.adapter is None:
            extra.append(k.text(tr("import.offline_note"), 13, muted=True))

        def cell(control: ft.Control, i: int) -> ft.Container:
            return ft.Container(control, width=COLS[i], expand=COLS[i] is None)

        heads = [tr("import.col.file"), tr("import.col.format"), tr("import.col.status")]
        rows: list[ft.Control] = [
            ft.Container(
                ft.Row([cell(k.overline(h), i) for i, h in enumerate(heads)], spacing=0),
                bgcolor=p.surface2,
                padding=ft.Padding.symmetric(vertical=10, horizontal=20),
            )
        ]
        for f in self.files[:MAX_ROWS]:
            text, kind = self._file_status(f)
            name = ft.Column(
                [
                    ft.Text(
                        f.name,
                        font_family=MONO,
                        size=13,
                        color=p.text,
                        no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(
                        str(f.path.parent),
                        size=11.5,
                        color=p.muted,
                        no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=0,
                tight=True,
            )
            rows.append(
                ft.Container(
                    ft.Row(
                        [
                            cell(name, 0),
                            cell(k.text((f.fmt or "–").upper(), 13, muted=True), 1),
                            cell(ft.Row([k.pill(text, kind)], tight=True), 2),
                        ],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    height=56,
                    padding=ft.Padding.symmetric(vertical=0, horizontal=20),
                    border=ft.Border.only(top=ft.BorderSide(1, p.border)),
                    tooltip=str(f.path),
                )
            )
        if len(self.files) > MAX_ROWS:
            rows.append(
                ft.Container(
                    k.text(tr("import.more", count=len(self.files) - MAX_ROWS), 13, muted=True),
                    padding=ft.Padding.symmetric(vertical=12, horizontal=20),
                )
            )
        self.w_files.content = ft.Column(
            [
                ft.Row(pills, spacing=8, wrap=True),
                *extra,
                k.card(ft.Column(rows, spacing=0), padding=0),
            ],
            spacing=10,
        )

    # --- action bar ------------------------------------------------------------------------------

    def _fill_action(self) -> None:
        p, k, tr, job = self.app.p, self.app.k, self.app.tr, self.job
        buttons: list[ft.Control] = []
        if job.running:
            cancel = k.secondary(tr("action.cancel"), self._cancel, ft.Icons.CLOSE)
            cancel.disabled = job.cancelling
            buttons.append(cancel)
        else:
            if job.resumable:
                buttons.append(k.secondary(tr("action.resume"), self._resume, ft.Icons.REPLAY))
            upload = k.primary(
                tr("import.action", count=len(self.pending_hashes)),
                lambda _: self._start(),
                ft.Icons.UPLOAD_ROUNDED,
            )
            upload.disabled = self.scanning or not self.pending_hashes or self.adapter is None
            buttons.append(upload)
        if self.notice:
            status = k.text(self.notice, 13, color=p.danger, expand=True)
        elif self.app.adapter is None:
            status = k.text(tr("export.need_connection"), 13, muted=True, expand=True)
        else:
            status = ft.Container(expand=True)
        self.w_action.content = ft.Container(
            ft.Row(
                [status, *buttons],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=p.surface,
            border=ft.Border.all(1, p.border),
            border_radius=16,
            padding=ft.Padding.symmetric(vertical=12, horizontal=16),
        )

    # --- progress --------------------------------------------------------------------------------

    def _fill_progress(self) -> None:
        p, k, tr, job = self.app.p, self.app.k, self.app.tr, self.job
        if not job.visible:
            self.w_progress.content = None
            return
        title = tr("upload.running") if job.running else tr("upload.done")
        if job.cancelling:
            title = tr("progress.cancelling")
        finished_ok = not job.running and not job.resumable and job.total > 0
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
        message = (
            [k.text(job.message, 14, color=p.warning if job.resumable else p.text)]
            if job.message
            else []
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
                            k.pill(tr("upload.uploaded", count=job.uploaded), "success"),
                            k.pill(tr("upload.duplicates", count=job.duplicates)),
                            k.pill(tr("progress.skipped", count=job.skipped)),
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

    # --- running ---------------------------------------------------------------------------------

    def _start(self) -> None:
        adapter = self.adapter
        if adapter is None:
            self.notice = self.app.tr("export.need_connection")
            self._refresh()
            return
        if not self.pending_hashes:
            self.notice = self.app.tr("import.nothing")
            self._refresh()
            return
        self.cancel_event = threading.Event()
        self.notice = ""
        self.job = UploadJob(visible=True, running=True, total=len(self.files))
        self.app.render()
        self.app._scroll_to_end()
        self.app.page.run_thread(self._upload_worker, adapter, list(self.files), self.cancel_event)

    def _upload_worker(
        self, adapter: UploadAdapter, files: list[UploadFile], cancel: threading.Event
    ) -> None:
        summary: UploadSummary | None = None
        try:
            with self.app.services.open_upload_registry() as registry:
                uploader = Uploader(adapter, registry, on_event=self._on_event)
                summary = uploader.run(files, cancel)
            self._compute_pending()
        except Exception as exc:  # report anything unexpected instead of freezing the UI
            log.exception("Upload crashed")
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

    def _on_event(self, event: UploadEvent | Waiting) -> None:
        job, tr = self.job, self.app.tr
        match event:
            case Uploading(f):
                job.step = tr("upload.now", name=f.name)
                self._refresh()
                return
            case UploadProgress(done, total, f):
                job.step = ""
                job.done, job.total = done, total
                job.lines.append((f"[{done}/{total}] {f.name}", "text"))
            case Uploaded(f):
                job.uploaded += 1
                self.status[f.path] = (tr("import.status.uploaded"), "success")
                job.lines.append((f"    {tr('log.upload.uploaded')}", "success"))
            case UploadSkipped(f, reason):
                if reason == "duplicate":
                    job.duplicates += 1
                else:
                    job.skipped += 1
                reason_text = tr(f"reason.{reason}")
                kind = "warning" if f.problem else "neutral"
                self.status[f.path] = (reason_text, kind)  # the table says what the log says
                job.lines.append((f"    {tr('log.upload.skipped', reason=reason_text)}", "muted"))
            case UploadFailed(f, message):
                job.failed += 1
                self.status[f.path] = (tr("import.status.failed"), "danger")
                job.lines.append((f"    {tr('log.upload.failed', error=message)}", "danger"))
            case Waiting(seconds, _):
                job.lines.append((f"    {tr('log.waiting', seconds=int(seconds))}", "muted"))
        del job.lines[:-MAX_LOG_LINES]
        self._refresh()

    def _finish(self, summary: UploadSummary) -> None:
        tr, job = self.app.tr, self.job
        job.uploaded, job.duplicates = summary.uploaded, summary.duplicates
        job.skipped, job.failed = summary.skipped, summary.failed
        if summary.cancelled:
            job.message, job.resumable = tr("upload.cancelled"), True
        elif summary.paused_reason:
            job.message, job.resumable = tr(f"progress.paused.{summary.paused_reason}"), True
            if summary.paused_reason == "authentication":
                self.app.adapter = None

    def _cancel(self, _: Any) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.job.cancelling = True
            self._refresh()

    def _resume(self, _: Any) -> None:
        if self.app.adapter is None:
            self.app.go("connect")
            return
        self._start()  # the registry skips everything already uploaded
