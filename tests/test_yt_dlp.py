import json
import subprocess

import pytest

from yt_agent.errors import ExternalCommandError, InvalidInputError
from yt_agent.models import DownloadTarget, VideoInfo
from yt_agent.yt_dlp import ResolutionResult, download_target, normalize_target, resolve_payload, resolve_targets, search


def test_normalize_target_wraps_bare_youtube_id() -> None:
    assert normalize_target("abc123def45") == "https://www.youtube.com/watch?v=abc123def45"


def test_normalize_target_rejects_free_form_text() -> None:
    with pytest.raises(InvalidInputError):
        normalize_target("not a url")


def test_normalize_target_accepts_youtube_hosts() -> None:
    assert normalize_target("https://youtu.be/abc123def45") == "https://youtu.be/abc123def45"
    assert normalize_target("https://music.youtube.com/watch?v=abc123def45") == "https://music.youtube.com/watch?v=abc123def45"


def test_normalize_target_accepts_casefolded_and_trailing_dot_hosts() -> None:
    assert (
        normalize_target("  https://WWW.YouTube.Com./watch?v=abc123def45  ")
        == "https://WWW.YouTube.Com./watch?v=abc123def45"
    )
    assert normalize_target("https://Music.YouTube.Com./watch?v=abc123def45") == (
        "https://Music.YouTube.Com./watch?v=abc123def45"
    )


def test_normalize_target_rejects_non_youtube_hosts() -> None:
    with pytest.raises(InvalidInputError, match="Only YouTube URLs are supported"):
        normalize_target("http://127.0.0.1:8080/admin")


def test_normalize_target_rejects_lookalike_suffix_hosts() -> None:
    with pytest.raises(InvalidInputError, match="Only YouTube URLs are supported"):
        normalize_target("https://youtube.com.evil.com/watch?v=abc123def45")


def test_search_parses_dump_single_json(monkeypatch) -> None:
    payload = {
        "entries": [
            {
                "id": "abc123def45",
                "title": "Demo",
                "channel": "Channel",
                "duration": 91,
                "upload_date": "20260307",
                "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                "extractor_key": "youtube",
            }
        ]
    }

    def fake_run(args, text, capture_output, check, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)
    results = search("demo", limit=5)
    assert results == [
        VideoInfo(
            video_id="abc123def45",
            title="Demo",
            channel="Channel",
            upload_date="2026-03-07",
            duration_seconds=91,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            original_url=None,
        )
    ]


def test_resolve_targets_expands_playlist(monkeypatch) -> None:
    payload = {
        "title": "Playlist",
        "entries": [
            {
                "id": "abc123def45",
                "title": "First",
                "channel": "Channel",
                "duration": 91,
                "upload_date": "20260307",
                "extractor_key": "youtube",
            },
            None,
        ],
    }
    monkeypatch.setattr("yt_agent.yt_dlp.fetch_info", lambda target: payload)
    result = resolve_targets(["https://www.youtube.com/playlist?list=PL123"])
    assert isinstance(result, ResolutionResult)
    assert [item.info.video_id for item in result.targets] == ["abc123def45"]
    assert result.targets[0].info.webpage_url == "https://www.youtube.com/watch?v=abc123def45"
    assert "Skipped unavailable playlist entry #2" in result.skipped_messages[0]


def test_resolve_payload_handles_single_video() -> None:
    payload = {
        "id": "abc123def45",
        "title": "Demo",
        "channel": "Channel",
        "duration": 91,
        "upload_date": "20260307",
        "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
        "extractor_key": "youtube",
    }
    result = resolve_payload("https://www.youtube.com/watch?v=abc123def45", payload)
    assert [item.info.video_id for item in result.targets] == ["abc123def45"]
    assert result.skipped_messages == []


def _make_settings(tmp_path):
    from yt_agent.config import Settings

    return Settings(
        download_root=tmp_path / "downloads",
        archive_file=tmp_path / "archive.txt",
    )


def _make_target():
    info = VideoInfo(
        video_id="abc123def45",
        title="Demo",
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    return DownloadTarget(original_input=info.webpage_url, info=info)


def test_download_target_returns_none_on_archive_skip(monkeypatch, tmp_path) -> None:
    """When yt-dlp exits 0 with no output (archive skip), download_target returns None."""
    settings = _make_settings(tmp_path)

    def fake_run(args, text, capture_output, check, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    result = download_target(_make_target(), settings)
    assert result is None


def test_download_target_audio_mode_uses_audio_format(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    captured: list[list[str]] = []

    def fake_run(args, text, capture_output, check, **kwargs):
        captured.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    download_target(_make_target(), settings, mode="audio")
    args = captured[0]
    fmt_idx = args.index("--format")
    assert args[fmt_idx + 1] == "bestaudio/best"
    assert "--extract-audio" in args
    assert "--audio-format" in args


def test_download_target_video_mode_uses_video_format(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    captured: list[list[str]] = []

    def fake_run(args, text, capture_output, check, **kwargs):
        captured.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    download_target(_make_target(), settings, mode="video")
    args = captured[0]
    fmt_idx = args.index("--format")
    assert args[fmt_idx + 1] == "bv*+ba/b"
    assert "--extract-audio" not in args


def test_run_json_raises_on_timeout(monkeypatch) -> None:
    def fake_run(args, text, capture_output, check, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=300)

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)
    with pytest.raises(ExternalCommandError, match="timed out"):
        search("demo", limit=5)


def test_download_target_fetch_subs_appends_write_subs(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    captured: list[list[str]] = []

    def fake_run(args, text, capture_output, check, **kwargs):
        captured.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    download_target(_make_target(), settings, fetch_subs=True)
    args = captured[0]
    assert "--write-subs" in args
    assert "--write-auto-subs" not in args
    assert "--sub-langs" in args


def test_download_target_auto_subs_appends_write_auto_subs(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    captured: list[list[str]] = []

    def fake_run(args, text, capture_output, check, **kwargs):
        captured.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    download_target(_make_target(), settings, fetch_subs=True, auto_subs=True)
    args = captured[0]
    assert "--write-auto-subs" in args
    assert "--write-subs" not in args


def test_download_target_no_subtitle_flags_by_default(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    captured: list[list[str]] = []

    def fake_run(args, text, capture_output, check, **kwargs):
        captured.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    download_target(_make_target(), settings)
    args = captured[0]
    assert "--write-subs" not in args
    assert "--write-auto-subs" not in args
    assert "--sub-langs" not in args
