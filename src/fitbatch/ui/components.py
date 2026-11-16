"""Reusable widgets in the FitBatch style, built from the current palette.

Interactive pieces are real Flet buttons (keyboard focus, hover, focus ring); containers are
used only for layout. Minimum height of click targets: 40–44 px.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from fitbatch.core.activity_types import describe
from fitbatch.ui.theme import BODY, BODY_MEDIUM, BODY_SEMIBOLD, DISPLAY, MONO, Palette

Handler = Callable[[Any], None]


def _shape(radius: float) -> ft.RoundedRectangleBorder:
    return ft.RoundedRectangleBorder(radius=radius)


class Kit:
    def __init__(self, palette: Palette) -> None:
        self.p = palette

    # --- text -----------------------------------------------------------------------------------

    def title(self, text: str, size: int = 30) -> ft.Text:
        return ft.Text(text, font_family=DISPLAY, size=size, color=self.p.text)

    def text(self, text: str, size: int = 15, *, muted: bool = False, **kw: Any) -> ft.Text:
        kw.setdefault("color", self.p.muted if muted else self.p.text)
        kw.setdefault("font_family", BODY)
        return ft.Text(text, size=size, **kw)

    def strong(self, text: str, size: int = 15, **kw: Any) -> ft.Text:
        kw.setdefault("color", self.p.text)
        return ft.Text(text, font_family=BODY_SEMIBOLD, size=size, **kw)

    def overline(self, text: str) -> ft.Text:
        return ft.Text(text.upper(), font_family=BODY_SEMIBOLD, size=12, color=self.p.muted)

    def mono(self, text: str, size: int = 13, color: str | None = None) -> ft.Text:
        return ft.Text(
            text, font_family=MONO, size=size, color=color or self.p.text, selectable=True
        )

    # --- surfaces -------------------------------------------------------------------------------

    def card(self, content: ft.Control, padding: Any = 20, **kw: Any) -> ft.Container:
        return ft.Container(
            content=content,
            bgcolor=self.p.surface,
            border=ft.Border.all(1, self.p.border),
            border_radius=16,
            padding=padding,
            **kw,
        )

    def step_number(self, n: int) -> ft.Container:
        return ft.Container(
            ft.Text(str(n), font_family=BODY_SEMIBOLD, size=13, color=self.p.primary_text),
            width=26,
            height=26,
            border_radius=13,
            bgcolor=self.p.primary_soft,
            alignment=ft.Alignment.CENTER,
        )

    def kpi(self, label: str, value: str, unit: str = "", *, accent: bool = False) -> ft.Container:
        number = ft.Text(
            spans=[
                ft.TextSpan(value),
                ft.TextSpan(
                    f" {unit}" if unit else "",
                    style=ft.TextStyle(size=18, color=self.p.muted, font_family=DISPLAY),
                ),
            ],
            font_family=DISPLAY,
            size=32,
            color=self.p.primary_text if accent else self.p.text,
        )
        return self.card(
            ft.Column([self.overline(label), number], spacing=2),
            padding=ft.Padding.symmetric(vertical=14, horizontal=16),
        )

    def pill(self, text: str, kind: str = "neutral") -> ft.Container:
        colors = {
            "success": (self.p.success_soft, self.p.success),
            "warning": (self.p.warning_soft, self.p.warning),
            "danger": (self.p.danger_soft, self.p.danger),
            "accent": (self.p.primary_soft, self.p.primary_text),
            "neutral": (self.p.surface2, self.p.muted),
        }[kind]
        return ft.Container(
            ft.Text(text, font_family=BODY_SEMIBOLD, size=12, color=colors[1]),
            bgcolor=colors[0],
            border_radius=999,
            padding=ft.Padding.symmetric(vertical=2, horizontal=10),
        )

    def type_badge(self, type_key: str) -> ft.Row:
        t = describe(type_key)
        return ft.Row(
            [
                ft.Container(
                    width=10, height=10, border_radius=3, bgcolor=self.p.group_color(t.category)
                ),
                self.text(t.name, 14, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
            ],
            spacing=8,
            tight=True,
        )

    # --- buttons --------------------------------------------------------------------------------

    def primary(self, label: str, on_click: Handler | None, icon: Any = None) -> ft.FilledButton:
        return ft.FilledButton(
            content=ft.Text(label, font_family=BODY_SEMIBOLD, size=15),
            icon=icon,
            on_click=on_click,
            height=44,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: self.p.primary,
                    ft.ControlState.DISABLED: self.p.surface2,
                },
                color={
                    ft.ControlState.DEFAULT: self.p.on_primary,
                    ft.ControlState.DISABLED: self.p.muted,
                },
                shape=_shape(10),
                padding=ft.Padding.symmetric(vertical=0, horizontal=20),
            ),
        )

    def danger(self, label: str, on_click: Handler | None, icon: Any = None) -> ft.FilledButton:
        """Filled red button for actions that cannot be undone (always confirmed first)."""
        return ft.FilledButton(
            content=ft.Text(label, font_family=BODY_SEMIBOLD, size=15),
            icon=icon,
            on_click=on_click,
            height=44,
            style=ft.ButtonStyle(
                bgcolor={
                    ft.ControlState.DEFAULT: self.p.danger,
                    ft.ControlState.DISABLED: self.p.surface2,
                },
                color={
                    ft.ControlState.DEFAULT: self.p.on_primary,
                    ft.ControlState.DISABLED: self.p.muted,
                },
                shape=_shape(10),
                padding=ft.Padding.symmetric(vertical=0, horizontal=20),
            ),
        )

    def secondary(
        self, label: str, on_click: Handler | None, icon: Any = None, *, danger: bool = False
    ) -> ft.OutlinedButton:
        return ft.OutlinedButton(
            content=ft.Text(label, font_family=BODY_MEDIUM, size=14),
            icon=icon,
            on_click=on_click,
            height=40,
            style=ft.ButtonStyle(
                color=self.p.danger if danger else self.p.text,
                icon_color=self.p.danger if danger else self.p.text,
                side=ft.BorderSide(1, self.p.field),
                shape=_shape(10),
                padding=ft.Padding.symmetric(vertical=0, horizontal=14),
            ),
        )

    def quiet(self, label: str, on_click: Handler | None, icon: Any = None) -> ft.TextButton:
        return ft.TextButton(
            content=ft.Text(label, font_family=BODY_MEDIUM, size=13),
            icon=icon,
            on_click=on_click,
            height=36,
            style=ft.ButtonStyle(color=self.p.text, icon_color=self.p.text, shape=_shape(8)),
        )

    def segmented(
        self, options: Sequence[tuple[str, str]], selected: str, on_select: Callable[[str], None]
    ) -> ft.Container:
        buttons = []
        for key, label in options:
            on = key == selected
            buttons.append(
                ft.TextButton(
                    content=ft.Text(label, font_family=BODY_SEMIBOLD if on else BODY, size=13),
                    on_click=lambda _, k=key: on_select(k),
                    height=36,
                    style=ft.ButtonStyle(
                        bgcolor=self.p.surface if on else ft.Colors.TRANSPARENT,
                        color=self.p.text if on else self.p.muted,
                        shape=_shape(8),
                        padding=ft.Padding.symmetric(vertical=0, horizontal=14),
                    ),
                )
            )
        return ft.Container(
            ft.Row(buttons, spacing=4, tight=True, wrap=True),
            bgcolor=self.p.surface2,
            border_radius=10,
            padding=4,
        )

    def chip(self, label: str, active: bool, on_click: Handler) -> ft.OutlinedButton:
        """Toggle chip: the selected state is shown by colour AND a check icon."""
        return ft.OutlinedButton(
            content=ft.Text(label, font_family=BODY_SEMIBOLD, size=14),
            icon=ft.Icons.CHECK if active else None,
            on_click=on_click,
            height=40,
            style=ft.ButtonStyle(
                bgcolor=self.p.primary_soft if active else ft.Colors.TRANSPARENT,
                color=self.p.primary_text if active else self.p.text,
                icon_color=self.p.primary_text,
                side=ft.BorderSide(1, self.p.primary_text if active else self.p.field),
                shape=ft.StadiumBorder(),
                padding=ft.Padding.symmetric(vertical=0, horizontal=14),
            ),
        )

    # --- inputs ---------------------------------------------------------------------------------

    def field(self, label: str, value: str = "", **kw: Any) -> ft.TextField:
        return ft.TextField(
            label=label,
            value=value,
            border_color=self.p.field,
            focused_border_color=self.p.primary_text,
            border_radius=10,
            cursor_color=self.p.primary_text,
            text_style=ft.TextStyle(font_family=BODY, size=15, color=self.p.text),
            label_style=ft.TextStyle(font_family=BODY, size=13, color=self.p.muted),
            filled=True,
            fill_color=self.p.bg,
            **kw,
        )

    def checkbox(
        self, value: bool, on_change: Handler | None, label: str = "", **kw: Any
    ) -> ft.Checkbox:
        return ft.Checkbox(
            value=value,
            on_change=on_change,
            label=label or None,
            active_color=self.p.primary,
            check_color=self.p.on_primary,
            border_side=ft.BorderSide(2, self.p.field),
            label_style=ft.TextStyle(font_family=BODY, size=14, color=self.p.text),
            **kw,
        )
