import json
import sqlite3
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import yt_agent.backup as backup_module
from yt_agent.backup import (
    BACKUP_FORMAT,
    BACKUP_VERSION,
    create_catalog_backup,
    restore_catalog_backup,
    validate_catalog_backup,
)
from yt_agent.catalog import CatalogStore, CommentUpsert, PlaylistUpsert, VideoUpsert
from yt_agent.cli import app
from yt_agent.errors import InvalidInputError
from yt_agent.models import ChapterEntry, SubtitleTrack, TranscriptSegment

runner = CliRunner()


def _seed(store: CatalogStore, root: Path, *, video_id: str = "abc123def45") -> None:
    store.ensure_schema()
    store.upsert_video(
        VideoUpsert(
            video_id=video_id,
            title="Demo",
            channel="Channel",
            upload_date="2026-07-10",
            duration_seconds=120,
            extractor_key="Youtube",
            webpage_url=f"https://www.youtube.com/watch?v={video_id}",
            requested_input="saved search",
            source_query="demo query",
            output_path=root / "media" / "demo.mp4",
            info_json_path=root / "media" / "demo.info.json",
            downloaded_at=datetime.now(UTC).isoformat(),
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    store.replace_chapters(
        video_id,
        [ChapterEntry(position=0, title="Intro", start_seconds=0.0, end_seconds=10.5)],
    )
    store.replace_transcripts(
        video_id,
        [
            (
                SubtitleTrack(
                    lang="en",
                    source="indexed-sidecar",
                    is_auto=False,
                    format="vtt",
                    file_path=root / "subs" / "demo.en.vtt",
                ),
                [TranscriptSegment(0, 1.25, 2.5, "hello searchable world")],
            )
        ],
    )
    store.upsert_playlist_entry(
        PlaylistUpsert(
            playlist_id="PL123",
            title="Playlist",
            channel="Channel",
            webpage_url="https://www.youtube.com/playlist?list=PL123",
            position=4,
        ),
        video_id,
    )
    store.replace_comments(
        video_id,
        [CommentUpsert("comment-1", "Alice", "backup searchable", None, 3, None)],
    )
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO video_curation (video_id, note, rating, updated_at) VALUES (?, ?, ?, ?)",
            (video_id, "Important", 5, datetime.now(UTC).isoformat()),
        )
        tag_id = conn.execute("INSERT INTO tags (name) VALUES (?)", ("research",)).lastrowid
        conn.execute("INSERT INTO video_tags (video_id, tag_id) VALUES (?, ?)", (video_id, tag_id))
        collection_id = conn.execute(
            "INSERT INTO collections (name, description, created_at) VALUES (?, ?, ?)",
            ("Watch later", "Curated", datetime.now(UTC).isoformat()),
        ).lastrowid
        conn.execute(
            "INSERT INTO collection_videos (collection_id, video_id, position) VALUES (?, ?, ?)",
            (collection_id, video_id, 1),
        )
        conn.execute(
            "INSERT INTO bookmarks (video_id, timestamp_seconds, label, note, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (video_id, 12.5, "Key point", "Review", datetime.now(UTC).isoformat()),
        )


def test_catalog_backup_round_trip_preserves_all_records_and_search(tmp_path: Path) -> None:
    source = CatalogStore(tmp_path / "source.sqlite")
    _seed(source, tmp_path)

    bundle = create_catalog_backup(source)
    summary = validate_catalog_backup(bundle)
    destination = CatalogStore(tmp_path / "destination.sqlite")
    destination.ensure_schema()
    restored = restore_catalog_backup(destination, bundle)
    round_tripped = create_catalog_backup(destination)

    assert bundle["format"] == BACKUP_FORMAT
    assert bundle["version"] == BACKUP_VERSION
    assert summary == restored
    assert summary.as_dict() == {
        "videos": 1,
        "chapters": 1,
        "subtitle_tracks": 1,
        "transcript_segments": 1,
        "playlists": 1,
        "playlist_entries": 1,
    }
    assert round_tripped["catalog"] == bundle["catalog"]
    assert destination.search_clips("searchable", source="transcript")[0].video_id == (
        "abc123def45"
    )
    assert destination.search_clips("Intro", source="chapters")[0].video_id == "abc123def45"
    assert destination.search_comments("backup")[0]["comment_id"] == "comment-1"


@pytest.mark.parametrize("version", [1, 2, 3])
def test_restore_accepts_older_backup_versions(tmp_path: Path, version: int) -> None:
    source = CatalogStore(tmp_path / "source.sqlite")
    _seed(source, tmp_path)
    bundle = create_catalog_backup(source)
    bundle["version"] = version
    bundle["catalog"].pop("video_stats")
    if version < 3:
        bundle["catalog"].pop("comments")
    if version == 1:
        for collection in (
            "video_curation",
            "tags",
            "video_tags",
            "collections",
            "collection_videos",
            "bookmarks",
        ):
            bundle["catalog"].pop(collection)
    destination = CatalogStore(tmp_path / f"destination-{version}.sqlite")
    destination.ensure_schema()

    restore_catalog_backup(destination, bundle)

    assert destination.get_video("abc123def45") is not None
    assert bool(destination.search_comments("backup")) is (version >= 3)


def test_restore_dry_run_validates_without_mutating(tmp_path: Path) -> None:
    source = CatalogStore(tmp_path / "source.sqlite")
    _seed(source, tmp_path)
    bundle = create_catalog_backup(source)
    destination = CatalogStore(tmp_path / "destination.sqlite")
    _seed(destination, tmp_path, video_id="existing1234")

    summary = restore_catalog_backup(destination, bundle, dry_run=True)

    assert summary.videos == 1
    assert destination.get_video("existing1234") is not None
    assert destination.get_video("abc123def45") is None


def test_restore_rejects_invalid_relationship_before_mutating(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    _seed(store, tmp_path, video_id="existing1234")
    bundle = create_catalog_backup(store)
    bundle["catalog"]["chapters"][0]["video_id"] = "missing-video"

    with pytest.raises(InvalidInputError, match="unknown video"):
        restore_catalog_backup(store, bundle)

    assert store.get_video("existing1234") is not None


def test_restore_rolls_back_delete_when_insert_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    _seed(store, tmp_path, video_id="existing1234")
    bundle = create_catalog_backup(store)

    def fail_insert(conn, records):
        raise sqlite3.IntegrityError("injected failure")

    monkeypatch.setattr(backup_module, "_insert_records", fail_insert)

    with pytest.raises(InvalidInputError, match="violates catalog constraints"):
        restore_catalog_backup(store, bundle)

    assert store.get_video("existing1234") is not None


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda bundle: bundle.update(version=999), "Unsupported"),
        (lambda bundle: bundle["catalog"]["videos"][0].pop("title"), "invalid fields"),
        (
            lambda bundle: bundle["catalog"]["transcript_segments"][0].update(
                start_seconds=float("nan")
            ),
            "finite number",
        ),
    ],
)
def test_validate_catalog_backup_rejects_unsupported_or_lossy_payloads(
    tmp_path: Path, mutation, match: str
) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    _seed(store, tmp_path)
    bundle = create_catalog_backup(store)
    mutation(bundle)

    with pytest.raises(InvalidInputError, match=match):
        validate_catalog_backup(bundle)


