from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from apps.core.security import redact_secrets


class SecretSafeJsonFormatter(logging.Formatter):
    """Emit single-line structured logs with credential-like text redacted."""

    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
        }
        if record.exc_info:
            event["exception"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"))
