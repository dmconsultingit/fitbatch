import gzip
import threading
from pathlib import Path

import pytest

from fitbatch.adapters.base import (
    AdapterError,
    AuthenticationError,
    DuplicateError,
    InvalidFileError,
    RateLimitError,
)
from fitbatch.adapters.fake import FakeAdapter
from fitbatch.core.export import Waiting
from fitbatch.core.models import Capability
from fitbatch.core.upload import (
    Uploaded,
    Uploader,
    UploadFailed,
    UploadRegistry,
    UploadSkipped,
    check_file,
    scan_upload_paths,
)

FIT = bytes([14, 16, 0, 0, 0, 0, 0, 0]) + b".FIT" + b"\x00\x00" + b"activity-data"
GPX = b'<?xml version="1.0"?><gpx version="1.1"><trk/></gpx>'
TCX = b'<?xml version="1.0"?><TrainingCenterDatabase><Activities/></TrainingCenterDatabase>'


def write(folder: Path, name: str, data: bytes) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if name.endswith(".gz"):
        path.write_bytes(gzip.compress(data))
    else:
        path.write_bytes(data)
    return path


def uploader_for(
    tmp_path: Path, **adapter_kw
) -> tuple[Uploader, FakeAdapter, list, UploadRegistry]:
    adapter = FakeAdapter(
        capabilities=(Capability.LIST_ACTIVITIES, Capability.DOWNLOAD, Capability.UPLOAD),
        **adapter_kw,
    )
    registry = UploadRegistry(tmp_path / "registry.sqlite3")
    events: list = []
    sleeps: list[float] = []
    uploader = Uploader(adapter, registry, on_event=events.append, sleep=sleeps.append)
    return uploader, adapter, events, registry


# --- checking files ------------------------------------------------------------------------------


def test_check_recognises_formats_and_gzip(tmp_path: Path) -> None:
    fit = check_file(write(tmp_path, "ride.fit", FIT))
    gpx = check_file(write(tmp_path, "Run.GPX", GPX))
    tcx_gz = check_file(write(tmp_path, "swim.tcx.gz", TCX))
    assert (fit.fmt, fit.compressed, fit.problem) == ("fit", False, None)
    assert (gpx.fmt, gpx.problem) == ("gpx", None)
    assert (tcx_gz.fmt, tcx_gz.compressed, tcx_gz.problem) == ("tcx", True, None)
    assert tcx_gz.upload_name == "swim.tcx"
    assert len(fit.sha256) == 64


def test_check_rejects_wrong_content_and_types(tmp_path: Path) -> None:
    assert check_file(write(tmp_path, "fake.fit", b"not a fit file at all")).problem == (
        "wrong_content"
    )
    assert check_file(write(tmp_path, "notes.txt", b"hello")).problem == "unsupported"
    assert check_file(write(tmp_path, "empty.gpx", b"")).problem == "empty"
    broken = tmp_path / "broken.fit.gz"
    broken.write_bytes(b"not gzip")
    assert check_file(broken).problem == "unreadable"


def test_same_content_has_same_hash_compressed_or_not(tmp_path: Path) -> None:
    plain = check_file(write(tmp_path, "a.fit", FIT))
    packed = check_file(write(tmp_path, "b.fit.gz", FIT))
    assert plain.sha256 == packed.sha256


def test_scan_folder_takes_only_activity_files(tmp_path: Path) -> None:
    write(tmp_path, "export/activities/1.fit.gz", FIT)
    write(tmp_path, "export/activities/2.gpx", GPX)
    write(tmp_path, "export/profile.csv", b"a,b")
    write(tmp_path, "export/media/photo.jpg", b"jpg")
    files = scan_upload_paths([tmp_path / "export"])
    assert sorted(f.name for f in files) == ["1.fit.gz", "2.gpx"]


