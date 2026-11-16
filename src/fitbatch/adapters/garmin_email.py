"""Garmin Connect via email + password, using python-garminconnect (unofficial, D2).

Security (N2):
- the password is passed straight to the library and never stored or logged; the library
  clears it after a successful login, and on failure the client object is discarded;
- the session tokens are kept only in the encrypted ``SecretStore`` (never in
  ``~/.garminconnect``), loaded and saved as JSON text;
- error messages are our own, so library details (URLs, responses) never reach the user.
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from fitbatch.adapters.base import (
    AdapterError,
    AuthenticationError,
    DuplicateError,
    InvalidFileError,
    NoFileError,
    RateLimitError,
)
from fitbatch.core.models import Activity, Capability, DownloadedFile, ExportFormat, Workout
from fitbatch.storage.files import safe_filename
from fitbatch.storage.secrets import SecretStore

PLATFORM_ID = "garmin"
METHOD_ID = "email"
MAX_UNPACKED_BYTES = 200 * 1024 * 1024  # guards against zip bombs; real FIT files are far smaller

log = logging.getLogger(__name__)

MfaProvider = Callable[[], str]
ClientFactory = Callable[..., Any]


def _default_factory(**kwargs: Any) -> Any:
    from garminconnect import Garmin  # imported lazily: heavy import, optional in tests

    # Garmin.login() without a token store falls back to $GARMINTOKENS and would then write the
    # tokens there in plain text. FitBatch keeps them only in its encrypted store.
    os.environ.pop("GARMINTOKENS", None)
    return Garmin(**kwargs)


@contextmanager
def _translate_errors(action: str) -> Iterator[None]:
    """Turn library exceptions into adapter errors with safe, user-facing messages."""
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectConnectionError,
        GarminConnectNotFoundError,
        GarminConnectTooManyRequestsError,
    )

    try:
        yield
    except GarminConnectTooManyRequestsError as exc:
        log.warning("%s: rate limited by Garmin", action)
        raise RateLimitError("Garmin is limiting requests (429)") from exc
    except GarminConnectAuthenticationError as exc:
        log.warning("%s: authentication rejected", action)
        raise AuthenticationError("Garmin rejected the login or the session expired") from exc
    except GarminConnectNotFoundError as exc:
        raise NoFileError("Garmin has no file for this activity") from exc
    except GarminConnectConnectionError as exc:
        log.warning("%s failed: %s", action, exc)
        raise AdapterError(f"{action} failed: connection problem with Garmin Connect") from exc


class GarminEmailLogin:
    """Login flow for the email + password method: saved session, password, MFA code."""

    def __init__(self, secrets: SecretStore, client_factory: ClientFactory | None = None) -> None:
        self._secrets = secrets
        self._factory = client_factory or _default_factory

    def has_saved_session(self) -> bool:
        return self._secrets.load(PLATFORM_ID, METHOD_ID) is not None

    def restore(self) -> GarminEmailAdapter | None:
        """Connect with the saved session; ``None`` when nothing is saved.

        A session rejected by Garmin is deleted, so the next attempt asks for the password.
        """
        saved = self._secrets.load(PLATFORM_ID, METHOD_ID)
        if saved is None:
            return None
        client = self._factory()
        try:
            with _translate_errors("Login with saved session"):
                client.login(saved.decode("utf-8"))
        except AuthenticationError:
            self._secrets.delete(PLATFORM_ID, METHOD_ID)
            raise
        return self._connected(client)

    def login(self, email: str, password: str, mfa_provider: MfaProvider) -> GarminEmailAdapter:
        """Log in with email and password; ``mfa_provider`` is called only if Garmin asks."""
        client = self._factory(email=email, password=password, prompt_mfa=mfa_provider)
        del password  # keep no reference in this frame
        with _translate_errors("Login"):
            client.login()
        return self._connected(client)

    def logout(self) -> None:
        """Delete the saved session ("Usuń zapisane dane", F3)."""
        self._secrets.delete(PLATFORM_ID, METHOD_ID)

    def _connected(self, client: Any) -> GarminEmailAdapter:
        adapter = GarminEmailAdapter(client, on_session_change=self._save_session)
        adapter.persist_session()
        return adapter

    def _save_session(self, tokens_json: str) -> None:
        self._secrets.save(PLATFORM_ID, METHOD_ID, tokens_json.encode("utf-8"))


def _parse_gmt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("T", " ")[:19]).replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_local(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # No timezone in startTimeLocal: assume this computer's zone (D8) as a fallback only.
        return datetime.fromisoformat(value.replace("T", " ")[:19]).astimezone()
    except ValueError:
        return None


class GarminEmailAdapter:
    """Lists and downloads activities through an authenticated python-garminconnect client."""

    LIST_MARGIN = timedelta(days=1)  # Garmin filters by the activity's local date

    def __init__(self, client: Any, on_session_change: Callable[[str], None]) -> None:
        self._client = client
        self._on_session_change = on_session_change
        self._last_session: str | None = None
        profile_id = getattr(client, "profile_id", None)
        self._account = str(profile_id or getattr(client, "display_name", None) or "unknown")

    @property
    def platform_id(self) -> str:
        return PLATFORM_ID

    @property
    def method_id(self) -> str:
        return METHOD_ID

    @property
    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.LIST_ACTIVITIES,
                Capability.DOWNLOAD,
                Capability.UPLOAD,
                Capability.WORKOUTS,
            }
        )

    @property
    def remote_formats(self) -> frozenset[ExportFormat]:
        return frozenset(ExportFormat)

    @property
    def account(self) -> str:
        return self._account

    @property
    def account_label(self) -> str:
        """Human-readable account name for the status bar (F4)."""
        return str(
            getattr(self._client, "full_name", None)
            or getattr(self._client, "display_name", None)
            or self._account
        )

    def persist_session(self) -> None:
        """Save the session if the library refreshed its tokens since the last save."""
        tokens = self._client.client.dumps()
        if tokens != self._last_session:
            self._on_session_change(tokens)
            self._last_session = tokens

    # --- listing ------------------------------------------------------------------------------

    def list_activities(
        self, start: datetime, end: datetime, categories: Sequence[str] | None = None
    ) -> Sequence[Activity]:
        """Activities in the range; ``categories`` asks Garmin to filter by top-level category.

        Garmin filters server-side only by category (not by subtype); if it refuses a category the
        whole range is fetched instead and the caller's local filter does the rest.
        """
        query_start = (start - self.LIST_MARGIN).date().isoformat()
        query_end = (end + self.LIST_MARGIN).date().isoformat()
        raw: list[dict[str, Any]] = []
        try:
            for category in categories or [None]:
                with _translate_errors("Activity list"):
                    raw.extend(
                        self._client.get_activities_by_date(query_start, query_end, category) or []
                    )
        except (AuthenticationError, RateLimitError):
            raise
        except AdapterError:
            if not categories:
                raise
            log.info("Category filter refused by Garmin; listing the whole range instead")
            with _translate_errors("Activity list"):
                raw = self._client.get_activities_by_date(query_start, query_end, None) or []
        self.persist_session()
        unique = {str(r.get("activityId")): r for r in raw}
        activities = [a for a in (self._to_activity(r) for r in unique.values()) if a is not None]
        return sorted(
            (a for a in activities if start <= a.start_time <= end), key=lambda a: a.start_time
        )

    def activity_types(self) -> list[dict[str, Any]]:
        """Garmin's global activity type catalogue (no personal data)."""
        with _translate_errors("Activity types"):
            types = self._client.get_activity_types()
        self.persist_session()
        if not isinstance(types, list):
            raise AdapterError("Unexpected activity type list from Garmin")
        return types

    def _to_activity(self, raw: dict[str, Any]) -> Activity | None:
        start = _parse_gmt(raw.get("startTimeGMT")) or _parse_local(raw.get("startTimeLocal"))
        activity_id = raw.get("activityId")
        if start is None or activity_id is None:
            log.warning("Skipping activity without id or start time")
            return None
        return Activity(
            platform=PLATFORM_ID,
            account=self._account,
            activity_id=str(activity_id),
            name=raw.get("activityName") or "",
            type_key=(raw.get("activityType") or {}).get("typeKey") or "other",
            start_time=start,
            duration_s=raw.get("duration"),
            distance_m=raw.get("distance"),
            has_gps=bool(raw.get("hasPolyline") or raw.get("startLatitude") is not None),
        )

    # --- downloading --------------------------------------------------------------------------

    def download(self, activity: Activity, fmt: ExportFormat) -> list[DownloadedFile]:
        from garminconnect import Garmin

        formats = Garmin.ActivityDownloadFormat
        library_format = {
            ExportFormat.FIT: formats.ORIGINAL,
            ExportFormat.TCX: formats.TCX,
            ExportFormat.GPX: formats.GPX,
            ExportFormat.KML: formats.KML,
            ExportFormat.SPLITS_CSV: formats.CSV,
        }[fmt]
        with _translate_errors(f"Download {fmt.value}"):
            data = self._client.download_activity(activity.activity_id, dl_fmt=library_format)
        self.persist_session()
        if not data:
            raise NoFileError("Garmin returned an empty file")
        if fmt is ExportFormat.FIT:
            return unpack_original(data)
        _check_content(fmt, data)
        return [DownloadedFile(f".{fmt.value}", data)]

    # --- uploading (F18) ------------------------------------------------------------------------

    def upload(self, file_name: str, data: bytes) -> None:
        """Upload one FIT/GPX/TCX file as an import.

        Garmin's import endpoint marks the activity as imported, so Garmin does not pass it on to
        connected apps (e.g. Strava): importing a history that came from there makes no copies.
        The library needs a path, so the data goes through a private temporary file.
        """
        suffix = PurePosixPath(file_name).suffix.lower()
        if suffix not in (".fit", ".gpx", ".tcx"):
            raise InvalidFileError(f"Unsupported file type: {suffix or 'none'}")
        stem = safe_filename(PurePosixPath(file_name).stem) or "activity"
        with tempfile.TemporaryDirectory(prefix="fitbatch-upload-") as tmp:
            path = Path(tmp) / f"{stem}{suffix}"
            path.write_bytes(data)
            try:
                self._client.import_activity(str(path))
            except Exception as exc:  # the library raises several unrelated types
                raise _upload_error(exc) from exc
        self.persist_session()

    # --- workout library ------------------------------------------------------------------------

    WORKOUT_PAGE = 100

    def list_workouts(self) -> Sequence[Workout]:
        """Every workout in the library (paged by 100), newest change first."""
        raw: list[dict[str, Any]] = []
        start = 0
        while True:
            with _translate_errors("Workout list"):
                page = self._client.get_workouts(start, self.WORKOUT_PAGE) or []
            raw.extend(r for r in page if isinstance(r, dict))
            if len(page) < self.WORKOUT_PAGE or start > 20_000:
                break
            start += self.WORKOUT_PAGE
        self.persist_session()
        unique = {str(r["workoutId"]): r for r in raw if r.get("workoutId") is not None}
        workouts = [self._to_workout(r) for r in unique.values()]
        return sorted(
            workouts,
            key=lambda w: w.updated or w.created or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )

    def _to_workout(self, raw: dict[str, Any]) -> Workout:
        return Workout(
            platform=PLATFORM_ID,
            account=self._account,
            workout_id=str(raw["workoutId"]),
            name=raw.get("workoutName") or "",
            sport_key=(raw.get("sportType") or {}).get("sportTypeKey") or "other",
            created=_parse_local(raw.get("createdDate")),
            updated=_parse_local(raw.get("updatedDate")),
            duration_s=raw.get("estimatedDurationInSecs"),
            distance_m=raw.get("estimatedDistanceInMeters"),
        )

    def workout_definition(self, workout: Workout) -> bytes:
        with _translate_errors("Workout backup"):
            data = self._client.get_workout_by_id(workout.workout_id)
        self.persist_session()
        if not isinstance(data, dict) or not data:
            raise AdapterError("Unexpected workout data from Garmin")
        return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")

    def workout_file(self, workout: Workout) -> bytes:
        with _translate_errors("Workout FIT"):
            data = self._client.download_workout(workout.workout_id)
        self.persist_session()
        if not data or not _is_fit(data):
            raise NoFileError("Garmin has no FIT file for this workout")
        return data

    def delete_workout(self, workout: Workout) -> None:
        try:
            self._client.delete_workout(workout.workout_id)
        except Exception as exc:  # the library raises several unrelated types here
            raise _request_error(exc, "Delete workout") from exc
        self.persist_session()


