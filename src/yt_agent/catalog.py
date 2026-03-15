"""SQLite-backed catalog and FTS queries for yt-agent."""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

from yt_agent.models import (
    CatalogVideo,
    ChapterEntry,
    ClipSearchHit,
    SubtitleTrack,
    TranscriptSegment,
)
from yt_agent.security import ensure_private_file

__all__ = [
    "SCHEMA",
    "TRANSCRIPT_EXISTS_CLAUSE",
    "TRANSCRIPT_MISSING_CLAUSE",
    "CHAPTER_EXISTS_CLAUSE",
    "CHAPTER_MISSING_CLAUSE",
    "VIDEO_ORDER_BY",
    "VideoUpsert",
    "PlaylistUpsert",
    "VideoDetails",
    "CatalogStore",
]


logger = logging.getLogger("yt_agent")

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    upload_date TEXT,
    duration_seconds INTEGER,
    extractor_key TEXT NOT NULL,
    webpage_url TEXT NOT NULL,
    requested_input TEXT,
    source_query TEXT,
    output_path TEXT,
    info_json_path TEXT,
    downloaded_at TEXT,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
    chapter_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    title TEXT NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL,
    UNIQUE(video_id, position)
);

CREATE TABLE IF NOT EXISTS subtitle_tracks (
    track_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    lang TEXT NOT NULL,
    source TEXT NOT NULL,
    is_auto INTEGER NOT NULL DEFAULT 0,
    format TEXT NOT NULL,
    file_path TEXT NOT NULL,
    UNIQUE(video_id, lang, source, file_path)
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    segment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id INTEGER NOT NULL REFERENCES subtitle_tracks(track_id) ON DELETE CASCADE,
    video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    segment_index INTEGER NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    text TEXT NOT NULL,
    UNIQUE(track_id, segment_index)
);

CREATE TABLE IF NOT EXISTS playlists (
    playlist_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    webpage_url TEXT
);

