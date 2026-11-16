"""Garmin email adapter tested against a fake python-garminconnect client (no network)."""

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from fitbatch.adapters.base import AuthenticationError, NoFileError, RateLimitError
from fitbatch.adapters.garmin_email import GarminEmailLogin, unpack_original
from fitbatch.core.models import ExportFormat
from fitbatch.storage.secrets import InMemorySecretStore

TOKENS = json.dumps({"di_token": "t", "di_refresh_token": "r", "di_client_id": "c"})
FIT_BYTES = b"\x0e\x10\x00\x00\x00\x00\x00\x00.FIT" + b"\x00" * 20

RAW_ACTIVITIES = [
    {
        "activityId": 111,
        "activityName": "Morning run",
        "activityType": {"typeKey": "running"},
        "startTimeGMT": "2026-09-05 06:00:00",
        "startTimeLocal": "2026-09-05 08:00:00",
        "duration": 1800.0,
        "distance": 5000.0,
        "hasPolyline": True,
    },
    {
        "activityId": 222,
        "activityName": "Gym",
        "activityType": {"typeKey": "strength_training"},
        "startTimeGMT": "2026-10-01 17:00:00",
        "hasPolyline": False,
    },
]


class FakeInnerClient:
    def __init__(self) -> None:
        self.tokens = TOKENS

    def dumps(self) -> str:
        return self.tokens


class FakeGarmin:
    instances: list["FakeGarmin"] = []
    login_error: Exception | None = None
    download_result: Any = b""
    download_error: Exception | None = None

    def __init__(self, email: str | None = None, password: str | None = None, prompt_mfa=None):
        self.email, self.password, self.prompt_mfa = email, password, prompt_mfa
        self.client = FakeInnerClient()
        self.profile_id = 42
        self.display_name = "dawid"
        self.full_name = "Dawid M"
        self.login_calls: list[Any] = []
        FakeGarmin.instances.append(self)

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        self.login_calls.append(tokenstore)
        if FakeGarmin.login_error is not None:
            raise FakeGarmin.login_error
        self.password = None
        return None, None

    def get_activities_by_date(self, start: str, end: str, activity_type: Any) -> list[dict]:
        self.list_query = (start, end)
        return RAW_ACTIVITIES

    def download_activity(self, activity_id: str, dl_fmt: Any) -> bytes:
        self.download_query = (activity_id, dl_fmt)
        if FakeGarmin.download_error is not None:
            raise FakeGarmin.download_error
        return FakeGarmin.download_result


@pytest.fixture(autouse=True)
def reset_fake() -> None:
    FakeGarmin.instances = []
    FakeGarmin.login_error = None
    FakeGarmin.download_result = b""
    FakeGarmin.download_error = None


@pytest.fixture
def secrets() -> InMemorySecretStore:
    return InMemorySecretStore()


@pytest.fixture
def flow(secrets: InMemorySecretStore) -> GarminEmailLogin:
    return GarminEmailLogin(secrets, client_factory=FakeGarmin)


def test_password_login_saves_only_tokens(flow: GarminEmailLogin, secrets) -> None:
    adapter = flow.login("me@example.com", "s3cret-pass", mfa_provider=lambda: "123456")
    saved = secrets.load("garmin", "email")
    assert saved == TOKENS.encode()
    assert b"s3cret-pass" not in saved
    assert adapter.account == "42"
    assert adapter.account_label == "Dawid M"
    assert FakeGarmin.instances[0].prompt_mfa() == "123456"


def test_restore_uses_saved_tokens_without_password(flow: GarminEmailLogin, secrets) -> None:
    assert flow.restore() is None
    secrets.save("garmin", "email", TOKENS.encode())
    adapter = flow.restore()
    assert adapter is not None
    garmin = FakeGarmin.instances[-1]
    assert garmin.password is None
    assert garmin.login_calls == [TOKENS]


def test_rejected_session_is_deleted(flow: GarminEmailLogin, secrets) -> None:
    secrets.save("garmin", "email", TOKENS.encode())
    FakeGarmin.login_error = GarminConnectAuthenticationError("expired")
    with pytest.raises(AuthenticationError):
        flow.restore()
    assert secrets.load("garmin", "email") is None


def test_rate_limited_session_is_kept(flow: GarminEmailLogin, secrets) -> None:
    secrets.save("garmin", "email", TOKENS.encode())
    FakeGarmin.login_error = GarminConnectTooManyRequestsError("429")
    with pytest.raises(RateLimitError):
        flow.restore()
    assert secrets.load("garmin", "email") is not None


def test_login_error_message_hides_library_details(flow: GarminEmailLogin) -> None:
    FakeGarmin.login_error = GarminConnectAuthenticationError(
        "401 https://sso.garmin.com/?password=s3cret-pass"
    )
    with pytest.raises(AuthenticationError) as exc:
        flow.login("me@example.com", "s3cret-pass", mfa_provider=lambda: "")
    assert "s3cret" not in str(exc.value)
    assert "sso.garmin.com" not in str(exc.value)