def test_backup_create_and_restore_commands_round_trip(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = CatalogStore(settings.catalog_file)
    _seed(store, tmp_path)
    backup_path = tmp_path / "backup.json"
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    created = runner.invoke(app, ["backup", "create", str(backup_path), "--output", "json"])
    assert created.exit_code == 0, created.output
    assert backup_path.is_file()
    assert json.loads(created.stdout)["summary"]["transcript_segments"] == 1

    store.clear()
    restored = runner.invoke(app, ["backup", "restore", str(backup_path), "--output", "json"])
    assert restored.exit_code == 0, restored.output
    assert json.loads(restored.stdout)["summary"]["dry_run"] is False
    assert CatalogStore(settings.catalog_file).get_video("abc123def45") is not None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission modes only")
def test_backup_create_preserves_shared_directory_permissions(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = CatalogStore(settings.catalog_file)
    _seed(store, tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    sibling = shared / "sibling.txt"
    sibling.write_text("keep\n", encoding="utf-8")
    sibling.chmod(0o644)
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["backup", "create", str(shared / "backup.json")])

    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert stat.S_IMODE(sibling.stat().st_mode) == 0o644


def test_backup_restore_command_dry_run_does_not_prepare_storage(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = CatalogStore(tmp_path / "source.sqlite")
    _seed(source, tmp_path)
    backup_path = tmp_path / "backup.json"
    backup_path.write_text(json.dumps(create_catalog_backup(source)), encoding="utf-8")
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._prepare_storage",
        lambda configured: pytest.fail("dry-run prepared storage"),
    )

    result = runner.invoke(
        app,
        ["backup", "restore", str(backup_path), "--dry-run", "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["summary"]["dry_run"] is True
    assert not settings.catalog_file.exists()
