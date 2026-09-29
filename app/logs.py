"""Logging (task 6.1; N1.3, N3.4).

The application never logs passwords, SSNs, account numbers, or decrypted values on purpose; tests check
that. As a second line of defence, every log line is passed through RedactFilter, which blanks anything
shaped like an SSN or a bank account number before it is written. In production the logs go to stderr,
where systemd's journal collects them.
"""
from __future__ import annotations

import logging
import re

from flask import Flask

SSN_SHAPE = re.compile(r"(?<![0-9])[0-9]{3}-?[0-9]{2}-?[0-9]{4}(?![0-9])")
ACCOUNT_SHAPE = re.compile(r"(?<![0-9])[0-9]{10,17}(?![0-9])")  # bank account numbers (4-17 digits; long ones)
REDACTED = "[redacted]"


class RedactFilter(logging.Filter):
    """Blanks SSN-shaped and account-number-shaped values in a log record's final message."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = ACCOUNT_SHAPE.sub(REDACTED, SSN_SHAPE.sub(REDACTED, message))
        if cleaned != message:
            record.msg, record.args = cleaned, None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = ACCOUNT_SHAPE.sub(REDACTED, SSN_SHAPE.sub(REDACTED, record.exc_text))
        return True


def configure_logging(app: Flask) -> None:
    """Redact on every handler the app and its services write through. Production also logs at INFO to stderr."""
    root = logging.getLogger()
    if app.config["SETTINGS"].is_production and not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    for handler in root.handlers + app.logger.handlers:
        if not any(isinstance(f, RedactFilter) for f in handler.filters):
            handler.addFilter(RedactFilter())
