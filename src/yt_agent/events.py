"""Stable JSONL lifecycle events for long-running operations."""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yt_agent.security import ensure_private_file, sanitize_json_payload

__all__ = ["JsonlEventWriter"]


class JsonlEventWriter:
    """Append versioned, sequence-numbered events to an explicit private file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.sequence = 0
        ensure_private_file(path)

    def emit(self, event: str, **fields: Any) -> None:
        self.sequence += 1
        payload = {
            "schema_version": 1,
            "event": event,
            "sequence": self.sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            **fields,
        }
        flags = os.O_WRONLY | os.O_APPEND
        if os.name != "nt":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.path, flags)
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            os.close(fd)
            raise OSError(f"Refusing unsafe lifecycle event file: {self.path}")
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(sanitize_json_payload(payload), sort_keys=True))
            handle.write("\n")
