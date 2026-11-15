from pathlib import Path

import pytest

from fitbatch.core.login_guard import (
    FAILURE_LOCK_S,
    RATE_LIMIT_LOCK_S,
    LoginGuard,
    LoginLockedError,
)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_locks_after_three_failures_and_survives_restart(tmp_path: Path) -> None:
    clock, path = Clock(), tmp_path / "guard.json"
    guard = LoginGuard(path, clock)
    for _ in range(3):
        guard.check()
        guard.record_failure()
    with pytest.raises(LoginLockedError):
        LoginGuard(path, clock).check()  # a restart does not reset the lock
    clock.now += FAILURE_LOCK_S + 1
    LoginGuard(path, clock).check()


def test_success_resets_failures(tmp_path: Path) -> None:
    guard = LoginGuard(tmp_path / "guard.json", Clock())
    guard.record_failure()
    guard.record_failure()
    guard.record_success()
    guard.record_failure()
    guard.check()


def test_rate_limit_locks_longer(tmp_path: Path) -> None:
    clock = Clock()
    guard = LoginGuard(tmp_path / "guard.json", clock)
    guard.record_rate_limit()
    clock.now += FAILURE_LOCK_S + 1
    with pytest.raises(LoginLockedError):
        guard.check()
    clock.now += RATE_LIMIT_LOCK_S
    guard.check()


def test_corrupt_state_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "guard.json"
    path.write_text("not json", encoding="utf-8")
    LoginGuard(path, Clock()).check()
