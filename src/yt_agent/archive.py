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
    expected_extractor = info.extractor_key.casefold()
    for entry in entries:
        extractor, separator, video_id = entry.partition(" ")
        if separator and extractor.casefold() == expected_extractor and video_id == info.video_id:
            return True
    return False