CREATE TABLE IF NOT EXISTS playlist_entries (
    playlist_id TEXT NOT NULL REFERENCES playlists(playlist_id) ON DELETE CASCADE,
    video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    position INTEGER,
    PRIMARY KEY (playlist_id, video_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chapter_fts USING fts5(
    video_id UNINDEXED,
    chapter_id UNINDEXED,
    title
);

CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
    video_id UNINDEXED,
    segment_id UNINDEXED,
    text
);
"""

TRANSCRIPT_EXISTS_CLAUSE = """
EXISTS (
    SELECT 1
    FROM transcript_segments t
    WHERE t.video_id = v.video_id
)
""".strip()

TRANSCRIPT_MISSING_CLAUSE = """
NOT EXISTS (
    SELECT 1
    FROM transcript_segments t
    WHERE t.video_id = v.video_id
)
""".strip()

CHAPTER_EXISTS_CLAUSE = """
EXISTS (
    SELECT 1
    FROM chapters c
    WHERE c.video_id = v.video_id
)
""".strip()

CHAPTER_MISSING_CLAUSE = """
NOT EXISTS (
    SELECT 1
    FROM chapters c
    WHERE c.video_id = v.video_id
)
""".strip()

VIDEO_ORDER_BY = """
 ORDER BY COALESCE(v.upload_date, '') DESC, COALESCE(v.downloaded_at, '') DESC LIMIT ?
""".strip()


@dataclass(frozen=True)
class VideoUpsert:
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
    indexed_at: str


@dataclass(frozen=True)
class PlaylistUpsert:
    playlist_id: str
    title: str
    channel: str
    webpage_url: str | None
    position: int | None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _row_to_catalog_video(row: sqlite3.Row) -> CatalogVideo:
    payload = _row_to_dict(row)
    return CatalogVideo(
        video_id=str(payload["video_id"]),
        title=str(payload["title"]),
        channel=str(payload["channel"]),
        upload_date=str(payload["upload_date"]) if payload.get("upload_date") is not None else None,
        duration_seconds=int(payload["duration_seconds"])
        if payload.get("duration_seconds") is not None
        else None,
        extractor_key=str(payload["extractor_key"]),
        webpage_url=str(payload["webpage_url"]),
        requested_input=str(payload["requested_input"])
        if payload.get("requested_input") is not None
        else None,
        source_query=str(payload["source_query"])
        if payload.get("source_query") is not None
        else None,
        output_path=Path(str(payload["output_path"])) if payload.get("output_path") else None,
        info_json_path=Path(str(payload["info_json_path"]))
        if payload.get("info_json_path")
        else None,
        downloaded_at=str(payload["downloaded_at"])
        if payload.get("downloaded_at") is not None
        else None,
        chapter_count=int(payload.get("chapter_count") or 0),
        transcript_segment_count=int(payload.get("transcript_segment_count") or 0),
        playlist_count=int(payload.get("playlist_count") or 0),
    )


def _fts_query(query: str) -> str:
    tokens = [re.sub(r"[^\w\-]", "", token) for token in query.split() if token]
    tokens = [t for t in tokens if t]
    return " ".join(f'"{token}"' for token in tokens) if tokens else ""


def _language_match_clause(language: str) -> tuple[str, str]:
    raw = language.strip()
    # Support the wildcard styles we document publicly:
    # - SQL LIKE prefixes such as en%
    # - glob / regex-ish forms such as en* and en.*
    pattern = raw.replace(".*", "%").replace("*", "%").replace("?", "_")
    if "%" in pattern or "_" in pattern:
        return "LIKE", pattern
    return "=", raw


class VideoDetails(TypedDict):
    video: CatalogVideo
    chapters: list[ChapterEntry]
    subtitle_tracks: list[SubtitleTrack]
    transcript_preview: list[TranscriptSegment]


class CatalogStore:
    """High-level catalog API used by the CLI, TUI, and indexer."""

    def __init__(self, path: Path, *, readonly: bool = False) -> None:
        self.path = path
        self.readonly = readonly

    def connect(self, *, readonly: bool | None = None) -> sqlite3.Connection:
        effective_readonly = self.readonly if readonly is None else readonly
        if effective_readonly:
            if not self.path.exists():
                raise FileNotFoundError(self.path)
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        else:
            ensure_private_file(self.path)
            conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        if logger.isEnabledFor(logging.DEBUG):
            conn.set_trace_callback(self._trace_sql)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _trace_sql(statement: str) -> None:
        logger.debug("SQL: %s", statement)

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def initialize(self) -> None:
        self.ensure_schema()

    def upsert_video(self, payload: VideoUpsert) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO videos (
                    video_id, title, channel, upload_date, duration_seconds,
                    extractor_key, webpage_url, requested_input, source_query,
                    output_path, info_json_path, downloaded_at, indexed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    title = excluded.title,
                    channel = excluded.channel,
                    upload_date = excluded.upload_date,
                    duration_seconds = excluded.duration_seconds,
                    extractor_key = excluded.extractor_key,
                    webpage_url = excluded.webpage_url,
                    requested_input = excluded.requested_input,
                    source_query = excluded.source_query,
                    output_path = excluded.output_path,
                    info_json_path = excluded.info_json_path,
                    downloaded_at = COALESCE(excluded.downloaded_at, videos.downloaded_at),
                    indexed_at = excluded.indexed_at
                """,
                (
                    payload.video_id,
                    payload.title,
                    payload.channel,
                    payload.upload_date,
                    payload.duration_seconds,
                    payload.extractor_key,
                    payload.webpage_url,
                    payload.requested_input,
                    payload.source_query,
                    str(payload.output_path) if payload.output_path else None,
                    str(payload.info_json_path) if payload.info_json_path else None,
                    payload.downloaded_at,
                    payload.indexed_at,
                ),
            )

    def replace_chapters(self, video_id: str, chapters: Sequence[ChapterEntry]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM chapter_fts WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM chapters WHERE video_id = ?", (video_id,))
            for chapter in chapters:
                cursor = conn.execute(
                    """
                    INSERT INTO chapters (video_id, position, title, start_seconds, end_seconds)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        chapter.position,
                        chapter.title,
                        chapter.start_seconds,
                        chapter.end_seconds,
                    ),
                )
                chapter_id = int(cursor.lastrowid or 0)
                conn.execute(
                    "INSERT INTO chapter_fts (video_id, chapter_id, title) VALUES (?, ?, ?)",
                    (video_id, chapter_id, chapter.title),
                )

    def replace_transcripts(
        self,
        video_id: str,
        tracks: Sequence[tuple[SubtitleTrack, Sequence[TranscriptSegment]]],
    ) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM transcript_fts WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM transcript_segments WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM subtitle_tracks WHERE video_id = ?", (video_id,))
            for track, segments in tracks:
                cursor = conn.execute(
                    """
                    INSERT INTO subtitle_tracks (video_id, lang, source, is_auto, format, file_path)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        video_id,
                        track.lang,
                        track.source,
                        int(track.is_auto),
                        track.format,
                        str(track.file_path),
                    ),
                )
                track_id = int(cursor.lastrowid or 0)
                for segment in segments:
                    segment_cursor = conn.execute(
                        """
                        INSERT INTO transcript_segments (
                            track_id, video_id, segment_index, start_seconds, end_seconds, text
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            track_id,
                            video_id,
                            segment.segment_index,
                            segment.start_seconds,
                            segment.end_seconds,
                            segment.text,
                        ),
                    )
                    segment_id = int(segment_cursor.lastrowid or 0)
                    conn.execute(
                        "INSERT INTO transcript_fts (video_id, segment_id, text) VALUES (?, ?, ?)",
                        (video_id, segment_id, segment.text),
                    )

    def upsert_playlist_entry(self, playlist: PlaylistUpsert, video_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO playlists (playlist_id, title, channel, webpage_url)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(playlist_id) DO UPDATE SET
                    title = excluded.title,
                    channel = excluded.channel,
                    webpage_url = COALESCE(excluded.webpage_url, playlists.webpage_url)
                """,
                (playlist.playlist_id, playlist.title, playlist.channel, playlist.webpage_url),
            )
            conn.execute(
                """
                INSERT INTO playlist_entries (playlist_id, video_id, position)
                VALUES (?, ?, ?)
                ON CONFLICT(playlist_id, video_id) DO UPDATE SET position = excluded.position
                """,
                (playlist.playlist_id, video_id, playlist.position),
            )

    def get_video(self, video_id: str, *, readonly: bool | None = None) -> CatalogVideo | None:
        try:
            with self.connect(readonly=readonly) as conn:
                row = conn.execute(
                    """
                    SELECT
                        v.*,
                        (SELECT COUNT(*) FROM chapters c WHERE c.video_id = v.video_id)
                            AS chapter_count,
                        (
                            SELECT COUNT(*)
                            FROM transcript_segments t
                            WHERE t.video_id = v.video_id
                        ) AS transcript_segment_count,
                        (SELECT COUNT(*) FROM playlist_entries p WHERE p.video_id = v.video_id)
                            AS playlist_count
                    FROM videos v
                    WHERE v.video_id = ?
                    """,
                    (video_id,),
                ).fetchone()
        except FileNotFoundError:
            return None
        if row is None:
            return None
        return _row_to_catalog_video(row)

    def list_videos(
        self,
        *,
        limit: int = 25,
        channel: str | None = None,
        playlist_id: str | None = None,
        has_transcript: bool | None = None,
        has_chapters: bool | None = None,
    ) -> list[CatalogVideo]:
        query = """
            SELECT DISTINCT
                v.*,
                (SELECT COUNT(*) FROM chapters c WHERE c.video_id = v.video_id)
                    AS chapter_count,
                (
                    SELECT COUNT(*)
                    FROM transcript_segments t
                    WHERE t.video_id = v.video_id
                ) AS transcript_segment_count,
                (SELECT COUNT(*) FROM playlist_entries p WHERE p.video_id = v.video_id)
                    AS playlist_count
            FROM videos v
            LEFT JOIN playlist_entries pe ON pe.video_id = v.video_id
            LEFT JOIN playlists pl ON pl.playlist_id = pe.playlist_id
        """
        clauses: list[str] = []
        params: list[object] = []
        if channel:
            clauses.append("v.channel = ?")
            params.append(channel)
        if playlist_id:
            clauses.append("(pl.playlist_id = ? OR pl.title = ?)")
            params.extend([playlist_id, playlist_id])
        if has_transcript is True:
            clauses.append(TRANSCRIPT_EXISTS_CLAUSE)
        if has_transcript is False:
            clauses.append(TRANSCRIPT_MISSING_CLAUSE)
        if has_chapters is True:
            clauses.append(CHAPTER_EXISTS_CLAUSE)
        if has_chapters is False:
            clauses.append(CHAPTER_MISSING_CLAUSE)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += f" {VIDEO_ORDER_BY}"
        params.append(limit)
        try:
            with self.connect() as conn:
                rows = conn.execute(query, params).fetchall()
        except FileNotFoundError:
            return []
        return [_row_to_catalog_video(row) for row in rows]

    def search_videos(
        self,
        query: str,
        *,
        limit: int = 25,
        channel: str | None = None,
        playlist_id: str | None = None,
        has_transcript: bool | None = None,
        has_chapters: bool | None = None,
    ) -> list[CatalogVideo]:
        if not query.strip():
            return self.list_videos(
                limit=limit,
                channel=channel,
                playlist_id=playlist_id,
                has_transcript=has_transcript,
                has_chapters=has_chapters,
            )
        sql = """
            SELECT DISTINCT
                v.*,
                (SELECT COUNT(*) FROM chapters c WHERE c.video_id = v.video_id)
                    AS chapter_count,
                (
                    SELECT COUNT(*)
                    FROM transcript_segments t
                    WHERE t.video_id = v.video_id
                ) AS transcript_segment_count,
                (SELECT COUNT(*) FROM playlist_entries p WHERE p.video_id = v.video_id)
                    AS playlist_count
            FROM videos v
            LEFT JOIN playlist_entries pe ON pe.video_id = v.video_id
            LEFT JOIN playlists pl ON pl.playlist_id = pe.playlist_id
        """
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_pattern = f"%{escaped}%"
        clauses: list[str] = [
            (
                "(v.title LIKE ? ESCAPE '\\' "
                "OR v.channel LIKE ? ESCAPE '\\' "
                "OR v.video_id LIKE ? ESCAPE '\\')"
            )
        ]
        params: list[object] = [like_pattern, like_pattern, like_pattern]
        if channel:
            clauses.append("v.channel = ?")
            params.append(channel)
        if playlist_id:
            clauses.append("(pl.playlist_id = ? OR pl.title = ?)")
            params.extend([playlist_id, playlist_id])
        if has_transcript is True:
            clauses.append(TRANSCRIPT_EXISTS_CLAUSE)
        if has_transcript is False:
            clauses.append(TRANSCRIPT_MISSING_CLAUSE)
        if has_chapters is True:
            clauses.append(CHAPTER_EXISTS_CLAUSE)
        if has_chapters is False:
            clauses.append(CHAPTER_MISSING_CLAUSE)
        sql += " WHERE " + " AND ".join(clauses)
        sql += f" {VIDEO_ORDER_BY}"
        params.append(limit)
        try:
            with self.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
        except FileNotFoundError:
            return []
        return [_row_to_catalog_video(row) for row in rows]

    def list_channels(self) -> list[str]:
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT channel
                    FROM videos
                    WHERE channel <> ''
                    ORDER BY channel COLLATE NOCASE
                    """
                ).fetchall()
        except FileNotFoundError:
            return []
        return [str(row["channel"]) for row in rows]

    def list_playlists(self) -> list[dict[str, str]]:
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT pl.playlist_id, pl.title, pl.channel, COUNT(pe.video_id) AS entry_count
                    FROM playlists pl
                    LEFT JOIN playlist_entries pe ON pe.playlist_id = pl.playlist_id
                    GROUP BY pl.playlist_id, pl.title, pl.channel
                    ORDER BY pl.title COLLATE NOCASE
                    """
                ).fetchall()
        except FileNotFoundError:
            return []
        return [
            {
                "playlist_id": str(row["playlist_id"]),
                "title": str(row["title"]),
                "channel": str(row["channel"]),
                "entry_count": str(row["entry_count"]),
            }
            for row in rows
        ]

    def library_stats(self) -> dict[str, int]:
        try:
            with self.connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM videos) AS videos,
                        (SELECT COUNT(*) FROM videos WHERE output_path IS NOT NULL) AS local_media,
                        (SELECT COUNT(*) FROM playlists) AS playlists,
                        (SELECT COUNT(*) FROM chapters) AS chapters,
                        (SELECT COUNT(*) FROM subtitle_tracks) AS subtitle_tracks,
                        (SELECT COUNT(*) FROM transcript_segments) AS transcript_segments,
                        (SELECT COUNT(DISTINCT channel) FROM videos WHERE channel <> '') AS channels
                    """
                ).fetchone()
        except FileNotFoundError:
            return {
                "videos": 0,
                "local_media": 0,
                "playlists": 0,
                "chapters": 0,
                "subtitle_tracks": 0,
                "transcript_segments": 0,
                "channels": 0,
            }
        return {
            "videos": int(row["videos"] or 0),
            "local_media": int(row["local_media"] or 0),
            "playlists": int(row["playlists"] or 0),
            "chapters": int(row["chapters"] or 0),
            "subtitle_tracks": int(row["subtitle_tracks"] or 0),
            "transcript_segments": int(row["transcript_segments"] or 0),
            "channels": int(row["channels"] or 0),
        }

    def video_chapters(self, video_id: str) -> list[ChapterEntry]:
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT position, title, start_seconds, end_seconds
                    FROM chapters
                    WHERE video_id = ?
                    ORDER BY position
                    """,
                    (video_id,),
                ).fetchall()
        except FileNotFoundError:
            return []
        return [
            ChapterEntry(
                position=int(row["position"]),
                title=str(row["title"]),
                start_seconds=float(row["start_seconds"]),
                end_seconds=float(row["end_seconds"]) if row["end_seconds"] is not None else None,
            )
            for row in rows
        ]

    def subtitle_tracks(self, video_id: str) -> list[SubtitleTrack]:
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT lang, source, is_auto, format, file_path
                    FROM subtitle_tracks
                    WHERE video_id = ?
                    ORDER BY is_auto, lang
                    """,
                    (video_id,),
                ).fetchall()
        except FileNotFoundError:
            return []
        return [
            SubtitleTrack(
                lang=str(row["lang"]),
                source=str(row["source"]),
                is_auto=bool(row["is_auto"]),
                format=str(row["format"]),
                file_path=Path(str(row["file_path"])),
            )
            for row in rows
        ]

    def transcript_preview(self, video_id: str, *, limit: int = 6) -> list[TranscriptSegment]:
        try:
            with self.connect() as conn:
                rows = conn.execute(
                    """
                    SELECT segment_index, start_seconds, end_seconds, text
                    FROM transcript_segments
                    WHERE video_id = ?
                    ORDER BY segment_index
                    LIMIT ?
                    """,
                    (video_id, limit),
                ).fetchall()
        except FileNotFoundError:
            return []
        return [
            TranscriptSegment(
                segment_index=int(row["segment_index"]),
                start_seconds=float(row["start_seconds"]),
                end_seconds=float(row["end_seconds"]),
                text=str(row["text"]),
            )
            for row in rows
        ]

    def get_video_details(self, video_id: str) -> VideoDetails | None:
        try:
            with self.connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        v.*,
                        (SELECT COUNT(*) FROM chapters c WHERE c.video_id = v.video_id)
                            AS chapter_count,
                        (
                            SELECT COUNT(*)
                            FROM transcript_segments t
                            WHERE t.video_id = v.video_id
                        ) AS transcript_segment_count,
                        (SELECT COUNT(*) FROM playlist_entries p WHERE p.video_id = v.video_id)
                            AS playlist_count
                    FROM videos v
                    WHERE v.video_id = ?
                    """,
                    (video_id,),
                ).fetchone()
                if row is None:
                    return None
                video = _row_to_catalog_video(row)

                chapter_rows = conn.execute(
                    """
                    SELECT position, title, start_seconds, end_seconds
                    FROM chapters
                    WHERE video_id = ?
                    ORDER BY position
                    """,
                    (video_id,),
                ).fetchall()
                chapters = [
                    ChapterEntry(
                        position=int(r["position"]),
                        title=str(r["title"]),
                        start_seconds=float(r["start_seconds"]),
                        end_seconds=float(r["end_seconds"])
                        if r["end_seconds"] is not None
                        else None,
                    )
                    for r in chapter_rows
                ]

                track_rows = conn.execute(
                    """
                    SELECT lang, source, is_auto, format, file_path
                    FROM subtitle_tracks
                    WHERE video_id = ?
                    ORDER BY is_auto, lang
                    """,
                    (video_id,),
                ).fetchall()
                tracks = [
                    SubtitleTrack(
                        lang=str(r["lang"]),
                        source=str(r["source"]),
                        is_auto=bool(r["is_auto"]),
                        format=str(r["format"]),
                        file_path=Path(str(r["file_path"])),
                    )
                    for r in track_rows
                ]

                seg_rows = conn.execute(
                    """
                    SELECT segment_index, start_seconds, end_seconds, text
                    FROM transcript_segments
                    WHERE video_id = ?
                    ORDER BY segment_index
                    LIMIT 6
                    """,
                    (video_id,),
                ).fetchall()
                preview = [
                    TranscriptSegment(
                        segment_index=int(r["segment_index"]),
                        start_seconds=float(r["start_seconds"]),
                        end_seconds=float(r["end_seconds"]),
                        text=str(r["text"]),
                    )
                    for r in seg_rows
                ]
        except FileNotFoundError:
            return None

        return {
            "video": video,
            "chapters": chapters,
            "subtitle_tracks": tracks,
            "transcript_preview": preview,
        }

    def search_clips(
        self,
        query: str,
        *,
        source: str = "all",
        channel: str | None = None,
        language: str | None = None,
        limit: int = 10,
    ) -> list[ClipSearchHit]:
        sources = {"chapters", "transcript"} if source == "all" else {source}
        hits: list[ClipSearchHit] = []
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        try:
            with self.connect() as conn:
                if "chapters" in sources:
                    chapter_sql = """
                        SELECT
                            c.chapter_id,
                            v.video_id,
                            v.title,
                            v.channel,
                            v.webpage_url,
                            v.output_path,
                            c.start_seconds,
                            COALESCE(
                                c.end_seconds,
                                v.duration_seconds,
                                c.start_seconds + 60.0
                            ) AS end_seconds,
                            c.title AS match_text,
                            c.title AS context,
                            bm25(chapter_fts) AS score
                        FROM chapter_fts
                        JOIN chapters c ON c.chapter_id = CAST(chapter_fts.chapter_id AS INTEGER)
                        JOIN videos v ON v.video_id = chapter_fts.video_id
                        WHERE chapter_fts MATCH ?
                    """
                    params: list[object] = [fts_query]
                    if channel:
                        chapter_sql += " AND v.channel = ?"
                        params.append(channel)
                    chapter_sql += " ORDER BY score, c.start_seconds LIMIT ?"
                    params.append(limit)
                    for row in conn.execute(chapter_sql, params):
                        hits.append(
                            ClipSearchHit(
                                result_id=f"chapter:{row['chapter_id']}",
                                source="chapters",
                                video_id=str(row["video_id"]),
                                title=str(row["title"]),
                                channel=str(row["channel"]),
                                webpage_url=str(row["webpage_url"]),
                                start_seconds=float(row["start_seconds"]),
                                end_seconds=float(row["end_seconds"]),
                                score=float(row["score"]),
                                match_text=str(row["match_text"]),
                                context=str(row["context"]),
                                output_path=Path(str(row["output_path"]))
                                if row["output_path"]
                                else None,
                            )
                        )
                if "transcript" in sources:
                    transcript_sql = """
                        SELECT
                            t.segment_id,
                            v.video_id,
                            v.title,
                            v.channel,
                            v.webpage_url,
                            v.output_path,
                            t.start_seconds,
                            t.end_seconds,
                            t.text AS match_text,
                            snippet(transcript_fts, 2, '[', ']', ' ... ', 12) AS context,
                            bm25(transcript_fts) AS score
                        FROM transcript_fts
                        JOIN transcript_segments t
                            ON t.segment_id = CAST(transcript_fts.segment_id AS INTEGER)
                        JOIN videos v ON v.video_id = transcript_fts.video_id
                        LEFT JOIN subtitle_tracks st ON st.track_id = t.track_id
                        WHERE transcript_fts MATCH ?
                    """
                    params = [fts_query]
                    if channel:
                        transcript_sql += " AND v.channel = ?"
                        params.append(channel)
                    if language:
                        operator, pattern = _language_match_clause(language)
                        transcript_sql += f" AND st.lang {operator} ?"
                        params.append(pattern)
                    transcript_sql += " ORDER BY score, t.start_seconds LIMIT ?"
                    params.append(limit)
                    for row in conn.execute(transcript_sql, params):
                        hits.append(
                            ClipSearchHit(
                                result_id=f"transcript:{row['segment_id']}",
                                source="transcript",
                                video_id=str(row["video_id"]),
                                title=str(row["title"]),
                                channel=str(row["channel"]),
                                webpage_url=str(row["webpage_url"]),
                                start_seconds=float(row["start_seconds"]),
                                end_seconds=float(row["end_seconds"]),
                                score=float(row["score"]),
                                match_text=str(row["match_text"]),
                                context=str(row["context"]),
                                output_path=Path(str(row["output_path"]))
                                if row["output_path"]
                                else None,
                            )
                        )
        except FileNotFoundError:
            return []
        hits.sort(key=lambda item: (item.score, item.source, item.start_seconds))
        return hits[:limit]

    def get_clip_hit(self, result_id: str, *, readonly: bool | None = None) -> ClipSearchHit | None:
        if ":" not in result_id:
            return None
        source, raw_id = result_id.split(":", 1)
        if not raw_id.isdigit():
            return None
        try:
            conn_manager = self.connect(readonly=readonly)
        except FileNotFoundError:
            return None
        with conn_manager as conn:
            if source == "chapter":
                row = conn.execute(
                    """
                    SELECT
                        c.chapter_id,
                        v.video_id,
                        v.title,
                        v.channel,
                        v.webpage_url,
                        v.output_path,
                        c.start_seconds,
                        COALESCE(
                            c.end_seconds,
                            v.duration_seconds,
                            c.start_seconds + 60.0
                        ) AS end_seconds,
                        c.title AS match_text,
                        c.title AS context
                    FROM chapters c
                    JOIN videos v ON v.video_id = c.video_id
                    WHERE c.chapter_id = ?
                    """,
                    (int(raw_id),),
                ).fetchone()
                if row is None:
                    return None
                return ClipSearchHit(
                    result_id=result_id,
                    source="chapters",
                    video_id=str(row["video_id"]),
                    title=str(row["title"]),
                    channel=str(row["channel"]),
                    webpage_url=str(row["webpage_url"]),
                    start_seconds=float(row["start_seconds"]),
                    end_seconds=float(row["end_seconds"]),
                    score=0.0,
                    match_text=str(row["match_text"]),
                    context=str(row["context"]),
                    output_path=Path(str(row["output_path"])) if row["output_path"] else None,
                )
            if source == "transcript":
                row = conn.execute(
                    """
                    SELECT
                        t.segment_id,
                        t.segment_index,
                        v.video_id,
                        v.title,
                        v.channel,
                        v.webpage_url,
                        v.output_path,
                        t.start_seconds,
                        t.end_seconds,
                        t.text AS match_text
                    FROM transcript_segments t
                    JOIN videos v ON v.video_id = t.video_id
                    WHERE t.segment_id = ?
                    """,
                    (int(raw_id),),
                ).fetchone()
                if row is None:
                    return None
                context_rows = conn.execute(
                    """
                    SELECT text
                    FROM transcript_segments
                    WHERE video_id = ? AND segment_index BETWEEN ? AND ?
                    ORDER BY segment_index
                    """,
                    (
                        row["video_id"],
                        max(int(row["segment_index"]) - 1, 0),
                        int(row["segment_index"]) + 1,
                    ),
                ).fetchall()
                context = " ".join(str(item["text"]) for item in context_rows)
                return ClipSearchHit(
                    result_id=result_id,
                    source="transcript",
                    video_id=str(row["video_id"]),
                    title=str(row["title"]),
                    channel=str(row["channel"]),
                    webpage_url=str(row["webpage_url"]),
                    start_seconds=float(row["start_seconds"]),
                    end_seconds=float(row["end_seconds"]),
                    score=0.0,
                    match_text=str(row["match_text"]),
                    context=context,
                    output_path=Path(str(row["output_path"])) if row["output_path"] else None,
                )
        return None

    def delete_video(self, video_id: str) -> bool:
        with self.connect() as conn:
            conn.execute("DELETE FROM chapter_fts WHERE video_id = ?", (video_id,))
            conn.execute("DELETE FROM transcript_fts WHERE video_id = ?", (video_id,))
            cursor = conn.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        shutil.rmtree(self.path.parent / "subtitle-cache" / video_id, ignore_errors=True)
        return cursor.rowcount > 0

    def clear(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM chapter_fts")
            conn.execute("DELETE FROM transcript_fts")
            conn.execute("DELETE FROM playlist_entries")
            conn.execute("DELETE FROM playlists")
            conn.execute("DELETE FROM transcript_segments")
            conn.execute("DELETE FROM subtitle_tracks")
            conn.execute("DELETE FROM chapters")
            conn.execute("DELETE FROM videos")