def _status_code(exc: BaseException) -> int | None:
    """HTTP status from the exception or anything it was raised from."""
    current: BaseException | None = exc
    for _ in range(6):
        if current is None:
            break
        status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int):
            return status
        current = current.__cause__ or current.__context__
    return None


def _upload_error(exc: Exception) -> AdapterError:
    """Map a library error from an upload to an adapter error with a safe message."""
    from garminconnect import (
        GarminConnectAuthenticationError,
        GarminConnectInvalidFileFormatError,
        GarminConnectTooManyRequestsError,
    )

    status = _status_code(exc)
    if isinstance(exc, GarminConnectTooManyRequestsError) or status == 429:
        log.warning("Upload: rate limited by Garmin")
        return RateLimitError("Garmin is limiting requests (429)")
    if isinstance(exc, GarminConnectAuthenticationError) or status == 401:
        log.warning("Upload: authentication rejected")
        return AuthenticationError("Garmin rejected the login or the session expired")
    if status == 409 or "duplicate" in str(exc).lower():
        return DuplicateError("Garmin already has this activity")
    if isinstance(exc, GarminConnectInvalidFileFormatError) or status in (400, 413, 415, 422):
        log.info("Upload: file rejected by Garmin (status %s)", status)
        return InvalidFileError("Garmin could not read this file")
    log.warning("Upload failed (status %s): %s", status, type(exc).__name__)
    return AdapterError("Upload failed: connection problem with Garmin Connect")


