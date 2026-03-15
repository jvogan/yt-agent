from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import pytest

from yt_agent.catalog import CatalogStore, VideoUpsert
from yt_agent.models import ChapterEntry, SubtitleTrack, TranscriptSegment

TOTAL_VIDEOS = 1000
PAGE_SIZE = 100
MATCH_INTERVAL = 100
FTS_QUERY = "benchmarkneedle"
FTS_SEARCH_BUDGET_SECONDS = 1.0
TOTAL_BUDGET_SECONDS = 10.0


def _video_id(index: int) -> str:
    return f"vid{index:08d}"


def _video_upsert(tmp_path: Path, index: int, indexed_at: str) -> VideoUpsert:
    video_id = _video_id(index)
    return VideoUpsert(
        video_id=video_id,
        title=f"Catalog benchmark video {index:04d}",
        channel=f"Channel {index % 10:02d}",
        upload_date=f"2026-03-{(index % 28) + 1:02d}",
        duration_seconds=120 + (index % 300),
        extractor_key="youtube",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
        requested_input=None,
        source_query="catalog benchmark",
        output_path=tmp_path / "downloads" / f"{video_id}.mp4",
        info_json_path=tmp_path / "info" / f"{video_id}.info.json",
        downloaded_at=None,
        indexed_at=indexed_at,
    )


def _chapter(index: int) -> ChapterEntry:
    title = f"Scale chapter {index}"
    if index % MATCH_INTERVAL == 0:
        title = f"{title} {FTS_QUERY}"
    return ChapterEntry(
        position=0,
        title=title,
        start_seconds=0.0,
        end_seconds=30.0,
    )


def _transcript_track(tmp_path: Path, index: int) -> SubtitleTrack:
    video_id = _video_id(index)
    return SubtitleTrack(
        lang="en",
        source="manual",
        is_auto=False,
        format="vtt",
        file_path=tmp_path / "subtitles" / f"{video_id}.en.vtt",
    )


def _transcript_segment(index: int) -> TranscriptSegment:
    text = f"Transcript segment {index}"
    if index % MATCH_INTERVAL == 0:
        text = f"{text} {FTS_QUERY}"
    return TranscriptSegment(
        segment_index=0,
        start_seconds=0.0,
        end_seconds=5.0,
        text=text,
    )


@pytest.mark.slow
def test_catalog_store_handles_1000_videos_within_scale_budget(tmp_path: Path) -> None:
    started_at = perf_counter()
    store = CatalogStore(tmp_path / "catalog.sqlite")
    store.initialize()
    indexed_at = datetime.now(UTC).isoformat()

    expected_match_count = TOTAL_VIDEOS // MATCH_INTERVAL

    for index in range(TOTAL_VIDEOS):
        video_id = _video_id(index)
        store.upsert_video(_video_upsert(tmp_path, index, indexed_at))
        store.replace_chapters(video_id, [_chapter(index)])
        store.replace_transcripts(
            video_id,
            [(_transcript_track(tmp_path, index), [_transcript_segment(index)])],
        )

    all_videos = store.list_videos(limit=TOTAL_VIDEOS + 1)
    first_page = store.list_videos(limit=PAGE_SIZE)

    assert len(all_videos) == TOTAL_VIDEOS
    assert len(first_page) == PAGE_SIZE
    assert store.library_stats()["videos"] == TOTAL_VIDEOS

    search_started_at = perf_counter()
    hits = store.search_clips(FTS_QUERY, source="all", limit=expected_match_count * 2)
    search_duration = perf_counter() - search_started_at

    assert search_duration < FTS_SEARCH_BUDGET_SECONDS, (
        f"FTS search took {search_duration:.3f}s for {TOTAL_VIDEOS} videos"
    )
    assert len(hits) == expected_match_count * 2
    assert Counter(hit.source for hit in hits) == {
        "chapters": expected_match_count,
        "transcript": expected_match_count,
    }

    store.clear()
    stats = store.library_stats()

    assert stats["videos"] == 0
    assert stats["chapters"] == 0
    assert stats["subtitle_tracks"] == 0
    assert stats["transcript_segments"] == 0
    assert store.list_videos(limit=TOTAL_VIDEOS) == []
    assert store.search_clips(FTS_QUERY, source="all", limit=10) == []

    total_duration = perf_counter() - started_at
    assert total_duration < TOTAL_BUDGET_SECONDS, (
        f"Catalog scale benchmark took {total_duration:.3f}s overall"
    )
