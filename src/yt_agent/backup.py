"""Versioned backup for core indexed content, comments, and user curation."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from yt_agent.catalog import CatalogStore
from yt_agent.errors import InvalidInputError

BACKUP_FORMAT = "yt-agent-catalog-backup"
BACKUP_VERSION = 4

_VIDEO_FIELDS = (
    "video_id",
    "title",
    "channel",
    "upload_date",
    "duration_seconds",
    "extractor_key",
    "webpage_url",
    "requested_input",
    "source_query",
    "output_path",
    "info_json_path",
    "downloaded_at",
    "indexed_at",
)
_CHAPTER_FIELDS = (
    "chapter_id",
    "video_id",
    "position",
    "title",
    "start_seconds",
    "end_seconds",
)
_TRACK_FIELDS = ("track_id", "video_id", "lang", "source", "is_auto", "format", "file_path")
_SEGMENT_FIELDS = (
    "segment_id",
    "track_id",
    "video_id",
    "segment_index",
    "start_seconds",
    "end_seconds",
    "text",
)
_PLAYLIST_FIELDS = ("playlist_id", "title", "channel", "webpage_url")
_ENTRY_FIELDS = ("playlist_id", "video_id", "position")
_CURATION_FIELDS = ("video_id", "note", "rating", "updated_at")
_TAG_FIELDS = ("tag_id", "name")
_VIDEO_TAG_FIELDS = ("video_id", "tag_id")
_COLLECTION_FIELDS_ROW = ("collection_id", "name", "description", "created_at")
_COLLECTION_VIDEO_FIELDS = ("collection_id", "video_id", "position")
_BOOKMARK_FIELDS = ("bookmark_id", "video_id", "timestamp_seconds", "label", "note", "created_at")
_COMMENT_FIELDS = (
    "comment_id",
    "video_id",
    "author",
    "text",
    "published_at",
    "like_count",
    "parent_id",
)
_STATS_FIELDS = (
    "snapshot_id",
    "video_id",
    "view_count",
    "like_count",
    "comment_count",
    "fetched_at",
    "provider",
)
_COLLECTION_FIELDS = {
    "videos": _VIDEO_FIELDS,
    "chapters": _CHAPTER_FIELDS,
    "subtitle_tracks": _TRACK_FIELDS,
    "transcript_segments": _SEGMENT_FIELDS,
    "playlists": _PLAYLIST_FIELDS,
    "playlist_entries": _ENTRY_FIELDS,
    "video_curation": _CURATION_FIELDS,
    "tags": _TAG_FIELDS,
    "video_tags": _VIDEO_TAG_FIELDS,
    "collections": _COLLECTION_FIELDS_ROW,
    "collection_videos": _COLLECTION_VIDEO_FIELDS,
    "bookmarks": _BOOKMARK_FIELDS,
    "comments": _COMMENT_FIELDS,
    "video_stats": _STATS_FIELDS,
}


@dataclass(frozen=True)
class BackupSummary:
    videos: int
    chapters: int
    subtitle_tracks: int
    transcript_segments: int
    playlists: int
    playlist_entries: int

    def as_dict(self) -> dict[str, int]:
        return {
            "videos": self.videos,
            "chapters": self.chapters,
            "subtitle_tracks": self.subtitle_tracks,
            "transcript_segments": self.transcript_segments,
            "playlists": self.playlists,
            "playlist_entries": self.playlist_entries,
        }


def _rows(
    conn: sqlite3.Connection,
    table: str,
    fields: tuple[str, ...],
    order: str,
) -> list[dict[str, Any]]:
    columns = ", ".join(fields)
    query = f"SELECT {columns} FROM {table} ORDER BY {order}"  # noqa: S608
    return [dict(row) for row in conn.execute(query)]


def create_catalog_backup(store: CatalogStore) -> dict[str, Any]:
    """Return a complete JSON-serializable snapshot of catalog-owned records."""
    empty: dict[str, list[dict[str, Any]]] = {name: [] for name in _COLLECTION_FIELDS}
    try:
        with store.connect(readonly=True) as conn:
            conn.execute("BEGIN")
            catalog_schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            existing = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            orders = {
                "videos": "video_id",
                "chapters": "chapter_id",
                "subtitle_tracks": "track_id",
                "transcript_segments": "segment_id",
                "playlists": "playlist_id",
                "playlist_entries": "playlist_id, video_id",
                "video_curation": "video_id",
                "tags": "tag_id",
                "video_tags": "video_id, tag_id",
                "collections": "collection_id",
                "collection_videos": "collection_id, video_id",
                "bookmarks": "bookmark_id",
                "comments": "comment_id",
                "video_stats": "snapshot_id",
            }
            catalog = {
                name: _rows(conn, name, fields, orders[name]) if name in existing else []
                for name, fields in _COLLECTION_FIELDS.items()
            }
    except FileNotFoundError:
        catalog_schema_version = 0
        catalog = empty
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "catalog_schema_version": catalog_schema_version,
        "catalog": catalog,
    }


def _require_record(value: object, fields: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidInputError(f"Backup {label} must be a JSON object.")
    if set(value) != set(fields):
        missing = sorted(set(fields) - set(value))
        extra = sorted(set(value) - set(fields))
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if extra:
            details.append(f"unexpected {', '.join(extra)}")
        raise InvalidInputError(f"Backup {label} has invalid fields ({'; '.join(details)}).")
    return value


def _string(record: dict[str, Any], field: str, label: str, *, optional: bool = False) -> None:
    value = record[field]
    if optional and value is None:
        return
    if not isinstance(value, str):
        raise InvalidInputError(f"Backup {label}.{field} must be a string.")


def _integer(record: dict[str, Any], field: str, label: str, *, optional: bool = False) -> None:
    value = record[field]
    if optional and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(f"Backup {label}.{field} must be an integer.")


def _number(record: dict[str, Any], field: str, label: str, *, optional: bool = False) -> None:
    value = record[field]
    if optional and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidInputError(f"Backup {label}.{field} must be a finite number.")


def _validate_records(catalog: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    legacy_collections = {
        "videos",
        "chapters",
        "subtitle_tracks",
        "transcript_segments",
        "playlists",
        "playlist_entries",
    }
    if set(catalog) == legacy_collections:
        catalog = {
            **catalog,
            **{name: [] for name in set(_COLLECTION_FIELDS) - legacy_collections},
        }
    version_two_collections = set(_COLLECTION_FIELDS) - {"comments", "video_stats"}
    if set(catalog) == version_two_collections:
        catalog = {**catalog, "comments": [], "video_stats": []}
    version_three_collections = set(_COLLECTION_FIELDS) - {"video_stats"}
    if set(catalog) == version_three_collections:
        catalog = {**catalog, "video_stats": []}
    if set(catalog) != set(_COLLECTION_FIELDS):
        raise InvalidInputError("Backup catalog collections are missing or unsupported.")
    result: dict[str, list[dict[str, Any]]] = {}
    for collection, fields in _COLLECTION_FIELDS.items():
        raw_records = catalog[collection]
        if not isinstance(raw_records, list):
            raise InvalidInputError(f"Backup catalog.{collection} must be a JSON array.")
        result[collection] = [
            _require_record(value, fields, f"catalog.{collection}[{index}]")
            for index, value in enumerate(raw_records)
        ]
    return result


def _validate_types(records: dict[str, list[dict[str, Any]]]) -> None:
    for index, row in enumerate(records["videos"]):
        label = f"catalog.videos[{index}]"
        for field in ("video_id", "title", "channel", "extractor_key", "webpage_url", "indexed_at"):
            _string(row, field, label)
        for field in (
            "upload_date",
            "requested_input",
            "source_query",
            "output_path",
            "info_json_path",
            "downloaded_at",
        ):
            _string(row, field, label, optional=True)
        _integer(row, "duration_seconds", label, optional=True)
        if not row["video_id"]:
            raise InvalidInputError(f"Backup {label}.video_id must not be empty.")
        if row["duration_seconds"] is not None and row["duration_seconds"] < 0:
            raise InvalidInputError(f"Backup {label}.duration_seconds must not be negative.")
    for index, row in enumerate(records["chapters"]):
        label = f"catalog.chapters[{index}]"
        _integer(row, "chapter_id", label)
        _string(row, "video_id", label)
        _integer(row, "position", label)
        _string(row, "title", label)
        _number(row, "start_seconds", label)
        _number(row, "end_seconds", label, optional=True)
        if row["chapter_id"] <= 0 or row["position"] < 0 or row["start_seconds"] < 0:
            raise InvalidInputError(f"Backup {label} has invalid chapter bounds or identifiers.")
        if row["end_seconds"] is not None and row["end_seconds"] < row["start_seconds"]:
            raise InvalidInputError(f"Backup {label}.end_seconds precedes start_seconds.")
    for index, row in enumerate(records["subtitle_tracks"]):
        label = f"catalog.subtitle_tracks[{index}]"
        _integer(row, "track_id", label)
        for field in ("video_id", "lang", "source", "format", "file_path"):
            _string(row, field, label)
        if not isinstance(row["is_auto"], (bool, int)) or row["is_auto"] not in (0, 1):
            raise InvalidInputError(f"Backup {label}.is_auto must be true or false.")
        if row["track_id"] <= 0:
            raise InvalidInputError(f"Backup {label}.track_id must be positive.")
    for index, row in enumerate(records["transcript_segments"]):
        label = f"catalog.transcript_segments[{index}]"
        for field in ("segment_id", "track_id", "segment_index"):
            _integer(row, field, label)
        _string(row, "video_id", label)
        _number(row, "start_seconds", label)
        _number(row, "end_seconds", label)
        _string(row, "text", label)
        if (
            row["segment_id"] <= 0
            or row["track_id"] <= 0
            or row["segment_index"] < 0
            or row["start_seconds"] < 0
            or row["end_seconds"] < row["start_seconds"]
        ):
            raise InvalidInputError(f"Backup {label} has invalid segment bounds or identifiers.")
    for index, row in enumerate(records["playlists"]):
        label = f"catalog.playlists[{index}]"
        for field in ("playlist_id", "title", "channel"):
            _string(row, field, label)
        _string(row, "webpage_url", label, optional=True)
        if not row["playlist_id"]:
            raise InvalidInputError(f"Backup {label}.playlist_id must not be empty.")
    for index, row in enumerate(records["playlist_entries"]):
        label = f"catalog.playlist_entries[{index}]"
        _string(row, "playlist_id", label)
        _string(row, "video_id", label)
        _integer(row, "position", label, optional=True)
        if row["position"] is not None and row["position"] < 0:
            raise InvalidInputError(f"Backup {label}.position must not be negative.")
    for index, row in enumerate(records["video_stats"]):
        label = f"catalog.video_stats[{index}]"
        _integer(row, "snapshot_id", label)
        _string(row, "video_id", label)
        for field in ("view_count", "like_count", "comment_count"):
            _integer(row, field, label, optional=True)
            if row[field] is not None and row[field] < 0:
                raise InvalidInputError(f"Backup {label}.{field} must not be negative.")
        _string(row, "fetched_at", label)
        _string(row, "provider", label)
        if row["snapshot_id"] <= 0:
            raise InvalidInputError(f"Backup {label}.snapshot_id must be positive.")
    for index, row in enumerate(records["comments"]):
        label = f"catalog.comments[{index}]"
        for field in ("comment_id", "video_id", "author", "text"):
            _string(row, field, label)
        for field in ("published_at", "parent_id"):
            _string(row, field, label, optional=True)
        _integer(row, "like_count", label)
        if not row["comment_id"] or row["like_count"] < 0:
            raise InvalidInputError(f"Backup {label} has an invalid id or like count.")
    for index, row in enumerate(records["video_curation"]):
        label = f"catalog.video_curation[{index}]"
        for field in ("video_id", "note", "updated_at"):
            _string(row, field, label)
        _integer(row, "rating", label, optional=True)
        if row["rating"] is not None and not 1 <= row["rating"] <= 5:
            raise InvalidInputError(f"Backup {label}.rating must be between 1 and 5.")
    for index, row in enumerate(records["tags"]):
        label = f"catalog.tags[{index}]"
        _integer(row, "tag_id", label)
        _string(row, "name", label)
        if row["tag_id"] <= 0 or not row["name"]:
            raise InvalidInputError(f"Backup {label} has an invalid id or name.")
    for index, row in enumerate(records["collections"]):
        label = f"catalog.collections[{index}]"
        _integer(row, "collection_id", label)
        for field in ("name", "description", "created_at"):
            _string(row, field, label)
        if row["collection_id"] <= 0 or not row["name"]:
            raise InvalidInputError(f"Backup {label} has an invalid id or name.")
    for index, row in enumerate(records["bookmarks"]):
        label = f"catalog.bookmarks[{index}]"
        _integer(row, "bookmark_id", label)
        _string(row, "video_id", label)
        _number(row, "timestamp_seconds", label)
        for field in ("label", "note", "created_at"):
            _string(row, field, label)
        if row["bookmark_id"] <= 0 or row["timestamp_seconds"] < 0:
            raise InvalidInputError(f"Backup {label} has invalid bookmark coordinates.")
    for collection, left_field, right_field in (
        ("video_tags", "video_id", "tag_id"),
        ("collection_videos", "collection_id", "video_id"),
    ):
        for index, row in enumerate(records[collection]):
            label = f"catalog.{collection}[{index}]"
            _string(row, left_field, label) if left_field == "video_id" else _integer(
                row, left_field, label
            )
            _integer(row, right_field, label) if right_field == "tag_id" else _string(
                row, right_field, label
            )
            if "position" in row:
                _integer(row, "position", label, optional=True)
                if row["position"] is not None and row["position"] < 0:
                    raise InvalidInputError(f"Backup {label}.position must not be negative.")
    for index, row in enumerate(records["video_curation"]):
        label = f"catalog.video_curation[{index}]"
        _string(row, "video_id", label)
        _string(row, "note", label)
        _integer(row, "rating", label, optional=True)
        _string(row, "updated_at", label)
        if row["rating"] is not None and not 1 <= row["rating"] <= 5:
            raise InvalidInputError(f"Backup {label}.rating must be between 1 and 5.")
    for index, row in enumerate(records["tags"]):
        label = f"catalog.tags[{index}]"
        _integer(row, "tag_id", label)
        _string(row, "name", label)
    for index, row in enumerate(records["video_tags"]):
        label = f"catalog.video_tags[{index}]"
        _string(row, "video_id", label)
        _integer(row, "tag_id", label)
    for index, row in enumerate(records["collections"]):
        label = f"catalog.collections[{index}]"
        _integer(row, "collection_id", label)
        _string(row, "name", label)
        _string(row, "description", label)
        _string(row, "created_at", label)
    for index, row in enumerate(records["collection_videos"]):
        label = f"catalog.collection_videos[{index}]"
        _integer(row, "collection_id", label)
        _string(row, "video_id", label)
        _integer(row, "position", label, optional=True)
    for index, row in enumerate(records["bookmarks"]):
        label = f"catalog.bookmarks[{index}]"
        _integer(row, "bookmark_id", label)
        _string(row, "video_id", label)
        _number(row, "timestamp_seconds", label)
        _string(row, "label", label)
        _string(row, "note", label)
        _string(row, "created_at", label)
        if row["timestamp_seconds"] < 0:
            raise InvalidInputError(f"Backup {label}.timestamp_seconds must not be negative.")


def _unique(
    rows: list[dict[str, Any]], fields: tuple[str, ...], label: str
) -> set[tuple[Any, ...]]:
    values = {tuple(row[field] for field in fields) for row in rows}
    if len(values) != len(rows):
        raise InvalidInputError(f"Backup {label} contains duplicate records.")
    return values


def validate_catalog_backup(payload: object) -> BackupSummary:
    """Validate the full backup envelope and all cross-record relationships."""
    if not isinstance(payload, dict):
        raise InvalidInputError("Backup must contain a top-level JSON object.")
    required = {"format", "version", "created_at", "catalog_schema_version", "catalog"}
    if set(payload) != required:
        raise InvalidInputError("Backup envelope fields are missing or unsupported.")
    if payload["format"] != BACKUP_FORMAT or payload["version"] not in (
        1,
        2,
        3,
        BACKUP_VERSION,
    ):
        raise InvalidInputError("Unsupported catalog backup format or version.")
    if not isinstance(payload["created_at"], str) or not payload["created_at"]:
        raise InvalidInputError("Backup created_at must be a non-empty string.")
    try:
        datetime.fromisoformat(payload["created_at"])
    except ValueError as exc:
        raise InvalidInputError("Backup created_at must be an ISO-8601 timestamp.") from exc
    schema_version = payload["catalog_schema_version"]
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 0
    ):
        raise InvalidInputError("Backup catalog_schema_version must be a non-negative integer.")
    if not isinstance(payload["catalog"], dict):
        raise InvalidInputError("Backup catalog must be a JSON object.")
    records = _validate_records(payload["catalog"])
    _validate_types(records)

    video_ids = {value[0] for value in _unique(records["videos"], ("video_id",), "videos")}
    playlist_ids = {
        value[0] for value in _unique(records["playlists"], ("playlist_id",), "playlists")
    }
    _unique(records["chapters"], ("chapter_id",), "chapters")
    _unique(records["chapters"], ("video_id", "position"), "chapters")
    track_ids = {
        value[0] for value in _unique(records["subtitle_tracks"], ("track_id",), "subtitle_tracks")
    }
    _unique(
        records["subtitle_tracks"],
        ("video_id", "lang", "source", "file_path"),
        "subtitle_tracks",
    )
    _unique(records["transcript_segments"], ("segment_id",), "transcript_segments")
    _unique(
        records["transcript_segments"],
        ("track_id", "segment_index"),
        "transcript_segments",
    )
    _unique(records["playlist_entries"], ("playlist_id", "video_id"), "playlist_entries")
    _unique(records["comments"], ("comment_id",), "comments")
    _unique(records["video_stats"], ("snapshot_id",), "video_stats")
    _unique(records["video_curation"], ("video_id",), "video_curation")
    tag_ids = {value[0] for value in _unique(records["tags"], ("tag_id",), "tags")}
    tag_names = [str(row["name"]).casefold() for row in records["tags"]]
    if len(tag_names) != len(set(tag_names)):
        raise InvalidInputError("Backup tags contains duplicate names.")
    collection_ids = {
        value[0] for value in _unique(records["collections"], ("collection_id",), "collections")
    }
    collection_names = [str(row["name"]).casefold() for row in records["collections"]]
    if len(collection_names) != len(set(collection_names)):
        raise InvalidInputError("Backup collections contains duplicate names.")
    _unique(records["video_tags"], ("video_id", "tag_id"), "video_tags")
    _unique(
        records["collection_videos"],
        ("collection_id", "video_id"),
        "collection_videos",
    )
    _unique(records["bookmarks"], ("bookmark_id",), "bookmarks")
    tracks_by_id = {row["track_id"]: row for row in records["subtitle_tracks"]}
    if any(row["video_id"] not in video_ids for row in records["chapters"]):
        raise InvalidInputError("Backup chapter references an unknown video.")
    if any(row["video_id"] not in video_ids for row in records["subtitle_tracks"]):
        raise InvalidInputError("Backup subtitle track references an unknown video.")
    for row in records["transcript_segments"]:
        if row["track_id"] not in track_ids or row["video_id"] not in video_ids:
            raise InvalidInputError("Backup transcript segment has an unknown parent.")
        if tracks_by_id[row["track_id"]]["video_id"] != row["video_id"]:
            raise InvalidInputError("Backup transcript segment video does not match its track.")
    if any(
        row["playlist_id"] not in playlist_ids or row["video_id"] not in video_ids
        for row in records["playlist_entries"]
    ):
        raise InvalidInputError("Backup playlist entry has an unknown parent.")
    if any(row["video_id"] not in video_ids for row in records["comments"]):
        raise InvalidInputError("Backup comment references an unknown video.")
    if any(row["video_id"] not in video_ids for row in records["video_stats"]):
        raise InvalidInputError("Backup stats snapshot references an unknown video.")
    if any(row["video_id"] not in video_ids for row in records["video_curation"]):
        raise InvalidInputError("Backup curation row references an unknown video.")
    if any(
        row["video_id"] not in video_ids or row["tag_id"] not in tag_ids
        for row in records["video_tags"]
    ):
        raise InvalidInputError("Backup video tag references an unknown parent.")
    if any(
        row["collection_id"] not in collection_ids or row["video_id"] not in video_ids
        for row in records["collection_videos"]
    ):
        raise InvalidInputError("Backup collection video references an unknown parent.")
    if any(row["video_id"] not in video_ids for row in records["bookmarks"]):
        raise InvalidInputError("Backup bookmark references an unknown video.")
    tag_ids = {value[0] for value in _unique(records["tags"], ("tag_id",), "tags")}
    collection_ids = {
        value[0] for value in _unique(records["collections"], ("collection_id",), "collections")
    }
    _unique(records["video_curation"], ("video_id",), "video_curation")
    _unique(records["video_tags"], ("video_id", "tag_id"), "video_tags")
    _unique(
        records["collection_videos"],
        ("collection_id", "video_id"),
        "collection_videos",
    )
    _unique(records["bookmarks"], ("bookmark_id",), "bookmarks")
    if any(row["video_id"] not in video_ids for row in records["video_curation"]):
        raise InvalidInputError("Backup curation references an unknown video.")
    if any(
        row["video_id"] not in video_ids or row["tag_id"] not in tag_ids
        for row in records["video_tags"]
    ):
        raise InvalidInputError("Backup video tag has an unknown parent.")
    if any(
        row["video_id"] not in video_ids or row["collection_id"] not in collection_ids
        for row in records["collection_videos"]
    ):
        raise InvalidInputError("Backup collection member has an unknown parent.")
    if any(row["video_id"] not in video_ids for row in records["bookmarks"]):
        raise InvalidInputError("Backup bookmark references an unknown video.")
    return BackupSummary(
        **{
            name: len(records[name])
            for name in (
                "videos",
                "chapters",
                "subtitle_tracks",
                "transcript_segments",
                "playlists",
                "playlist_entries",
            )
        }
    )


def _insert_records(conn: sqlite3.Connection, records: dict[str, list[dict[str, Any]]]) -> None:
    for table in (
        "videos",
        "playlists",
        "chapters",
        "subtitle_tracks",
        "transcript_segments",
        "playlist_entries",
        "video_curation",
        "tags",
        "video_tags",
        "collections",
        "collection_videos",
        "bookmarks",
        "comments",
        "video_stats",
    ):
        fields = _COLLECTION_FIELDS[table]
        placeholders = ", ".join("?" for _ in fields)
        columns = ", ".join(fields)
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"  # noqa: S608
        conn.executemany(
            query,
            ([row[field] for field in fields] for row in records[table]),
        )
    conn.executemany(
        "INSERT INTO chapter_fts (video_id, chapter_id, title) VALUES (?, ?, ?)",
        ((row["video_id"], row["chapter_id"], row["title"]) for row in records["chapters"]),
    )
    conn.executemany(
        "INSERT INTO comment_fts (video_id, comment_id, text) VALUES (?, ?, ?)",
        ((row["video_id"], row["comment_id"], row["text"]) for row in records["comments"]),
    )
    conn.executemany(
        "INSERT INTO transcript_fts (video_id, segment_id, text) VALUES (?, ?, ?)",
        (
            (row["video_id"], row["segment_id"], row["text"])
            for row in records["transcript_segments"]
        ),
    )


def restore_catalog_backup(
    store: CatalogStore, payload: object, *, dry_run: bool = False
) -> BackupSummary:
    """Replace catalog-owned data after full validation, atomically when writing."""
    summary = validate_catalog_backup(payload)
    if dry_run:
        return summary
    if not isinstance(payload, dict):  # Already guaranteed by validation above.
        raise InvalidInputError("Backup must contain a top-level JSON object.")
    records = _validate_records(payload["catalog"])
    try:
        with store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in (
                "comment_fts",
                "chapter_fts",
                "transcript_fts",
                "playlist_entries",
                "collection_videos",
                "video_tags",
                "bookmarks",
                "video_curation",
                "comments",
                "video_stats",
                "transcript_segments",
                "subtitle_tracks",
                "chapters",
                "playlists",
                "collections",
                "tags",
                "videos",
            ):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608
            _insert_records(conn, records)
    except sqlite3.IntegrityError as exc:
        raise InvalidInputError(f"Backup violates catalog constraints: {exc}") from exc
    return summary


__all__ = [
    "BACKUP_FORMAT",
    "BACKUP_VERSION",
    "BackupSummary",
    "create_catalog_backup",
    "restore_catalog_backup",
    "validate_catalog_backup",
]
