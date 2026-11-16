"""Composition root: builds the application services and wires adapters to the logic.

User interfaces (CLI, GUI) use only this module and ``core``; they never call a platform or
create an adapter themselves (N15).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fitbatch.adapters.base import AdapterError, AuthenticationError, RateLimitError
from fitbatch.adapters.garmin_email import GarminEmailAdapter, GarminEmailLogin, MfaProvider
from fitbatch.core.activity_types import ActivityCatalog, use_catalog
from fitbatch.core.login_guard import LoginGuard
from fitbatch.core.registry import DownloadRegistry
from fitbatch.core.settings import Settings
from fitbatch.core.upload import UploadRegistry
from fitbatch.storage.files import atomic_write
from fitbatch.storage.paths import AppPaths, default_paths
from fitbatch.storage.secrets import SecretStore, default_secret_store

log = logging.getLogger(__name__)

CATALOG_FILE = "garmin_activity_types.json"
CATALOG_MAX_AGE_S = 7 * 24 * 3600


@dataclass(slots=True)
class AppServices:
    paths: AppPaths
    secrets: SecretStore
    login_guard: LoginGuard
    garmin_email: GarminEmailLogin

    def load_settings(self) -> Settings:
        return Settings.load(self.paths.settings_file)

    def open_registry(self) -> DownloadRegistry:
        return DownloadRegistry(self.paths.registry_file)

    def open_upload_registry(self) -> UploadRegistry:
        """Uploaded files per account (F18); same database file as the download registry."""
        return UploadRegistry(self.paths.registry_file)

    def connect_saved_garmin_email(self) -> GarminEmailAdapter | None:
        """Reconnect with the saved session; ``None`` if the user has to log in again."""
        try:
            adapter = self.garmin_email.restore()
        except AuthenticationError:
            log.info("Saved Garmin session rejected; password login required")
            return None
        if adapter is not None:
            log.info("Connected to Garmin with the saved session (account %s)", adapter.account)
        return adapter

    def login_garmin_email(
        self, email: str, password: str, mfa_provider: MfaProvider
    ) -> GarminEmailAdapter:
        """Password login guarded by the attempt limit (N2). Never retried automatically."""
        self.login_guard.check()
        try:
            adapter = self.garmin_email.login(email, password, mfa_provider)
        except RateLimitError:
            log.warning("Password login rate limited by Garmin; login paused")
            self.login_guard.record_rate_limit()
            raise
        except AuthenticationError:
            log.warning("Password login rejected")
            self.login_guard.record_failure()
            raise
        finally:
            del password
        self.login_guard.record_success()
        log.info("Logged in to Garmin with password (account %s)", adapter.account)
        return adapter

    # --- activity type catalogue (F7c, N17) ------------------------------------------------------

    @property
    def catalog_file(self) -> Path:
        return self.paths.root / CATALOG_FILE

    def load_catalog(self) -> None:
        """Use the copy fetched from Garmin when present, else the snapshot bundled with the app."""
        use_catalog(ActivityCatalog.load(self.catalog_file))

    def refresh_catalog(
        self, adapter: GarminEmailAdapter, clock: Callable[[], float] = time.time
    ) -> bool:
        """Fetch Garmin's type list at most once a week; failures keep the current catalogue."""
        try:
            age = clock() - self.catalog_file.stat().st_mtime
            if age < CATALOG_MAX_AGE_S:
                return False
        except FileNotFoundError:
            pass
        try:
            types = adapter.activity_types()
            fresh = ActivityCatalog(types)
        except (AdapterError, ValueError) as exc:
            log.warning("Activity type catalogue not refreshed: %s", exc)
            return False
        document = {
            "source": "Garmin Connect /activity-service/activity/activityTypes",
            "fetched": datetime.now(UTC).isoformat(),
            "types": [{k: t.get(k) for k in ("typeId", "typeKey", "parentTypeId")} for t in types],
        }
        atomic_write(self.catalog_file, json.dumps(document, indent=1).encode("utf-8"))
        use_catalog(fresh)
        log.info("Activity type catalogue refreshed (%d types)", len(fresh.all_keys()))
        return True


def create_services(paths: AppPaths | None = None) -> AppServices:
    paths = (paths or default_paths()).ensure()
    secrets = default_secret_store(paths.secrets_dir)
    services = AppServices(
        paths=paths,
        secrets=secrets,
        login_guard=LoginGuard(paths.root / "login_guard.json"),
        garmin_email=GarminEmailLogin(secrets),
    )
    services.load_catalog()
    return services
