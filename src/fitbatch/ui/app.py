"""Main window of FitBatch, built with Flet.

The window talks only to ``services`` and ``core`` (N15). Network work runs in background
threads so the window never freezes (N5); progress arrives as export events.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any

import flet as ft

from fitbatch import __version__
from fitbatch.adapters.base import AdapterError
from fitbatch.core.export import (
    Exporter,
    ExportEvent,
    ExportRequest,
    ExportSummary,
    Progress,
)
from fitbatch.core.filters import ActivityFilter
from fitbatch.core.login_guard import LoginLockedError
from fitbatch.core.models import Activity, ExportFormat
from fitbatch.core.settings import SUPPORTED_LANGUAGES, Settings
from fitbatch.i18n import Translator
from fitbatch.services import AppServices, create_services
from fitbatch.ui.logic import (
    EARLIEST,
    SHORTCUTS,
    activity_row,
    format_event,
    local_day_bounds,
    parse_day,
    remember_recent,
    shortcut_range,
    split_phrases,
)

log = logging.getLogger(__name__)

APP_TITLE = "FitBatch"
MAX_LOG_LINES = 500
MFA_TIMEOUT_S = 300


class FitBatchApp:
    def __init__(self, page: ft.Page, services: AppServices) -> None:
        self.page = page
        self.services = services
        self.settings: Settings = services.load_settings()
        self.tr = Translator(self.settings.language)
        self.adapter: Any = None  # connected adapter (PlatformAdapter) or None (offline)

        # export state
        self.listed: list[Activity] = []
        self.listed_range: tuple[datetime, datetime] | None = None
        self.matched: list[Activity] = []
        self.selected_ids: set[str] = set()
        self.cancel_event: threading.Event | None = None
        self.last_job: tuple[ExportRequest, list[Activity]] | None = None
        self.folder_picker = ft.FilePicker()

    # --- start-up ---------------------------------------------------------------------------------

    def start(self) -> None:
        self.page.title = f"{APP_TITLE} {__version__}"
        self.page.window.width = 1040
        self.page.window.height = 800
        self.page.padding = 20
        if self.services.garmin_email.has_saved_session():
            self._show_busy(self.tr("conn.restoring"))
            self.page.run_thread(self._restore_session)
        else:
            self.show_connection()

    def _restore_session(self) -> None:
        try:
            self.adapter = self.services.connect_saved_garmin_email()
        except AdapterError as exc:
            log.warning("Restoring the saved session failed: %s", exc)
            self.adapter = None
        if self.adapter is None:
            self.show_connection()
        else:
            self.show_main()

    def _show_busy(self, text: str) -> None:
        self.page.appbar = None
        self.page.controls.clear()
        self.page.add(ft.Row([ft.ProgressRing(), ft.Text(text)], spacing=16))
        self.page.update()

    # --- top bar (F4) -------------------------------------------------------------------------

    def _app_bar(self) -> ft.AppBar:
        tr = self.tr
        if self.adapter is not None:
            status = ft.TextButton(
                tr("status.connected", account=self.adapter.account_label),
                icon=ft.Icons.CLOUD_DONE,
                icon_color=ft.Colors.GREEN,
                on_click=lambda _: self._show_accounts(),
            )
        else:
            status = ft.TextButton(
                tr("status.offline"),
                icon=ft.Icons.CLOUD_OFF,
                icon_color=ft.Colors.GREY,
                on_click=lambda _: self._show_accounts(),
            )
        return ft.AppBar(
            title=ft.Text(APP_TITLE),
            actions=[
                status,
                ft.IconButton(
                    ft.Icons.SETTINGS,
                    tooltip=tr("common.settings"),
                    on_click=lambda _: self._show_settings(),
                ),
                ft.IconButton(
                    ft.Icons.INFO_OUTLINE,
                    tooltip=tr("common.about"),
                    on_click=lambda _: self._show_about(),
                ),
            ],
        )

    # --- connection screen (F1) -------------------------------------------------------------------

    def show_connection(self) -> None:
        self._on_main = False
        tr = self.tr
        email = ft.TextField(label=tr("conn.email"), width=360, autofocus=True)
        password = ft.TextField(
            label=tr("conn.password"), width=360, password=True, can_reveal_password=True
        )
        message = ft.Text(color=ft.Colors.ERROR)
        busy = ft.ProgressRing(visible=False, width=20, height=20)
        connect = ft.FilledButton(tr("conn.connect"), icon=ft.Icons.LOGIN)

        def do_connect(_: ft.Event | None = None) -> None:
            address, secret = (email.value or "").strip(), password.value or ""
            password.value = ""  # N2: do not keep the password in the widget
            if not address or not secret:
                message.value = tr("conn.missing_fields")
                self.page.update()
                return
            message.value = tr("conn.connecting")
            message.color = ft.Colors.ON_SURFACE_VARIANT
            busy.visible, connect.disabled = True, True
            self.page.update()
            self.page.run_thread(self._login, address, secret, message, busy, connect)

        connect.on_click = do_connect
        password.on_submit = do_connect

        self.page.appbar = self._app_bar()
        self.page.controls.clear()
        self.page.add(
            ft.Column(
                [
                    ft.Text(tr("conn.title"), size=22, weight=ft.FontWeight.BOLD),
                    ft.Text(tr("conn.platform"), weight=ft.FontWeight.BOLD),
                    ft.RadioGroup(
                        value="garmin",
                        content=ft.Row(
                            [
                                ft.Radio(value="garmin", label="Garmin Connect"),
                                ft.Radio(
                                    value="suunto",
                                    label=f"Suunto ({tr('common.soon')})",
                                    disabled=True,
                                ),
                            ]
                        ),
                    ),
                    ft.Text(tr("conn.method"), weight=ft.FontWeight.BOLD),
                    ft.RadioGroup(
                        value="email",
                        content=ft.Column(
                            [
                                ft.Radio(
                                    value="api_key", label=tr("conn.method.api_key"), disabled=True
                                ),
                                ft.Radio(value="email", label=tr("conn.method.email")),
                            ]
                        ),
                    ),
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.WARNING_AMBER, color=ft.Colors.AMBER),
                            ft.Text(tr("conn.email_warning"), expand=True),
                        ],
                        width=620,
                    ),
                    email,
                    password,
                    ft.Row([connect, busy]),
                    message,
                    ft.TextButton(tr("conn.skip"), on_click=lambda _: self.show_main()),
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )
        )
        self.page.update()

    def _login(
        self,
        email: str,
        password: str,
        message: ft.Text,
        busy: ft.ProgressRing,
        connect: ft.FilledButton,
    ) -> None:
        tr = self.tr
        try:
            self.adapter = self.services.login_garmin_email(email, password, self._ask_mfa)
        except LoginLockedError as exc:
            message.value = tr("conn.locked", minutes=int(exc.remaining_s // 60) + 1)
        except AdapterError as exc:
            message.value = tr("conn.failed", error=str(exc))
        else:
            self.show_main()
            return
        finally:
            del password
        message.color = ft.Colors.ERROR
        busy.visible, connect.disabled = False, False
        self.page.update()

    def _ask_mfa(self) -> str:
        """Called by the login thread when Garmin asks for a code; waits for the dialog."""
        tr = self.tr
        done = threading.Event()
        result = {"code": ""}
        code = ft.TextField(label=tr("conn.mfa_title"), autofocus=True, width=200)

        def submit(_: ft.Event | None = None) -> None:
            result["code"] = (code.value or "").strip()
            self.page.pop_dialog()
            done.set()

        code.on_submit = submit
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                title=ft.Text(tr("conn.mfa_title")),
                content=ft.Column([ft.Text(tr("conn.mfa_text")), code], tight=True),
                actions=[ft.TextButton(tr("common.ok"), on_click=submit)],
            )
        )
        done.wait(MFA_TIMEOUT_S)
        return result["code"]

    # --- dialogs: accounts (F3), settings (F21), about (N10) -------------------------------------

    def _show_accounts(self) -> None:
        tr = self.tr
        flow = self.services.garmin_email
        if self.adapter is not None:
            status = tr("accounts.email_connected", account=self.adapter.account_label)
        elif flow.has_saved_session():
            status = tr("accounts.email_saved")
        else:
            status = tr("accounts.email_none")

        def delete(_: ft.Event) -> None:
            flow.logout()
            self.adapter = None
            self.page.pop_dialog()
            self.show_connection()

        def connect(_: ft.Event) -> None:
            self.page.pop_dialog()
            self.show_connection()

        actions: list[ft.Control] = []
        if self.adapter is not None or flow.has_saved_session():
            actions.append(ft.TextButton(tr("accounts.delete"), on_click=delete))
        else:
            actions.append(ft.TextButton(tr("accounts.connect"), on_click=connect))
        actions.append(ft.TextButton(tr("common.close"), on_click=lambda _: self.page.pop_dialog()))
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(tr("accounts.title")),
                content=ft.Column(
                    [
                        ft.Text("Garmin Connect", weight=ft.FontWeight.BOLD),
                        ft.Text(status),
                        ft.Text(tr("accounts.api_key"), color=ft.Colors.ON_SURFACE_VARIANT),
                        ft.Text(f"Suunto: {tr('common.soon')}", color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                    tight=True,
                ),
                actions=actions,
            )
        )

    def _show_settings(self) -> None:
        tr = self.tr
        language = ft.Dropdown(
            label=tr("settings.language"),
            value=self.settings.language,
            options=[
                ft.DropdownOption(key=lang, text=lang.upper()) for lang in SUPPORTED_LANGUAGES
            ],
            width=160,
        )
        folder = ft.TextField(
            label=tr("settings.default_folder"), value=self.settings.download_dir, width=460
        )
        formats = {
            fmt: ft.Checkbox(
                label=tr(f"format.{fmt.value}"), value=fmt.value in self.settings.formats
            )
            for fmt in ExportFormat
        }

        def save(_: ft.Event) -> None:
            self.settings.language = language.value or self.settings.language
            self.settings.download_dir = (folder.value or "").strip() or self.settings.download_dir
            chosen = [fmt.value for fmt, box in formats.items() if box.value]
            self.settings.formats = chosen or self.settings.formats
            self.settings.save(self.services.paths.settings_file)
            self.tr.language = self.settings.language
            self.page.pop_dialog()
            if self._on_main:
                self.show_main()
            else:
                self.show_connection()

        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(tr("common.settings")),
                content=ft.Column(
                    [
                        language,
                        folder,
                        ft.Text(tr("settings.default_formats"), weight=ft.FontWeight.BOLD),
                        *formats.values(),
                        ft.Text(tr("settings.restart_hint"), size=12),
                    ],
                    tight=True,
                ),
                actions=[
                    ft.TextButton(tr("common.cancel"), on_click=lambda _: self.page.pop_dialog()),
                    ft.FilledButton(tr("common.save"), on_click=save),
                ],
            )
        )

    def _show_about(self) -> None:
        tr = self.tr
        self.page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(f"{APP_TITLE} {__version__}"),
                content=ft.Text(
                    f"{tr('app.tagline')}\n\n{tr('app.disclaimer')}\n\n{tr('app.license')}"
                ),
                actions=[ft.TextButton(tr("common.ok"), on_click=lambda _: self.page.pop_dialog())],
            )
        )

    # --- main screen: export (F5–F16) -------------------------------------------------------------

    _on_main = False

    def show_main(self) -> None:
        self._on_main = True
        tr = self.tr
        today = date.today()
        first, last = shortcut_range("30d", today)
        if self.listed_range is not None:
            first = self.listed_range[0].date()
            last = self.listed_range[1].date()

        self.date_from = ft.TextField(label=tr("export.from"), value=first.isoformat(), width=170)
        self.date_to = ft.TextField(label=tr("export.to"), value=last.isoformat(), width=170)
        self.range_error = ft.Text(color=ft.Colors.ERROR)

        def calendar_for(field: ft.TextField) -> ft.IconButton:
            def pick(_: ft.Event) -> None:
                def chosen(e: ft.Event) -> None:
                    value = e.control.value
                    if value is not None:
                        field.value = (
                            value.date() if isinstance(value, datetime) else value
                        ).isoformat()
                        self.page.update()

                self.page.show_dialog(
                    ft.DatePicker(
                        value=parse_day(field.value or "") or today,
                        first_date=datetime.combine(EARLIEST, datetime.min.time()),
                        last_date=datetime.combine(today, datetime.min.time()),
                        on_change=chosen,
                    )
                )

            return ft.IconButton(ft.Icons.CALENDAR_MONTH, on_click=pick)

        def apply_shortcut(name: str) -> None:
            a, b = shortcut_range(name, date.today())
            self.date_from.value, self.date_to.value = a.isoformat(), b.isoformat()
            self.page.update()

        shortcuts = [
            ft.OutlinedButton(
                tr(f"export.range.{name}"), on_click=lambda _, n=name: apply_shortcut(n)
            )
            for name in SHORTCUTS
        ]

        self.format_boxes = {
            fmt: ft.Checkbox(
                label=tr(f"format.{fmt.value}"), value=fmt.value in self.settings.formats
            )
            for fmt in ExportFormat
        }
        self.include = ft.TextField(
            label=tr("export.include"),
            width=380,
            hint_text=", ".join(self.settings.recent_include[:3]),
            on_change=lambda _: self._refresh_matches(),
        )
        self.exclude = ft.TextField(
            label=tr("export.exclude"),
            width=380,
            hint_text=", ".join(self.settings.recent_exclude[:3]),
            on_change=lambda _: self._refresh_matches(),
        )
        self.type_boxes: dict[str, ft.Checkbox] = {}
        self.types_row = ft.Row(wrap=True, spacing=4)
        self.folder = ft.TextField(
            label=tr("export.folder"), value=self.settings.download_dir, expand=True
        )

        async def choose_folder(_: ft.Event) -> None:
            path = await self.folder_picker.get_directory_path(
                dialog_title=tr("export.folder"), initial_directory=self.folder.value or None
            )
            if path:
                self.folder.value = path
                self.page.update()

        self.overwrite = ft.Checkbox(label=tr("export.overwrite"), value=False)
        self.list_button = ft.FilledButton(
            tr("export.list"), icon=ft.Icons.LIST, on_click=lambda _: self._start_listing()
        )
        self.match_text = ft.Text()
        self.activity_list = ft.ListView(height=240, spacing=0)
        self.start_button = ft.FilledButton(
            tr("export.start"), icon=ft.Icons.DOWNLOAD, on_click=lambda _: self._start_export()
        )
        self.cancel_button = ft.OutlinedButton(
            tr("export.cancel"), icon=ft.Icons.CANCEL, visible=False, on_click=self._cancel
        )
        self.resume_button = ft.OutlinedButton(
            tr("export.resume"), icon=ft.Icons.REPLAY, visible=False, on_click=self._resume
        )
        self.open_button = ft.OutlinedButton(
            tr("export.open_folder"),
            icon=ft.Icons.FOLDER_OPEN,
            visible=False,
            on_click=self._open_folder,
        )
        self.progress = ft.ProgressBar(value=0, visible=False)
        self.progress_text = ft.Text()
        self.summary_text = ft.Text(weight=ft.FontWeight.BOLD)
        self.log_view = ft.ListView(height=180, spacing=0, auto_scroll=True)

        self.page.appbar = self._app_bar()
        self.page.controls.clear()
        self.page.add(
            ft.Column(
                [
                    ft.Row(
                        [
                            self.date_from,
                            calendar_for(self.date_from),
                            self.date_to,
                            calendar_for(self.date_to),
                            *shortcuts,
                        ],
                        wrap=True,
                    ),
                    self.range_error,
                    ft.Text(tr("export.formats"), weight=ft.FontWeight.BOLD),
                    ft.Row(list(self.format_boxes.values()), wrap=True),
                    ft.Row([self.include, self.exclude], wrap=True),
                    ft.Text(tr("export.types"), weight=ft.FontWeight.BOLD),
                    self.types_row,
                    ft.Row(
                        [
                            self.folder,
                            ft.OutlinedButton(
                                tr("export.choose_folder"),
                                icon=ft.Icons.FOLDER_OPEN,
                                on_click=choose_folder,
                            ),
                        ]
                    ),
                    self.overwrite,
                    ft.Row([self.list_button, self.match_text], wrap=True),
                    ft.Row(
                        [
                            ft.TextButton(
                                tr("export.select_all"), on_click=lambda _: self._select(True)
                            ),
                            ft.TextButton(
                                tr("export.select_none"), on_click=lambda _: self._select(False)
                            ),
                        ]
                    ),
                    ft.Container(self.activity_list, border=ft.Border.all(1, ft.Colors.OUTLINE)),
                    ft.Row(
                        [
                            self.start_button,
                            self.cancel_button,
                            self.resume_button,
                            self.open_button,
                        ],
                        wrap=True,
                    ),
                    self.progress,
                    self.progress_text,
                    self.summary_text,
                    ft.Container(self.log_view, border=ft.Border.all(1, ft.Colors.OUTLINE)),
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            )
        )
        self._rebuild_types()
        self._refresh_matches()
        self.page.update()

    # --- listing and filters (F7, F8) ------------------------------------------------------------

    def _read_range(self) -> tuple[datetime, datetime] | None:
        tr = self.tr
        first = parse_day(self.date_from.value or "")
        last = parse_day(self.date_to.value or "")
        self.date_from.error = None if first else tr("export.bad_date")
        self.date_to.error = None if last else tr("export.bad_date")
        if first is None or last is None:
            self.range_error.value = ""
            return None
        if first > last:
            self.range_error.value = tr("export.bad_range")
            return None
        self.range_error.value = ""
        return local_day_bounds(first, last)

    def _start_listing(self) -> None:
        tr = self.tr
        if self.adapter is None:
            self.summary_text.value = tr("export.need_connection")
            self.page.update()
            return
        bounds = self._read_range()
        if bounds is None:
            self.page.update()
            return
        self.list_button.disabled = True
        self.match_text.value = tr("export.listing")
        self.page.update()
        self.page.run_thread(self._list_worker, bounds)

    def _list_worker(self, bounds: tuple[datetime, datetime]) -> None:
        try:
            self.listed = list(self.adapter.list_activities(*bounds))
            self.listed_range = bounds
            self.selected_ids = {a.activity_id for a in self.listed}
            self.summary_text.value = ""
        except AdapterError as exc:
            self.summary_text.value = self.tr("export.error", error=str(exc))
        finally:
            self.list_button.disabled = False
        self._rebuild_types()
        self._refresh_matches()
        self.page.update()

    def _rebuild_types(self) -> None:
        present = sorted({a.type_key for a in self.listed})
        previous = {k: box.value for k, box in self.type_boxes.items()}
        self.type_boxes = {
            key: ft.Checkbox(
                label=key,
                value=previous.get(key, True),
                on_change=lambda _: self._refresh_matches(),
            )
            for key in present
        }
        self.types_row.controls = list(self.type_boxes.values())

    def _current_filter(self) -> ActivityFilter:
        chosen = [k for k, box in self.type_boxes.items() if box.value]
        all_types = len(chosen) == len(self.type_boxes)
        return ActivityFilter(
            include_names=split_phrases(self.include.value or ""),
            exclude_names=split_phrases(self.exclude.value or ""),
            type_keys=[] if all_types else (chosen or ["<none>"]),
        )

    def _refresh_matches(self) -> None:
        self.matched = self._current_filter().apply(self.listed)
        rows: list[ft.Control] = []
        for activity in self.matched:

            def toggle(e: ft.Event, activity_id: str = activity.activity_id) -> None:
                if e.control.value:
                    self.selected_ids.add(activity_id)
                else:
                    self.selected_ids.discard(activity_id)
                self._update_counter()

            rows.append(
                ft.Checkbox(
                    label=activity_row(activity),
                    value=activity.activity_id in self.selected_ids,
                    on_change=toggle,
                )
            )
        self.activity_list.controls = rows
        if self.listed_range is not None:
            self._update_counter(update=False)
        else:
            self.match_text.value = ""
        self.page.update()

    def _selected_activities(self) -> list[Activity]:
        return [a for a in self.matched if a.activity_id in self.selected_ids]

    def _update_counter(self, update: bool = True) -> None:
        tr = self.tr
        self.match_text.value = (
            f"{tr('export.matches', matched=len(self.matched), total=len(self.listed))} · "
            f"{tr('export.selected', count=len(self._selected_activities()))}"
        )
        if update:
            self.page.update()

    def _select(self, value: bool) -> None:
        ids = {a.activity_id for a in self.matched}
        self.selected_ids = self.selected_ids | ids if value else self.selected_ids - ids
        self._refresh_matches()

    # --- export run (F13–F16) -------------------------------------------------------------------

    def _start_export(self) -> None:
        tr = self.tr
        if self.adapter is None or self.listed_range is None:
            self.summary_text.value = tr("export.need_connection")
            self.page.update()
            return
        formats = [fmt for fmt, box in self.format_boxes.items() if box.value]
        if not formats:
            self.summary_text.value = tr("export.no_format")
            self.page.update()
            return
        activities = self._selected_activities()
        if not activities:
            self.summary_text.value = tr("export.nothing_selected")
            self.page.update()
            return
        request = ExportRequest(
            start=self.listed_range[0],
            end=self.listed_range[1],
            formats=formats,
            out_dir=Path(self.folder.value or self.settings.download_dir).expanduser(),
            activity_filter=self._current_filter(),
            overwrite=bool(self.overwrite.value),
        )
        self._remember_phrases()
        self._run(request, activities)

    def _remember_phrases(self) -> None:
        self.settings.recent_include = remember_recent(
            self.settings.recent_include, split_phrases(self.include.value or "")
        )
        self.settings.recent_exclude = remember_recent(
            self.settings.recent_exclude, split_phrases(self.exclude.value or "")
        )
        self.settings.save(self.services.paths.settings_file)

    def _run(self, request: ExportRequest, activities: list[Activity]) -> None:
        self.last_job = (request, activities)
        self.cancel_event = threading.Event()
        self.log_view.controls.clear()
        self.summary_text.value = ""
        self.progress.value, self.progress.visible = 0, True
        self._set_running(True)
        self.page.run_thread(self._export_worker, request, activities, self.cancel_event)

    def _set_running(self, running: bool) -> None:
        self.start_button.disabled = running
        self.list_button.disabled = running
        self.cancel_button.visible = running
        self.cancel_button.disabled = False
        if running:
            self.resume_button.visible = False
            self.open_button.visible = False
        self.page.update()

    def _export_worker(
        self, request: ExportRequest, activities: list[Activity], cancel: threading.Event
    ) -> None:
        tr = self.tr
        summary: ExportSummary | None = None
        try:
            with self.services.open_registry() as registry:
                exporter = Exporter(self.adapter, registry, on_event=self._on_event)
                summary = exporter.run(request, activities, cancel)
        except Exception as exc:  # report anything unexpected instead of freezing the UI
            log.exception("Export crashed")
            self.summary_text.value = tr("export.error", error=str(exc))
        finally:
            self._safe_persist()
        if summary is not None:
            self._show_summary(summary)
        self._set_running(False)

    def _safe_persist(self) -> None:
        try:
            if self.adapter is not None:
                self.adapter.persist_session()
        except Exception:  # never let session saving break the UI
            log.exception("Saving the session failed")

    def _on_event(self, event: ExportEvent) -> None:
        if isinstance(event, Progress):
            self.progress.value = event.done / max(event.total, 1)
            self.progress_text.value = self.tr(
                "export.progress", done=event.done, total=event.total, name=event.activity.name
            )
            self._log(f"[{event.done}/{event.total}] {activity_row(event.activity)}")
        else:
            line = format_event(event, self.tr)
            if line:
                self._log(f"    {line}")
        self.page.update()

    def _log(self, line: str) -> None:
        self.log_view.controls.append(ft.Text(line, size=12, selectable=True))
        if len(self.log_view.controls) > MAX_LOG_LINES:
            del self.log_view.controls[: len(self.log_view.controls) - MAX_LOG_LINES]

    def _show_summary(self, summary: ExportSummary) -> None:
        tr = self.tr
        text = tr(
            "export.summary", saved=summary.saved, skipped=summary.skipped, failed=summary.failed
        )
        if summary.cancelled:
            text += " " + tr("export.cancelled")
        if summary.paused_reason:
            text += " " + tr(f"export.paused.{summary.paused_reason}")
            if summary.paused_reason == "authentication":
                self.adapter = None
                self.page.appbar = self._app_bar()
        self.summary_text.value = text
        self.resume_button.visible = summary.cancelled or summary.paused_reason is not None
        self.open_button.visible = True
        if not (summary.cancelled or summary.paused_reason):
            self.progress.value = 1

    def _cancel(self, _: ft.Event) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.cancel_button.disabled = True
            self.progress_text.value = self.tr("export.cancelling")
            self.page.update()

    def _resume(self, _: ft.Event) -> None:
        if self.last_job is None:
            return
        if self.adapter is None:
            self.show_connection()
            return
        self._run(*self.last_job)  # the registry skips everything already saved (F15)

    def _open_folder(self, _: ft.Event) -> None:
        folder = self.last_job[0].out_dir if self.last_job else Path(self.folder.value or ".")
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606 - opens the user's own export folder in Explorer


def main() -> None:
    from fitbatch.logging_setup import setup_logging

    services = create_services()
    setup_logging(services.paths.logs_dir)

    def build(page: ft.Page) -> None:
        FitBatchApp(page, services).start()

    ft.run(build)


if __name__ == "__main__":
    main()
