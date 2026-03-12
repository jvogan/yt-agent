import subprocess
from pathlib import Path

import pytest

from yt_agent.errors import ExternalCommandError
from yt_agent.transcripts import (
    _parse_timestamp,
    fetch_subtitle_sidecars,
    infer_subtitle_track,
    parse_subtitle_file,
)


def test_parse_subtitle_file_reads_vtt_with_identifiers_tags_and_skips_empty_blocks(tmp_path: Path) -> None:
    subtitle_path = tmp_path / "demo.vtt"
    subtitle_path.write_text(
        "\n".join(
            [
                "WEBVTT",
                "",
                "intro",
                "00:00:01.000 --> 00:00:03.000",
                "<c.colorE5E5E5>Hello</c>",
                "",
                "bad block",
                "still bad",
                "",
                "00:00:04.000 --> 00:00:06.000",
                "",
                "00:00:07.500 --> 00:00:09.000",
                "World",
            ]
        ),
        encoding="utf-8",
    )

    segments = parse_subtitle_file(subtitle_path)

    assert [(segment.start_seconds, segment.end_seconds, segment.text) for segment in segments] == [
        (1.0, 3.0, "Hello"),
        (7.5, 9.0, "World"),
    ]


def test_parse_subtitle_file_reads_srt_in_both_common_layouts_and_skips_bad_entries(tmp_path: Path) -> None:
    subtitle_path = tmp_path / "demo.srt"
    subtitle_path.write_text(
        "\n".join(
            [
                "1",
                "00:00:01,000 --> 00:00:03,000",
                "<i>Hello</i>",
                "",
                "00:00:03,500 --> 00:00:04,500",
                "again",
                "",
                "2",
                "not a timestamp",
                "ignored",
                "",
                "3",
                "00:00:05,000 --> 00:00:06,000",
                "",
            ]
        ),
        encoding="utf-8",
    )

    segments = parse_subtitle_file(subtitle_path)

    assert [(segment.segment_index, segment.text) for segment in segments] == [
        (0, "Hello"),
        (1, "again"),
    ]


def test_parse_subtitle_file_skips_short_and_empty_srt_blocks(tmp_path: Path) -> None:
    subtitle_path = tmp_path / "empty-ish.srt"
    subtitle_path.write_text(
        "\n".join(
            [
                "lonely line",
                "",
                "1",
                "00:00:01,000 --> 00:00:02,000",
                "",
                "2",
                "00:00:03,000 --> 00:00:04,000",
                "<i></i>",
            ]
        ),
        encoding="utf-8",
    )

    assert parse_subtitle_file(subtitle_path) == []


def test_parse_subtitle_file_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="Unsupported subtitle timestamp"):
        _parse_timestamp("not-a-time")

    with pytest.raises(ValueError, match="Unsupported subtitle format"):
        parse_subtitle_file(Path("demo.txt"))


def test_infer_subtitle_track_marks_unknown_and_prefers_manual_over_auto(tmp_path: Path) -> None:
    unknown = infer_subtitle_track(tmp_path / "demo.vtt")
    assert unknown.lang == "unknown"
    assert unknown.is_auto is False
    assert unknown.format == "vtt"

    manual = infer_subtitle_track(
        tmp_path / "demo.en.vtt",
        manual_languages={"en"},
        auto_languages={"en"},
    )
    assert manual.lang == "en"
    assert manual.is_auto is False
    assert manual.source == "indexed-sidecar"


def test_fetch_subtitle_sidecars_returns_manual_results_without_auto_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "subs"
    protected: list[Path] = []
    calls: list[list[str]] = []

    def fake_run(args, text, capture_output, check):
        calls.append(args)
        destination.mkdir(exist_ok=True)
        (destination / "abc123def45.info.json").write_text("{}", encoding="utf-8")
        (destination / "abc123def45.en.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.transcripts.ensure_private_directory", lambda path: destination.mkdir(exist_ok=True))
    monkeypatch.setattr("yt_agent.transcripts.protect_private_tree", lambda path: protected.append(path))
    monkeypatch.setattr("yt_agent.transcripts.command_path", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.transcripts.normalize_target", lambda target: f"normalized:{target}")
    monkeypatch.setattr("yt_agent.transcripts.subprocess.run", fake_run)

    info_json, paths = fetch_subtitle_sidecars("abc123def45", destination, languages=["en", "fr"], allow_auto_subs=False)

    assert info_json == destination / "abc123def45.info.json"
    assert paths == [destination / "abc123def45.en.vtt"]
    assert protected == [destination]
    assert calls == [
        [
            "/usr/bin/yt-dlp",
            "--skip-download",
            "--no-warnings",
            "--write-info-json",
            "--sub-langs",
            "en,fr",
            "--sub-format",
            "vtt",
            "--convert-subs",
            "vtt",
            "--output",
            str(destination / "%(id)s.%(ext)s"),
            "--write-subs",
            "normalized:abc123def45",
        ]
    ]


def test_fetch_subtitle_sidecars_cleans_retry_artifacts_but_preserves_info_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "subs"
    destination.mkdir()

    info_json = destination / "abc123def45.info.json"
    stale_text = destination / "abc123def45.en.txt"
    call_count = 0

    def fake_run(args, text, capture_output, check):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            info_json.write_text("{}", encoding="utf-8")
            stale_text.write_text("manual attempt", encoding="utf-8")
        else:
            (destination / "abc123def45.en.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.transcripts.subprocess.run", fake_run)
    monkeypatch.setattr("yt_agent.transcripts.command_path", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.transcripts.normalize_target", lambda target: target)

    info, paths = fetch_subtitle_sidecars(
        "https://www.youtube.com/watch?v=abc123def45",
        destination,
        languages=["en"],
        allow_auto_subs=True,
    )

    assert info == info_json
    assert len(paths) == 1
    assert info_json.exists()
    assert not stale_text.exists()
    assert call_count == 2


def test_fetch_subtitle_sidecars_raises_external_error_on_command_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "subs"

    monkeypatch.setattr("yt_agent.transcripts.command_path", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.transcripts.normalize_target", lambda target: target)
    monkeypatch.setattr(
        "yt_agent.transcripts.subprocess.run",
        lambda args, text, capture_output, check: subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr=" subtitle download failed ",
        ),
    )

    with pytest.raises(ExternalCommandError, match="yt-dlp failed while fetching subtitles"):
        fetch_subtitle_sidecars("abc123def45", destination, languages=["en"], allow_auto_subs=True)