def test_scan_does_not_repeat_a_file_chosen_twice(tmp_path: Path) -> None:
    path = write(tmp_path, "ride.fit", FIT)
    assert len(scan_upload_paths([path, tmp_path, path])) == 1


# --- uploading -----------------------------------------------------------------------------------


def test_uploads_new_files_and_skips_them_next_time(tmp_path: Path) -> None:
    files = scan_upload_paths(
        [write(tmp_path, "ride.fit", FIT), write(tmp_path, "run.gpx.gz", GPX)]
    )
    uploader, adapter, events, registry = uploader_for(tmp_path)
    summary = uploader.run(files)
    assert summary.uploaded == 2
    assert adapter.uploads == [("ride.fit", FIT), ("run.gpx", GPX)]  # .gz unpacked before sending
    assert sum(isinstance(e, Uploaded) for e in events) == 2

    again = uploader.run(files)
    assert again.uploaded == 0
    assert again.skipped_by_reason == {"uploaded_before": 2}
    assert uploader.pending(files) == []
    registry.close()


def test_problem_files_and_copies_are_skipped_without_a_request(tmp_path: Path) -> None:
    files = scan_upload_paths(
        [
            write(tmp_path, "a.fit", FIT),
            write(tmp_path, "copy-of-a.fit.gz", FIT),
            write(tmp_path, "renamed.tcx", b"plain text"),
        ]
    )
    uploader, adapter, _events, registry = uploader_for(tmp_path)
    summary = uploader.run(files)
    assert len(adapter.uploads) == 1
    assert summary.skipped_by_reason == {"same_file": 1, "wrong_content": 1}
    registry.close()


def test_duplicate_is_remembered_and_not_sent_again(tmp_path: Path) -> None:
    def duplicate(_name: str, _data: bytes) -> None:
        raise DuplicateError("Garmin already has this activity")

    files = scan_upload_paths([write(tmp_path, "ride.fit", FIT)])
    uploader, _adapter, events, registry = uploader_for(tmp_path, upload_fail_with=duplicate)
    summary = uploader.run(files)
    assert (summary.duplicates, summary.uploaded, summary.failed) == (1, 0, 0)
    assert any(isinstance(e, UploadSkipped) and e.reason == "duplicate" for e in events)
    again = uploader.run(files)  # known duplicate: counted the same way, no new request
    assert (again.duplicates, again.skipped) == (1, 0)
    assert len(_adapter.uploads) == 0
    registry.close()


def test_rate_limit_waits_twice_then_pauses(tmp_path: Path) -> None:
    def limited(_name: str, _data: bytes) -> None:
        raise RateLimitError("429")

    files = scan_upload_paths([write(tmp_path, "a.fit", FIT), write(tmp_path, "b.gpx", GPX)])
    uploader, _adapter, events, registry = uploader_for(tmp_path, upload_fail_with=limited)
    summary = uploader.run(files)
    assert summary.paused_reason == "rate_limit"
    assert [e.seconds for e in events if isinstance(e, Waiting)] == [30.0, 60.0]
    assert summary.uploaded == 0
    assert uploader.pending(files) == files  # nothing recorded: resuming sends both again
    registry.close()


def test_expired_session_pauses_the_job(tmp_path: Path) -> None:
    def expired(_name: str, _data: bytes) -> None:
        raise AuthenticationError("session expired")

    files = scan_upload_paths([write(tmp_path, "a.fit", FIT), write(tmp_path, "b.gpx", GPX)])
    uploader, _adapter, _events, registry = uploader_for(tmp_path, upload_fail_with=expired)
    summary = uploader.run(files)
    assert summary.paused_reason == "authentication"
    assert summary.failed == 1  # stopped after the first file
    registry.close()


