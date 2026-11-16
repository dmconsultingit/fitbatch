"""Bulk upload of activity files to a platform (F18), on top of any ``UploadAdapter``.

Files come from the user's disk: FIT, GPX and TCX, also gzip-compressed (``.fit.gz`` etc., as in a
Strava bulk export). Each file is checked before anything is sent. Files already uploaded to this
account (or reported as duplicates) are remembered by content hash, so running the same import
again sends nothing twice. Progress is reported as events, like the export.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from fitbatch.adapters.base import (
    AdapterError,
    AuthenticationError,
    DuplicateError,
    InvalidFileError,
    RateLimitError,
    UploadAdapter,
)
from fitbatch.core.export import RATE_LIMIT_WAITS_S, Waiting

log = logging.getLogger(__name__)

UPLOAD_FORMATS = {".fit": "fit", ".gpx": "gpx", ".tcx": "tcx"}
MAX_FILE_BYTES = 100 * 1024 * 1024  # uncompressed; real activity files are far smaller
_TEXT_SIGNATURES = {"gpx": b"<gpx", "tcx": b"<TrainingCenterDatabase"}


# --- files ---------------------------------------------------------------------------------------


class FileProblem(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class UploadFile:
    """One file chosen for upload, checked but not sent yet."""

    path: Path
    fmt: str  # "fit" | "gpx" | "tcx"
    compressed: bool
    sha256: str = ""  # of the uncompressed content; empty when the file could not be read
    # Why it cannot be sent: "unsupported", "unreadable", "too_large", "empty", "wrong_content".
    problem: str | None = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def upload_name(self) -> str:
        """The name sent to the platform: without ``.gz``."""
        return self.path.name[:-3] if self.compressed else self.path.name


def _format_of(path: Path) -> tuple[str, bool] | None:
    """(format, compressed) from the file name, or ``None`` when not an activity file."""
    suffixes = [s.lower() for s in path.suffixes[-2:]]
    compressed = bool(suffixes) and suffixes[-1] == ".gz"
    ext = (suffixes[-2] if len(suffixes) == 2 else "") if compressed else (suffixes or [""])[-1]
    fmt = UPLOAD_FORMATS.get(ext)
    return (fmt, compressed) if fmt else None


def read_content(path: Path, compressed: bool) -> bytes:
    """The file's activity data (decompressed), with a size limit against gzip bombs."""
    try:
        if not compressed:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise FileProblem("too_large")
            data = path.read_bytes()
        else:
            with gzip.open(path, "rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise FileProblem("too_large")
    except FileProblem:
        raise
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        raise FileProblem("unreadable") from exc
    if not data:
        raise FileProblem("empty")
    return data


def looks_like(fmt: str, data: bytes) -> bool:
    """Cheap content check, so a renamed or broken file is not sent to the platform."""
    if fmt == "fit":
        return len(data) >= 14 and data[8:12] == b".FIT"
    return _TEXT_SIGNATURES[fmt] in data[:4000]


def check_file(path: Path) -> UploadFile:
    found = _format_of(path)
    if found is None:
        return UploadFile(path, "", False, problem="unsupported")
    fmt, compressed = found
    try:
        data = read_content(path, compressed)
    except FileProblem as problem:
        return UploadFile(path, fmt, compressed, problem=problem.reason)
    digest = hashlib.sha256(data).hexdigest()
    if not looks_like(fmt, data):
        return UploadFile(path, fmt, compressed, digest, problem="wrong_content")
    return UploadFile(path, fmt, compressed, digest)


def scan_upload_paths(paths: Iterable[Path], *, recursive: bool = True) -> list[UploadFile]:
    """Check the chosen files and folders. In folders, only activity files are taken."""
    seen: set[Path] = set()
    result: list[UploadFile] = []

    def add(path: Path) -> None:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key not in seen:
            seen.add(key)
            result.append(check_file(path))

    for chosen in paths:
        if chosen.is_dir():
            pattern = chosen.rglob("*") if recursive else chosen.glob("*")
            for path in sorted(pattern):
                if path.is_file() and _format_of(path) is not None:
                    add(path)
        else:
            add(chosen)
    return result


# --- registry ------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    platform    TEXT NOT NULL,
    account     TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    result      TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    PRIMARY KEY (platform, account, sha256)
)
"""


class UploadRegistry:
    """Remembers which file contents went to which account: "uploaded" or "duplicate"."""

    def __init__(self, db_path: Path | str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> UploadRegistry:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def result(self, platform: str, account: str, sha256: str) -> str | None:
        row = self._conn.execute(
            "SELECT result FROM uploads WHERE platform=? AND account=? AND sha256=?",
            (platform, account, sha256),
        ).fetchone()
        return str(row[0]) if row else None

    def mark(self, platform: str, account: str, file: UploadFile, result: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO uploads VALUES (?, ?, ?, ?, ?, ?)",
            (platform, account, file.sha256, file.name, result, datetime.now(UTC).isoformat()),
        )
        self._conn.commit()


# --- events --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UploadEvent:
    pass


@dataclass(frozen=True, slots=True)
class UploadProgress(UploadEvent):
    done: int
    total: int
    file: UploadFile


@dataclass(frozen=True, slots=True)
class Uploading(UploadEvent):
    """Sent right before the network request."""

    file: UploadFile


@dataclass(frozen=True, slots=True)
class Uploaded(UploadEvent):
    file: UploadFile


@dataclass(frozen=True, slots=True)
class UploadSkipped(UploadEvent):
    file: UploadFile
    # "duplicate" (the platform has it) | "uploaded_before" | "same_file" | a FileProblem reason
    reason: str


@dataclass(frozen=True, slots=True)
class UploadFailed(UploadEvent):
    file: UploadFile
    message: str


@dataclass(slots=True)
class UploadSummary:
    uploaded: int = 0
    duplicates: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    paused_reason: str | None = None  # "rate_limit" | "authentication": resume later
    skipped_by_reason: dict[str, int] = field(default_factory=dict)


class _JobPaused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- uploader ------------------------------------------------------------------------------------


class Uploader:
    def __init__(
        self,
        adapter: UploadAdapter,
        registry: UploadRegistry,
        *,
        on_event: Callable[[UploadEvent | Waiting], None] = lambda _event: None,
        delay_s: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._adapter = adapter
        self._registry = registry
        self._emit = on_event
        self._delay_s = delay_s
        self._sleep = sleep

    def pending(self, files: Sequence[UploadFile]) -> list[UploadFile]:
        """Files that would really be sent: readable and not uploaded to this account before."""
        seen: set[str] = set()
        result = []
        for f in files:
            if f.problem or f.sha256 in seen or self._known(f):
                continue
            seen.add(f.sha256)
            result.append(f)
        return result

    def _previous(self, f: UploadFile) -> str | None:
        """ "uploaded" or "duplicate" if this content went to this account before."""
        return self._registry.result(self._adapter.platform_id, self._adapter.account, f.sha256)

    def _known(self, f: UploadFile) -> bool:
        return self._previous(f) is not None

    def run(
        self, files: Sequence[UploadFile], cancel: threading.Event | None = None
    ) -> UploadSummary:
        summary = UploadSummary()
        seen: set[str] = set()
        total = len(files)
        log.info("Upload started: %d files, platform %s", total, self._adapter.platform_id)
        try:
            for done, f in enumerate(files, 1):
                if cancel is not None and cancel.is_set():
                    summary.cancelled = True
                    break
                self._emit(UploadProgress(done, total, f))
                if f.problem:
                    self._skip(summary, f, f.problem)
                elif f.sha256 in seen:
                    self._skip(summary, f, "same_file")
                elif (previous := self._previous(f)) == "duplicate":
                    # Garmin said so last time: count it the same way, without a new request.
                    summary.duplicates += 1
                    self._emit(UploadSkipped(f, "duplicate"))
                elif previous is not None:
                    self._skip(summary, f, "uploaded_before")
                else:
                    seen.add(f.sha256)
                    self._upload_one(f, summary)
        except _JobPaused as paused:
            summary.paused_reason = paused.reason
        log.info(
            "Upload finished: uploaded %d, duplicates %d, skipped %d %s, failed %d, "
            "cancelled %s, paused %s",
            summary.uploaded,
            summary.duplicates,
            summary.skipped,
            summary.skipped_by_reason,
            summary.failed,
            summary.cancelled,
            summary.paused_reason,
        )
        return summary

    def _skip(self, summary: UploadSummary, f: UploadFile, reason: str) -> None:
        summary.skipped += 1
        summary.skipped_by_reason[reason] = summary.skipped_by_reason.get(reason, 0) + 1
        self._emit(UploadSkipped(f, reason))

    def _upload_one(self, f: UploadFile, summary: UploadSummary) -> None:
        platform, account = self._adapter.platform_id, self._adapter.account
        try:
            data = read_content(f.path, f.compressed)
        except FileProblem as problem:
            return self._skip(summary, f, problem.reason)
        self._emit(Uploading(f))
        try:
            self._upload_with_rate_limit(f, data)
        except DuplicateError:
            self._registry.mark(platform, account, f, "duplicate")
            summary.duplicates += 1
            self._emit(UploadSkipped(f, "duplicate"))
            return None
        except AuthenticationError as exc:
            summary.failed += 1
            self._emit(UploadFailed(f, str(exc)))
            raise _JobPaused("authentication") from exc
        except (InvalidFileError, AdapterError) as exc:
            summary.failed += 1
            self._emit(UploadFailed(f, str(exc)))
            return None
        finally:
            self._sleep(self._delay_s)  # N4: moderate request rate
        self._registry.mark(platform, account, f, "uploaded")
        summary.uploaded += 1
        self._emit(Uploaded(f))
        return None

    def _upload_with_rate_limit(self, f: UploadFile, data: bytes) -> None:
        for wait_s in (*RATE_LIMIT_WAITS_S, None):
            try:
                self._adapter.upload(f.upload_name, data)
                return
            except RateLimitError as exc:
                if wait_s is None:
                    raise _JobPaused("rate_limit") from exc
                self._emit(Waiting(wait_s, "rate_limit"))
                self._sleep(wait_s)
