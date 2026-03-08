import pytest

from youtube_cli.errors import SelectionError
from youtube_cli.models import VideoInfo
from youtube_cli.selector import parse_selection, select_results


def _results() -> list[VideoInfo]:
    return [
        VideoInfo(
            video_id="abc123def45",
            title="First",
            channel="One",
            upload_date="2026-03-07",
            duration_seconds=60,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
        ),
        VideoInfo(
            video_id="def123abc45",
            title="Second",
            channel="Two",
            upload_date="2026-03-08",
            duration_seconds=120,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=def123abc45",
        ),
    ]


def test_parse_selection_supports_multiple_indexes() -> None:
    assert parse_selection("1, 2", 2) == [1, 2]


def test_parse_selection_rejects_invalid_token() -> None:
    with pytest.raises(SelectionError):
        parse_selection("1,two", 2)


def test_select_results_falls_back_to_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("youtube_cli.selector.select_with_fzf", lambda results: (_ for _ in ()).throw(SelectionError("missing")))
    monkeypatch.setattr("youtube_cli.selector.Prompt.ask", lambda _: "2")
    selected = select_results(_results(), prefer_fzf=True, configured_selector="fzf")
    assert [item.video_id for item in selected] == ["def123abc45"]
