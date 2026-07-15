"""Persistent saved sources and deterministic incremental synchronization."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from yt_agent import yt_dlp
from yt_agent.archive import is_archived, load_archive_entries
from yt_agent.config import Settings
from yt_agent.errors import InvalidInputError, YtAgentError
from yt_agent.manifest import append_manifest_record
from yt_agent.models import DownloadTarget, ManifestRecord, VideoInfo
from yt_agent.security import atomic_write_text

__all__ = [
    "SavedSource",
    "SourceStore",
    "SyncItem",
    "SyncReport",
    "source_store_path",
    "run_sync",
]


_SOURCE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SOURCE_KINDS = {"channel", "playlist"}


@dataclass(frozen=True)
class SavedSource:
    name: str
    kind: str
    url: str
    created_at: str
    last_synced_at: str | None = None
    seen_video_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SavedSource:
        seen = payload.get("seen_video_ids")
        return cls(
            name=str(payload.get("name") or ""),
            kind=str(payload.get("kind") or ""),
            url=str(payload.get("url") or ""),
            created_at=str(payload.get("created_at") or ""),
            last_synced_at=str(payload["last_synced_at"])
            if payload.get("last_synced_at")
            else None,
            seen_video_ids=tuple(sorted({str(item) for item in seen}))
            if isinstance(seen, list)
            else (),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "url": self.url,
            "created_at": self.created_at,
            "last_synced_at": self.last_synced_at,
            "seen_video_ids": list(self.seen_video_ids),
        }


def source_store_path(settings: Settings) -> Path:
    return settings.catalog_file.parent / "sources.json"


class SourceStore:
    """Private JSON persistence for channel and playlist definitions."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def list(self) -> list[SavedSource]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidInputError(f"Saved source file is invalid JSON: {self.path}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
            raise InvalidInputError(f"Saved source file has an invalid structure: {self.path}")
        sources = [
            SavedSource.from_dict(item)
            for item in payload["sources"]
            if isinstance(item, dict)
        ]
        return sorted(sources, key=lambda item: item.name.casefold())

    def save(self, sources: Sequence[SavedSource]) -> None:
        ordered = sorted(sources, key=lambda item: item.name.casefold())
        payload = {
            "schema_version": 1,
            "sources": [item.as_dict() for item in ordered],
        }
        atomic_write_text(self.path, f"{json.dumps(payload, indent=2)}\n", encoding="utf-8")

    def add(self, name: str, kind: str, url: str) -> SavedSource:
        normalized_name = name.strip()
        normalized_kind = kind.strip().casefold()
        if not _SOURCE_NAME_RE.fullmatch(normalized_name):
            raise InvalidInputError(
                "Source name must be 1-64 letters, numbers, dots, underscores, or hyphens."
            )
        if normalized_kind not in _SOURCE_KINDS:
            raise InvalidInputError("Source kind must be 'channel' or 'playlist'.")
        normalized_url = yt_dlp.normalize_target(url)
        parsed = urlsplit(normalized_url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if normalized_kind == "playlist" and "list" not in parse_qs(parsed.query):
            raise InvalidInputError("Playlist sources must use a YouTube playlist URL.")
        if normalized_kind == "channel" and not (
            path_parts
            and (
                path_parts[0].startswith("@")
                or path_parts[0].casefold() in {"channel", "user", "c"}
            )
        ):
            raise InvalidInputError("Channel sources must use a YouTube channel URL.")
        sources = self.list()
        if any(item.name.casefold() == normalized_name.casefold() for item in sources):
            raise InvalidInputError(f"Saved source already exists: {normalized_name}")
        source = SavedSource(
            name=normalized_name,
            kind=normalized_kind,
            url=normalized_url,
            created_at=datetime.now(UTC).isoformat(),
        )
        self.save([*sources, source])
        return source

    def remove(self, name: str) -> bool:
        sources = self.list()
        kept = [item for item in sources if item.name.casefold() != name.strip().casefold()]
        if len(kept) == len(sources):
            return False
        self.save(kept)
        return True


@dataclass(frozen=True)
class SyncItem:
    source: str
    video_id: str | None
    title: str | None
    status: str
    message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class SyncReport:
    dry_run: bool
    index: bool
    download: bool
    sources: int
    items: tuple[SyncItem, ...]

    def as_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.status] = counts.get(item.status, 0) + 1
        return {
            "schema_version": 1,
            "command": "sync run",
            "status": "ok" if not counts.get("failed") else "partial",
            "dry_run": self.dry_run,
            "network_fetch_attempted": True,
            "index": self.index,
            "download": self.download,
            "summary": {"sources": self.sources, "items": len(self.items), **counts},
            "items": [item.as_dict() for item in self.items],
        }


