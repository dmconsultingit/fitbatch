import json
from pathlib import Path

import pytest

from fitbatch.core.activity_types import (
    ActivityCatalog,
    category_counts,
    describe,
    display_name,
    humanize,
)
from fitbatch.core.library import scan_folder


def test_bundled_catalogue_has_garmin_categories() -> None:
    cat = ActivityCatalog.bundled()
    keys = [c.key for c in cat.categories()]
    assert len(cat.all_keys()) >= 150
    assert len(keys) == 16
    assert keys[:3] == ["cycling", "running", "swimming"]
    assert "all" not in cat.all_keys()


def test_subtypes_belong_to_their_category() -> None:
    assert describe("mountain_biking").category == "cycling"
    assert describe("indoor_rowing").category == "fitness_equipment"
    assert describe("cycling").category == "cycling"
    assert describe("cycling").is_category


def test_category_lists_its_own_type_first() -> None:
    cycling = next(c for c in ActivityCatalog.bundled().categories() if c.key == "cycling")
    assert cycling.types[0].key == "cycling"
    assert "mountain_biking" in cycling.keys
    assert cycling.name == "Cycling"


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("kayaking_v2", "Kayaking"),
        ("skate_skiing_ws", "Skate skiing"),
        ("swimToBikeTransition_v2", "Swim-to-bike transition"),
        ("e_bike_fitness", "E-bike"),
        ("indoor_climbing", "Indoor climbing"),
        ("fitness_equipment", "Fitness equipment"),
        ("someNewThing_v3", "Some new thing"),
    ],
)
def test_humanize(key: str, name: str) -> None:
    assert humanize(key) == name


def test_unknown_type_goes_to_other() -> None:
    t = describe("brand_new_sport")
    assert (t.category, t.name) == ("other", "Brand new sport")
    assert display_name("") == "Other"


def test_load_prefers_valid_cache_and_falls_back(tmp_path: Path) -> None:
    cache = tmp_path / "types.json"
    cache.write_text(
        json.dumps(
            {
                "types": [
                    {"typeId": 17, "typeKey": "all", "parentTypeId": None},
                    {"typeId": 2, "typeKey": "cycling", "parentTypeId": 17},
                    {"typeId": 900, "typeKey": "hover_biking", "parentTypeId": 2},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert ActivityCatalog.load(cache).describe("hover_biking").category == "cycling"
    cache.write_text("broken", encoding="utf-8")
    assert len(ActivityCatalog.load(cache).all_keys()) >= 150
    assert len(ActivityCatalog.load(None).all_keys()) >= 150


def test_category_counts() -> None:
    counts = category_counts(["cycling", "mountain_biking", "strength_training"])
    assert counts == {"cycling": 2, "fitness_equipment": 1}


def test_scan_folder_groups_files_by_activity(tmp_path: Path) -> None:
    for name in [
        "2026-11-14_mountain_biking_25077802599.fit",
        "2026-11-14_mountain_biking_25077802599.gpx",
        "2026-11-14_mountain_biking_25077802599.splits.csv",
        "2026-11-14_mountain_biking_25077802599_1.fit",
        "2026-11-15_walking_25088812631.fit",
        "notes.txt",
        "2026-13-40_bad_1.fit",
    ]:
        (tmp_path / name).write_bytes(b"x")
    entries = scan_folder(tmp_path)
    assert [e.activity_id for e in entries] == ["25088812631", "25077802599"]
    ride = entries[1]
    assert ride.type_key == "mountain_biking"
    assert ride.formats == ["FIT", "GPX", "CSV"]
    assert len(ride.files) == 4


def test_scan_missing_folder_is_empty(tmp_path: Path) -> None:
    assert scan_folder(tmp_path / "nope") == []
