"""Activity type catalogue: Garmin's own categories and subtypes (N17, F7c).

Garmin publishes 154 types in two levels: 16 categories (``cycling``, ``fitness_equipment``…)
and their subtypes (``mountain_biking``, ``indoor_rowing``…). A category is itself a type too
(an activity can be plain ``cycling``). The app ships a snapshot of that catalogue and refreshes
it from Garmin when connected, so filters work offline and show every type Garmin knows.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

ROOT_KEY = "all"
OTHER = "other"

# Display order of categories: the usual amateur sports first.
CATEGORY_ORDER = (
    "cycling",
    "running",
    "swimming",
    "multi_sport",
    "fitness_equipment",
    "walking",
    "hiking",
    "winter_sports",
    "water_sports",
    "team_sports",
    "racket_sports",
    "diving",
    "para_sports",
    "other",
    "steps",
    "safety",
)

_NAME_OVERRIDES = {
    "fitness_equipment": "Fitness equipment",
    "multi_sport": "Multisport",
    "e_bike_fitness": "E-bike",
    "e_bike_mountain": "E-mountain bike",
    "enduro_mtb": "Enduro MTB",
    "e_enduro_mtb": "E-enduro MTB",
    "bmx": "BMX",
    "hiit": "HIIT",
    "rc_drone": "RC drone",
    "atv_v2": "ATV",
    "ccr_diving": "CCR diving",
    "e_sport": "E-sport",
    "paddelball": "Padel",
    "tennis_v2": "Tennis",
    "transition_v2": "Transition",
    "swimToBikeTransition_v2": "Swim-to-bike transition",
    "bikeToRunTransition_v2": "Bike-to-run transition",
    "runToBikeTransition_v2": "Run-to-bike transition",
    "resort_skiing_snowboarding_ws": "Resort skiing/snowboarding",
    "backcountry_skiing_snowboarding_ws": "Backcountry skiing/snowboarding",
    "stand_up_paddleboarding_v2": "Stand up paddleboarding",
    "cross_country_indoor_skiing": "Indoor cross-country skiing",
}


def humanize(type_key: str) -> str:
    """``kayaking_v2`` → "Kayaking", ``swimToBikeTransition_v2`` → overrides, else readable."""
    if type_key in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[type_key]
    key = re.sub(r"_(v\d+|ws)$", "", type_key)
    key = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", key).lower()
    words = key.replace("_", " ").split()
    return " ".join(words).capitalize() if words else "Other"


@dataclass(frozen=True, slots=True)
class ActivityType:
    key: str
    name: str
    category: str  # category key; a category's own type points to itself

    @property
    def is_category(self) -> bool:
        return self.key == self.category


@dataclass(slots=True)
class Category:
    key: str
    name: str
    types: list[ActivityType] = field(default_factory=list)  # its own type first, then subtypes

    @property
    def keys(self) -> list[str]:
        return [t.key for t in self.types]


class ActivityCatalog:
    def __init__(self, raw_types: Iterable[dict[str, Any]]) -> None:
        rows = [r for r in raw_types if isinstance(r, dict) and r.get("typeKey")]
        by_id = {r.get("typeId"): r for r in rows}
        root_ids = {r.get("typeId") for r in rows if r["typeKey"] == ROOT_KEY}
        self._types: dict[str, ActivityType] = {}
        for r in rows:
            key = r["typeKey"]
            if key == ROOT_KEY:
                continue
            parent = by_id.get(r.get("parentTypeId"))
            if parent is None or r.get("parentTypeId") in root_ids:
                category = key  # top level: the category itself
            else:
                category = parent["typeKey"]
            self._types[key] = ActivityType(key, humanize(key), category)
        if not self._types:
            raise ValueError("Empty activity type catalogue")

    # --- loading ---------------------------------------------------------------------------------

    @classmethod
    def bundled(cls) -> ActivityCatalog:
        text = (
            resources.files("fitbatch.data")
            .joinpath("garmin_activity_types.json")
            .read_text(encoding="utf-8")
        )
        return cls.from_document(json.loads(text))

    @classmethod
    def from_document(cls, document: Any) -> ActivityCatalog:
        types = document.get("types") if isinstance(document, dict) else document
        if not isinstance(types, list):
            raise ValueError("Unexpected activity type catalogue format")
        return cls(types)

    @classmethod
    def load(cls, cache: Path | None) -> ActivityCatalog:
        """The cached copy from Garmin when readable, otherwise the bundled snapshot."""
        if cache is not None:
            try:
                return cls.from_document(json.loads(cache.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return cls.bundled()

    # --- queries ---------------------------------------------------------------------------------

    def describe(self, type_key: str) -> ActivityType:
        key = (type_key or OTHER).strip()
        known = self._types.get(key) or self._types.get(key.lower())
        if known is not None:
            return known
        return ActivityType(key, humanize(key), OTHER)  # a type newer than the catalogue

    def category_name(self, category_key: str) -> str:
        return self.describe(category_key).name

    def categories(self) -> list[Category]:
        groups: dict[str, Category] = {}
        for t in self._types.values():
            cat = groups.setdefault(
                t.category, Category(t.category, self.category_name(t.category))
            )
            cat.types.append(t)
        for cat in groups.values():
            cat.types.sort(key=lambda t: (not t.is_category, t.name))
        order = {key: i for i, key in enumerate(CATEGORY_ORDER)}
        return sorted(groups.values(), key=lambda c: (order.get(c.key, len(order)), c.name))

    def all_keys(self) -> set[str]:
        return set(self._types)

    def categories_of(self, type_keys: Iterable[str]) -> list[str]:
        return sorted({self.describe(k).category for k in type_keys})


_catalog = ActivityCatalog.bundled()


def catalog() -> ActivityCatalog:
    return _catalog


def use_catalog(new: ActivityCatalog) -> None:
    global _catalog
    _catalog = new


def describe(type_key: str) -> ActivityType:
    return _catalog.describe(type_key)


def display_name(type_key: str) -> str:
    return _catalog.describe(type_key).name


def category_counts(type_keys: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in type_keys:
        cat = describe(key).category
        counts[cat] = counts.get(cat, 0) + 1
    return counts
