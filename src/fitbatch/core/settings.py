"""User settings stored as JSON in the user's data folder (F21, D13)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from platformdirs import user_documents_dir

from fitbatch.core.models import ExportFormat
from fitbatch.storage.files import atomic_write

SUPPORTED_LANGUAGES = ("pl", "en")


def _default_download_dir() -> str:
    return str(Path(user_documents_dir()) / "FitBatch")


@dataclass(slots=True)
class Settings:
    download_dir: str = field(default_factory=_default_download_dir)
    formats: list[str] = field(default_factory=lambda: [ExportFormat.FIT.value])
    language: str = "pl"

    @classmethod
    def load(cls, path: Path) -> Settings:
        """Read settings; a missing or unreadable file falls back to defaults."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return cls()
        defaults = cls()
        known = {k: raw[k] for k in asdict(defaults) if k in raw}
        settings = cls(**known)
        if settings.language not in SUPPORTED_LANGUAGES:
            settings.language = defaults.language
        valid_formats = {f.value for f in ExportFormat}
        settings.formats = [f for f in settings.formats if f in valid_formats] or defaults.formats
        return settings

    def save(self, path: Path) -> None:
        data = json.dumps(asdict(self), indent=2, ensure_ascii=False)
        atomic_write(path, data.encode("utf-8"))
