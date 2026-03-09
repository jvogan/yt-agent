from datetime import UTC, datetime
from pathlib import Path

from yt_agent.catalog import CatalogStore, VideoUpsert
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
