from pathlib import Path

import pytest
from garminconnect import GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

from fitbatch.adapters.base import AuthenticationError, RateLimitError
from fitbatch.adapters.garmin_email import GarminEmailLogin
from fitbatch.core.login_guard import LoginGuard, LoginLockedError
from fitbatch.services import AppServices
from fitbatch.storage.paths import AppPaths
from fitbatch.storage.secrets import InMemorySecretStore
from tests.test_garmin_email import FakeGarmin


@pytest.fixture
def services(tmp_path: Path) -> AppServices:
    FakeGarmin.instances = []
    FakeGarmin.login_error = None
    secrets = InMemorySecretStore()
    return AppServices(
        paths=AppPaths(tmp_path).ensure(),
        secrets=secrets,
        login_guard=LoginGuard(tmp_path / "guard.json"),
        garmin_email=GarminEmailLogin(secrets, client_factory=FakeGarmin),
    )


def test_three_wrong_passwords_lock_login(services: AppServices) -> None:
    FakeGarmin.login_error = GarminConnectAuthenticationError("401")
    for _ in range(3):
        with pytest.raises(AuthenticationError):
            services.login_garmin_email("me@example.com", "wrong", lambda: "")
    attempts = len(FakeGarmin.instances)
    with pytest.raises(LoginLockedError):
        services.login_garmin_email("me@example.com", "wrong", lambda: "")
    assert len(FakeGarmin.instances) == attempts  # Garmin was not contacted again


def test_rate_limit_at_login_locks_immediately(services: AppServices) -> None:
    FakeGarmin.login_error = GarminConnectTooManyRequestsError("429")
    with pytest.raises(RateLimitError):
        services.login_garmin_email("me@example.com", "pw", lambda: "")
    with pytest.raises(LoginLockedError):
        services.login_garmin_email("me@example.com", "pw", lambda: "")


def test_successful_login_then_saved_session(services: AppServices) -> None:
    services.login_garmin_email("me@example.com", "pw", lambda: "")
    assert services.connect_saved_garmin_email() is not None


class CatalogAdapter:
    def __init__(self, types: list[dict] | Exception) -> None:
        self._types = types
        self.calls = 0

    def activity_types(self) -> list[dict]:
        self.calls += 1
        if isinstance(self._types, Exception):
            raise self._types
        return self._types


NEW_TYPES = [
    {"typeId": 17, "typeKey": "all", "parentTypeId": None},
    {"typeId": 2, "typeKey": "cycling", "parentTypeId": 17},
    {"typeId": 901, "typeKey": "hover_biking", "parentTypeId": 2},
]


def test_catalog_refreshed_once_a_week(services: AppServices) -> None:
    from fitbatch.core.activity_types import ActivityCatalog, describe, use_catalog

    adapter = CatalogAdapter(NEW_TYPES)
    try:
        assert services.refresh_catalog(adapter) is True
        assert describe("hover_biking").category == "cycling"
        assert services.refresh_catalog(adapter) is False  # fresh copy on disk
        assert adapter.calls == 1
        week_later = services.catalog_file.stat().st_mtime + 8 * 24 * 3600
        assert services.refresh_catalog(adapter, clock=lambda: week_later) is True
    finally:
        use_catalog(ActivityCatalog.bundled())


def test_catalog_refresh_failure_keeps_current(services: AppServices) -> None:
    from fitbatch.adapters.base import AdapterError

    assert services.refresh_catalog(CatalogAdapter(AdapterError("down"))) is False
    assert not services.catalog_file.exists()
