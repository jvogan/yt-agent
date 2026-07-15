import sys

import pytest

from yt_agent.catalog import CatalogStore, VideoUpsert
from yt_agent.repair import _remove_stale_cache, repair_library


def _video(settings):
    return VideoUpsert(
        video_id="abc123def45",
        title="Demo",
        channel="Channel",
        upload_date=None,
        duration_seconds=10,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
        requested_input=None,
        source_query=None,
        output_path=None,
        info_json_path=None,
        downloaded_at=None,
        indexed_at="2026-01-01T00:00:00Z",
    )


def test_repair_previews_fts_and_stale_cache_without_writes(settings) -> None:
    settings.manifest_file.parent.mkdir(parents=True)
    settings.manifest_file.write_text("", encoding="utf-8")
    settings.archive_file.write_text("", encoding="utf-8")
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    store.upsert_video(_video(settings))
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO chapters (video_id, position, title, start_seconds) VALUES (?, ?, ?, ?)",
            ("abc123def45", 0, "Intro", 0.0),
        )
    stale = settings.catalog_file.parent / "subtitle-cache" / "orphan"
    stale.mkdir(parents=True)
    (stale / "sub.vtt").write_text("WEBVTT", encoding="utf-8")

    report = repair_library(settings)

    assert {item.action for item in report.actions} == {
        "rebuild_fts",
        "remove_stale_subtitle_cache",
    }
    assert stale.exists()
    assert report.as_dict()["media_deleted"] == 0


def test_repair_apply_rebuilds_fts_and_removes_only_stale_cache(settings) -> None:
    settings.manifest_file.parent.mkdir(parents=True)
    settings.manifest_file.write_text("", encoding="utf-8")
    settings.archive_file.write_text("", encoding="utf-8")
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    store.upsert_video(_video(settings))
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO chapters (video_id, position, title, start_seconds) VALUES (?, ?, ?, ?)",
            ("abc123def45", 0, "Repairable", 0.0),
        )
    stale = settings.catalog_file.parent / "subtitle-cache" / "orphan"
    stale.mkdir(parents=True)

    report = repair_library(settings, apply=True)

    assert all(item.status == "applied" for item in report.actions)
    assert not stale.exists()
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM chapter_fts").fetchone()[0] == 1


def test_repair_reindexes_manifest_when_catalog_is_missing(settings, monkeypatch) -> None:
    settings.manifest_file.parent.mkdir(parents=True)
    settings.manifest_file.write_text('{"video_id":"abc123def45"}\n', encoding="utf-8")
    settings.archive_file.write_text("", encoding="utf-8")
    called = []
    monkeypatch.setattr("yt_agent.repair.index_refresh", lambda current: called.append(current))

    preview = repair_library(settings)
    applied = repair_library(settings, apply=True)

    assert [item.action for item in preview.actions] == ["reindex_manifest"]
    assert [item.action for item in applied.actions] == ["reindex_manifest"]
    assert called == [settings]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_repair_refuses_symlinked_subtitle_cache_root(settings, tmp_path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    victim = external / "orphan"
    victim.mkdir()
    cache_root = settings.catalog_file.parent / "subtitle-cache"
    cache_root.parent.mkdir(parents=True)
    cache_root.symlink_to(external, target_is_directory=True)

    with pytest.raises(OSError, match="symlinked subtitle-cache"):
        _remove_stale_cache(settings, cache_root / "orphan")

    assert victim.exists()
