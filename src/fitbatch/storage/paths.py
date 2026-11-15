"""Where FitBatch keeps its data: the user's profile (D13), e.g. ``%APPDATA%\\FitBatch``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "FitBatch"
DATA_DIR_ENV = "FITBATCH_DATA_DIR"  # override for development and tests


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def registry_file(self) -> Path:
        return self.root / "registry.sqlite3"

    @property
    def secrets_dir(self) -> Path:
        return self.root / "secrets"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def ensure(self) -> AppPaths:
        for directory in (self.root, self.secrets_dir, self.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def default_paths() -> AppPaths:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return AppPaths(Path(override))
    return AppPaths(Path(user_data_dir(APP_NAME, appauthor=False, roaming=True)))
