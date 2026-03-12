"""Helpers for deterministic media, sidecar, and clip paths."""

from __future__ import annotations

import re
from pathlib import Path

from yt_agent.models import VideoInfo, format_seconds

INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
MULTISPACE = re.compile(r"\s+")
INVALID_ID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
INVALID_EXT_CHARS = re.compile(r"[^A-Za-z0-9]+")


def sanitize_component(value: str | None, fallback: str) -> str:
    """Normalize a path component for cross-platform filesystem use."""

    candidate = value or ""
    cleaned = INVALID_PATH_CHARS.sub(" ", candidate)
    cleaned = MULTISPACE.sub(" ", cleaned).strip(" .")
    cleaned = cleaned or fallback
    return cleaned[:180]


def normalized_upload_date(value: str | None) -> str:
    return value or "undated"


def sanitize_file_id(value: str | None, fallback: str = "unknown-id") -> str:
    """Normalize an extractor-provided id for safe filesystem use."""

    candidate = value or ""
    cleaned = INVALID_ID_CHARS.sub("_", candidate).strip("._-")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned[:64] or fallback


def sanitize_extension(value: str | None, fallback: str = "mp4") -> str:
    cleaned = INVALID_EXT_CHARS.sub("", (value or "").casefold())
    return cleaned[:12] or fallback


def build_output_template(download_root: Path, info: VideoInfo) -> Path:
    """Build the yt-dlp output template for a single video."""

    channel = sanitize_component(info.channel, "Unknown Channel")
    title = sanitize_component(info.title, "Untitled")
    upload_date = normalized_upload_date(info.upload_date)
    file_id = sanitize_file_id(info.video_id)
    filename = f"{upload_date} - {title} [{file_id}].%(ext)s"
    return download_root / channel / filename


def build_clip_output_path(
    clip_root: Path,
    info: VideoInfo,
    *,
    label: str,
    start_seconds: float,
    end_seconds: float,
    extension: str = "mp4",
) -> Path:
    """Build a deterministic local path for an extracted clip."""

    channel = sanitize_component(info.channel, "Unknown Channel")
    title = sanitize_component(info.title, "Untitled")
    safe_label = sanitize_component(label, "clip")
    file_id = sanitize_file_id(info.video_id)
    timerange = f"{format_seconds(start_seconds).replace(':', '-')}_{format_seconds(end_seconds).replace(':', '-')}"
    safe_extension = sanitize_extension(extension)
    filename = f"{title} [{file_id}] {timerange} {safe_label}.{safe_extension}"
    return clip_root / channel / filename


def info_json_path_for_media(media_path: Path) -> Path:
    """Return the yt-dlp sidecar path for a downloaded media file."""

    return Path(f"{media_path}.info.json")


def alternate_info_json_path_for_media(media_path: Path) -> Path:
    return media_path.with_suffix(".info.json")


def discover_info_json(media_path: Path) -> Path | None:
    for candidate in (info_json_path_for_media(media_path), alternate_info_json_path_for_media(media_path)):
        if candidate.exists():
            return candidate
    return None


def discover_subtitle_files(media_path: Path) -> list[Path]:
    parent = media_path.parent
    prefixes = (f"{media_path.name}.", f"{media_path.stem}.")
    matches: list[Path] = []
    for candidate in sorted(parent.iterdir()):
        if not candidate.is_file():
            continue
        if candidate.suffix.casefold() not in {".vtt", ".srt"}:
            continue
        if candidate.suffix == media_path.suffix:
            continue
        if any(candidate.name.startswith(prefix) for prefix in prefixes):
            matches.append(candidate)
    return matches
