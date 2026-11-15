"""Diagnostic log with rotation and automatic removal of sensitive data (N2, N8)."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "fitbatch.log"
_MASK = "***"

# key=value / key: value / "key": "value" for anything that looks like a credential
_KEY_VALUE = re.compile(
    r"""(?ix)
    (["']?(?:password|passwd|pwd|token|access_token|refresh_token|secret|api[_-]?key|
          authorization|cookie|session|ticket|mfa|otp|2fa)["']?\s*[:=]\s*)
    (["']?)(?:(?:bearer|basic|token)\s+)?[^\s"',;}]+(\2)
    """
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+")


def redact(text: str) -> str:
    """Replace values of credential-like fields with ``***``."""
    text = _KEY_VALUE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_MASK}{m.group(3)}", text)
    return _BEARER.sub(lambda m: f"{m.group(1)} {_MASK}", text)


class RedactingFilter(logging.Filter):
    """Redacts the fully formatted message, including arguments and exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:
            formatter = logging.Formatter()
            record.exc_text = redact(formatter.formatException(record.exc_info))
            record.exc_info = None
        return True


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> logging.Handler:
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        logs_dir / LOG_FILE_NAME, maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    return handler