def test_rejected_file_fails_and_the_job_goes_on(tmp_path: Path) -> None:
    def picky(name: str, _data: bytes) -> None:
        if name == "a.fit":
            raise InvalidFileError("Garmin could not read this file")

    files = scan_upload_paths([write(tmp_path, "a.fit", FIT), write(tmp_path, "b.gpx", GPX)])
    uploader, adapter, events, registry = uploader_for(tmp_path, upload_fail_with=picky)
    summary = uploader.run(files)
    assert (summary.failed, summary.uploaded) == (1, 1)
    assert [n for n, _ in adapter.uploads] == ["b.gpx"]
    assert any(isinstance(e, UploadFailed) for e in events)
    assert len(uploader.pending(files)) == 1  # the failed file can be tried again
    registry.close()


def test_cancel_stops_before_the_next_file(tmp_path: Path) -> None:
    files = scan_upload_paths([write(tmp_path, "a.fit", FIT), write(tmp_path, "b.gpx", GPX)])
    cancel = threading.Event()

    def stop_after_first(_name: str, _data: bytes) -> None:
        cancel.set()

    uploader, adapter, _events, registry = uploader_for(tmp_path, upload_fail_with=stop_after_first)
    summary = uploader.run(files, cancel)
    assert summary.cancelled
    assert len(adapter.uploads) == 1
    registry.close()


def test_registry_is_per_account(tmp_path: Path) -> None:
    files = scan_upload_paths([write(tmp_path, "a.fit", FIT)])
    first, _a, _e, registry = uploader_for(tmp_path)
    first.run(files)
    registry.close()
    other = FakeAdapter(
        capabilities=(Capability.UPLOAD,),
        account="someone-else",
    )
    with UploadRegistry(tmp_path / "registry.sqlite3") as registry2:
        assert Uploader(other, registry2, sleep=lambda _s: None).run(files).uploaded == 1


# --- Garmin error mapping ------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _library_error(status: int, message: str = "API Error") -> Exception:
    from garminconnect import GarminConnectConnectionError

    inner = GarminConnectConnectionError(f"{message} {status}")
    inner.response = _Response(status)
    try:
        try:
            raise inner
        except GarminConnectConnectionError as e:
            raise GarminConnectConnectionError("Import error") from e
    except GarminConnectConnectionError as outer:
        return outer


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (409, DuplicateError),
        (429, RateLimitError),
        (401, AuthenticationError),
        (400, InvalidFileError),
        (500, AdapterError),
    ],
)
def test_garmin_upload_errors_are_mapped(status: int, expected: type) -> None:
    from fitbatch.adapters.garmin_email import _upload_error

    error = _upload_error(_library_error(status))
    assert type(error) is expected


class _Client:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[tuple[str, bytes]] = []
        self.client = self

    def dumps(self) -> str:
        return "{}"

    def import_activity(self, path: str) -> dict:
        p = Path(path)
        self.sent.append((p.name, p.read_bytes()))
        if self.error:
            raise self.error
        return {"status": "uploaded"}


def test_garmin_adapter_uploads_through_a_temporary_file() -> None:
    from fitbatch.adapters.garmin_email import GarminEmailAdapter

    client = _Client()
    adapter = GarminEmailAdapter(client, on_session_change=lambda _t: None)
    adapter.upload("2019 ride: <morning>.fit", FIT)
    assert client.sent == [("2019 ride morning.fit", FIT)]
    assert Capability.UPLOAD in adapter.capabilities


def test_garmin_adapter_reports_duplicate() -> None:
    from fitbatch.adapters.garmin_email import GarminEmailAdapter

    adapter = GarminEmailAdapter(_Client(_library_error(409)), on_session_change=lambda _t: None)
    with pytest.raises(DuplicateError):
        adapter.upload("ride.fit", FIT)


def test_garmin_adapter_refuses_other_file_types() -> None:
    from fitbatch.adapters.garmin_email import GarminEmailAdapter

    adapter = GarminEmailAdapter(_Client(), on_session_change=lambda _t: None)
    with pytest.raises(InvalidFileError):
        adapter.upload("notes.txt", b"hello")
