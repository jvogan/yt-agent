"""Archive helpers for duplicate download prevention."""

from __future__ import annotations

from pathlib import Path

from yt_agent.models import VideoInfo


def ensure_archive_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def load_archive_entries(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def is_archived(entries: set[str], info: VideoInfo) -> bool:
    return info.archive_key in entries
