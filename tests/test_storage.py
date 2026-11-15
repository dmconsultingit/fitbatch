import sys
from pathlib import Path

import pytest

from fitbatch.core.settings import Settings
from fitbatch.storage.files import atomic_write, safe_filename
from fitbatch.storage.paths import default_paths
from fitbatch.storage.secrets import (
    DpapiSecretStore,
    InMemorySecretStore,
    SecretStoreError,
)


def test_default_paths_use_override(isolated_data_dir: Path) -> None:
    paths = default_paths().ensure()
    assert paths.root == isolated_data_dir
    assert paths.secrets_dir.is_dir()
    assert paths.logs_dir.is_dir()


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "file.bin"
    atomic_write(target, b"one")
    atomic_write(target, b"two")
    assert target.read_bytes() == b"two"
    assert [p.name for p in target.parent.iterdir()] == ["file.bin"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("2026-09-05_cycling_Zawody: Enduro?.fit", "2026-09-05_cycling_Zawody Enduro.fit"),
        ('a<b>c"d/e\\f|g*h', "abcdefgh"),
        ("CON.fit", "_CON.fit"),
        ("name. ", "name"),
        ("???", "_"),
        ("Łódź – trening", "Łódź – trening"),
    ],
)
def test_safe_filename(name: str, expected: str) -> None:
    assert safe_filename(name) == expected


def test_settings_roundtrip_and_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert Settings.load(path).formats == ["fit"]
    settings = Settings(download_dir=str(tmp_path), formats=["fit", "gpx"], language="en")
    settings.save(path)
    assert Settings.load(path) == settings


def test_settings_ignore_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"language": "xx", "formats": ["exe"], "unknown": 1}', encoding="utf-8")
    loaded = Settings.load(path)
    assert loaded.language == "pl"
    assert loaded.formats == ["fit"]


def test_in_memory_secret_store() -> None:
    store = InMemorySecretStore()
    store.save("garmin", "email", b"token")
    assert store.load("garmin", "email") == b"token"
    store.delete("garmin", "email")
    assert store.load("garmin", "email") is None


def test_secret_store_rejects_path_tricks() -> None:
    with pytest.raises(SecretStoreError):
        InMemorySecretStore().save("../evil", "email", b"x")


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_roundtrip_is_encrypted_on_disk(tmp_path: Path) -> None:
    store = DpapiSecretStore(tmp_path)
    secret = b"session-token-1234567890"
    store.save("garmin", "email", secret)
    on_disk = (tmp_path / "garmin_email.bin").read_bytes()
    assert secret not in on_disk
    assert store.load("garmin", "email") == secret
    store.delete("garmin", "email")
    assert store.load("garmin", "email") is None


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_dpapi_rejects_tampered_file(tmp_path: Path) -> None:
    store = DpapiSecretStore(tmp_path)
    store.save("garmin", "email", b"token")
    path = tmp_path / "garmin_email.bin"
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))
    with pytest.raises(SecretStoreError):
        store.load("garmin", "email")
