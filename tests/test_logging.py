import logging
from pathlib import Path

import pytest

from fitbatch.logging_setup import LOG_FILE_NAME, redact, setup_logging


@pytest.mark.parametrize(
    "text",
    [
        "login password=hunter2 ok",
        '{"password": "hunter2"}',
        "api_key: hunter2",
        "Authorization: Bearer hunter2.abc-def",
        "refresh_token='hunter2'",
    ],
)
def test_redact_hides_secrets(text: str) -> None:
    result = redact(text)
    assert "hunter2" not in result
    assert "***" in result


def test_redact_keeps_normal_text() -> None:
    assert redact("Downloaded 12 / 148: 2026-09-05_cycling.fit") == (
        "Downloaded 12 / 148: 2026-09-05_cycling.fit"
    )


def test_log_file_never_contains_secret(tmp_path: Path) -> None:
    handler = setup_logging(tmp_path)
    log = logging.getLogger("fitbatch.test")
    try:
        log.info("Login with password=%s", "hunter2")
        try:
            raise RuntimeError("token=hunter2 rejected")
        except RuntimeError:
            log.exception("Failed")
    finally:
        handler.close()
        logging.getLogger().removeHandler(handler)
    content = (tmp_path / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "hunter2" not in content
    assert "Failed" in content
