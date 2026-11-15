from dataclasses import replace

import pytest

from fitbatch.core.filters import ActivityFilter
from fitbatch.core.models import Activity


def named(activity: Activity, name: str, type_key: str = "cycling") -> Activity:
    return replace(activity, name=name, type_key=type_key)


@pytest.mark.parametrize(
    ("name", "include", "expected"),
    [
        ("Zwift - E1 Endurance", ["E1"], True),
        ("Zone1 recovery", ["E1"], False),  # whole words only (D14)
        ("FTP Ramp test", ["ftp ramp"], True),  # phrases, case-insensitive
        ("Trening siłowy Łódź", ["łódź"], True),  # Polish letters
        ("Łódźka", ["łódź"], False),
        ("Morning ride", ["TFS", "Zwift"], False),
        ("TFS intervals", ["TFS", "Zwift"], True),  # "or" between phrases
        ("a.b c", ["a.b"], True),  # taken literally, not as a regular expression
    ],
)
def test_include_names(activity: Activity, name: str, include: list[str], expected: bool) -> None:
    assert ActivityFilter(include_names=include).matches(named(activity, name)) is expected


def test_exclude_wins_over_include(activity: Activity) -> None:
    f = ActivityFilter(include_names=["enduro"], exclude_names=["Zwift"])
    assert f.matches(named(activity, "Enduro race"))
    assert not f.matches(named(activity, "Zwift enduro"))


def test_type_filter_and_combination(activity: Activity) -> None:
    f = ActivityFilter(include_names=["enduro"], type_keys=["mountain_biking"])
    assert f.matches(named(activity, "Enduro", "mountain_biking"))
    assert not f.matches(named(activity, "Enduro", "running"))


def test_empty_filter_matches_all(activity: Activity) -> None:
    f = ActivityFilter(include_names=["  "], exclude_names=[])
    assert not f.is_active
    assert f.apply([activity]) == [activity]
