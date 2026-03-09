from yt_agent.errors import SelectionError
from yt_agent.models import VideoInfo
from yt_agent.selector import parse_selection, select_results


def _video(video_id: str) -> VideoInfo:
    return VideoInfo(
        video_id=video_id,
        title=f"Video {video_id}",
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
    )


def test_parse_selection_parses_multiple_indexes() -> None:
    assert parse_selection("1, 3", 4) == [1, 3]


def test_parse_selection_rejects_out_of_range() -> None:
    try:
        parse_selection("7", 3)
    except SelectionError as exc:
        assert "out of range" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected SelectionError")


def test_select_results_falls_back_to_prompt(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.selector.select_with_fzf", lambda results: (_ for _ in ()).throw(SelectionError("missing")))
    selected = select_results([_video("abc123def45"), _video("def123abc45")], prefer_fzf=True, raw_selection="2")
    assert [item.video_id for item in selected] == ["def123abc45"]
