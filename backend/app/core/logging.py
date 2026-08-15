from __future__ import annotations

import logging
import re


class SecretRedactingFilter(logging.Filter):
    """Keep provider credentials and signed asset URLs out of local logs."""
    _patterns = (
        (re.compile(r"(Bearer\s+)[^\s]+", re.I), r"\1***"),
        (re.compile(r"((?:api[_-]?key|authorization|secret)[=:\s]+)[^\s,]+", re.I), r"\1***"),
        (re.compile(r"([?&](?:X-Tos-Signature|X-Tos-Credential|token)=)[^&\s]+", re.I), r"\1***"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern, replacement in self._patterns:
            message = pattern.sub(replacement, message)
        record.msg, record.args = message, ()
        return True


def configure_logging() -> None:
    root = logging.getLogger()
    if not any(isinstance(item, SecretRedactingFilter) for item in root.filters):
        root.addFilter(SecretRedactingFilter())
