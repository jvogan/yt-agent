"""User-owned catalog annotations, groupings, and timestamp bookmarks."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from yt_agent.catalog import CatalogStore
from yt_agent.errors import InvalidInputError


class CurationStore:
    def __init__(self, catalog: CatalogStore) -> None:
        self.catalog = catalog

    def set_annotation(self, video_id: str, *, note: str, rating: int | None) -> None:
        if rating is not None and not 1 <= rating <= 5:
            raise InvalidInputError("Rating must be between 1 and 5.")
        self._require_video(video_id)
        with self.catalog.connect() as conn:
            conn.execute(
                """
                INSERT INTO video_curation (video_id, note, rating, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET note=excluded.note,
                    rating=excluded.rating, updated_at=excluded.updated_at
                """,
                (video_id, note, rating, datetime.now(UTC).isoformat()),
            )

    def clear_annotation(self, video_id: str) -> None:
        with self.catalog.connect() as conn:
            conn.execute("DELETE FROM video_curation WHERE video_id = ?", (video_id,))

    def add_tag(self, video_id: str, name: str) -> None:
        self._require_video(video_id)
        normalized = name.strip()
        if not normalized:
            raise InvalidInputError("Tag cannot be empty.")
        with self.catalog.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (normalized,))
            conn.execute(
                """INSERT OR IGNORE INTO video_tags (video_id, tag_id)
                SELECT ?, tag_id FROM tags WHERE name = ? COLLATE NOCASE""",
                (video_id, normalized),
            )

    def remove_tag(self, video_id: str, name: str) -> None:
        with self.catalog.connect() as conn:
            conn.execute(
                """DELETE FROM video_tags WHERE video_id = ? AND tag_id =
                (SELECT tag_id FROM tags WHERE name = ? COLLATE NOCASE)""",
                (video_id, name.strip()),
            )
            conn.execute("DELETE FROM tags WHERE tag_id NOT IN (SELECT tag_id FROM video_tags)")

    def create_collection(self, name: str, description: str = "") -> int:
        if not name.strip():
            raise InvalidInputError("Collection name cannot be empty.")
        try:
            with self.catalog.connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO collections (name, description, created_at) VALUES (?, ?, ?)",
                    (name.strip(), description, datetime.now(UTC).isoformat()),
                )
                return int(cursor.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            raise InvalidInputError(f"Collection '{name}' already exists.") from exc

    def delete_collection(self, collection_id: int) -> None:
        with self.catalog.connect() as conn:
            conn.execute("DELETE FROM collections WHERE collection_id = ?", (collection_id,))

    def set_collection_video(self, collection_id: int, video_id: str, *, add: bool) -> None:
        self._require_video(video_id)
        with self.catalog.connect() as conn:
            if add:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO collection_videos
                        (collection_id, video_id) VALUES (?, ?)""",
                        (collection_id, video_id),
                    )
                except sqlite3.IntegrityError as exc:
                    raise InvalidInputError(f"Collection {collection_id} was not found.") from exc
            else:
                conn.execute(
                    "DELETE FROM collection_videos WHERE collection_id = ? AND video_id = ?",
                    (collection_id, video_id),
                )

    def add_bookmark(
        self, video_id: str, timestamp_seconds: float, *, label: str = "", note: str = ""
    ) -> int:
        self._require_video(video_id)
        if timestamp_seconds < 0:
            raise InvalidInputError("Bookmark timestamp must not be negative.")
        with self.catalog.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO bookmarks
                (video_id, timestamp_seconds, label, note, created_at) VALUES (?, ?, ?, ?, ?)""",
                (video_id, timestamp_seconds, label, note, datetime.now(UTC).isoformat()),
            )
            return int(cursor.lastrowid or 0)

    def remove_bookmark(self, bookmark_id: int) -> None:
        with self.catalog.connect() as conn:
            conn.execute("DELETE FROM bookmarks WHERE bookmark_id = ?", (bookmark_id,))

    def list_all(self, *, video_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        with self.catalog.connect(readonly=True) as conn:
            if video_id:
                params = (video_id,)
                annotations_rows = conn.execute(
                    "SELECT * FROM video_curation WHERE video_id = ?", params
                )
                tag_rows = conn.execute(
                    """SELECT vt.video_id, t.name FROM video_tags vt
                    JOIN tags t USING(tag_id) WHERE vt.video_id = ? ORDER BY t.name""",
                    params,
                )
                bookmark_rows = conn.execute(
                    """SELECT * FROM bookmarks WHERE video_id = ?
                    ORDER BY timestamp_seconds""",
                    params,
                )
                collection_rows = conn.execute(
                    """SELECT c.collection_id, c.name, c.description, cv.video_id
                    FROM collections c JOIN collection_videos cv USING(collection_id)
                    WHERE cv.video_id = ? ORDER BY c.name""",
                    params,
                )
            else:
                annotations_rows = conn.execute("SELECT * FROM video_curation")
                tag_rows = conn.execute(
                    """SELECT vt.video_id, t.name FROM video_tags vt
                    JOIN tags t USING(tag_id) ORDER BY t.name"""
                )
                bookmark_rows = conn.execute(
                    "SELECT * FROM bookmarks ORDER BY video_id, timestamp_seconds"
                )
                collection_rows = conn.execute(
                    """SELECT c.collection_id, c.name, c.description, cv.video_id
                    FROM collections c LEFT JOIN collection_videos cv USING(collection_id)
                    ORDER BY c.name"""
                )
            result = {
                "annotations": [dict(row) for row in annotations_rows],
                "tags": [dict(row) for row in tag_rows],
                "bookmarks": [dict(row) for row in bookmark_rows],
                "collections": [dict(row) for row in collection_rows],
            }
        return result

    def search(self, query: str) -> list[dict[str, Any]]:
        escaped = query.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        pattern = f"%{escaped}%"
        with self.catalog.connect(readonly=True) as conn:
            rows = conn.execute(
                """SELECT DISTINCT v.video_id, v.title, vc.note, vc.rating
                FROM videos v LEFT JOIN video_curation vc USING(video_id)
                LEFT JOIN video_tags vt USING(video_id) LEFT JOIN tags t USING(tag_id)
                LEFT JOIN collection_videos cv USING(video_id)
                LEFT JOIN collections c USING(collection_id)
                LEFT JOIN bookmarks b USING(video_id)
                WHERE v.title LIKE ? ESCAPE '!' OR vc.note LIKE ? ESCAPE '!'
                    OR t.name LIKE ? ESCAPE '!' OR c.name LIKE ? ESCAPE '!'
                    OR b.label LIKE ? ESCAPE '!' OR b.note LIKE ? ESCAPE '!'
                ORDER BY v.title""",
                (pattern,) * 6,
            ).fetchall()
        return [dict(row) for row in rows]

    def _require_video(self, video_id: str) -> None:
        if self.catalog.get_video(video_id) is None:
            raise InvalidInputError(f"Catalog video '{video_id}' was not found.")
