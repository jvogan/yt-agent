import subprocess

import pytest

from yt_agent.errors import SelectionError
from yt_agent.models import VideoInfo
from yt_agent.selector import (
    _format_line,
    parse_selection,
    prompt_for_selection,
    select_results,
    select_with_fzf,
)


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


@pytest.mark.parametrize("selection", ["", "   ", "q", "Quit", "exit"])
def test_parse_selection_returns_empty_for_blank_or_quit(selection: str) -> None:
    assert parse_selection(selection, 4) == []


def test_parse_selection_rejects_out_of_range() -> None:
    try:
        parse_selection("7", 3)
    except SelectionError as exc:
        assert "out of range" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected SelectionError")


def test_parse_selection_rejects_non_numeric_tokens() -> None:
    with pytest.raises(SelectionError, match="comma-separated result numbers"):
        parse_selection("1,two", 3)


def test_parse_selection_deduplicates_indexes() -> None:
    assert parse_selection("2, 2, 1, 2", 3) == [2, 1]


def test_prompt_for_selection_returns_empty_without_results() -> None:
    assert prompt_for_selection([]) == []


def test_prompt_for_selection_uses_prompt_when_raw_selection_missing(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.selector.Prompt.ask", lambda message: "1,3")
    selected = prompt_for_selection([_video("abc123def45"), _video("def123abc45"), _video("ghi123jkl45")])
    assert [item.video_id for item in selected] == ["abc123def45", "ghi123jkl45"]


def test_select_results_falls_back_to_prompt(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.selector.select_with_fzf", lambda results: (_ for _ in ()).throw(SelectionError("missing")))
    selected = select_results([_video("abc123def45"), _video("def123abc45")], prefer_fzf=True, raw_selection="2")
    assert [item.video_id for item in selected] == ["def123abc45"]


def test_format_line_replaces_tabs() -> None:
    video = VideoInfo(
        video_id="abc123def45",
        title="Title\twith\ttabs",
        channel="Chan\tnel",
        upload_date="2026\t03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    line = _format_line(1, video)
    fields = line.split("\t")
    assert len(fields) == 7
    assert "\t" not in fields[1]
    assert "Title with tabs" == fields[1]
    assert "Chan nel" == fields[2]


def test_format_line_strips_newlines_and_ansi_sequences() -> None:
    video = VideoInfo(
        video_id="abc123def45",
        title="Line one\nLine two\x1b[31m",
        channel="Chan\r\nnel",
        upload_date="2026-03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    line = _format_line(1, video)
    assert "\n" not in line
    assert "\r" not in line
    assert "\x1b" not in line


def test_select_with_fzf_requires_binary(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.selector.shutil.which", lambda name: None)
    with pytest.raises(SelectionError, match="not installed"):
        select_with_fzf([_video("abc123def45")])


def test_select_with_fzf_returns_empty_when_user_cancels(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.selector.shutil.which", lambda name: "/opt/homebrew/bin/fzf")
    monkeypatch.setattr(
        "yt_agent.selector.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 130, stdout="", stderr=""),
    )

    assert select_with_fzf([_video("abc123def45")]) == []


def test_select_with_fzf_raises_stderr_message(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.selector.shutil.which", lambda name: "/opt/homebrew/bin/fzf")
    monkeypatch.setattr(
        "yt_agent.selector.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="broken pipe"),
    )

    with pytest.raises(SelectionError, match="broken pipe"):
        select_with_fzf([_video("abc123def45")])


def test_select_with_fzf_uses_default_error_message_when_stderr_empty(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.selector.shutil.which", lambda name: "/opt/homebrew/bin/fzf")
    monkeypatch.setattr(
        "yt_agent.selector.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr=""),
    )

    with pytest.raises(SelectionError, match="fzf selection failed"):
        select_with_fzf([_video("abc123def45")])


def test_select_with_fzf_returns_selected_results(monkeypatch) -> None:
    results = [_video("abc123def45"), _video("def123abc45"), _video("ghi123jkl45")]

    monkeypatch.setattr("yt_agent.selector.shutil.which", lambda name: "/opt/homebrew/bin/fzf")

    def fake_run(args, **kwargs):
        assert kwargs["input"].count("\n") == 2
        return subprocess.CompletedProcess(
            args,
            0,
            stdout="2\tVideo def123abc45\tChannel\t01:31\t2026-03-07\tdef123abc45\thttps://...\n"
            "invalid line\n"
            "9\tOut of range\tChannel\t01:31\t2026-03-07\tmissing\thttps://...\n"
            "1\tVideo abc123def45\tChannel\t01:31\t2026-03-07\tabc123def45\thttps://...\n",
            stderr="",
        )

    monkeypatch.setattr("yt_agent.selector.subprocess.run", fake_run)

    selected = select_with_fzf(results)
    assert [item.video_id for item in selected] == ["def123abc45", "abc123def45"]


def test_select_with_fzf_returns_empty_when_no_valid_indexes_selected(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.selector.shutil.which", lambda name: "/opt/homebrew/bin/fzf")
    monkeypatch.setattr(
        "yt_agent.selector.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout="invalid line\n99\tMissing\tChannel\t01:31\t2026-03-07\tmissing\thttps://...\n",
            stderr="",
        ),
    )

    assert select_with_fzf([_video("abc123def45")]) == []


def test_select_results_returns_empty_without_results() -> None:
    assert select_results([], prefer_fzf=True, configured_selector="fzf") == []


def test_select_results_falls_back_to_prompt_without_raw_selection(monkeypatch) -> None:
    results = [_video("abc123def45"), _video("def123abc45")]
    monkeypatch.setattr("yt_agent.selector.select_with_fzf", lambda results: (_ for _ in ()).throw(SelectionError("missing")))
    monkeypatch.setattr("yt_agent.selector.prompt_for_selection", lambda results: [results[0]])

    selected = select_results(results, configured_selector="fzf")
    assert [item.video_id for item in selected] == ["abc123def45"]


def test_select_results_uses_raw_selection_for_non_interactive_mode() -> None:
    selected = select_results(
        [_video("abc123def45"), _video("def123abc45"), _video("ghi123jkl45")],
        raw_selection="1,3",
    )
    assert [item.video_id for item in selected] == ["abc123def45", "ghi123jkl45"]
