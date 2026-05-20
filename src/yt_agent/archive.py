"""Archive helpers for duplicate download prevention."""

from __future__ import annotations

from pathlib import Path

from yt_agent.models import VideoInfo
from yt_agent.security import ensure_private_file

__all__ = [
    "ensure_archive_file",
    "load_archive_entries",
    "is_archived",
]



def ensure_archive_file(path: Path) -> None:
    ensure_private_file(path)


def load_archive_entries(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def is_archived(entries: set[str], info: VideoInfo) -> bool:
    return info.archive_key in entries
