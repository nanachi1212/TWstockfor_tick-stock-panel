"""Credential redaction shared by backend and desktop log handlers."""
from __future__ import annotations

import logging
import re

_SECRET_PATTERNS = (
    r"(?i)(api[_-]?key|token|password|secret|webhook)(\s*[=:]\s*)[^\s,;]+",
    r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
    r"(?i)(x-api-key\s*:\s*)[^\s]+",
    r"(?i)(api\.telegram\.org/bot)[^/\s?]+",
)


def sanitize_log(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = re.sub(pattern, lambda match: f"{match.group(1)}[已隱藏]", text)
    return text


class SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return sanitize_log(super().format(record))
