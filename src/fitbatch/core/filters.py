"""Activity list filters (F7) applied before anything is downloaded."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from fitbatch.core.models import Activity


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Whole word or phrase, case-insensitive, taken literally (D14).

    "E1" matches "Zwift - E1 Endurance" but not "Zone1"; Polish letters count as word letters.
    """
    return re.compile(rf"(?<![^\W_]){re.escape(phrase.strip())}(?![^\W_])", re.IGNORECASE)


@dataclass(slots=True)
class ActivityFilter:
    include_names: Sequence[str] = ()  # (a) name contains any of these phrases
    exclude_names: Sequence[str] = ()  # (b) name contains none of these; wins over (a)
    type_keys: Iterable[str] = ()  # (c) platform type keys; empty = all types
    _include: list[re.Pattern[str]] = field(init=False, repr=False)
    _exclude: list[re.Pattern[str]] = field(init=False, repr=False)
    _types: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._include = [_phrase_pattern(p) for p in self.include_names if p.strip()]
        self._exclude = [_phrase_pattern(p) for p in self.exclude_names if p.strip()]
        self._types = frozenset(t.strip().lower() for t in self.type_keys if t.strip())

    @property
    def is_active(self) -> bool:
        return bool(self._include or self._exclude or self._types)

    def matches(self, activity: Activity) -> bool:
        if self._types and activity.type_key.lower() not in self._types:
            return False
        name = activity.name
        if self._include and not any(p.search(name) for p in self._include):
            return False
        return not any(p.search(name) for p in self._exclude)

    def apply(self, activities: Iterable[Activity]) -> list[Activity]:
        return [a for a in activities if self.matches(a)]
