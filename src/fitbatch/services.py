"""Composition root: builds the application services and wires adapters to the logic.

User interfaces (CLI, GUI) use only this module and ``core``; they never call a platform or
create an adapter themselves (N15).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fitbatch.adapters.base import AuthenticationError, RateLimitError
from fitbatch.adapters.garmin_email import GarminEmailAdapter, GarminEmailLogin, MfaProvider
from fitbatch.core.login_guard import LoginGuard
from fitbatch.core.registry import DownloadRegistry
from fitbatch.core.settings import Settings
from fitbatch.storage.paths import AppPaths, default_paths
from fitbatch.storage.secrets import SecretStore, default_secret_store

log = logging.getLogger(__name__)


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


def create_services(paths: AppPaths | None = None) -> AppServices:
    paths = (paths or default_paths()).ensure()
    secrets = default_secret_store(paths.secrets_dir)
    return AppServices(
        paths=paths,
        secrets=secrets,
        login_guard=LoginGuard(paths.root / "login_guard.json"),
        garmin_email=GarminEmailLogin(secrets),
    )
