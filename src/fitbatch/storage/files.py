"""Safe file writing (N3) and Windows-safe file names (F10)."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}  # fmt: skip


def atomic_write(path: Path, data: bytes) -> None:
    """Write to a temporary file in the same folder, then rename: no half-written files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def safe_filename(name: str, replacement: str = "") -> str:
    """Remove characters Windows does not allow in file names and avoid reserved names."""
    cleaned = _INVALID_CHARS.sub(replacement, name).strip().rstrip(". ")
    if not cleaned:
        return "_"
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned
