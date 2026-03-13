import json
from dataclasses import replace
from pathlib import Path

from yt_agent.catalog import CatalogStore
from yt_agent.catalog import VideoUpsert
from yt_agent.indexer import (
    _index_transcripts,
    _load_info_json,
    _playlist_id_from_payload,
    _split_languages,
    _subtitle_cache_root,
    index_manifest_record,
    index_refresh,
    index_target,
)
from yt_agent.manifest import append_manifest_record
from yt_agent.models import DownloadTarget, ManifestRecord, VideoInfo


def _target() -> DownloadTarget:
    info = VideoInfo(
        video_id="abc123def45",
        title="Demo Video",
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=120,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    return DownloadTarget(original_input=info.webpage_url, info=info)


def _upsert_video(catalog: CatalogStore, info: VideoInfo, *, requested_input: str | None = None) -> None:
    catalog.upsert_video(
        VideoUpsert(
            video_id=info.video_id,
            title=info.title,
            channel=info.channel,
            upload_date=info.upload_date,
            duration_seconds=info.duration_seconds,
            extractor_key=info.extractor_key,
            webpage_url=info.webpage_url,
            requested_input=requested_input or info.webpage_url,
            source_query=None,
            output_path=None,
            info_json_path=None,
            downloaded_at=None,
            indexed_at="2026-03-13T00:00:00+00:00",
        )
    )


def test_index_refresh_backfills_from_manifest_and_sidecars(settings) -> None:
    output_path = settings.download_root / "Channel" / "2026-03-07 - Demo Video [abc123def45].mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"video")
    info_json_path = Path(f"{output_path}.info.json")
    info_json_path.write_text(
        json.dumps(
            {
                "id": "abc123def45",
                "title": "Demo Video",
                "channel": "Channel",
                "duration": 120,
                "upload_date": "20260307",
                "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                "extractor_key": "youtube",
                "chapters": [{"title": "Intro", "start_time": 0, "end_time": 10}],
            }
        ),
        encoding="utf-8",
    )
    Path(f"{output_path}.en.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello there\n",
        encoding="utf-8",
    )

    record = ManifestRecord.from_download(_target(), output_path=output_path, info_json_path=info_json_path)
    append_manifest_record(settings.manifest_file, record)

    summary = index_refresh(settings)
    assert summary.videos == 1
    assert summary.chapters == 1
    assert summary.transcript_segments == 1

    summary = index_refresh(settings)
    assert summary.videos == 1
    assert summary.chapters == 1
    assert summary.transcript_segments == 1

    store = CatalogStore(settings.catalog_file)
    details = store.get_video_details("abc123def45")
    assert details is not None
    assert len(details["chapters"]) == 1
    assert len(details["subtitle_tracks"]) == 1


def test_load_info_json_returns_none_for_corrupt_file(tmp_path: Path) -> None:
    corrupt = tmp_path / "bad.info.json"
    corrupt.write_text("{not valid json", encoding="utf-8")
    assert _load_info_json(corrupt) is None

    assert _load_info_json(None) is None
    assert _load_info_json(tmp_path / "missing.json") is None


def test_subtitle_cache_root_sanitizes_video_id(settings) -> None:
    info = VideoInfo(
        video_id="../../escape",
        title="Demo Video",
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=120,
        extractor_key="generic",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    cache_root = _subtitle_cache_root(settings, info)
    assert cache_root.parent == settings.catalog_file.parent / "subtitle-cache"
    assert cache_root.name == "escape"


def test_split_languages_prefers_override_and_filters_empty_values(settings) -> None:
    custom_settings = replace(settings, subtitle_languages=" en.*, en , , fr ,, ")

    assert _split_languages(custom_settings) == ["en.*", "en", "fr"]
    assert _split_languages(custom_settings, override=" es , , de ,, ") == ["es", "de"]


def test_index_transcripts_fetches_remote_sidecars_and_skips_missing_files(settings, monkeypatch) -> None:
    import yt_agent.indexer as indexer

    catalog = CatalogStore(settings.catalog_file)
    catalog.ensure_schema()
    info = _target().info
    _upsert_video(catalog, info)

    cache_root = settings.catalog_file.parent / "fetched"
    cache_root.mkdir(parents=True, exist_ok=True)
    fetched_info_json = cache_root / f"{info.video_id}.info.json"
    fetched_info_json.write_text(
        json.dumps(
            {
                "subtitles": {"en": [{"ext": "vtt"}]},
                "automatic_captions": {"es": [{"ext": "vtt"}]},
            }
        ),
        encoding="utf-8",
    )
    manual_subtitle = cache_root / f"{info.video_id}.en.vtt"
    manual_subtitle.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nmanual line\n",
        encoding="utf-8",
    )
    auto_subtitle = cache_root / f"{info.video_id}.es.vtt"
    auto_subtitle.write_text(
        "WEBVTT\n\n00:00:04.000 --> 00:00:06.000\nauto line\n",
        encoding="utf-8",
    )
    missing_subtitle = cache_root / f"{info.video_id}.fr.vtt"
    fetch_calls: list[dict[str, object]] = []

    def fake_fetch_subtitle_sidecars(target: str, destination: Path, *, languages: list[str], allow_auto_subs: bool):
        fetch_calls.append(
            {
                "target": target,
                "destination": destination,
                "languages": languages,
                "allow_auto_subs": allow_auto_subs,
            }
        )
        return fetched_info_json, [missing_subtitle, manual_subtitle, auto_subtitle]

    monkeypatch.setattr(indexer, "fetch_subtitle_sidecars", fake_fetch_subtitle_sidecars)

    segments = _index_transcripts(
        catalog,
        info,
        media_path=None,
        info_json_path=None,
        settings=settings,
        fetch_subs=True,
        auto_subs=True,
        lang=" en , , es ,, ",
    )

    assert segments == 2
    assert fetch_calls == [
        {
            "target": info.webpage_url,
            "destination": _subtitle_cache_root(settings, info),
            "languages": ["en", "es"],
            "allow_auto_subs": True,
        }
    ]

    details = catalog.get_video_details(info.video_id)
    assert details is not None
    tracks = {track.lang: track for track in details["subtitle_tracks"]}
    assert set(tracks) == {"en", "es"}
    assert tracks["en"].is_auto is False
    assert tracks["es"].is_auto is True
    assert [segment.text for segment in details["transcript_preview"]] == ["manual line", "auto line"]


def test_index_manifest_record_discovers_info_json_when_record_path_is_missing(settings, monkeypatch) -> None:
    import yt_agent.indexer as indexer

    output_path = settings.download_root / "Channel" / "manifest-only.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"video")
    info_json_path = output_path.with_suffix(".info.json")
    info_json_path.write_text(
        json.dumps(
            {
                "id": "abc123def45",
                "title": "Discovered Title",
                "channel": "Discovered Channel",
                "duration": 120,
                "upload_date": "20260307",
                "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                "extractor_key": "youtube",
            }
        ),
        encoding="utf-8",
    )
    record = ManifestRecord(
        video_id="abc123def45",
        title="Manifest Title",
        channel="Manifest Channel",
        upload_date="2026-03-07",
        duration_seconds=120,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
        output_path=str(output_path),
        requested_input="https://www.youtube.com/watch?v=abc123def45",
        source_query=None,
        downloaded_at="2026-03-13T00:00:00+00:00",
        info_json_path=None,
    )
    calls: list[Path] = []

    def fake_discover_info_json(path: Path) -> Path | None:
        calls.append(path)
        return info_json_path

    monkeypatch.setattr(indexer, "discover_info_json", fake_discover_info_json)

    summary = index_manifest_record(settings, record)

    assert summary.videos == 1
    assert calls == [output_path]
    video = CatalogStore(settings.catalog_file).get_video("abc123def45")
    assert video is not None
    assert video.title == "Discovered Title"
    assert video.channel == "Discovered Channel"
    assert video.info_json_path == info_json_path


def test_playlist_id_from_payload_prefers_explicit_ids_and_falls_back_to_hash() -> None:
    assert _playlist_id_from_payload({"id": "PL123"}, "https://example.com/playlist") == "PL123"
    assert _playlist_id_from_payload({"playlist_id": "PL456"}, "https://example.com/playlist") == "PL456"
    assert _playlist_id_from_payload({}, "https://example.com/playlist") == (
        f"playlist:{abs(hash('https://example.com/playlist'))}"
    )


def test_index_target_indexes_playlist_entries_and_preserves_positions(settings, monkeypatch) -> None:
    import yt_agent.indexer as indexer

    playlist_payload = {
        "id": "PL123",
        "title": "Playlist Title",
        "channel": "Playlist Channel",
        "webpage_url": "https://www.youtube.com/playlist?list=PL123",
        "entries": [
            {
                "id": "video-one",
                "title": "First Video",
                "channel": "Video Channel",
                "duration": 10,
                "upload_date": "20260307",
                "extractor_key": "youtube",
                "webpage_url": "https://www.youtube.com/watch?v=video-one",
            },
            None,
            {
                "id": "video-two",
                "title": "Second Video",
                "uploader": "Uploader Name",
                "duration": 20,
                "upload_date": "20260308",
                "extractor": "youtube",
                "webpage_url": "https://www.youtube.com/watch?v=video-two",
            },
        ],
    }

    monkeypatch.setattr(indexer.yt_dlp, "fetch_info", lambda target: playlist_payload)

    summary = index_target(settings, "https://www.youtube.com/playlist?list=PL123")

    assert summary.videos == 2
    assert summary.playlists == 1
    store = CatalogStore(settings.catalog_file)
    assert {video.video_id for video in store.list_videos(limit=10)} == {"video-one", "video-two"}
    with store.connect(readonly=True) as conn:
        playlist_row = conn.execute("SELECT * FROM playlists").fetchone()
        playlist_entries = conn.execute(
            "SELECT playlist_id, video_id, position FROM playlist_entries ORDER BY position"
        ).fetchall()

    assert playlist_row is not None
    assert playlist_row["playlist_id"] == "PL123"
    assert playlist_row["title"] == "Playlist Title"
    assert playlist_row["channel"] == "Playlist Channel"
    assert playlist_row["webpage_url"] == "https://www.youtube.com/playlist?list=PL123"
    assert [(row["video_id"], row["position"]) for row in playlist_entries] == [
        ("video-one", 1),
        ("video-two", 3),
    ]


def test_index_target_indexes_single_video_payload_without_playlist_entry(settings, monkeypatch) -> None:
    import yt_agent.indexer as indexer

    video_payload = {
        "id": "solo-video",
        "title": "Solo Video",
        "channel": "Solo Channel",
        "duration": 30,
        "upload_date": "20260309",
        "extractor_key": "youtube",
        "webpage_url": "https://www.youtube.com/watch?v=solo-video",
    }

    monkeypatch.setattr(indexer.yt_dlp, "fetch_info", lambda target: video_payload)

    summary = index_target(settings, "https://www.youtube.com/watch?v=solo-video")

    assert summary.videos == 1
    assert summary.playlists == 0
    store = CatalogStore(settings.catalog_file)
    video = store.get_video("solo-video")
    assert video is not None
    with store.connect(readonly=True) as conn:
        playlist_entry_count = conn.execute("SELECT COUNT(*) FROM playlist_entries").fetchone()[0]
    assert playlist_entry_count == 0
