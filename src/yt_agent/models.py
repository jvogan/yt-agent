"""Typed data models used across yt-agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yt_agent.errors import InvalidInputError


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


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
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


def format_seconds(value: float | int | None) -> str:
    if value is None:
        return "--:--"
    total = max(0, int(value))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True)
class VideoInfo:
    """Normalized metadata for a downloadable or indexed video."""

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
        return format_seconds(self.duration_seconds)


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
    info_json_path: str | None = None

    @classmethod
    def from_download(
        cls,
        target: DownloadTarget,
        *,
        output_path: Path,
        downloaded_at: datetime | None = None,
        info_json_path: Path | None = None,
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
            info_json_path=str(info_json_path) if info_json_path else None,
        )

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "ManifestRecord":
        return cls(
            video_id=str(values.get("video_id") or ""),
            title=str(values.get("title") or "Untitled"),
            channel=str(values.get("channel") or "Unknown Channel"),
            upload_date=_format_upload_date(values.get("upload_date")),
            duration_seconds=_coerce_duration(values.get("duration_seconds")),
            extractor_key=str(values.get("extractor_key") or "youtube"),
            webpage_url=str(values.get("webpage_url") or ""),
            output_path=str(values.get("output_path") or ""),
            requested_input=str(values.get("requested_input") or ""),
            source_query=values.get("source_query") if isinstance(values.get("source_query"), str) else None,
            downloaded_at=str(values.get("downloaded_at") or ""),
            info_json_path=str(values.get("info_json_path")) if values.get("info_json_path") else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ChapterEntry:
    """A chapter extracted from yt-dlp metadata."""

    position: int
    title: str
    start_seconds: float
    end_seconds: float | None

    @property
    def display_range(self) -> str:
        return f"{format_seconds(self.start_seconds)} - {format_seconds(self.end_seconds)}"


@dataclass(frozen=True)
class SubtitleTrack:
    """A locally indexed subtitle asset."""

    lang: str
    source: str
    is_auto: bool
    format: str
    file_path: Path


@dataclass(frozen=True)
class TranscriptSegment:
    """A timed subtitle or transcript segment."""

    segment_index: int
    start_seconds: float
    end_seconds: float
    text: str

    @property
    def display_range(self) -> str:
        return f"{format_seconds(self.start_seconds)} - {format_seconds(self.end_seconds)}"


@dataclass(frozen=True)
class CatalogVideo:
    """A catalog entry enriched with indexing counts."""

    video_id: str
    title: str
    channel: str
    upload_date: str | None
    duration_seconds: int | None
    extractor_key: str
    webpage_url: str
    requested_input: str | None
    source_query: str | None
    output_path: Path | None
    info_json_path: Path | None
    downloaded_at: str | None
    chapter_count: int
    transcript_segment_count: int
    playlist_count: int

    @property
    def display_duration(self) -> str:
        return format_seconds(self.duration_seconds)

    @property
    def has_local_media(self) -> bool:
        return self.output_path is not None and self.output_path.exists()

    @property
    def file_path(self) -> Path | None:
        return self.output_path

    @property
    def transcript_count(self) -> int:
        return self.transcript_segment_count


@dataclass(frozen=True)
class ClipSearchHit:
    """A searchable clip span sourced from chapters or transcripts."""

    result_id: str
    source: str
    video_id: str
    title: str
    channel: str
    webpage_url: str
    start_seconds: float
    end_seconds: float
    score: float
    match_text: str
    context: str
    output_path: Path | None = None

    @property
    def display_range(self) -> str:
        return f"{format_seconds(self.start_seconds)} - {format_seconds(self.end_seconds)}"


@dataclass(frozen=True)
class PlaylistEntryRecord:
    """A playlist edge stored in the catalog."""

    playlist_id: str
    video_id: str
    position: int


def chapter_from_payload(index: int, payload: dict[str, Any]) -> ChapterEntry | None:
    start_seconds = _coerce_float(payload.get("start_time"))
    if start_seconds is None:
        return None
    end_seconds = _coerce_float(payload.get("end_time"))
    title = str(payload.get("title") or f"Chapter {index + 1}").strip() or f"Chapter {index + 1}"
    return ChapterEntry(position=index, title=title, start_seconds=start_seconds, end_seconds=end_seconds)
