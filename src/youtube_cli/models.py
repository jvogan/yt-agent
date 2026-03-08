"""Typed data models used across the CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from youtube_cli.errors import InvalidInputError


def _coerce_duration(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _format_upload_date(value: Any) -> str | None:
    if not value or not isinstance(value, str):
        return None
    if len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _fallback_webpage_url(video_id: str, extractor_key: str) -> str:
    if extractor_key == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}"
    return video_id


def _normalize_webpage_url(candidate: Any, video_id: str, extractor_key: str) -> str:
    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
        return candidate
    return _fallback_webpage_url(video_id, extractor_key)


@dataclass(frozen=True)
class VideoInfo:
    """Normalized metadata for a downloadable video."""

    video_id: str
    title: str
    channel: str
    upload_date: str | None
    duration_seconds: int | None
    extractor_key: str
    webpage_url: str
    original_url: str | None = None

    @classmethod
    def from_yt_dlp(cls, payload: dict[str, Any], *, original_url: str | None = None) -> "VideoInfo":
        video_id = str(payload.get("id") or "").strip()
        if not video_id:
            raise InvalidInputError("yt-dlp metadata did not include a video id.")

        extractor_key = str(payload.get("extractor_key") or payload.get("extractor") or "youtube")
        title = str(payload.get("title") or "Untitled").strip() or "Untitled"
        channel = (
            str(
                payload.get("channel")
                or payload.get("uploader")
                or payload.get("uploader_id")
                or payload.get("creator")
                or "Unknown Channel"
            ).strip()
            or "Unknown Channel"
        )
        webpage_url = _normalize_webpage_url(
            payload.get("webpage_url") or payload.get("original_url"),
            video_id,
            extractor_key,
        )
        return cls(
            video_id=video_id,
            title=title,
            channel=channel,
            upload_date=_format_upload_date(payload.get("upload_date")),
            duration_seconds=_coerce_duration(payload.get("duration")),
            extractor_key=extractor_key,
            webpage_url=webpage_url,
            original_url=original_url,
        )

    @property
    def archive_key(self) -> str:
        return f"{self.extractor_key} {self.video_id}"

    @property
    def display_duration(self) -> str:
        if self.duration_seconds is None:
            return "--:--"
        minutes, seconds = divmod(self.duration_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class DownloadTarget:
    """A single resolved video target."""

    original_input: str
    info: VideoInfo
    source_query: str | None = None


@dataclass(frozen=True)
class ManifestRecord:
    """A persisted download record."""

    video_id: str
    title: str
    channel: str
    upload_date: str | None
    duration_seconds: int | None
    extractor_key: str
    webpage_url: str
    output_path: str
    requested_input: str
    source_query: str | None
    downloaded_at: str

    @classmethod
    def from_download(
        cls,
        target: DownloadTarget,
        *,
        output_path: Path,
        downloaded_at: datetime | None = None,
    ) -> "ManifestRecord":
        ts = downloaded_at or datetime.now(UTC)
        return cls(
            video_id=target.info.video_id,
            title=target.info.title,
            channel=target.info.channel,
            upload_date=target.info.upload_date,
            duration_seconds=target.info.duration_seconds,
            extractor_key=target.info.extractor_key,
            webpage_url=target.info.webpage_url,
            output_path=str(output_path),
            requested_input=target.original_input,
            source_query=target.source_query,
            downloaded_at=ts.isoformat(),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
