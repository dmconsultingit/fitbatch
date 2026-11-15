"""Encrypted storage of API keys and session tokens (N2, F2, F3). Passwords are never stored.

On Windows secrets are encrypted with DPAPI, bound to the current Windows user on this
computer (D11): copied to another computer they cannot be decrypted and the user logs in again.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Protocol

from fitbatch.storage.files import atomic_write

_KEY_PART = re.compile(r"^[a-z0-9_-]{1,40}$")
_ENTROPY = b"FitBatch secret store v1"


class SecretStoreError(Exception):
    """A secret could not be stored or read. The message never contains the secret."""


class SecretStore(Protocol):
    """One secret per platform and access method, e.g. (``garmin``, ``email``)."""

    def save(self, platform: str, method: str, secret: bytes) -> None: ...

    def load(self, platform: str, method: str) -> bytes | None: ...

    def delete(self, platform: str, method: str) -> None: ...


def _check(platform: str, method: str) -> None:
    if not (_KEY_PART.match(platform) and _KEY_PART.match(method)):
        raise SecretStoreError("Invalid platform or method identifier")


class InMemorySecretStore:
    """Non-persistent store for tests."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], bytes] = {}

    def save(self, platform: str, method: str, secret: bytes) -> None:
        _check(platform, method)
        self._data[(platform, method)] = secret

    def load(self, platform: str, method: str) -> bytes | None:
        _check(platform, method)
        return self._data.get((platform, method))

    def delete(self, platform: str, method: str) -> None:
        _check(platform, method)
        self._data.pop((platform, method), None)


class DpapiSecretStore:
    """Files in the secrets folder, each encrypted with Windows DPAPI for the current user."""

    def __init__(self, directory: Path) -> None:
        if sys.platform != "win32":
            raise SecretStoreError("DPAPI is only available on Windows")
        self._dir = directory

    def _path(self, platform: str, method: str) -> Path:
        _check(platform, method)
        return self._dir / f"{platform}_{method}.bin"

    def save(self, platform: str, method: str, secret: bytes) -> None:
        atomic_write(self._path(platform, method), _dpapi_protect(secret))

    def load(self, platform: str, method: str) -> bytes | None:
        path = self._path(platform, method)
        try:
            blob = path.read_bytes()
        except FileNotFoundError:
            return None
        return _dpapi_unprotect(blob)

    def delete(self, platform: str, method: str) -> None:
        self._path(platform, method).unlink(missing_ok=True)


def default_secret_store(directory: Path) -> SecretStore:
    if sys.platform == "win32":
        return DpapiSecretStore(directory)
    raise SecretStoreError("Secure storage is not implemented for this operating system yet")


# --- Windows DPAPI via ctypes (no extra dependency) -------------------------------------------

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _blob_args = [
        ctypes.POINTER(_DataBlob),  # data in
        wintypes.LPCWSTR,  # description
        ctypes.POINTER(_DataBlob),  # optional entropy
        ctypes.c_void_p,  # reserved
        ctypes.c_void_p,  # prompt struct
        wintypes.DWORD,  # flags
        ctypes.POINTER(_DataBlob),  # data out
    ]
    _crypt32.CryptProtectData.argtypes = _blob_args
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        *_blob_args[2:],
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    def _take(out: _DataBlob) -> bytes:
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def _dpapi_protect(data: bytes) -> bytes:
        data_in, _keep1 = _blob(data)
        entropy, _keep2 = _blob(_ENTROPY)
        out = _DataBlob()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(data_in), "FitBatch", ctypes.byref(entropy),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
        )  # fmt: skip
        if not ok:
            error = ctypes.get_last_error()
            raise SecretStoreError(f"Could not encrypt secret (Windows error {error})")
        return _take(out)

    def _dpapi_unprotect(blob: bytes) -> bytes:
        data_in, _keep1 = _blob(blob)
        entropy, _keep2 = _blob(_ENTROPY)
        out = _DataBlob()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(data_in), None, ctypes.byref(entropy),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
        )  # fmt: skip
        if not ok:
            raise SecretStoreError(
                "Could not decrypt saved credentials (saved by another user or computer?)"
            )
        return _take(out)

else:

    def _dpapi_protect(data: bytes) -> bytes:
        raise SecretStoreError("DPAPI is only available on Windows")

    def _dpapi_unprotect(blob: bytes) -> bytes:
        raise SecretStoreError("DPAPI is only available on Windows")
