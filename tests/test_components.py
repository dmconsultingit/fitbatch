"""Widgets can be built for every palette without a window (catches attribute/typo errors)."""

import flet as ft
import pytest

from fitbatch.core.activity_types import catalog
from fitbatch.ui.components import Kit
from fitbatch.ui.theme import DARK, LIGHT, Palette


@pytest.mark.parametrize("palette", [DARK, LIGHT], ids=["dark", "light"])
def test_every_component_builds(palette: Palette) -> None:
    k = Kit(palette)
    built = [
        k.title("T"),
        k.text("t", muted=True, color=palette.danger, font_family="Inter SemiBold"),
        k.strong("s", color=palette.muted),
        k.overline("o"),
        k.mono("m"),
        k.card(ft.Text("c")),
        k.step_number(1),
        k.kpi("label", "12", "km", accent=True),
        k.primary("p", None),
        k.secondary("s", None, danger=True),
        k.quiet("q", None),
        k.segmented([("a", "A"), ("b", "B")], "a", lambda _key: None),
        k.chip("c", True, lambda _e: None),
        k.field("f", "v"),
        k.checkbox(None, None, tristate=True),
    ]
    built += [k.pill("x", kind) for kind in ("success", "warning", "danger", "accent", "neutral")]
    assert all(isinstance(c, ft.Control) for c in built)


def test_type_badge_for_every_catalogue_type() -> None:
    k = Kit(DARK)
    for category in catalog().categories():
        for t in category.types:
            assert isinstance(k.type_badge(t.key), ft.Row)
    assert isinstance(k.type_badge("type_not_in_catalogue"), ft.Row)