def _request_error(exc: Exception, action: str) -> AdapterError:
    """Map a library error from a write request (not translated by the library) safely."""
    from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

    status = _status_code(exc)
    if isinstance(exc, GarminConnectTooManyRequestsError) or status == 429:
        log.warning("%s: rate limited by Garmin", action)
        return RateLimitError("Garmin is limiting requests (429)")
    if isinstance(exc, GarminConnectAuthenticationError) or status == 401:
        log.warning("%s: authentication rejected", action)
        return AuthenticationError("Garmin rejected the login or the session expired")
    if status == 404:
        return NoFileError("Garmin Connect has no such item (already deleted?)")
    log.warning("%s failed (status %s): %s", action, status, type(exc).__name__)
    return AdapterError(f"{action} failed: connection problem with Garmin Connect")


_SIGNATURES = {
    ExportFormat.TCX: b"<TrainingCenterDatabase",
    ExportFormat.GPX: b"<gpx",
    ExportFormat.KML: b"<kml",
}


def _check_content(fmt: ExportFormat, data: bytes) -> None:
    signature = _SIGNATURES.get(fmt)
    if signature is not None and signature not in data[:4000]:
        # Garmin answers with an empty document for activities added manually (no file).
        raise NoFileError(f"Garmin returned no {fmt.value.upper()} data")


def _is_fit(data: bytes) -> bool:
    return len(data) >= 12 and data[8:12] == b".FIT"


def unpack_original(data: bytes) -> list[DownloadedFile]:
    """The "original" download is a ZIP with one or more FIT files, or the uploaded file."""
    if not zipfile.is_zipfile(io.BytesIO(data)):
        if _is_fit(data):
            return [DownloadedFile(".fit", data)]
        raise AdapterError("Unexpected original file format from Garmin")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        # Archive member names are never used as paths, only their extension (no zip-slip).
        if sum(m.file_size for m in members) > MAX_UNPACKED_BYTES:
            raise AdapterError("Original file from Garmin is unexpectedly large")
        fits = [m for m in members if m.filename.lower().endswith(".fit")]
        if fits:
            return [DownloadedFile(".fit", archive.read(m)) for m in fits]
        # Uploaded as GPX/TCX: no FIT exists, keep the original unchanged.
        return [
            DownloadedFile(
                PurePosixPath(m.filename).suffix.lower() or ".bin", archive.read(m), True
            )
            for m in members
        ]
