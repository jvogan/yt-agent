import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yt_agent.backup import create_catalog_backup, restore_catalog_backup
from yt_agent.catalog import CATALOG_SCHEMA_VERSION, SCHEMA, CatalogStore, VideoUpsert
from yt_agent.curation import CurationStore
from yt_agent.errors import InvalidInputError


def _store(tmp_path: Path) -> tuple[CatalogStore, CurationStore]:
    catalog = CatalogStore(tmp_path / "catalog.sqlite")
    catalog.ensure_schema()
    catalog.upsert_video(
        VideoUpsert(
            "abc123def45",
            "Demo",
            "Channel",
            None,
            120,
            "youtube",
            "https://www.youtube.com/watch?v=abc123def45",
            None,
            None,
            None,
            None,
            None,
            datetime.now(UTC).isoformat(),
        )
    )
    return catalog, CurationStore(catalog)


def test_version_one_catalog_migrates_to_curation_schema(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA user_version = 1")

    CatalogStore(path).ensure_schema()

    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == CATALOG_SCHEMA_VERSION
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"video_curation", "tags", "collections", "bookmarks"} <= tables


def test_curation_crud_and_search(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    store.set_annotation("abc123def45", note="Excellent gradient lesson", rating=5)
    store.add_tag("abc123def45", "Learning")
    collection_id = store.create_collection("Watch again", "Favorites")
    store.set_collection_video(collection_id, "abc123def45", add=True)
    bookmark_id = store.add_bookmark("abc123def45", 42.5, label="Key idea", note="Review this")

    records = store.list_all(video_id="abc123def45")
    assert records["annotations"][0]["rating"] == 5
    assert records["tags"][0]["name"] == "Learning"
    assert records["collections"][0]["name"] == "Watch again"
    assert records["bookmarks"][0]["timestamp_seconds"] == 42.5
    assert store.search("gradient")[0]["video_id"] == "abc123def45"
    assert store.search("Learning")[0]["video_id"] == "abc123def45"

    store.remove_tag("abc123def45", "learning")
    store.remove_bookmark(bookmark_id)
    store.set_collection_video(collection_id, "abc123def45", add=False)
    store.delete_collection(collection_id)
    store.clear_annotation("abc123def45")
    assert store.list_all(video_id="abc123def45") == {
        "annotations": [],
        "tags": [],
        "bookmarks": [],
        "collections": [],
    }


def test_curation_validates_user_values(tmp_path: Path) -> None:
    _, store = _store(tmp_path)
    with pytest.raises(InvalidInputError, match="Rating"):
        store.set_annotation("abc123def45", note="", rating=6)
    with pytest.raises(InvalidInputError, match="Tag"):
        store.add_tag("abc123def45", " ")
    with pytest.raises(InvalidInputError, match="timestamp"):
        store.add_bookmark("abc123def45", -1)
    with pytest.raises(InvalidInputError, match="not found"):
        store.add_tag("missing", "tag")


def test_backup_round_trip_includes_user_curation(tmp_path: Path) -> None:
    catalog, store = _store(tmp_path / "source")
    store.set_annotation("abc123def45", note="Keep this", rating=4)
    store.add_tag("abc123def45", "favorite")
    collection_id = store.create_collection("Research")
    store.set_collection_video(collection_id, "abc123def45", add=True)
    store.add_bookmark("abc123def45", 12.0, label="Start")
    bundle = create_catalog_backup(catalog)

    destination = CatalogStore(tmp_path / "destination.sqlite")
    destination.ensure_schema()
    restore_catalog_backup(destination, bundle)

    restored = CurationStore(destination).list_all(video_id="abc123def45")
    assert restored["annotations"][0]["note"] == "Keep this"
    assert restored["tags"][0]["name"] == "favorite"
    assert restored["collections"][0]["name"] == "Research"
    assert restored["bookmarks"][0]["label"] == "Start"
