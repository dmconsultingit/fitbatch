"""Limit on password login attempts, kept across restarts (N2: protect the account from lockout)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fitbatch.storage.files import atomic_write

MAX_FAILURES = 3
FAILURE_LOCK_S = 5 * 60
RATE_LIMIT_LOCK_S = 15 * 60


class LoginLockedError(Exception):
    def __init__(self, remaining_s: float) -> None:
        super().__init__(f"Login paused for {int(remaining_s) + 1} s to protect the account")
        self.remaining_s = remaining_s


@dataclass(slots=True)
class _State:
    failures: int = 0
    locked_until: float = 0.0


class LoginGuard:
    """After 3 failed logins in a row, or a 429 from Garmin, block further attempts for a while."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._state = self._load()

    def _load(self) -> _State:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return _State(int(raw.get("failures", 0)), float(raw.get("locked_until", 0.0)))
        except (FileNotFoundError, ValueError, TypeError, AttributeError):
            return _State()

    def _save(self) -> None:
        data = {"failures": self._state.failures, "locked_until": self._state.locked_until}
        atomic_write(self._path, json.dumps(data).encode("utf-8"))

    def check(self) -> None:
        remaining = self._state.locked_until - self._clock()
        if remaining > 0:
            raise LoginLockedError(remaining)

    def record_success(self) -> None:
        self._state = _State()
        self._save()

    def record_failure(self) -> None:
        self._state.failures += 1
        if self._state.failures >= MAX_FAILURES:
            self._state.locked_until = self._clock() + FAILURE_LOCK_S
            self._state.failures = 0
        self._save()

    def record_rate_limit(self) -> None:
        self._state.locked_until = self._clock() + RATE_LIMIT_LOCK_S
        self._save()
