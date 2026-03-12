from datetime import UTC, datetime
from pathlib import Path

from yt_agent.catalog import CatalogStore, VideoUpsert, _fts_query
from yt_agent.models import ChapterEntry, SubtitleTrack, TranscriptSegment


def test_catalog_indexes_and_queries_video(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo Video",
            channel="Channel",
            upload_date="2026-03-07",
            duration_seconds=120,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input="https://www.youtube.com/watch?v=abc123def45",
            source_query=None,
            output_path=tmp_path / "demo.mp4",
            info_json_path=tmp_path / "demo.info.json",
            downloaded_at=datetime.now(UTC).isoformat(),
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    store.replace_chapters(
        "abc123def45",
        [
            ChapterEntry(position=0, title="Intro", start_seconds=0.0, end_seconds=15.0),
            ChapterEntry(position=1, title="Breakdown", start_seconds=15.0, end_seconds=45.0),
        ],
    )
    store.replace_transcripts(
        "abc123def45",
        [
            (
                SubtitleTrack(
                    lang="en",
                    source="manual",
                    is_auto=False,
                    format="vtt",
                    file_path=tmp_path / "demo.en.vtt",
                ),
                [
                    TranscriptSegment(
                        segment_index=0,
                        start_seconds=3.0,
                        end_seconds=8.0,
                        text="welcome back to the show",
                    )
                ],
            )
        ],
    )

    library_rows = store.list_videos(limit=5)
    assert len(library_rows) == 1
    assert library_rows[0].transcript_count == 1
    assert library_rows[0].chapter_count == 2

    title_rows = store.search_videos("Demo", limit=5)
    assert [row.video_id for row in title_rows] == ["abc123def45"]

    chapter_hits = store.search_clips("Intro", source="chapters", limit=5)
    assert [hit.result_id for hit in chapter_hits] == ["chapter:1"]

    transcript_hits = store.search_clips("welcome", source="transcript", limit=5)
    assert transcript_hits[0].result_id == "transcript:1"
    assert "welcome" in transcript_hits[0].match_text

    details = store.get_video_details("abc123def45")
    assert details is not None
    assert details["video"].title == "Demo Video"
    assert len(details["chapters"]) == 2
    assert len(details["subtitle_tracks"]) == 1


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.ensure_schema()
    store.ensure_schema()
    store.upsert_video(
        VideoUpsert(
            video_id="test123test1",
            title="Test",
            channel="Channel",
            upload_date=None,
            duration_seconds=None,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=test123test1",
            requested_input=None,
            source_query=None,
            output_path=None,
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    assert store.get_video("test123test1") is not None


def test_list_videos_returns_no_nones(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    for i in range(3):
        store.upsert_video(
            VideoUpsert(
                video_id=f"vid{i:09d}00",
                title=f"Video {i}",
                channel="Channel",
                upload_date=None,
                duration_seconds=None,
                extractor_key="youtube",
                webpage_url=f"https://www.youtube.com/watch?v=vid{i:09d}00",
                requested_input=None,
                source_query=None,
                output_path=None,
                info_json_path=None,
                downloaded_at=None,
                indexed_at=datetime.now(UTC).isoformat(),
            )
        )
    rows = store.list_videos(limit=10)
    assert len(rows) == 3
    assert all(row is not None for row in rows)


def test_list_videos_filter_by_channel(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    for vid_id, ch in [("abc123def45", "Alpha"), ("def123abc45", "Beta")]:
        store.upsert_video(
            VideoUpsert(
                video_id=vid_id,
                title=f"Video by {ch}",
                channel=ch,
                upload_date=None,
                duration_seconds=None,
                extractor_key="youtube",
                webpage_url=f"https://www.youtube.com/watch?v={vid_id}",
                requested_input=None,
                source_query=None,
                output_path=None,
                info_json_path=None,
                downloaded_at=None,
                indexed_at=datetime.now(UTC).isoformat(),
            )
        )
    rows = store.list_videos(channel="Alpha", limit=10)
    assert len(rows) == 1
    assert rows[0].channel == "Alpha"


def test_list_channels_returns_distinct_sorted(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    for vid_id, ch in [("abc123def45", "Zeta"), ("def123abc45", "Alpha"), ("ghi123jkl45", "Zeta")]:
        store.upsert_video(
            VideoUpsert(
                video_id=vid_id,
                title="Test",
                channel=ch,
                upload_date=None,
                duration_seconds=None,
                extractor_key="youtube",
                webpage_url=f"https://www.youtube.com/watch?v={vid_id}",
                requested_input=None,
                source_query=None,
                output_path=None,
                info_json_path=None,
                downloaded_at=None,
                indexed_at=datetime.now(UTC).isoformat(),
            )
        )
    channels = store.list_channels()
    assert channels == ["Alpha", "Zeta"]


def test_list_playlists_returns_entry_counts(tmp_path: Path) -> None:
    from yt_agent.catalog import PlaylistUpsert

    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo",
            channel="Channel",
            upload_date=None,
            duration_seconds=None,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input=None,
            source_query=None,
            output_path=None,
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    store.upsert_playlist_entry(
        PlaylistUpsert(
            playlist_id="PL123",
            title="My Playlist",
            channel="Channel",
            webpage_url=None,
            position=1,
        ),
        "abc123def45",
    )
    playlists = store.list_playlists()
    assert len(playlists) == 1
    assert playlists[0]["title"] == "My Playlist"
    assert playlists[0]["entry_count"] == "1"


def test_library_stats_returns_correct_counts(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo",
            channel="Channel",
            upload_date=None,
            duration_seconds=None,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input=None,
            source_query=None,
            output_path=tmp_path / "demo.mp4",
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    stats = store.library_stats()
    assert stats["videos"] == 1
    assert stats["local_media"] == 1
    assert stats["channels"] == 1


def test_get_clip_hit_returns_none_for_unknown_id(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    assert store.get_clip_hit("chapter:999") is None
    assert store.get_clip_hit("transcript:999") is None
    assert store.get_clip_hit("bad") is None


def test_catalog_clear_removes_all_data(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo",
            channel="Channel",
            upload_date=None,
            duration_seconds=None,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input=None,
            source_query=None,
            output_path=None,
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    assert store.library_stats()["videos"] == 1
    store.clear()
    assert store.library_stats()["videos"] == 0


def test_search_videos_uses_sql_filtering(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Python Tutorial",
            channel="Channel",
            upload_date=None,
            duration_seconds=None,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input=None,
            source_query=None,
            output_path=None,
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    store.upsert_video(
        VideoUpsert(
            video_id="def123abc45",
            title="Rust Guide",
            channel="Channel",
            upload_date=None,
            duration_seconds=None,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=def123abc45",
            requested_input=None,
            source_query=None,
            output_path=None,
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    results = store.search_videos("Python", limit=10)
    assert len(results) == 1
    assert results[0].title == "Python Tutorial"

    results = store.search_videos("", limit=10)
    assert len(results) == 2


def test_search_videos_escapes_like_wildcards(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    for video_id, title in [
        ("abc123def45", "100% coverage"),
        ("def123abc45", "100 percent"),
        ("ghi123jkl45", "name_with_underscores"),
        ("jkl123mno45", "nameXwithXunderscores"),
    ]:
        store.upsert_video(
            VideoUpsert(
                video_id=video_id,
                title=title,
                channel="Channel",
                upload_date=None,
                duration_seconds=None,
                extractor_key="youtube",
                webpage_url=f"https://www.youtube.com/watch?v={video_id}",
                requested_input=None,
                source_query=None,
                output_path=None,
                info_json_path=None,
                downloaded_at=None,
                indexed_at=datetime.now(UTC).isoformat(),
            )
        )

    percent_results = store.search_videos("100%", limit=10)
    underscore_results = store.search_videos("name_with_", limit=10)

    assert [row.video_id for row in percent_results] == ["abc123def45"]
    assert [row.video_id for row in underscore_results] == ["ghi123jkl45"]


def test_delete_video_removes_video_and_fts_entries(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo Video",
            channel="Channel",
            upload_date=None,
            duration_seconds=120,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input=None,
            source_query=None,
            output_path=None,
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    store.replace_chapters(
        "abc123def45",
        [ChapterEntry(position=0, title="Intro", start_seconds=0.0, end_seconds=15.0)],
    )
    store.replace_transcripts(
        "abc123def45",
        [
            (
                SubtitleTrack(
                    lang="en", source="manual", is_auto=False, format="vtt",
                    file_path=tmp_path / "demo.en.vtt",
                ),
                [TranscriptSegment(segment_index=0, start_seconds=0.0, end_seconds=5.0, text="hello world")],
            )
        ],
    )
    assert store.delete_video("abc123def45") is True
    assert store.get_video("abc123def45") is None
    assert store.library_stats()["videos"] == 0
    assert store.search_clips("Intro", source="chapters") == []
    assert store.search_clips("hello", source="transcript") == []


def test_delete_video_returns_false_for_unknown_id(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    assert store.delete_video("doesnotexist00") is False


def _indexed_store(tmp_path: Path) -> CatalogStore:
    """Create a store with one video, chapters, and transcripts for filter tests."""
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo Video",
            channel="Alpha",
            upload_date="2026-03-07",
            duration_seconds=120,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input=None,
            source_query=None,
            output_path=tmp_path / "demo.mp4",
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    store.replace_chapters(
        "abc123def45",
        [ChapterEntry(position=0, title="Intro segment", start_seconds=0.0, end_seconds=15.0)],
    )
    store.replace_transcripts(
        "abc123def45",
        [
            (
                SubtitleTrack(
                    lang="en",
                    source="manual",
                    is_auto=False,
                    format="vtt",
                    file_path=tmp_path / "demo.en.vtt",
                ),
                [
                    TranscriptSegment(
                        segment_index=0,
                        start_seconds=3.0,
                        end_seconds=8.0,
                        text="welcome back to the show",
                    )
                ],
            )
        ],
    )
    return store


def test_search_clips_returns_empty_on_stripped_query(tmp_path: Path) -> None:
    store = _indexed_store(tmp_path)
    assert store.search_clips("***") == []
    assert store.search_clips("   ") == []


def test_search_clips_filters_by_channel(tmp_path: Path) -> None:
    store = _indexed_store(tmp_path)
    hits = store.search_clips("Intro", source="chapters", channel="Alpha")
    assert len(hits) == 1
    hits = store.search_clips("Intro", source="chapters", channel="Beta")
    assert len(hits) == 0


def test_search_clips_filters_by_language(tmp_path: Path) -> None:
    store = _indexed_store(tmp_path)
    hits = store.search_clips("welcome", source="transcript", language="en")
    assert len(hits) == 1
    hits = store.search_clips("welcome", source="transcript", language="fr")
    assert len(hits) == 0


def test_search_clips_supports_documented_language_wildcards(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo Video",
            channel="Alpha",
            upload_date="2026-03-07",
            duration_seconds=120,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input=None,
            source_query=None,
            output_path=tmp_path / "demo.mp4",
            info_json_path=None,
            downloaded_at=None,
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    store.replace_transcripts(
        "abc123def45",
        [
            (
                SubtitleTrack(
                    lang="en-US",
                    source="manual",
                    is_auto=False,
                    format="vtt",
                    file_path=tmp_path / "demo.en-US.vtt",
                ),
                [
                    TranscriptSegment(
                        segment_index=0,
                        start_seconds=3.0,
                        end_seconds=8.0,
                        text="welcome back to the show",
                    )
                ],
            )
        ],
    )

    assert len(store.search_clips("welcome", source="transcript", language="en%")) == 1
    assert len(store.search_clips("welcome", source="transcript", language="en.*")) == 1
    assert len(store.search_clips("welcome", source="transcript", language="en*")) == 1
    assert len(store.search_clips("welcome", source="transcript", language="fr%")) == 0


def test_search_videos_filters_by_channel(tmp_path: Path) -> None:
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    for vid_id, ch in [("abc123def45", "Alpha"), ("def123abc45", "Beta")]:
        store.upsert_video(
            VideoUpsert(
                video_id=vid_id,
                title="Demo",
                channel=ch,
                upload_date=None,
                duration_seconds=None,
                extractor_key="youtube",
                webpage_url=f"https://www.youtube.com/watch?v={vid_id}",
                requested_input=None,
                source_query=None,
                output_path=None,
                info_json_path=None,
                downloaded_at=None,
                indexed_at=datetime.now(UTC).isoformat(),
            )
        )
    results = store.search_videos("Demo", channel="Alpha", limit=10)
    assert len(results) == 1
    assert results[0].channel == "Alpha"


def test_search_videos_filters_by_has_transcript(tmp_path: Path) -> None:
    store = _indexed_store(tmp_path)
    results = store.search_videos("Demo", has_transcript=True, limit=10)
    assert len(results) == 1
    results = store.search_videos("Demo", has_transcript=False, limit=10)
    assert len(results) == 0


def test_fts_query_returns_empty_for_special_char_input() -> None:
    assert _fts_query("***") == ""
    assert _fts_query("   ") == ""
    assert _fts_query("") == ""


def test_fts_query_quotes_valid_tokens() -> None:
    assert _fts_query("hello world") == '"hello" "world"'
    assert _fts_query("hello-world") == '"hello-world"'


def test_fts_query_strips_punctuation_before_quoting_tokens() -> None:
    assert _fts_query('hello, "world"!') == '"hello" "world"'
    assert _fts_query("chapter-title / demo") == '"chapter-title" "demo"'
