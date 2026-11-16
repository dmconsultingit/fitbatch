"""Downloaded files in a folder, grouped by activity (F17, works offline).

Files are recognised by FitBatch's default name pattern (F10): ``YYYY-MM-DD_type_ID[_n].ext``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

_NAME = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})_(?P<type>[a-z0-9_]+?)_(?P<id>\d+)(?:_(?P<n>\d+))?"
    r"(?P<ext>\.splits\.csv|\.[a-z0-9]+)$",
    re.IGNORECASE,
)
_FORMAT_LABELS = {".fit": "FIT", ".tcx": "TCX", ".gpx": "GPX", ".kml": "KML", ".splits.csv": "CSV"}


@dataclass(slots=True)
class LibraryEntry:
    day: date
    type_key: str
    activity_id: str
    files: list[Path] = field(default_factory=list)

    @property
    def formats(self) -> list[str]:
        found = {_label(p.name) for p in self.files}
        order = list(_FORMAT_LABELS.values())
        return sorted(found, key=lambda f: order.index(f) if f in order else len(order))


def _label(file_name: str) -> str:
    lower = file_name.lower()
    for ext, label in _FORMAT_LABELS.items():
        if lower.endswith(ext):
            return label
    return Path(file_name).suffix.lstrip(".").upper()


def scan_folder(folder: Path) -> list[LibraryEntry]:
    """Activities found in ``folder`` (not recursive), newest first; other files are ignored."""
    return scan_folders([folder])


def scan_folders(folders: Iterable[Path]) -> list[LibraryEntry]:
    """Activities in several folders (e.g. ``Garmin/export/fit``, ``…/tcx``) merged by activity."""
    entries: dict[tuple[str, str], LibraryEntry] = {}
    candidates: list[Path] = []
    for folder in folders:
        try:
            candidates.extend(p for p in folder.iterdir() if p.is_file())
        except OSError:
            continue
    for path in candidates:
        match = _NAME.match(path.name)
        if match is None:
            continue
        try:
            day = date.fromisoformat(match["day"])
        except ValueError:
            continue
        key = (match["id"], match["type"].lower())
        entry = entries.setdefault(key, LibraryEntry(day, match["type"].lower(), match["id"]))
        entry.files.append(path)
    return sorted(entries.values(), key=lambda e: (e.day, e.activity_id), reverse=True)
