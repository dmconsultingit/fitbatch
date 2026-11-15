"""Domain models shared by every layer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ExportFormat(StrEnum):
    """File formats offered by the single-activity export menu in Garmin Connect (F5)."""

    FIT = "fit"
    TCX = "tcx"
    GPX = "gpx"
    KML = "kml"
    SPLITS_CSV = "splits.csv"

    @property
    def needs_gps(self) -> bool:
        """Formats that only make sense for activities with a GPS track."""
        return self in (ExportFormat.GPX, ExportFormat.KML)


class Capability(StrEnum):
    """What an adapter can do (N11). The UI enables or greys out features based on this."""

    LIST_ACTIVITIES = "list_activities"
    DOWNLOAD = "download"
    UPLOAD = "upload"
    WORKOUTS = "workouts"


@dataclass(frozen=True, slots=True)
class Activity:
    """One activity as listed by a platform, before anything is downloaded."""

    platform: str
    account: str
    activity_id: str
    name: str
    type_key: str
    start_time: datetime  # timezone-aware; converted to local time only for display (D8)
    duration_s: float | None = None
    distance_m: float | None = None
    has_gps: bool = True

    def __post_init__(self) -> None:
        if self.start_time.tzinfo is None:
            raise ValueError("Activity.start_time must be timezone-aware")
