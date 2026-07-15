from yt_agent.archive import is_archived
from yt_agent.models import VideoInfo


def _video_info(*, extractor_key: str = "Youtube") -> VideoInfo:
    return VideoInfo(
        video_id="abc123def45",
        title="Demo",
        channel="Channel",
        upload_date=None,
        duration_seconds=None,
        extractor_key=extractor_key,
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )


def test_is_archived_matches_extractor_case_insensitively() -> None:
    assert is_archived({"youtube abc123def45"}, _video_info())


def test_is_archived_keeps_video_id_case_sensitive() -> None:
    assert not is_archived({"youtube ABC123DEF45"}, _video_info())


def test_is_archived_requires_complete_archive_key() -> None:
    assert not is_archived({"abc123def45", "youtube"}, _video_info())
