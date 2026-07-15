import json
import stat

import pytest

from yt_agent.errors import InvalidInputError
from yt_agent.sync import SourceStore, run_sync, source_store_path


def _payload():
    return {
        "entries": [
            {
                "id": "new123abc45",
                "title": "New",
                "channel": "Channel",
                "upload_date": "20260709",
                "extractor_key": "youtube",
            },
            {
                "id": "old123abc45",
                "title": "Old",
                "channel": "Channel",
                "upload_date": "20250101",
                "extractor_key": "youtube",
            },
        ]
    }


def _store_with_source(settings):
    store = SourceStore(source_store_path(settings))
    store.add("research", "channel", "https://www.youtube.com/@example/videos")
    return store


def test_source_store_add_list_remove_and_private_permissions(settings) -> None:
    store = _store_with_source(settings)

    sources = store.list()

    assert [(item.name, item.kind) for item in sources] == [("research", "channel")]
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert store.remove("RESEARCH") is True
    assert store.list() == []
    assert store.remove("missing") is False


def test_source_store_rejects_duplicate_and_non_youtube_url(settings) -> None:
    store = _store_with_source(settings)

    with pytest.raises(InvalidInputError, match="already exists"):
        store.add("Research", "playlist", "https://www.youtube.com/playlist?list=PL123")
    with pytest.raises(InvalidInputError, match="Only YouTube URLs"):
        store.add("other", "channel", "https://example.com/channel")
    with pytest.raises(InvalidInputError, match="Channel sources"):
        store.add("video", "channel", "abc123def45")
    with pytest.raises(InvalidInputError, match="Playlist sources"):
        store.add("not-playlist", "playlist", "https://www.youtube.com/@example/videos")


def test_sync_dry_run_fetches_but_does_not_update_state(settings) -> None:
    store = _store_with_source(settings)
    before = store.path.read_text(encoding="utf-8")

    report = run_sync(
        settings,
        names=["research"],
        latest=1,
        download=True,
        dry_run=True,
        fetch_info_fn=lambda _: _payload(),
    )

    assert [item.status for item in report.items] == ["would_download"]
    assert report.items[0].video_id == "new123abc45"
    assert store.path.read_text(encoding="utf-8") == before


def test_sync_is_incremental_and_applies_since(settings) -> None:
    store = _store_with_source(settings)
    indexed: list[str] = []

    first = run_sync(
        settings,
        since="2026-01-01",
        fetch_info_fn=lambda _: _payload(),
        index_fn=lambda settings, target, payload: indexed.append(target.info.video_id),
    )
    second = run_sync(
        settings,
        since="2026-01-01",
        fetch_info_fn=lambda _: _payload(),
        index_fn=lambda settings, target, payload: indexed.append(target.info.video_id),
    )

    assert [item.status for item in first.items] == ["indexed"]
    assert second.items == ()
    assert indexed == ["new123abc45"]
    saved = store.list()[0]
    assert saved.seen_video_ids == ("new123abc45",)
    assert saved.last_synced_at is not None


def test_sync_download_is_archive_aware_and_can_still_index(settings) -> None:
    _store_with_source(settings)
    settings.archive_file.write_text("youtube new123abc45\n", encoding="utf-8")
    indexed: list[str] = []
    downloaded: list[str] = []

    report = run_sync(
        settings,
        latest=1,
        download=True,
        fetch_info_fn=lambda _: _payload(),
        index_fn=lambda settings, target, payload: indexed.append(target.info.video_id),
        download_fn=lambda settings, target, index_after: downloaded.append(target.info.video_id),
    )

    assert [item.status for item in report.items] == ["indexed_archived"]
    assert indexed == ["new123abc45"]
    assert downloaded == []


def test_sync_validates_filters_and_source_names(settings) -> None:
    _store_with_source(settings)

    with pytest.raises(InvalidInputError, match="YYYY-MM-DD"):
        run_sync(settings, since="yesterday")
    with pytest.raises(InvalidInputError, match="Unknown saved source"):
        run_sync(settings, names=["missing"])
    with pytest.raises(InvalidInputError, match="requires --index or --download"):
        run_sync(settings, index=False, download=False)


def test_sync_report_json_is_structured(settings) -> None:
    _store_with_source(settings)
    report = run_sync(
        settings,
        latest=1,
        dry_run=True,
        fetch_info_fn=lambda _: _payload(),
    )

    payload = report.as_dict()
    assert payload["command"] == "sync run"
    assert payload["summary"]["would_index"] == 1
    assert json.dumps(payload)
