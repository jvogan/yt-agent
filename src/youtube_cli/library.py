"""Helpers for deterministic download paths."""

from __future__ import annotations

import re
from pathlib import Path

from youtube_cli.models import VideoInfo

INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MULTISPACE = re.compile(r"\s+")


def sanitize_component(value: str | None, fallback: str) -> str:
    """Normalize a path component for cross-platform filesystem use."""

    candidate = value or ""
    cleaned = INVALID_PATH_CHARS.sub(" ", candidate)
    cleaned = MULTISPACE.sub(" ", cleaned).strip(" .")
    cleaned = cleaned or fallback
    return cleaned[:180]


def normalized_upload_date(value: str | None) -> str:
    return value or "undated"


def build_output_template(download_root: Path, info: VideoInfo) -> Path:
    """Build the yt-dlp output template for a single video."""

    channel = sanitize_component(info.channel, "Unknown Channel")
    title = sanitize_component(info.title, "Untitled")
    upload_date = normalized_upload_date(info.upload_date)
    filename = f"{upload_date} - {title} [{info.video_id}].%(ext)s"
    return download_root / channel / filename
