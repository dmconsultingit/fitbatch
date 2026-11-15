"""Graphical interface for FitBatch, built with Flet."""

from __future__ import annotations

import flet as ft

from fitbatch import __version__

APP_TITLE = "FitBatch"
TAGLINE = "Bulk download & upload of your training data. For Garmin Connect."


def build_page(page: ft.Page) -> None:
    page.title = f"{APP_TITLE} {__version__}"
    page.window.width = 720
    page.window.height = 480
    page.padding = 32

    page.add(
        ft.Column(
            controls=[
                ft.Text(APP_TITLE, size=32, weight=ft.FontWeight.BOLD),
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
