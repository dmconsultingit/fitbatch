"""Main window of FitBatch, built with Flet."""

from __future__ import annotations

import flet as ft

from fitbatch import __version__

APP_TITLE = "FitBatch"
TAGLINE = "Bulk download & upload of your training data. For Garmin Connect."
DISCLAIMER = (
    "FitBatch is an independent, unofficial project. It is not affiliated with, endorsed by "
    "or sponsored by Garmin Ltd. or any other platform. Garmin and Garmin Connect are "
    "trademarks of Garmin Ltd. or its subsidiaries."
)


def about_dialog(page: ft.Page) -> ft.AlertDialog:
    def close(_: ft.Event) -> None:
        page.pop_dialog()

    return ft.AlertDialog(
        title=ft.Text(f"{APP_TITLE} {__version__}"),
        content=ft.Text(f"{TAGLINE}\n\n{DISCLAIMER}\n\nLicense: GPL-3.0-or-later"),
        actions=[ft.TextButton("OK", on_click=close)],
    )


def build_page(page: ft.Page) -> None:
    page.title = f"{APP_TITLE} {__version__}"
    page.window.width = 900
    page.window.height = 600
    page.padding = 24

    def show_about(_: ft.Event) -> None:
        page.show_dialog(about_dialog(page))

    page.appbar = ft.AppBar(
        title=ft.Text(APP_TITLE),
        actions=[ft.IconButton(ft.Icons.INFO_OUTLINE, tooltip="About", on_click=show_about)],
    )
    page.add(
        ft.Column(
            controls=[
                ft.Text(TAGLINE, size=16),
                ft.Text(f"Version {__version__}: work in progress.", italic=True),
            ],
            spacing=12,
        )
    )


def main() -> None:
    ft.run(build_page)


if __name__ == "__main__":
    main()
