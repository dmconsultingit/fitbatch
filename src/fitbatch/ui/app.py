"""Main window of FitBatch, built with Flet, in the FitBatch design system.

The window talks only to ``services`` and ``core`` (N15). Network work runs in background
threads so the window never freezes (N5); progress arrives as export events and is kept in
plain state, so a theme switch or a screen change during a download loses nothing.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import flet as ft

from fitbatch import __version__
from fitbatch.adapters.base import AdapterError
from fitbatch.core.activity_types import catalog, describe
from fitbatch.core.export import (
    Downloading,
    Exporter,
    ExportEvent,
    ExportRequest,
    ExportSummary,
    FileFailed,
    FileSaved,
    FileSkipped,
    Progress,
    default_file_name,
    export_root,
    format_dir,
)
from fitbatch.core.filters import ActivityFilter
from fitbatch.core.library import scan_folders
from fitbatch.core.login_guard import LoginLockedError
from fitbatch.core.models import Activity, ExportFormat
from fitbatch.core.settings import Settings
from fitbatch.i18n import Translator
from fitbatch.project import REPOSITORY_URL, SUPPORT_URL
from fitbatch.services import AppServices, create_services
from fitbatch.ui.components import Kit
from fitbatch.ui.logic import (
    EARLIEST,
    SHORTCUTS,
    activity_row,
    files_to_download,
    filter_summary,
    format_event,
    local_day_bounds,
    local_time,
    parse_day,
    remember_recent,
    shortcut_range,
    split_phrases,
)
from fitbatch.ui.theme import (
    ASSETS_DIR,
    BODY,
    BODY_SEMIBOLD,
    DISPLAY,
    FONTS,
    MONO,
    Palette,
    flet_theme,
    palette_for,
)

log = logging.getLogger(__name__)

APP_TITLE = "FitBatch"
MAX_LOG_LINES = 500
MFA_TIMEOUT_S = 300
SIDEBAR_WIDTH = 236

COLS = (44, 150, None, 220, 120)  # table (F8): checkbox, date, name, type, status
PAGE_SIZE = 50  # activities per table page: keeps the window fast with thousands of rows (N6)
MAX_SERVER_CATEGORIES = 4  # more chosen categories than this: list everything, filter locally
LOGO_FILE = "logo.png"  # optional: put the final logo in ui/assets/ and it replaces the wordmark


@dataclass(slots=True)
class JobState:
    """Download progress kept outside the widgets (survives re-rendering)."""

    visible: bool = False
    running: bool = False
    cancelling: bool = False
    done: int = 0
    total: int = 0
    current: str = ""
    step: str = ""  # what is happening right now
    saved: int = 0
    skipped: int = 0
    failed: int = 0
    lines: list[tuple[str, str]] = field(default_factory=list)  # (text, kind)
    message: str = ""
    resumable: bool = False


class FitBatchApp:
    def __init__(self, page: ft.Page, services: AppServices) -> None:
        self.page = page
        self.services = services
        self.settings: Settings = services.load_settings()
        self.tr = Translator(self.settings.language)
        self.adapter: Any = None  # connected adapter, or None (offline)
        self.screen = "export"
        self.folder_picker = ft.FilePicker()

        # export state
        first, last = shortcut_range("30d", date.today())
        self.date_from, self.date_to, self.range_key = first.isoformat(), last.isoformat(), "30d"
        self.formats: set[ExportFormat] = {
            f for f in ExportFormat if f.value in self.settings.formats
        } or {ExportFormat.FIT}
        self.include_text = ""
        self.exclude_text = ""
        # F7c: chosen type keys from Garmin's catalogue; None = every type (no type filter)
        chosen = set(self.settings.activity_types) & catalog().all_keys()
        self.types_selected: set[str] | None = chosen or None
        self.show_all_categories = False
        self.expanded_categories: set[str] = set()
        self.applied: tuple[str, str, list[str] | None] | None = None  # range + categories
        self.types_open = False
        self.type_query = ""
        self.page_index = 0
        self.folder = self.settings.download_dir
        self.overwrite = False
        self.listed: list[Activity] = []
        self.listed_range: tuple[datetime, datetime] | None = None
        self.listing = False
        self.matched: list[Activity] = []
        self.selected_ids: set[str] = set()
        self.notice = ""
        self.job = JobState()
        self.cancel_event: threading.Event | None = None
        self.last_job: tuple[ExportRequest, list[Activity]] | None = None

    # ===== start-up and rendering =================================================================

    @property
    def p(self) -> Palette:
        platform_dark = self.page.platform_brightness == ft.Brightness.DARK
        return palette_for(self.settings.theme, platform_dark)

    def start(self) -> None:
        page = self.page
        page.title = f"{APP_TITLE} {__version__}"
        page.fonts = dict(FONTS)
        page.window.width = 1280
        page.window.height = 860
        page.window.min_width = 1000
        page.window.min_height = 640
        page.padding = 0
        page.on_platform_brightness_change = lambda _: self.render()
        if self.services.garmin_email.has_saved_session():
            self.screen = "busy"
            self.render()
            page.run_thread(self._restore_session)
        else:
            self.screen = "connect"
            self.render()

    def _restore_session(self) -> None:
        try:
            self.adapter = self.services.connect_saved_garmin_email()
        except AdapterError as exc:
            log.warning("Restoring the saved session failed: %s", exc)
            self.adapter = None
        self.screen = "export" if self.adapter is not None else "connect"
        self.render()
        self._refresh_catalog_in_background()

    def _refresh_catalog_in_background(self) -> None:
        """Keep Garmin's type list current (at most weekly); the bundled copy works meanwhile."""
        adapter = self.adapter
        if adapter is None:
            return

        def work() -> None:
            if self.services.refresh_catalog(adapter) and self.screen == "export":
                self._fill_filters()
                self.page.update()

        self.page.run_thread(work)

    def go(self, screen: str) -> None:
        self.screen = screen
        self.render()

    def render(self) -> None:
        p = self.p
        page = self.page
        page.theme = flet_theme(p)
        page.dark_theme = flet_theme(p)
        page.theme_mode = ft.ThemeMode.DARK if p.dark else ft.ThemeMode.LIGHT
        page.bgcolor = p.bg
        self.k = Kit(p)

        builders = {
            "busy": self._busy_view,
            "connect": self._connect_view,
            "export": self._export_view,
            "files": self._files_view,
            "accounts": self._accounts_view,
            "settings": self._settings_view,
        }
        body, footer = builders[self.screen]()
        main_column = ft.Column(
            [
                ft.Container(self._header(), padding=ft.Padding.only(left=32, right=32, top=24)),
                ft.Container(
                    self._scroll_column(body),
                    padding=ft.Padding.only(left=32, right=32, top=16, bottom=16),
                    expand=True,
                ),
                *(
                    [ft.Container(footer, padding=ft.Padding.only(left=32, right=32, bottom=20))]
                    if footer
                    else []
                ),
            ],
            spacing=0,
            expand=True,
        )
        page.controls.clear()
        page.add(ft.Row([self._sidebar(), main_column], spacing=0, expand=True))
        page.update()

    def _scroll_column(self, body: ft.Control) -> ft.Column:
        self.w_scroll = ft.Column([body], scroll=ft.ScrollMode.AUTO, expand=True)
        return self.w_scroll

    def _scroll_to_end(self) -> None:
        self.page.run_task(self.w_scroll.scroll_to, offset=-1, duration=400)

    # ===== sidebar and header (F4) ================================================================

    def _sidebar(self) -> ft.Container:
        p, k, tr = self.p, self.k, self.tr
        logo = self._logo()

        def nav(key: str, icon: Any, label: str, *, disabled: bool = False) -> ft.TextButton:
            active = self.screen == key or (key == "accounts" and self.screen == "connect")
            color = p.primary_text if active else (p.muted if disabled else p.text)
            content: list[ft.Control] = [
                ft.Icon(icon, size=20, color=color),
                ft.Text(label, font_family=BODY_SEMIBOLD if active else BODY, size=15, color=color),
            ]
            if disabled:
                content.append(ft.Container(expand=True))
                content.append(k.pill(tr("common.soon")))
            return ft.TextButton(
                content=ft.Row(content, spacing=12),
                on_click=None if disabled else (lambda _: self.go(key)),
                disabled=disabled,
                height=44,
                width=SIDEBAR_WIDTH - 28,
                style=ft.ButtonStyle(
                    bgcolor=p.primary_soft if active else ft.Colors.TRANSPARENT,
                    shape=ft.RoundedRectangleBorder(radius=10),
                    padding=ft.Padding.symmetric(vertical=0, horizontal=12),
                    alignment=ft.Alignment.CENTER_LEFT,
                ),
            )

        def set_theme(value: str) -> None:
            self.settings.theme = value
            self._save_settings()
            self.render()

        current_theme = "dark" if p.dark else "light"
        return ft.Container(
            ft.Column(
                [
                    ft.Container(logo, padding=ft.Padding.only(left=8, bottom=18, top=4)),
                    nav("export", ft.Icons.DOWNLOAD_ROUNDED, tr("nav.export")),
                    nav("files", ft.Icons.FOLDER_OUTLINED, tr("nav.files")),
                    nav("import", ft.Icons.UPLOAD_ROUNDED, tr("nav.import"), disabled=True),
                    nav("accounts", ft.Icons.KEY_OUTLINED, tr("nav.accounts")),
                    nav("settings", ft.Icons.TUNE, tr("nav.settings")),
                    ft.Container(expand=True),
                    self._support_card(),
                    ft.Container(height=8),
                    k.text(tr("nav.theme"), 12, muted=True),
                    k.segmented(
                        [("dark", tr("theme.dark")), ("light", tr("theme.light"))],
                        current_theme,
                        set_theme,
                    ),
                    ft.Row(
                        [
                            k.text(f"FitBatch {__version__} · GPL-3.0", 11, muted=True),
                            ft.IconButton(
                                ft.Icons.INFO_OUTLINE,
                                icon_color=p.muted,
                                icon_size=18,
                                tooltip=tr("common.about"),
                                on_click=lambda _: self._show_about(),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                ],
                spacing=4,
                expand=True,
            ),
            width=SIDEBAR_WIDTH,
            bgcolor=p.surface,
            border=ft.Border.only(right=ft.BorderSide(1, p.border)),
            padding=ft.Padding.only(left=14, right=14, top=20, bottom=12),
        )

    def _logo(self) -> ft.Control:
        """The final logo when ``ui/assets/logo.png`` exists, otherwise the interim wordmark."""
        p, k = self.p, self.k
        if (ASSETS_DIR / LOGO_FILE).is_file():
            return ft.Image(
                src=LOGO_FILE, height=44, fit=ft.BoxFit.CONTAIN, semantics_label="FitBatch"
            )
        mark = ft.Container(
            ft.Icon(ft.Icons.BAR_CHART_ROUNDED, color=p.on_primary, size=22),
            width=36,
            height=36,
            border_radius=9,
            bgcolor=p.primary,
            alignment=ft.Alignment.CENTER,
        )
        words = ft.Column(
            [
                ft.Text("FitBatch", font_family=DISPLAY, size=24, color=p.text),
                k.text("for Garmin Connect", 11, muted=True),
            ],
            spacing=0,
        )
        return ft.Row([mark, words], spacing=10)

    def _support_card(self) -> ft.Container:
        """Voluntary support (buycoffee.to / Ko-fi style); the link lives in ``project.py``."""
        p, k, tr = self.p, self.k, self.tr
        button = ft.TextButton(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.FAVORITE_BORDER, color=p.primary_text, size=18),
                    ft.Text(tr("support.button"), font_family=BODY_SEMIBOLD, size=14),
                ],
                spacing=8,
                tight=True,
            ),
            url=SUPPORT_URL,
            tooltip=SUPPORT_URL,
            height=40,
            style=ft.ButtonStyle(
                color=p.primary_text,
                bgcolor=p.primary_soft,
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(vertical=0, horizontal=12),
            ),
        )
        return ft.Container(
            ft.Column([k.text(tr("support.text"), 12, muted=True), button], spacing=8),
            border=ft.Border.all(1, p.border),
            border_radius=12,
            padding=12,
        )

    def _header(self) -> ft.Row:
        p, tr = self.p, self.tr
        titles = {
            "busy": APP_TITLE,
            "connect": tr("conn.title"),
            "export": tr("export.title"),
            "files": tr("nav.files"),
            "accounts": tr("nav.accounts"),
            "settings": tr("nav.settings"),
        }
        if self.adapter is not None:
            dot, label = p.success, tr("status.connected", account=self.adapter.account_label)
            suffix = f" · {tr('status.method.email')}"
        else:
            dot, label, suffix = p.muted, tr("status.offline"), ""
        status = ft.OutlinedButton(
            content=ft.Row(
                [
                    ft.Container(width=9, height=9, border_radius=5, bgcolor=dot),
                    ft.Text(label, font_family=BODY, size=14, color=p.text),
                    ft.Text(suffix, font_family=BODY, size=14, color=p.muted),
                ],
                spacing=8,
                tight=True,
            ),
            on_click=lambda _: self.go("accounts"),
            height=40,
            style=ft.ButtonStyle(
                side=ft.BorderSide(1, p.border),
                shape=ft.StadiumBorder(),
                bgcolor=p.surface,
                padding=ft.Padding.symmetric(vertical=0, horizontal=14),
            ),
        )
        return ft.Row(
            [self.k.title(titles[self.screen]), status],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

    def _busy_view(self) -> tuple[ft.Control, None]:
        return (
            ft.Row(
                [ft.ProgressRing(color=self.p.primary), self.k.text(self.tr("conn.restoring"))],
                spacing=16,
            ),
            None,
        )

    # ===== connection screen (F1) =================================================================

    def _connect_view(self) -> tuple[ft.Control, None]:
        p, k, tr = self.p, self.k, self.tr
        email = k.field(tr("conn.email"), autofocus=True, width=420)
        password = k.field(tr("conn.password"), password=True, can_reveal_password=True, width=420)
        message = k.text("", 14)
        busy = ft.ProgressRing(visible=False, width=20, height=20, color=p.primary)
        connect = k.primary(tr("conn.connect"), None, ft.Icons.LOGIN)

        def do_connect(_: Any = None) -> None:
            address, secret = (email.value or "").strip(), password.value or ""
            password.value = ""  # N2: do not keep the password in the widget
            if not address or not secret:
                message.value, message.color = tr("conn.missing_fields"), p.danger
                self.page.update()
                return
            message.value, message.color = tr("conn.connecting"), p.muted
            busy.visible, connect.disabled = True, True
            self.page.update()
            self.page.run_thread(self._login, address, secret, message, busy, connect)

        connect.on_click = do_connect
        password.on_submit = do_connect

        def method_box(title: str, badge: ft.Control, text: str, *, active: bool) -> ft.Container:
            icon = ft.Icons.RADIO_BUTTON_CHECKED if active else ft.Icons.RADIO_BUTTON_OFF
            return ft.Container(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(icon, color=p.primary_text if active else p.muted, size=20),
                                k.strong(title, color=p.text if active else p.muted),
                                badge,
                            ],
                            spacing=10,
                        ),
                        k.text(text, 13, muted=True),
                    ],
                    spacing=6,
                ),
                border=ft.Border.all(1, p.primary_text if active else p.border),
                border_radius=12,
                padding=14,
                bgcolor=p.primary_soft if active else None,
            )

        card = k.card(
            ft.Column(
                [
                    k.text(tr("conn.subtitle"), muted=True),
                    method_box(
                        tr("conn.method.api_key"),
                        k.pill(tr("conn.method.api_key.badge"), "success"),
                        tr("conn.method.api_key.text"),
                        active=False,
                    ),
                    method_box(
                        tr("conn.method.email"),
                        k.pill(tr("conn.method.email.badge"), "warning"),
                        tr("conn.email_warning"),
                        active=True,
                    ),
                    email,
                    password,
                    ft.Row([connect, busy], spacing=12),
                    message,
                    k.quiet(tr("conn.skip"), lambda _: self.go("export"), ft.Icons.ARROW_FORWARD),
                ],
                spacing=14,
            ),
            padding=24,
            width=560,
        )
        return card, None

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
            self.go("export")
            self._refresh_catalog_in_background()
            return
        finally:
            del password
        message.color = self.p.danger
        busy.visible, connect.disabled = False, False
        self.page.update()

    def _ask_mfa(self) -> str:
        """Called by the login thread when Garmin asks for a code; waits for the dialog."""
        tr, k = self.tr, self.k
        done = threading.Event()
        result = {"code": ""}
        code = k.field(tr("conn.mfa_title"), autofocus=True, width=220)

        def submit(_: Any = None) -> None:
            result["code"] = (code.value or "").strip()
            self.page.pop_dialog()
            done.set()

        code.on_submit = submit
        self.page.show_dialog(
            ft.AlertDialog(
                modal=True,
                bgcolor=self.p.surface,
                title=k.strong(tr("conn.mfa_title"), 18),
                content=ft.Column([k.text(tr("conn.mfa_text")), code], tight=True, spacing=12),
                actions=[k.primary(tr("common.ok"), submit)],
            )
        )
        done.wait(MFA_TIMEOUT_S)
        return result["code"]

    # ===== export screen (F5–F16) =================================================================

    def _export_view(self) -> tuple[ft.Control, ft.Control]:
        self._recompute()
        self.w_kpis = ft.Row(spacing=12)
        self.w_formats = ft.Row(spacing=8, wrap=True)
        self.w_ranges = ft.Container()
        self.w_types = ft.Container()
        self.w_find_matches = self.k.text("", 13, muted=True)
        self.w_table = ft.Column(spacing=0)
        self.w_list_info = self.k.text("", 13, muted=True)
        self.w_apply = ft.Container()
        self.w_progress = ft.Container()
        self.w_action = ft.Container()
        self.w_from = self._date_field(self.tr("export.from"), "from")
        self.w_to = self._date_field(self.tr("export.to"), "to")
        self.w_range_error = self.k.text("", 13, color=self.p.danger)

        self._fill_kpis()
        self._fill_formats()
        self._fill_ranges()
        self._fill_filters()
        self._fill_table()
        self._fill_progress()
        self._fill_action()

        # The progress card is always at the bottom; while downloading the steps are locked
        # (nothing in them can change a running job) and unlock when it ends.
        running = self.job.running
        steps = [
            ft.Container(card, disabled=running, opacity=0.55 if running else 1.0)
            for card in (self._find_card(), self._table_card())
        ]
        bottom = [self.w_progress] if self.job.visible else []
        body = ft.Column([self.w_kpis, *steps, *bottom], spacing=16)
        return body, self.w_action

    # --- data -------------------------------------------------------------------------------------

    def _current_filter(self) -> ActivityFilter:
        if self.types_selected is None:
            types: list[str] = []
        else:
            types = sorted(self.types_selected) or ["<none>"]  # nothing chosen = nothing matches
        return ActivityFilter(
            include_names=split_phrases(self.include_text),
            exclude_names=split_phrases(self.exclude_text),
            type_keys=types,
        )

    def _recompute(self) -> None:
        self.matched = self._current_filter().apply(self.listed)

    def _selected(self) -> list[Activity]:
        return [a for a in self.matched if a.activity_id in self.selected_ids]

    def _platform_id(self) -> str:
        return self.adapter.platform_id if self.adapter is not None else "garmin"

    def _in_folder(self, activity: Activity) -> bool:
        base = Path(self.folder).expanduser()
        return all(
            (
                format_dir(base, self._platform_id(), f)
                / default_file_name(activity, f".{f.value}")
            ).exists()
            for f in self.formats
            if activity.has_gps or not f.needs_gps
        )

    def _refresh_export(self, *, filters: bool = False) -> None:
        """Re-fill the parts of the export screen that depend on the list and selection."""
        self._recompute()
        if filters:
            self._fill_filters()
        self._fill_kpis()
        self._fill_table()
        self._fill_action()
        self.page.update()

    # --- KPIs -------------------------------------------------------------------------------------

    def _fill_kpis(self) -> None:
        tr, k = self.tr, self.k
        selected = self._selected()
        cards = [
            k.kpi(tr("kpi.listed"), str(len(self.listed))),
            k.kpi(tr("kpi.selected"), str(len(selected)), accent=True),
            k.kpi(tr("kpi.files"), str(files_to_download(selected, sorted(self.formats)))),
        ]
        self.w_kpis.controls = [ft.Container(c, expand=True) for c in cards]

    # --- step 1: range and formats (F5, F6) -------------------------------------------------------

    def _date_field(self, label: str, which: str) -> ft.TextField:
        value = self.date_from if which == "from" else self.date_to

        def store(text: str) -> None:
            if which == "from":
                self.date_from = text
            else:
                self.date_to = text
            self.range_key = ""
            self._fill_ranges()
            self._fill_apply()
            self.page.update()

        def pick(_: Any) -> None:
            today = date.today()

            def chosen(e: Any) -> None:
                v = e.control.value
                if v is None:
                    return
                day = (v.date() if isinstance(v, datetime) else v).isoformat()
                text_field.value = day
                store(day)

            self.page.show_dialog(
                ft.DatePicker(
                    value=parse_day(text_field.value or "") or today,
                    first_date=datetime.combine(EARLIEST, datetime.min.time()),
                    last_date=datetime.combine(today, datetime.min.time()),
                    on_change=chosen,
                )
            )

        text_field = self.k.field(
            label,
            value,
            width=190,
            hint_text=self.tr("export.date_hint"),
            on_change=lambda e: store(e.control.value or ""),
            suffix=ft.IconButton(
                ft.Icons.CALENDAR_MONTH_OUTLINED,
                icon_color=self.p.muted,
                icon_size=20,
                tooltip=label,
                on_click=pick,
            ),
        )
        return text_field

    def _fill_ranges(self) -> None:
        def pick(key: str) -> None:
            first, last = shortcut_range(key, date.today())
            self.date_from, self.date_to, self.range_key = first.isoformat(), last.isoformat(), key
            self.w_from.value, self.w_to.value = self.date_from, self.date_to
            self._fill_ranges()
            self._fill_apply()
            self.page.update()

        options = [(key, self.tr(f"export.range.{key}")) for key in SHORTCUTS]
        self.w_ranges.content = self.k.segmented(options, self.range_key, pick)

    def _fill_formats(self) -> None:
        def toggle(fmt: ExportFormat) -> None:
            self.formats ^= {fmt}
            self._fill_formats()
            self._refresh_export()

        self.w_formats.controls = [
            self.k.chip(self.tr(f"format.{f.value}"), f in self.formats, lambda _, f=f: toggle(f))
            for f in ExportFormat
        ]

    def _find_card(self) -> ft.Container:
        """Step 1, one card: date range, names, activity types and file formats; "Apply filters"
        at the very bottom gets the list. Sub-sections are labelled and divided by thin lines."""
        p, k, tr = self.p, self.k, self.tr
        include = k.field(
            tr("filters.include"),
            self.include_text,
            expand=True,
            hint_text=", ".join(self.settings.recent_include[:3]),
            on_change=lambda e: self._set_phrases("include", e.control.value),
        )
        exclude = k.field(
            tr("filters.exclude"),
            self.exclude_text,
            expand=True,
            hint_text=", ".join(self.settings.recent_exclude[:3]),
            on_change=lambda e: self._set_phrases("exclude", e.control.value),
        )
        header = ft.Row(
            [
                k.step_number(1),
                k.strong(tr("step.find")),
                ft.Container(expand=True),
                self.w_find_matches,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        dates = ft.Row(
            [self.w_from, self.w_to, self.w_ranges],
            spacing=12,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        def section(label: str, *controls: ft.Control) -> ft.Column:
            return ft.Column([k.overline(label), *controls], spacing=10)

        def divider() -> ft.Divider:
            return ft.Divider(height=1, thickness=1, color=p.border)

        return k.card(
            ft.Column(
                [
                    header,
                    section(tr("section.dates"), dates, self.w_range_error),
                    divider(),
                    section(tr("section.name"), ft.Row([include, exclude], spacing=12)),
                    divider(),
                    self.w_types,
                    divider(),
                    section(
                        tr("export.save_as"),
                        self.w_formats,
                        k.text(tr("export.formats_hint"), 12, muted=True),
                    ),
                    self.w_apply,
                ],
                spacing=16,
            )
        )

    # --- step 1: filters (F6, F7) -----------------------------------------------------------------

    def _type_on(self, key: str) -> bool:
        return self.types_selected is None or key in self.types_selected

    def _types_summary(self) -> str:
        total = len(catalog().all_keys())
        if self.types_selected is None:
            return self.tr("filters.types_all")
        return self.tr("filters.types_some", chosen=len(self.types_selected), total=total)

    def _filter_texts(self) -> tuple[str, str]:
        all_keys = catalog().all_keys()
        chosen = len(all_keys) if self.types_selected is None else len(self.types_selected)
        summary = filter_summary(
            split_phrases(self.include_text),
            split_phrases(self.exclude_text),
            chosen,
            len(all_keys),
            self.tr,
        )
        matches = self.tr("filters.matches", matched=len(self.matched), total=len(self.listed))
        return f"· {summary}", matches

    def _fill_filters(self) -> None:
        """Refresh what in step 1 depends on the list or type choice (never the text fields)."""
        self.w_find_matches.value = self._filter_texts()[1] if self.listed_range else ""
        self.w_types.content = self._types_section()
        self._fill_apply()

    # --- activity types (F7c) ---------------------------------------------------------------------

    def _types_section(self) -> ft.Control:
        p, k, tr = self.p, self.k, self.tr
        chevron = ft.Icons.EXPAND_LESS if self.types_open else ft.Icons.EXPAND_MORE
        header = ft.TextButton(
            content=ft.Row(
                [
                    k.overline(tr("filters.types")),
                    k.text(f"· {self._types_summary()}", 13, muted=True, expand=True),
                    ft.Icon(chevron, color=p.muted),
                ],
                spacing=8,
            ),
            on_click=lambda _: self._toggle_types_open(),
            height=40,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(vertical=0, horizontal=4),
            ),
        )
        if not self.types_open:
            return header

        search = k.field(
            tr("filters.search"),
            self.type_query,
            width=280,
            prefix_icon=ft.Icons.SEARCH,
            on_change=lambda e: self._set_type_query(e.control.value),
        )
        tools: list[ft.Control] = [
            search,
            ft.Container(expand=True),
            k.quiet(tr("filters.select_all"), lambda _: self._set_types(None), ft.Icons.DONE_ALL),
            k.quiet(
                tr("filters.deselect_all"), lambda _: self._set_types(set()), ft.Icons.REMOVE_DONE
            ),
        ]
        if self.listed_range and self.listed:
            label = (
                tr("filters.show_listed") if self.show_all_categories else tr("filters.show_all")
            )
            tools.append(k.quiet(label, lambda _: self._toggle_show_all()))
        self.w_type_rows = ft.Column(spacing=0)
        self._fill_type_rows()
        hint = k.text(tr("filters.types_hint"), 12, muted=True)
        return ft.Column(
            [
                header,
                # No wrap here: the spacer uses expand, which Flutter cannot lay out inside a
                # wrapping row (it shows a grey error box instead).
                ft.Row(tools, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                self.w_type_rows,
                hint,
            ],
            spacing=8,
        )

    def _fill_type_rows(self) -> None:
        """Categories (tri-state) and their subtypes; a search shows only what matches, opened."""
        p, k, tr = self.p, self.k, self.tr
        type_counts: dict[str, int] = {}
        for a in self.listed:
            type_counts[a.type_key] = type_counts.get(a.type_key, 0) + 1
        cat_counts: dict[str, int] = {}
        for key, n in type_counts.items():
            cat = describe(key).category
            cat_counts[cat] = cat_counts.get(cat, 0) + n

        query = self.type_query.strip().lower()
        listed = bool(self.listed_range and self.listed)

        def matches(text: str) -> bool:
            return query in text.lower() or query in text.lower().replace("_", " ")

        rows: list[ft.Control] = []
        for c in catalog().categories():
            if query:
                if matches(c.name) or matches(c.key):
                    visible_types = c.types
                else:
                    visible_types = [t for t in c.types if matches(t.name) or matches(t.key)]
                if not visible_types:
                    continue
                expanded = True
            else:
                if listed and not self.show_all_categories and not cat_counts.get(c.key):
                    continue
                visible_types = c.types
                expanded = c.key in self.expanded_categories

            on = [key for key in c.keys if self._type_on(key)]
            value: bool | None = True if len(on) == len(c.keys) else (False if not on else None)
            line: list[ft.Control] = [
                k.checkbox(
                    value,
                    lambda _, keys=c.keys: self._toggle_category(keys),
                    tristate=True,
                    semantics_label=c.name,
                ),
                k.strong(c.name, 14),
            ]
            if listed:
                line.append(k.pill(str(cat_counts.get(c.key, 0))))
            line.append(ft.Container(expand=True))
            if len(c.types) > 1 and not query:
                line.append(
                    ft.IconButton(
                        ft.Icons.KEYBOARD_ARROW_DOWN if expanded else ft.Icons.KEYBOARD_ARROW_RIGHT,
                        icon_color=p.muted,
                        tooltip=tr("filters.collapse") if expanded else tr("filters.expand"),
                        on_click=lambda _, key=c.key: self._toggle_expand(key),
                    )
                )
            block: list[ft.Control] = [
                ft.Row(line, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ]
            if expanded:
                chips = []
                for t in visible_types:
                    name = tr("filters.general") if t.is_category else t.name
                    n = type_counts.get(t.key, 0)
                    chip_label = f"{name} · {n}" if listed and n else name
                    chips.append(
                        k.chip(
                            chip_label,
                            self._type_on(t.key),
                            lambda _, key=t.key: self._toggle_type(key),
                        )
                    )
                block.append(
                    ft.Container(
                        ft.Row(chips, spacing=6, wrap=True),
                        padding=ft.Padding.only(left=44, bottom=8),
                    )
                )
            rows.append(
                ft.Container(
                    ft.Column(block, spacing=4),
                    border=ft.Border.only(top=ft.BorderSide(1, p.border)),
                    padding=ft.Padding.symmetric(vertical=2, horizontal=4),
                )
            )
        if not rows:
            rows.append(
                ft.Container(
                    k.text(tr("filters.no_match", query=self.type_query.strip()), 13, muted=True),
                    padding=ft.Padding.symmetric(vertical=10, horizontal=4),
                )
            )
        self.w_type_rows.controls = rows

    def _toggle_types_open(self) -> None:
        self.types_open = not self.types_open
        self._fill_filters()
        self.page.update()

    def _set_type_query(self, value: str | None) -> None:
        # Only the list below the field changes, so the search field keeps its cursor.
        self.type_query = value or ""
        self._fill_type_rows()
        self.page.update()

    def _set_phrases(self, which: str, value: str | None) -> None:
        if which == "include":
            self.include_text = value or ""
        else:
            self.exclude_text = value or ""
        self.page_index = 0
        self._recompute()
        # Only the header texts change, so the text field (and its cursor) stays in place.
        self.w_find_matches.value = self._filter_texts()[1]
        self._refresh_export()

    def _set_types(self, chosen: set[str] | None) -> None:
        if chosen is not None and chosen >= catalog().all_keys():
            chosen = None
        self.types_selected = chosen
        self.settings.activity_types = sorted(chosen) if chosen is not None else []
        self._save_settings()
        self.page_index = 0
        self._refresh_export(filters=True)

    def _current_types(self) -> set[str]:
        if self.types_selected is None:
            return set(catalog().all_keys())
        return set(self.types_selected)

    def _toggle_category(self, keys: list[str]) -> None:
        current = self._current_types()
        if set(keys) <= current:
            current -= set(keys)
        else:
            current |= set(keys)
        self._set_types(current)

    def _toggle_type(self, key: str) -> None:
        self._set_types(self._current_types() ^ {key})

    def _toggle_expand(self, category: str) -> None:
        self.expanded_categories ^= {category}
        self._fill_type_rows()
        self.page.update()

    def _toggle_show_all(self) -> None:
        self.show_all_categories = not self.show_all_categories
        self._fill_filters()
        self.page.update()

    def _server_categories(self) -> list[str] | None:
        """Categories Garmin can filter by on its side; None = fetch everything."""
        if self.types_selected is None:
            return None
        categories = catalog().categories_of(self.types_selected)
        return categories if 0 < len(categories) <= MAX_SERVER_CATEGORIES else None

    # --- applying filters: getting the list (F6, F7, F8) -----------------------------------------

    def _needs_refresh(self) -> bool:
        """Something that needs Garmin changed since the list was fetched (range, categories)."""
        if not self.listed_range or self.applied is None:
            return False
        if (self.date_from, self.date_to) != self.applied[:2]:
            return True
        listed_categories = self.applied[2]
        if listed_categories is None:
            return False
        if self.types_selected is None:
            return True
        return not set(catalog().categories_of(self.types_selected)) <= set(listed_categories)

    def _fill_apply(self) -> None:
        """The "Apply filters" bar at the bottom of step 2 and the state of the current list."""
        p, k, tr = self.p, self.k, self.tr
        if self.adapter is None:
            hint, color = tr("list.empty_offline"), p.muted
        elif self.types_selected is not None and not self.types_selected:
            hint, color = tr("filters.no_types"), p.warning
        elif self.listing:
            hint, color = tr("list.loading"), p.muted
        elif self._needs_refresh():
            hint, color = tr("filters.pending"), p.warning
        elif self.listed_range:
            first, last = self.applied[:2] if self.applied else ("", "")
            hint = tr("filters.applied", count=len(self.listed), first=first, last=last)
            color = p.muted
        else:
            hint, color = tr("filters.not_applied"), p.muted
        button = k.primary(
            tr("filters.apply"), lambda _: self._start_listing(), ft.Icons.FILTER_LIST
        )
        button.disabled = (
            self.adapter is None
            or self.listing
            or self.job.running
            or (self.types_selected is not None and not self.types_selected)
        )
        if self.listing:
            indicator: ft.Control = ft.ProgressRing(
                width=18, height=18, stroke_width=2.5, color=p.primary
            )
        else:
            icon = ft.Icons.WARNING_AMBER if color == p.warning else ft.Icons.INFO_OUTLINE
            indicator = ft.Icon(icon, color=color, size=18)
        self.w_apply.content = ft.Container(
            ft.Row(
                [
                    indicator,
                    k.text(hint, 13, color=color, expand=True),
                    button,
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            border=ft.Border.only(top=ft.BorderSide(1, p.border)),
            padding=ft.Padding.only(top=12),
        )

    # --- step 2: choose & download (F5, F8) -------------------------------------------------------

    def _table_card(self) -> ft.Container:
        k, tr = self.k, self.tr
        top = ft.Row(
            [
                k.step_number(2),
                k.strong(tr("step.download")),
                self.w_list_info,
                ft.Container(expand=True),
                k.quiet(tr("list.select_all"), lambda _: self._select(True)),
                k.quiet(tr("list.select_none"), lambda _: self._select(False)),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        return k.card(
            ft.Column(
                [
                    ft.Container(top, padding=ft.Padding.symmetric(vertical=0, horizontal=20)),
                    self.w_table,
                ],
                spacing=12,
            ),
            padding=ft.Padding.only(top=16, bottom=4),
        )

    def _cell(self, control: ft.Control, index: int, align_end: bool = False) -> ft.Container:
        width = COLS[index]
        return ft.Container(
            control,
            width=width,
            expand=width is None,
            alignment=ft.Alignment.CENTER_RIGHT if align_end else ft.Alignment.CENTER_LEFT,
        )

    def _fill_table(self) -> None:
        p, k, tr = self.p, self.k, self.tr
        self.w_list_info.value = (
            tr("list.selected", selected=len(self._selected()), matched=len(self.matched))
            if self.listed_range
            else ""
        )
        if not self.listed_range:
            empty = tr("list.empty") if self.adapter is not None else tr("list.empty_offline")
            self.w_table.controls = [
                ft.Container(
                    k.text(empty, 14, muted=True),
                    padding=ft.Padding.symmetric(vertical=28, horizontal=20),
                )
            ]
            return

        def head(text: str, i: int, end: bool = False) -> ft.Container:
            return self._cell(k.overline(text), i, end)

        total = len(self.matched)
        pages = max(1, -(-total // PAGE_SIZE))
        self.page_index = min(self.page_index, pages - 1)
        first = self.page_index * PAGE_SIZE
        chunk = self.matched[first : first + PAGE_SIZE]

        rows: list[ft.Control] = [
            ft.Container(
                ft.Row(
                    [
                        self._cell(ft.Container(), 0),
                        head(tr("col.date"), 1),
                        head(tr("col.name"), 2),
                        head(tr("col.type"), 3),
                        head(tr("col.status"), 4, True),
                    ],
                    spacing=0,
                ),
                bgcolor=p.surface2,
                padding=ft.Padding.symmetric(vertical=10, horizontal=20),
            )
        ]
        rows.extend(self._activity_row(a) for a in chunk)
        if total > PAGE_SIZE:
            rows.append(self._pager(first, len(chunk), total, pages))
        self.w_table.controls = rows

    def _pager(self, first: int, shown: int, total: int, pages: int) -> ft.Container:
        p, k, tr = self.p, self.k, self.tr

        def move(step: int) -> None:
            self.page_index = max(0, min(pages - 1, self.page_index + step))
            self._fill_table()
            self.page.update()

        previous = ft.IconButton(
            ft.Icons.CHEVRON_LEFT,
            icon_color=p.text,
            tooltip=tr("list.prev"),
            disabled=self.page_index == 0,
            on_click=lambda _: move(-1),
        )
        following = ft.IconButton(
            ft.Icons.CHEVRON_RIGHT,
            icon_color=p.text,
            tooltip=tr("list.next"),
            disabled=self.page_index >= pages - 1,
            on_click=lambda _: move(1),
        )
        label = tr("list.page", first=first + 1, last=first + shown, total=total)
        return ft.Container(
            ft.Row(
                [k.text(label, 13, muted=True), previous, following],
                alignment=ft.MainAxisAlignment.END,
                spacing=4,
            ),
            border=ft.Border.only(top=ft.BorderSide(1, p.border)),
            padding=ft.Padding.symmetric(vertical=4, horizontal=12),
        )

    def _activity_row(self, a: Activity) -> ft.Container:
        p, k, tr = self.p, self.k, self.tr
        checked = a.activity_id in self.selected_ids

        def toggle(_: Any, activity_id: str = a.activity_id) -> None:
            self.selected_ids ^= {activity_id}
            self._refresh_export()

        status = (
            k.pill(tr("status.in_folder"), "success")
            if self._in_folder(a)
            else k.pill(tr("status.new"))
        )
        name = k.text(
            a.name or "–",
            14,
            font_family=BODY_SEMIBOLD,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        label = f"{a.name} {local_time(a)}"
        return ft.Container(
            ft.Row(
                [
                    self._cell(k.checkbox(checked, toggle, semantics_label=label), 0),
                    self._cell(k.text(local_time(a), 14, muted=True), 1),
                    self._cell(name, 2),
                    self._cell(k.type_badge(a.type_key), 3),
                    self._cell(status, 4, True),
                ],
                spacing=0,
            ),
            height=48,
            padding=ft.Padding.symmetric(vertical=0, horizontal=20),
            border=ft.Border.only(top=ft.BorderSide(1, p.border)),
            on_click=toggle,
            ink=True,
        )

    def _select(self, value: bool) -> None:
        """Select or deselect every matching activity, on all pages."""
        ids = {a.activity_id for a in self.matched}
        self.selected_ids = self.selected_ids | ids if value else self.selected_ids - ids
        self._refresh_export()

    def _read_range(self) -> tuple[datetime, datetime] | None:
        tr = self.tr
        first, last = parse_day(self.date_from), parse_day(self.date_to)
        self.w_from.error = None if first else tr("export.bad_date")
        self.w_to.error = None if last else tr("export.bad_date")
        self.w_range_error.value = ""
        if first is None or last is None:
            return None
        if first > last:
            self.w_range_error.value = tr("export.bad_range")
            return None
        return local_day_bounds(first, last)

    def _start_listing(self) -> None:
        if self.adapter is None:
            self.go("connect")
            return
        bounds = self._read_range()
        if bounds is None:
            self.page.update()
            return
        self.listing = True
        self._fill_apply()
        self.page.update()
        self.page.run_thread(self._list_worker, bounds, self._server_categories())

    def _list_worker(self, bounds: tuple[datetime, datetime], categories: list[str] | None) -> None:
        try:
            self.listed = list(self.adapter.list_activities(*bounds, categories=categories))
            self.listed_range = bounds
            self.applied = (self.date_from, self.date_to, categories)
            self.selected_ids = {a.activity_id for a in self.listed}
            self.page_index = 0
            self.notice = ""
        except AdapterError as exc:
            self.notice = self.tr("export.error", error=str(exc))
        finally:
            self.listing = False
        if self.screen == "export":
            self._refresh_export(filters=True)

    # --- progress (F13, F14, F16) -----------------------------------------------------------------

    def _fill_progress(self) -> None:
        p, k, tr, job = self.p, self.k, self.tr, self.job
        if not job.visible:
            self.w_progress.content = None
            return
        if job.running:
            title = tr("progress.cancelling") if job.cancelling else tr("progress.running")
        else:
            title = tr("progress.done")
        finished_ok = not job.running and not job.resumable and job.total > 0
        # Indeterminate (animated) while starting, so it is clear the app is working.
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
        failed_kind = "danger" if job.failed else "neutral"
        message = (
            [k.text(job.message, 14, color=p.warning if job.resumable else p.text)]
            if job.message
            else []
        )
        count = ft.Text(
            tr("progress.count", done=job.done, total=job.total),
            font_family=DISPLAY,
            size=24,
            color=p.text,
        )
        if job.running and job.step:
            now = job.step
        elif job.running and job.done == 0:
            now = tr("progress.starting")
        else:
            now = job.current
        current = k.text(
            now,
            13,
            muted=True,
            expand=True,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self.w_progress.content = k.card(
            ft.Column(
                [
                    ft.Row(
                        [
                            *(
                                [
                                    ft.ProgressRing(
                                        width=18, height=18, stroke_width=2.5, color=p.primary
                                    )
                                ]
                                if job.running
                                else []
                            ),
                            k.strong(title),
                            count,
                            current,
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
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
                            k.pill(tr("progress.saved", count=job.saved), "success"),
                            k.pill(tr("progress.skipped", count=job.skipped)),
                            k.pill(tr("progress.failed", count=job.failed), failed_kind),
                        ],
                        spacing=8,
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

    # --- action bar (F9, F10, F15, F16) -----------------------------------------------------------

    def _fill_action(self) -> None:
        p, k, tr, job = self.p, self.k, self.tr, self.job
        selected = self._selected()

        async def choose_folder(_: Any) -> None:
            path = await self.folder_picker.get_directory_path(
                dialog_title=tr("action.folder"), initial_directory=self.folder or None
            )
            if path:
                self.folder = path
                self._refresh_export()

        def set_overwrite(e: Any) -> None:
            self.overwrite = bool(e.control.value)

        buttons: list[ft.Control] = []
        if job.running:
            cancel = k.secondary(tr("action.cancel"), self._cancel, ft.Icons.CLOSE)
            cancel.disabled = job.cancelling
            buttons.append(cancel)
        else:
            if job.visible:
                buttons.append(
                    k.secondary(tr("action.open_folder"), self._open_folder, ft.Icons.FOLDER_OPEN)
                )
            if job.resumable:
                buttons.append(k.secondary(tr("action.resume"), self._resume, ft.Icons.REPLAY))
            download = k.primary(
                tr("action.download", count=len(selected)),
                lambda _: self._start_export(),
                ft.Icons.DOWNLOAD_ROUNDED,
            )
            download.disabled = not selected or not self.formats or self.adapter is None
            buttons.append(download)

        notice = [k.text(self.notice, 13, color=p.danger)] if self.notice else []
        folder_text = k.text(
            self.folder,
            14,
            muted=True,
            expand=True,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=self.folder,
        )
        self.w_action.content = ft.Container(
            ft.Row(
                [
                    ft.Icon(ft.Icons.FOLDER_OUTLINED, color=p.muted, size=18),
                    folder_text,
                    k.secondary(tr("action.change"), choose_folder),
                    k.checkbox(self.overwrite, set_overwrite, tr("action.overwrite")),
                    *notice,
                    *buttons,
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=p.surface,
            border=ft.Border.all(1, p.border),
            border_radius=16,
            padding=ft.Padding.symmetric(vertical=12, horizontal=16),
            shadow=ft.BoxShadow(
                blur_radius=24,
                color="#40000000" if p.dark else "#1A0F172A",
                offset=ft.Offset(0, 8),
            ),
        )

    # --- running the export -----------------------------------------------------------------------

    def _start_export(self) -> None:
        tr = self.tr
        activities = self._selected()
        if self.adapter is None or self.listed_range is None or not activities:
            self.notice = (
                tr("export.nothing_selected") if self.adapter else tr("export.need_connection")
            )
            self._fill_action()
            self.page.update()
            return
        request = ExportRequest(
            start=self.listed_range[0],
            end=self.listed_range[1],
            formats=[f for f in ExportFormat if f in self.formats],
            out_dir=Path(self.folder or self.settings.download_dir).expanduser(),
            activity_filter=self._current_filter(),
            overwrite=self.overwrite,
        )
        self.settings.recent_include = remember_recent(
            self.settings.recent_include, split_phrases(self.include_text)
        )
        self.settings.recent_exclude = remember_recent(
            self.settings.recent_exclude, split_phrases(self.exclude_text)
        )
        self._save_settings()
        self._run(request, activities)

    def _run(self, request: ExportRequest, activities: list[Activity]) -> None:
        self.last_job = (request, activities)
        self.cancel_event = threading.Event()
        self.notice = ""
        self.job = JobState(visible=True, running=True, total=len(activities))
        if self.screen == "export":
            self.render()  # steps locked, progress card at the bottom
            self._scroll_to_end()
        self.page.run_thread(self._export_worker, request, activities, self.cancel_event)

    def _refresh_job(self, *, table: bool = False) -> None:
        if self.screen == "export":
            self._fill_progress()
            self._fill_action()
            if table:
                self._fill_table()
        self.page.update()

    def _export_worker(
        self, request: ExportRequest, activities: list[Activity], cancel: threading.Event
    ) -> None:
        summary: ExportSummary | None = None
        try:
            with self.services.open_registry() as registry:
                exporter = Exporter(self.adapter, registry, on_event=self._on_event)
                summary = exporter.run(request, activities, cancel)
        except Exception as exc:  # report anything unexpected instead of freezing the UI
            log.exception("Export crashed")
            self.job.message = self.tr("export.error", error=str(exc))
        finally:
            self._safe_persist()
        self.job.running = False
        if summary is not None:
            self._finish(summary)
        if self.screen == "export":
            self.render()  # unlock the steps; the summary stays at the bottom
            self._scroll_to_end()
        else:
            self.page.update()

    def _safe_persist(self) -> None:
        try:
            if self.adapter is not None:
                self.adapter.persist_session()
        except Exception:  # never let session saving break the UI
            log.exception("Saving the session failed")

    def _on_event(self, event: ExportEvent) -> None:
        job = self.job
        if isinstance(event, Downloading):
            job.step = self.tr(
                "progress.downloading", fmt=event.fmt.value.upper(), name=event.activity.name
            )
            self._refresh_job()
            return
        if isinstance(event, Progress):
            job.step = ""
            job.done, job.total = event.done, event.total
            job.current = f"{event.activity.name} · {local_time(event.activity)}"
            job.lines.append(
                (f"[{event.done}/{event.total}] {activity_row(event.activity)}", "text")
            )
        else:
            if isinstance(event, FileSaved):
                job.saved += 1
                kind = "success"
            elif isinstance(event, FileFailed):
                job.failed += 1
                kind = "danger"
            else:
                if isinstance(event, FileSkipped):
                    job.skipped += 1
                kind = "muted"
            line = format_event(event, self.tr)
            if line:
                job.lines.append((f"    {line}", kind))
        del job.lines[:-MAX_LOG_LINES]
        self._refresh_job()

    def _finish(self, summary: ExportSummary) -> None:
        tr, job = self.tr, self.job
        job.saved, job.skipped, job.failed = summary.saved, summary.skipped, summary.failed
        job.current = ""
        if summary.cancelled:
            job.message, job.resumable = tr("progress.cancelled"), True
        elif summary.paused_reason:
            job.message, job.resumable = tr(f"progress.paused.{summary.paused_reason}"), True
            if summary.paused_reason == "authentication":
                self.adapter = None

    def _cancel(self, _: Any) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self.job.cancelling = True
            self._refresh_job()

    def _resume(self, _: Any) -> None:
        if self.last_job is None:
            return
        if self.adapter is None:
            self.go("connect")
            return
        self._run(*self.last_job)  # the registry skips everything already saved (F15)

    def _open_folder(self, _: Any = None) -> None:
        base = self.last_job[0].out_dir if self.last_job else Path(self.folder).expanduser()
        folder = export_root(base, self._platform_id())
        open_in_explorer(folder if folder.exists() else base)

    # ===== downloaded files (F17) =================================================================

    def _files_view(self) -> tuple[ft.Control, None]:
        p, k, tr = self.p, self.k, self.tr
        base = Path(self.folder).expanduser()
        folder = export_root(base, self._platform_id())
        entries = scan_folders([format_dir(base, self._platform_id(), f) for f in ExportFormat])
        folder_text = k.text(
            tr("files.folder", folder=str(folder)),
            14,
            muted=True,
            expand=True,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        top = ft.Row(
            [
                folder_text,
                k.text(tr("files.offline_note"), 13, muted=True),
                k.quiet(tr("files.refresh"), lambda _: self.render(), ft.Icons.REFRESH),
                k.secondary(
                    tr("action.open_folder"),
                    lambda _: open_in_explorer(folder if folder.exists() else base),
                    ft.Icons.FOLDER_OPEN,
                ),
            ],
            spacing=12,
        )
        widths = (140, 220, None, 220)
        heads = [
            tr("col.date"),
            tr("files.col.activity"),
            tr("files.col.file"),
            tr("files.col.formats"),
        ]

        def cell(control: ft.Control, i: int) -> ft.Container:
            return ft.Container(control, width=widths[i], expand=widths[i] is None)

        rows: list[ft.Control] = [
            ft.Container(
                ft.Row([cell(k.overline(h), i) for i, h in enumerate(heads)], spacing=0),
                bgcolor=p.surface2,
                padding=ft.Padding.symmetric(vertical=10, horizontal=20),
            )
        ]
        for e in entries:
            base = e.files[0].name.rsplit(".", 1)[0].removesuffix(".splits")
            file_text = ft.Text(
                base,
                font_family=MONO,
                size=13,
                color=p.text,
                no_wrap=True,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
            rows.append(
                ft.Container(
                    ft.Row(
                        [
                            cell(k.text(e.day.isoformat(), 14, muted=True), 0),
                            cell(k.type_badge(e.type_key), 1),
                            cell(file_text, 2),
                            cell(ft.Row([k.pill(f) for f in e.formats], spacing=4, wrap=True), 3),
                        ],
                        spacing=0,
                    ),
                    height=48,
                    padding=ft.Padding.symmetric(vertical=0, horizontal=20),
                    border=ft.Border.only(top=ft.BorderSide(1, p.border)),
                )
            )
        if not entries:
            rows.append(ft.Container(k.text(tr("files.empty"), 14, muted=True), padding=20))
        body = ft.Column(
            [
                k.card(top, padding=ft.Padding.symmetric(vertical=12, horizontal=20)),
                k.text(tr("files.count", count=len(entries)), 13, muted=True),
                k.card(ft.Column(rows, spacing=0), padding=0),
            ],
            spacing=12,
        )
        return body, None

    # ===== accounts (F3) ==========================================================================

    def _accounts_view(self) -> tuple[ft.Control, None]:
        p, k, tr = self.p, self.k, self.tr
        flow = self.services.garmin_email
        saved = flow.has_saved_session()
        if self.adapter is not None:
            state_pill = k.pill(tr("accounts.connected"), "success")
            detail = tr("accounts.account", account=self.adapter.account_label)
        elif saved:
            state_pill, detail = k.pill(tr("accounts.saved"), "accent"), tr("accounts.saved_text")
        else:
            state_pill, detail = k.pill(tr("accounts.not_connected")), tr("accounts.none_text")

        def delete(_: Any) -> None:
            flow.logout()
            self.adapter = None
            self.render()

        actions: list[ft.Control] = []
        if self.adapter is None:
            actions.append(
                k.primary(tr("accounts.connect"), lambda _: self.go("connect"), ft.Icons.LOGIN)
            )
        if self.adapter is not None or saved:
            actions.append(
                k.secondary(tr("accounts.delete"), delete, ft.Icons.DELETE_OUTLINE, danger=True)
            )

        email_title = ft.Row(
            [
                k.strong(tr("conn.method.email")),
                k.pill(tr("conn.method.email.badge"), "warning"),
                ft.Container(expand=True),
                state_pill,
            ],
            spacing=8,
        )
        email_box = ft.Container(
            ft.Column(
                [
                    email_title,
                    k.text(detail, 13, muted=True),
                    ft.Row(actions, spacing=8, wrap=True),
                ],
                spacing=10,
            ),
            border=ft.Border.all(1, p.border),
            border_radius=12,
            padding=14,
        )
        api_title = ft.Row(
            [
                k.strong(tr("conn.method.api_key")),
                k.pill(tr("conn.method.api_key.badge"), "success"),
            ],
            spacing=8,
        )
        api_box = ft.Container(
            ft.Column(
                [api_title, k.text(tr("conn.method.api_key.text"), 13, muted=True)], spacing=8
            ),
            border=ft.Border.all(1, p.field),
            border_radius=12,
            padding=14,
        )
        garmin = k.card(
            ft.Column([k.title("Garmin Connect", 22), email_box, api_box], spacing=14), expand=True
        )
        more = k.card(
            ft.Column(
                [
                    k.title(tr("accounts.more_platforms"), 22),
                    k.text(tr("accounts.more_text"), 14, muted=True),
                    k.pill(tr("common.soon")),
                ],
                spacing=10,
            ),
            expand=True,
        )
        row = ft.Row([garmin, more], spacing=16, vertical_alignment=ft.CrossAxisAlignment.START)
        return row, None

    # ===== settings (F21) =========================================================================

    def _settings_view(self) -> tuple[ft.Control, None]:
        p, k, tr = self.p, self.k, self.tr

        def row(title: str, hint: str, control: ft.Control, last: bool = False) -> ft.Container:
            return ft.Container(
                ft.Row(
                    [
                        ft.Column(
                            [k.strong(title), k.text(hint, 13, muted=True)], spacing=2, expand=True
                        ),
                        control,
                    ],
                    spacing=16,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(vertical=16, horizontal=0),
                border=None if last else ft.Border.only(bottom=ft.BorderSide(1, p.border)),
            )

        def set_theme(value: str) -> None:
            self.settings.theme = value
            self._save_settings()
            self.render()

        folder_field = k.field(tr("settings.folder"), self.settings.download_dir, width=420)

        def save_folder(_: Any = None) -> None:
            value = (folder_field.value or "").strip()
            if value:
                self.settings.download_dir = value
                self.folder = value
                self._save_settings()

        folder_field.on_blur = save_folder
        folder_field.on_submit = save_folder

        async def choose(_: Any) -> None:
            path = await self.folder_picker.get_directory_path(
                dialog_title=tr("settings.folder"), initial_directory=folder_field.value or None
            )
            if path:
                folder_field.value = path
                save_folder()
                self.page.update()

        def toggle_format(fmt: ExportFormat) -> None:
            current = set(self.settings.formats) ^ {fmt.value}
            if current:
                self.settings.formats = [f.value for f in ExportFormat if f.value in current]
                self._save_settings()
                self.render()

        chips = ft.Row(
            [
                k.chip(
                    tr(f"format.{f.value}"),
                    f.value in self.settings.formats,
                    lambda _, f=f: toggle_format(f),
                )
                for f in ExportFormat
            ],
            spacing=6,
            wrap=True,
            width=520,
        )
        themes = [
            ("dark", tr("theme.dark")),
            ("light", tr("theme.light")),
            ("system", tr("theme.system")),
        ]
        body = k.card(
            ft.Column(
                [
                    row(
                        tr("settings.theme"),
                        tr("settings.theme_hint"),
                        k.segmented(themes, self.settings.theme, set_theme),
                    ),
                    row(
                        tr("settings.folder"),
                        tr("settings.folder_hint"),
                        ft.Row([folder_field, k.secondary(tr("common.choose"), choose)], spacing=8),
                    ),
                    row(tr("settings.formats"), tr("settings.formats_hint"), chips),
                    row(
                        tr("settings.delay"),
                        tr("settings.delay_hint"),
                        k.pill("1.0 s"),
                        last=True,
                    ),
                ],
                spacing=0,
            ),
            padding=ft.Padding.symmetric(vertical=4, horizontal=20),
        )
        return body, None

    def _save_settings(self) -> None:
        self.settings.save(self.services.paths.settings_file)

    # ===== about (N10) ============================================================================

    def _show_about(self) -> None:
        k, tr = self.k, self.tr
        self.page.show_dialog(
            ft.AlertDialog(
                bgcolor=self.p.surface,
                title=k.title(f"{APP_TITLE} {__version__}", 26),
                content=ft.Column(
                    [
                        k.text(tr("app.tagline")),
                        k.text(tr("app.disclaimer"), 13, muted=True),
                        k.text(tr("app.license"), 13, muted=True),
                    ],
                    tight=True,
                    spacing=12,
                    width=480,
                ),
                actions=[
                    ft.TextButton(
                        tr("support.button"), icon=ft.Icons.FAVORITE_BORDER, url=SUPPORT_URL
                    ),
                    ft.TextButton(tr("about.source"), icon=ft.Icons.CODE, url=REPOSITORY_URL),
                    k.primary(tr("common.ok"), lambda _: self.page.pop_dialog()),
                ],
            )
        )


def open_in_explorer(folder: Path) -> None:
    if sys.platform == "win32" and folder.exists():
        os.startfile(folder)  # noqa: S606 - opens the user's own export folder in Explorer


def main() -> None:
    from fitbatch.logging_setup import setup_logging

    services = create_services()
    setup_logging(services.paths.logs_dir)

    def build(page: ft.Page) -> None:
        FitBatchApp(page, services).start()

    ft.run(build, assets_dir=str(ASSETS_DIR))


if __name__ == "__main__":
    main()
