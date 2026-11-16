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
import logging
import os
import zipfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any

from fitbatch.adapters.base import (
    AdapterError,
    AuthenticationError,
    NoFileError,
    RateLimitError,
)
from fitbatch.core.models import Activity, Capability, DownloadedFile, ExportFormat
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
        return frozenset({Capability.LIST_ACTIVITIES, Capability.DOWNLOAD})

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
