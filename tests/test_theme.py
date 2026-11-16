"""Every text colour in both palettes must meet WCAG AA against the backgrounds it is used on."""

from dataclasses import fields

import pytest

from fitbatch.ui.theme import ASSETS_DIR, DARK, FONTS, LIGHT, Palette, palette_for


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted([_luminance(a), _luminance(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [
    ("text", "bg"),
    ("text", "surface"),
    ("text", "surface2"),
    ("muted", "bg"),
    ("muted", "surface"),
    ("muted", "surface2"),
    ("primary_text", "surface"),
    ("primary_text", "primary_soft"),
    ("on_primary", "primary"),
    ("success", "surface"),
    ("success", "success_soft"),
    ("warning", "surface"),
    ("warning", "warning_soft"),
    ("danger", "surface"),
    ("danger", "danger_soft"),
    ("group_cycling", "surface"),
    ("group_fitness", "surface"),
    ("group_walking", "surface"),
    ("group_other", "surface"),
]


@pytest.mark.parametrize("palette", [DARK, LIGHT], ids=["dark", "light"])
@pytest.mark.parametrize(("fg", "bg"), TEXT_PAIRS)
def test_text_contrast_meets_wcag_aa(palette: Palette, fg: str, bg: str) -> None:
    ratio = contrast(getattr(palette, fg), getattr(palette, bg))
    assert ratio >= 4.5, f"{fg} on {bg}: {ratio:.2f}"


@pytest.mark.parametrize("palette", [DARK, LIGHT], ids=["dark", "light"])
def test_field_border_is_visible(palette: Palette) -> None:
    assert contrast(palette.field, palette.surface) >= 3.0


def test_palettes_define_every_token() -> None:
    for f in fields(Palette):
        assert getattr(DARK, f.name) is not None
        assert getattr(LIGHT, f.name) is not None


def test_palette_for_theme_setting() -> None:
    assert palette_for("light", platform_dark=True) is LIGHT
    assert palette_for("dark", platform_dark=False) is DARK
    assert palette_for("system", platform_dark=True) is DARK
    assert palette_for("system", platform_dark=False) is LIGHT


def test_bundled_fonts_exist() -> None:
    for relative in FONTS.values():
        assert (ASSETS_DIR / relative).is_file(), relative


def test_brand_assets_exist_and_logo_orange_is_visible() -> None:
    from fitbatch.ui.theme import APP_ICON, BRAND_ORANGE, BRAND_SYMBOL

    assert (ASSETS_DIR / BRAND_SYMBOL).is_file()
    assert (ASSETS_DIR / APP_ICON).read_bytes()[:4] == b"\x00\x00\x01\x00"  # ICO header
    # graphics need >= 3:1 (WCAG 1.4.11) on both app backgrounds and surfaces
    for background in (DARK.bg, DARK.surface, LIGHT.bg, LIGHT.surface):
        assert contrast(BRAND_ORANGE, background) >= 3.0, background