def test_refreshed_tokens_are_saved_again(flow: GarminEmailLogin, secrets) -> None:
    adapter = flow.login("me@example.com", "pw", mfa_provider=lambda: "")
    FakeGarmin.instances[0].client.tokens = TOKENS.replace('"t"', '"t2"')
    adapter.list_activities(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC))
    assert b'"t2"' in secrets.load("garmin", "email")


def test_list_maps_and_filters_by_exact_range(flow: GarminEmailLogin) -> None:
    adapter = flow.login("me@example.com", "pw", mfa_provider=lambda: "")
    acts = adapter.list_activities(
        datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, 23, 59, tzinfo=UTC)
    )
    assert [a.activity_id for a in acts] == ["111"]
    run = acts[0]
    assert run.start_time == datetime(2026, 9, 5, 6, 0, tzinfo=UTC)
    assert (run.type_key, run.has_gps, run.account) == ("running", True, "42")
    assert FakeGarmin.instances[0].list_query == ("2026-08-31", "2026-10-01")  # one-day margin


def zip_of(**files: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name.replace("_", "."), data)
    return buffer.getvalue()


def test_unpack_original_variants() -> None:
    single = unpack_original(zip_of(a_fit=FIT_BYTES))
    assert [(f.extension, f.is_substitute) for f in single] == [(".fit", False)]
    multi = unpack_original(zip_of(a_fit=FIT_BYTES, b_fit=FIT_BYTES))
    assert len(multi) == 2
    uploaded_gpx = unpack_original(zip_of(track_gpx=b"<gpx/>"))
    assert [(f.extension, f.is_substitute) for f in uploaded_gpx] == [(".gpx", True)]
    assert unpack_original(FIT_BYTES)[0].extension == ".fit"  # not zipped


def test_download_tcx_without_data_is_no_file(flow: GarminEmailLogin) -> None:
    adapter = flow.login("me@example.com", "pw", mfa_provider=lambda: "")
    act = adapter.list_activities(
        datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)
    )[0]
    FakeGarmin.download_result = b'<?xml version="1.0"?><empty/>'
    with pytest.raises(NoFileError):
        adapter.download(act, ExportFormat.TCX)
    FakeGarmin.download_result = b"<TrainingCenterDatabase>...</TrainingCenterDatabase>"
    assert adapter.download(act, ExportFormat.TCX)[0].extension == ".tcx"


def test_download_errors_are_translated(flow: GarminEmailLogin) -> None:
    adapter = flow.login("me@example.com", "pw", mfa_provider=lambda: "")
    act = adapter.list_activities(
        datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)
    )[0]
    FakeGarmin.download_error = GarminConnectTooManyRequestsError("429")
    with pytest.raises(RateLimitError):
        adapter.download(act, ExportFormat.FIT)
    FakeGarmin.download_error = GarminConnectNotFoundError("404")
    with pytest.raises(NoFileError):
        adapter.download(act, ExportFormat.FIT)


def test_default_factory_ignores_plaintext_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from fitbatch.adapters import garmin_email

    monkeypatch.setenv("GARMINTOKENS", "~/.garminconnect")
    client = garmin_email._default_factory()
    assert "GARMINTOKENS" not in __import__("os").environ
    assert client.__class__.__name__ == "Garmin"


class CategoryGarmin(FakeGarmin):
    calls: list = []

    def get_activities_by_date(self, start: str, end: str, activity_type: Any) -> list[dict]:
        CategoryGarmin.calls.append(activity_type)
        if activity_type == "refused":
            from garminconnect import GarminConnectConnectionError

            raise GarminConnectConnectionError("client error: sub type")
        if activity_type is None:
            return RAW_ACTIVITIES
        return [r for r in RAW_ACTIVITIES if r["activityType"]["typeKey"] == activity_type]

    def get_activity_types(self) -> list[dict]:
        return [{"typeId": 17, "typeKey": "all", "parentTypeId": None}]


def test_list_by_categories_asks_garmin_per_category(secrets) -> None:
    CategoryGarmin.calls = []
    adapter = GarminEmailLogin(secrets, client_factory=CategoryGarmin).login(
        "a@b.c", "pw", lambda: ""
    )
    start, end = datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)
    acts = adapter.list_activities(start, end, categories=["running"])
    assert [a.activity_id for a in acts] == ["111"]
    assert CategoryGarmin.calls == ["running"]


def test_refused_category_falls_back_to_full_list(secrets) -> None:
    CategoryGarmin.calls = []
    adapter = GarminEmailLogin(secrets, client_factory=CategoryGarmin).login(
        "a@b.c", "pw", lambda: ""
    )
    start, end = datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)
    acts = adapter.list_activities(start, end, categories=["refused"])
    assert len(acts) == 2
    assert CategoryGarmin.calls == ["refused", None]


def test_activity_types_are_returned(secrets) -> None:
    adapter = GarminEmailLogin(secrets, client_factory=CategoryGarmin).login(
        "a@b.c", "pw", lambda: ""
    )
    assert adapter.activity_types()[0]["typeKey"] == "all"
