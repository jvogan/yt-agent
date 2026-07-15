import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from yt_agent.backup import create_catalog_backup, restore_catalog_backup
from yt_agent.catalog import CATALOG_SCHEMA_VERSION, CatalogStore, VideoUpsert
from yt_agent.cli import app
from yt_agent.errors import InvalidInputError
from yt_agent.stats import refresh_stats, stats_history, stats_trends

runner = CliRunner()


def _seed(settings, *video_ids: str) -> CatalogStore:
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    for index, video_id in enumerate(video_ids):
        store.upsert_video(
            VideoUpsert(
                video_id=video_id,
                title=f"Video {index}",
                channel="Channel",
                upload_date=f"2026-07-{index + 1:02d}",
                duration_seconds=60,
                extractor_key="Youtube",
                webpage_url=f"https://www.youtube.com/watch?v={video_id}",
                requested_input=None,
                source_query=None,
                output_path=None,
                info_json_path=None,
                downloaded_at=None,
                indexed_at=datetime.now(UTC).isoformat(),
            )
        )
    return store


def test_stats_migration_creates_time_series_table(settings) -> None:
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()

    with store.connect(readonly=True) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'video_stats'"
        ).fetchone()

    assert version == CATALOG_SCHEMA_VERSION
    assert table is not None


def test_refresh_history_and_trends_store_nullable_counts(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seed(settings, "abc123def45")
    payloads = iter(
        [
            {"view_count": 100, "like_count": 10, "comment_count": None},
            {"view_count": 140, "like_count": 13, "comment_count": 4},
        ]
    )
    monkeypatch.setattr("yt_agent.stats.yt_dlp.fetch_info", lambda target: next(payloads))

    first = refresh_stats(store, ["abc123def45"])[0]
    second = refresh_stats(store, ["abc123def45"])[0]
    history = stats_history(store, "abc123def45")
    trend = stats_trends(store, ["abc123def45"])[0]

    assert first.snapshot_id is not None
    assert second.snapshot_id is not None
    assert [snapshot.view_count for snapshot in history] == [140, 100]
    assert trend.view_delta == 40
    assert trend.like_delta == 3
    assert trend.comment_delta is None


def test_stats_dry_run_is_bounded_and_does_not_fetch_or_write(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _seed(settings, "abc123def45", "def123abc45")
    monkeypatch.setattr(
        "yt_agent.stats.yt_dlp.fetch_info",
        lambda target: pytest.fail("dry-run fetched metadata"),
    )

    planned = refresh_stats(store, None, limit=1, dry_run=True)

    assert len(planned) == 1
    assert planned[0].snapshot_id is None
    with store.connect(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM video_stats").fetchone()[0] == 0
    with pytest.raises(InvalidInputError, match="batch limit"):
        refresh_stats(store, ["abc123def45", "def123abc45"], limit=1, dry_run=True)


def test_stats_snapshots_round_trip_in_lossless_backup(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    source = _seed(settings, "abc123def45")
    monkeypatch.setattr(
        "yt_agent.stats.yt_dlp.fetch_info",
        lambda target: {"view_count": 12, "like_count": 3, "comment_count": 1},
    )
    refresh_stats(source, ["abc123def45"])
    bundle = create_catalog_backup(source)
    destination = CatalogStore(tmp_path / "restored.sqlite")
    destination.ensure_schema()

    restore_catalog_backup(destination, bundle)

    assert stats_history(destination, "abc123def45")[0].view_count == 12


def test_stats_cli_refresh_show_and_trends(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(settings, "abc123def45")
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    payloads = iter(
        [
            {"view_count": 10, "like_count": 2, "comment_count": 1},
            {"view_count": 15, "like_count": 3, "comment_count": 1},
        ]
    )
    monkeypatch.setattr("yt_agent.stats.yt_dlp.fetch_info", lambda target: next(payloads))

    for _ in range(2):
        refreshed = runner.invoke(
            app,
            ["stats", "refresh", "abc123def45", "--output", "json"],
        )
        assert refreshed.exit_code == 0, refreshed.stdout
    shown = runner.invoke(
        app, ["stats", "show", "abc123def45", "--output", "json"]
    )
    trends = runner.invoke(
        app, ["stats", "trends", "abc123def45", "--output", "json"]
    )

    assert len(json.loads(shown.stdout)["snapshots"]) == 2
    assert json.loads(trends.stdout)["trends"][0]["view_delta"] == 5


def test_stats_refresh_cli_dry_run_avoids_network_and_state(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(settings, "abc123def45")
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.stats.yt_dlp.fetch_info", lambda target: pytest.fail("network used")
    )

    result = runner.invoke(
        app,
        [
            "stats",
            "refresh",
            "abc123def45",
            "--dry-run",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["summary"] == {"videos": 1, "dry_run": True, "provider": "yt-dlp"}