def _parse_since(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInputError("--since must use YYYY-MM-DD format.") from exc


def _payload_targets(
    source: SavedSource, payload: dict[str, Any]
) -> list[tuple[DownloadTarget, dict[str, Any]]]:
    raw_entries = payload.get("entries")
    entries = raw_entries if isinstance(raw_entries, list) else [payload]
    results: list[tuple[DownloadTarget, dict[str, Any]]] = []
    seen: set[str] = set()
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        try:
            info = VideoInfo.from_yt_dlp(raw_entry, original_url=source.url)
        except InvalidInputError:
            continue
        if info.video_id in seen:
            continue
        seen.add(info.video_id)
        results.append((DownloadTarget(source.url, info), raw_entry))
    return sorted(
        results,
        key=lambda item: (item[0].info.upload_date or "", item[0].info.video_id),
        reverse=True,
    )


def _index_one(settings: Settings, target: DownloadTarget, payload: dict[str, Any]) -> None:
    # Reuse the indexer's payload path so syncing does not refetch every video.
    from yt_agent.indexer import _index_video_payload, catalog_for_settings

    _index_video_payload(
        catalog_for_settings(settings),
        target.info,
        payload,
        requested_input=target.original_input,
        source_query=None,
        output_path=None,
        info_json_path=None,
        downloaded_at=None,
        settings=settings,
        fetch_subs=False,
        auto_subs=False,
        lang=None,
    )


def _download_one(settings: Settings, target: DownloadTarget, *, index_after: bool) -> str:
    execution = yt_dlp.download_target(target, settings)
    if execution is None:
        return "archived"
    record = ManifestRecord.from_download(
        target,
        output_path=execution.output_path,
        info_json_path=execution.info_json_path,
    )
    append_manifest_record(settings.manifest_file, record)
    if index_after:
        from yt_agent.indexer import index_manifest_record

        index_manifest_record(settings, record)
    return "downloaded"


def run_sync(
    settings: Settings,
    *,
    names: list[str] | None = None,
    since: str | None = None,
    latest: int | None = None,
    index: bool = True,
    download: bool = False,
    dry_run: bool = False,
    fetch_info_fn: Callable[[str], dict[str, Any]] = yt_dlp.fetch_info,
    index_fn: Callable[[Settings, DownloadTarget, dict[str, Any]], None] = _index_one,
    download_fn: Callable[..., str] = _download_one,
) -> SyncReport:
    """Fetch saved sources and process only video IDs not seen by earlier successful runs."""
    if not index and not download:
        raise InvalidInputError("sync run requires --index or --download.")
    if latest is not None and latest < 1:
        raise InvalidInputError("--latest must be at least 1.")
    since_date = _parse_since(since)
    store = SourceStore(source_store_path(settings))
    all_sources = store.list()
    if names:
        requested = {name.casefold() for name in names}
        selected = [item for item in all_sources if item.name.casefold() in requested]
        found = {item.name.casefold() for item in selected}
        missing = sorted(requested - found)
        if missing:
            raise InvalidInputError(f"Unknown saved source(s): {', '.join(missing)}")
    else:
        selected = all_sources
    if not selected:
        raise InvalidInputError("No saved sources found. Add one with 'yt-agent sync add'.")

    archive_entries = load_archive_entries(settings.archive_file)
    items: list[SyncItem] = []
    replacements: dict[str, SavedSource] = {}
    for source in selected:
        try:
            payload = fetch_info_fn(source.url)
        except YtAgentError as exc:
            items.append(SyncItem(source.name, None, None, "failed", str(exc)))
            continue
        candidates = []
        known = set(source.seen_video_ids)
        for target, entry in _payload_targets(source, payload):
            if target.info.video_id in known:
                continue
            if since_date is not None:
                upload_date = target.info.upload_date
                try:
                    parsed_upload_date = date.fromisoformat(upload_date) if upload_date else None
                except ValueError:
                    parsed_upload_date = None
                if parsed_upload_date is None or parsed_upload_date < since_date:
                    continue
            candidates.append((target, entry))
        if latest is not None:
            candidates = candidates[:latest]

        completed_ids: set[str] = set()
        for target, entry in candidates:
            archived = is_archived(archive_entries, target.info)
            if dry_run:
                planned = "would_index"
                if download and archived:
                    planned = "would_skip_archived" if not index else "would_index_archived"
                elif download:
                    planned = "would_download"
                items.append(
                    SyncItem(source.name, target.info.video_id, target.info.title, planned)
                )
                continue
            try:
                if download and not archived:
                    status = download_fn(settings, target, index_after=index)
                    if status == "downloaded":
                        archive_entries.add(target.info.archive_key)
                elif index:
                    index_fn(settings, target, entry)
                    status = "indexed_archived" if archived and download else "indexed"
                else:
                    status = "archived"
            except YtAgentError as exc:
                items.append(
                    SyncItem(
                        source.name,
                        target.info.video_id,
                        target.info.title,
                        "failed",
                        str(exc),
                    )
                )
                continue
            completed_ids.add(target.info.video_id)
            items.append(SyncItem(source.name, target.info.video_id, target.info.title, status))
        if not dry_run:
            replacements[source.name.casefold()] = replace(
                source,
                last_synced_at=datetime.now(UTC).isoformat(),
                seen_video_ids=tuple(sorted(known | completed_ids)),
            )

    if not dry_run:
        updated = [replacements.get(item.name.casefold(), item) for item in all_sources]
        store.save(updated)
    return SyncReport(dry_run, index, download, len(selected), tuple(items))
