"""Registry of downloaded activities (N14), independent of the access method (F10)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from fitbatch.core.models import Activity, ExportFormat

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    platform      TEXT NOT NULL,
    account       TEXT NOT NULL,
    activity_id   TEXT NOT NULL,
    format        TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    downloaded_at TEXT NOT NULL,
    PRIMARY KEY (platform, account, activity_id, format)
)
"""


class DownloadRegistry:
    """Remembers which activity was saved in which format, keyed by platform + account + ID."""

    def __init__(self, db_path: Path | str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> DownloadRegistry:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def is_downloaded(self, activity: Activity, fmt: ExportFormat) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM downloads WHERE platform=? AND account=? AND activity_id=? AND format=?",
            (activity.platform, activity.account, activity.activity_id, fmt.value),
        ).fetchone()
        return row is not None

    def mark_downloaded(self, activity: Activity, fmt: ExportFormat, file_path: Path) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO downloads VALUES (?, ?, ?, ?, ?, ?)",
            (
                activity.platform,
                activity.account,
                activity.activity_id,
                fmt.value,
                str(file_path),
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()

    def count(self) -> int:
        (n,) = self._conn.execute("SELECT COUNT(*) FROM downloads").fetchone()
        return int(n)
