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
