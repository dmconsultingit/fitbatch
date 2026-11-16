"""FitBatch design tokens (dark and light) and the Flet themes built from them.

Every text/background pair is at least 4.5:1 (WCAG AA); field borders at least 3:1.
Colours come from here only; widgets never hard-code hex values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import flet as ft

ASSETS_DIR = Path(__file__).parent / "assets"

# Static OFL fonts bundled with the app (no network). One family per weight: Flutter does not
# derive weights from a single file reliably.
FONTS = {
    "Inter": "fonts/Inter-Regular.ttf",
    "Inter Medium": "fonts/Inter-Medium.ttf",
    "Inter SemiBold": "fonts/Inter-SemiBold.ttf",
    "Barlow SemiBold": "fonts/BarlowSemiCondensed-SemiBold.ttf",
    "Barlow Bold": "fonts/BarlowSemiCondensed-Bold.ttf",
    "JetBrains Mono": "fonts/JetBrainsMono-Regular.ttf",
}
BODY = "Inter"
BODY_MEDIUM = "Inter Medium"
BODY_SEMIBOLD = "Inter SemiBold"
DISPLAY = "Barlow Bold"
DISPLAY_SEMIBOLD = "Barlow SemiBold"
MONO = "JetBrains Mono"


@dataclass(frozen=True, slots=True)
class Palette:
    dark: bool
    bg: str
    surface: str
    surface2: str
    row_hover: str
    border: str
    field: str
    text: str
    muted: str
    primary: str
    on_primary: str
    primary_text: str
    primary_soft: str
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    group_cycling: str
    group_fitness: str
    group_walking: str
    group_other: str

    def group_color(self, group: str) -> str:
        """Colour for a Garmin category (always shown next to the type name, never alone)."""
        return {
            "cycling": self.group_cycling,
            "running": self.group_cycling,
            "multi_sport": self.group_cycling,
            "fitness_equipment": self.group_fitness,
            "swimming": self.group_fitness,
            "walking": self.group_walking,
            "hiking": self.group_walking,
        }.get(group, self.group_other)


DARK = Palette(
    dark=True,
    bg="#0B1220",
    surface="#151E31",
    surface2="#1C2740",
    row_hover="#1A2338",
    border="#26324A",
    field="#64748B",
    text="#F1F5F9",
    muted="#94A3B8",
    primary="#F97316",
    on_primary="#0B1220",
    primary_text="#FB923C",
    primary_soft="#3A2A26",  # #F97316 at ~16 % over the surface
    success="#34D399",
    success_soft="#183A3B",
    warning="#FBBF24",
    warning_soft="#3A3A2E",
    danger="#F87171",
    danger_soft="#3A2834",
    group_cycling="#FB923C",
    group_fitness="#60A5FA",
    group_walking="#34D399",
    group_other="#A78BFA",
)

LIGHT = Palette(
    dark=False,
    bg="#F6F7F9",
    surface="#FFFFFF",
    surface2="#F1F5F9",
    row_hover="#F8FAFC",
    border="#E2E8F0",
    field="#64748B",
    text="#0F172A",
    muted="#475569",
    primary="#C2410C",
    on_primary="#FFFFFF",
    primary_text="#C2410C",
    primary_soft="#FCF4F0",  # accent text on it: 4.77:1
    success="#047857",
    success_soft="#E6F2EE",
    warning="#B45309",
    warning_soft="#FBF6F0",  # warning text on it: 4.67:1
    danger="#B91C1C",
    danger_soft="#FBEDED",
    group_cycling="#C2410C",
    group_fitness="#1D4ED8",
    group_walking="#047857",
    group_other="#6D28D9",
)


def palette_for(theme: str, platform_dark: bool) -> Palette:
    if theme == "system":
        return DARK if platform_dark else LIGHT
    return LIGHT if theme == "light" else DARK


def flet_theme(p: Palette) -> ft.Theme:
    """Material 3 theme so built-in controls (fields, checkboxes, dialogs) match the tokens."""
    return ft.Theme(
        font_family=BODY,
        use_material3=True,
        color_scheme=ft.ColorScheme(
            primary=p.primary,
            on_primary=p.on_primary,
            secondary=p.primary_text,
            on_secondary=p.on_primary,
            error=p.danger,
            on_error=p.on_primary,
            surface=p.surface,
            on_surface=p.text,
            on_surface_variant=p.muted,
            outline=p.field,
            outline_variant=p.border,
            surface_container=p.surface,
            surface_container_high=p.surface,
            surface_container_highest=p.surface2,
            surface_container_low=p.surface,
            surface_container_lowest=p.bg,
        ),
        scaffold_bgcolor=p.bg,
        divider_color=p.border,
        hover_color=p.row_hover,
    )
