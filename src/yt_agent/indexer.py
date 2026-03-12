"""Catalog indexing flows for manifests, sidecars, and ad hoc targets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yt_agent import yt_dlp
from yt_agent.catalog import CatalogStore, PlaylistUpsert, VideoUpsert
from yt_agent.chapters import extract_chapters
from yt_agent.config import Settings
from yt_agent.library import discover_info_json, discover_subtitle_files, sanitize_file_id
from yt_agent.manifest import iter_manifest_records
from yt_agent.models import ManifestRecord, SubtitleTrack, TranscriptSegment, VideoInfo
from yt_agent.transcripts import fetch_subtitle_sidecars, infer_subtitle_track, parse_subtitle_file


@dataclass(frozen=True)
class IndexSummary:
    """User-facing summary of indexing work."""

    videos: int = 0
    playlists: int = 0
    chapters: int = 0
    transcript_segments: int = 0

    def merge(self, other: "IndexSummary") -> "IndexSummary":
        return IndexSummary(
            videos=self.videos + other.videos,
            playlists=self.playlists + other.playlists,
            chapters=self.chapters + other.chapters,
            transcript_segments=self.transcript_segments + other.transcript_segments,
        )


def catalog_for_settings(settings: Settings) -> CatalogStore:
    catalog = CatalogStore(settings.catalog_file)
    catalog.ensure_schema()
    return catalog


def _load_info_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return result
    except json.JSONDecodeError:
        return None


def _subtitle_cache_root(settings: Settings, info: VideoInfo) -> Path:
    return settings.catalog_file.parent / "subtitle-cache" / sanitize_file_id(info.video_id)


def _split_languages(settings: Settings, override: str | None = None) -> list[str]:
    raw = override or settings.subtitle_languages
    return [item.strip() for item in raw.split(",") if item.strip()]


def _index_transcripts(
    catalog: CatalogStore,
    info: VideoInfo,
    *,
    media_path: Path | None,
    info_json_path: Path | None,
    settings: Settings,
    fetch_subs: bool,
    auto_subs: bool,
    lang: str | None,
) -> int:
    subtitle_paths = discover_subtitle_files(media_path) if media_path and media_path.exists() else []
    info_payload = _load_info_json(info_json_path)
    if not subtitle_paths and fetch_subs:
        fetched_info_json, fetched_subtitle_paths = fetch_subtitle_sidecars(
            info.webpage_url,
            _subtitle_cache_root(settings, info),
            languages=_split_languages(settings, override=lang),
            allow_auto_subs=auto_subs,
        )
        info_json_path = fetched_info_json or info_json_path
        info_payload = _load_info_json(info_json_path)
        subtitle_paths = fetched_subtitle_paths

    manual_languages = set()
    auto_languages = set()
    if info_payload is not None:
        subtitles = info_payload.get("subtitles")
        automatic_captions = info_payload.get("automatic_captions")
        if isinstance(subtitles, dict):
            manual_languages = {str(key) for key in subtitles}
        if isinstance(automatic_captions, dict):
            auto_languages = {str(key) for key in automatic_captions}

    indexed_tracks: list[tuple[SubtitleTrack, list[TranscriptSegment]]] = []
    for subtitle_path in subtitle_paths:
        if not subtitle_path.exists():
            continue
        segments = parse_subtitle_file(subtitle_path)
        if not segments:
            continue
        track = infer_subtitle_track(
            subtitle_path,
            manual_languages=manual_languages,
            auto_languages=auto_languages,
        )
        indexed_tracks.append((track, segments))
    if indexed_tracks:
        catalog.replace_transcripts(info.video_id, indexed_tracks)
    return sum(len(segments) for _, segments in indexed_tracks)


def _index_video_payload(
    catalog: CatalogStore,
    info: VideoInfo,
    payload: dict[str, Any] | None,
    *,
    requested_input: str,
    source_query: str | None,
    output_path: Path | None,
    info_json_path: Path | None,
    downloaded_at: str | None,
    settings: Settings,
    fetch_subs: bool,
    auto_subs: bool,
    lang: str | None,
) -> IndexSummary:
    catalog.upsert_video(
        VideoUpsert(
            video_id=info.video_id,
            title=info.title,
            channel=info.channel,
            upload_date=info.upload_date,
            duration_seconds=info.duration_seconds,
            extractor_key=info.extractor_key,
            webpage_url=info.webpage_url,
            requested_input=requested_input,
            source_query=source_query,
            output_path=output_path,
            info_json_path=info_json_path,
            downloaded_at=downloaded_at,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    chapter_count = 0
    if payload:
        chapters = extract_chapters(payload)
        if chapters:
            catalog.replace_chapters(info.video_id, chapters)
            chapter_count = len(chapters)
    transcript_segments = _index_transcripts(
        catalog,
        info,
        media_path=output_path,
        info_json_path=info_json_path,
        settings=settings,
        fetch_subs=fetch_subs,
        auto_subs=auto_subs,
        lang=lang,
    )
    return IndexSummary(videos=1, chapters=chapter_count, transcript_segments=transcript_segments)


def index_manifest_record(
    settings: Settings,
    record: ManifestRecord,
    *,
    fetch_subs: bool = False,
    auto_subs: bool = False,
    lang: str | None = None,
) -> IndexSummary:
    catalog = catalog_for_settings(settings)
    output_path = Path(record.output_path) if record.output_path else None
    info_json_path = Path(record.info_json_path) if record.info_json_path else None
    if info_json_path is None and output_path is not None:
        info_json_path = discover_info_json(output_path)
    payload = _load_info_json(info_json_path)
    info = (
        VideoInfo.from_yt_dlp(payload, original_url=record.requested_input)
        if payload is not None
        else VideoInfo(
            video_id=record.video_id,
            title=record.title,
            channel=record.channel,
            upload_date=record.upload_date,
            duration_seconds=record.duration_seconds,
            extractor_key=record.extractor_key,
            webpage_url=record.webpage_url,
            original_url=record.requested_input,
        )
    )
    return _index_video_payload(
        catalog,
        info,
        payload,
        requested_input=record.requested_input,
        source_query=record.source_query,
        output_path=output_path,
        info_json_path=info_json_path,
        downloaded_at=record.downloaded_at,
        settings=settings,
        fetch_subs=fetch_subs,
        auto_subs=auto_subs,
        lang=lang,
    )


def index_refresh(
    settings: Settings,
    *,
    fetch_subs: bool = False,
    auto_subs: bool = False,
    lang: str | None = None,
) -> IndexSummary:
    summary = IndexSummary()
    for record in iter_manifest_records(settings.manifest_file):
        summary = summary.merge(
            index_manifest_record(
                settings,
                record,
                fetch_subs=fetch_subs,
                auto_subs=auto_subs,
                lang=lang,
            )
        )
    return summary


def _playlist_id_from_payload(payload: dict[str, Any], source_input: str) -> str:
    playlist_id = payload.get("id") or payload.get("playlist_id")
    if playlist_id:
        return str(playlist_id)
    return f"playlist:{abs(hash(source_input))}"


def index_target(
    settings: Settings,
    target: str,
    *,
    fetch_subs: bool = False,
    auto_subs: bool = False,
    lang: str | None = None,
) -> IndexSummary:
    payload = yt_dlp.fetch_info(target)
    catalog = catalog_for_settings(settings)
    entries = payload.get("entries")
    if isinstance(entries, list):
        playlist_id = _playlist_id_from_payload(payload, target)
        summary = IndexSummary(playlists=1)
        for position, entry in enumerate(entries, start=1):
            if not entry:
                continue
            info = VideoInfo.from_yt_dlp(entry, original_url=target)
            summary = summary.merge(
                _index_video_payload(
                    catalog,
                    info,
                    entry,
                    requested_input=target,
                    source_query=None,
                    output_path=None,
                    info_json_path=None,
                    downloaded_at=None,
                    settings=settings,
                    fetch_subs=fetch_subs,
                    auto_subs=auto_subs,
                    lang=lang,
                )
            )
            catalog.upsert_playlist_entry(
                PlaylistUpsert(
                    playlist_id=playlist_id,
                    title=str(payload.get("title") or "Untitled Playlist"),
                    channel=str(payload.get("channel") or payload.get("uploader") or "Unknown Channel"),
                    webpage_url=str(payload.get("webpage_url") or target),
                    position=position,
                ),
                info.video_id,
            )
        return summary

    info = VideoInfo.from_yt_dlp(payload, original_url=target)
    return _index_video_payload(
        catalog,
        info,
        payload,
        requested_input=target,
        source_query=None,
        output_path=None,
        info_json_path=None,
        downloaded_at=None,
        settings=settings,
        fetch_subs=fetch_subs,
        auto_subs=auto_subs,
        lang=lang,
    )
