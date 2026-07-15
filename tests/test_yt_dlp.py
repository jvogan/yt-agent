import json
import subprocess
from dataclasses import replace

import pytest

from yt_agent.errors import DependencyError, ExternalCommandError, InvalidInputError
from yt_agent.models import DownloadTarget, VideoInfo
from yt_agent.yt_dlp import (
    ResolutionResult,
    command_path,
    download_target,
    fetch_comments,
    normalize_target,
    record_live,
    resolve_payload,
    resolve_targets,
    search,
)


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


def test_normalize_target_rejects_scheme_only_input() -> None:
    with pytest.raises(InvalidInputError, match="Only YouTube URLs are supported"):
        normalize_target("https://")


def test_normalize_target_rejects_non_youtube_https_host() -> None:
    with pytest.raises(InvalidInputError, match="Only YouTube URLs are supported"):
        normalize_target("https://example.com/watch?v=abc123def45")


def test_normalize_target_rejects_lookalike_suffix_hosts() -> None:
    with pytest.raises(InvalidInputError, match="Only YouTube URLs are supported"):
        normalize_target("https://youtube.com.evil.com/watch?v=abc123def45")


def test_command_path_raises_when_yt_dlp_is_missing(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: None)

    with pytest.raises(DependencyError, match="Required tool 'yt-dlp' is not installed or not on PATH."):
        command_path()


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


def test_search_debug_logging_redacts_query(monkeypatch, caplog) -> None:
    def fake_run(args, text, capture_output, check, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout='{"entries": []}', stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    with caplog.at_level("DEBUG", logger="yt_agent"):
        search("private project codename", limit=5)

    log_text = caplog.text
    assert "ytsearch5:<query>" in log_text
    assert "private project codename" not in log_text


def test_search_rejects_url_queries(monkeypatch) -> None:
    called = False

    def fake_run(*args, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess(args[0], 0, stdout="{}", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    with pytest.raises(InvalidInputError, match="must not contain a URL"):
        search("https://www.youtube.com/watch?v=abc123def45", limit=5)

    assert called is False


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


def test_download_target_has_no_wall_clock_timeout(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    captured_kwargs: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    download_target(_make_target(), settings)

    assert "timeout" not in captured_kwargs


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


@pytest.mark.parametrize(
    ("field", "unsafe_value", "kwargs"),
    [
        ("video_format", "bv*;rm -rf", {"mode": "video"}),
        ("audio_format", "best\nworst", {"mode": "audio"}),
        ("subtitle_languages", "en,$(touch x)", {"fetch_subs": True}),
        ("subtitle_languages", "--all-subs", {"fetch_subs": True}),
    ],
)
def test_download_target_rejects_unsafe_config_arguments(
    monkeypatch,
    tmp_path,
    field: str,
    unsafe_value: str,
    kwargs: dict[str, object],
) -> None:
    settings = replace(_make_settings(tmp_path), **{field: unsafe_value})
    calls: list[list[str]] = []

    def fake_run(args, text, capture_output, check, **run_kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    with pytest.raises(InvalidInputError, match="Invalid characters"):
        download_target(_make_target(), settings, **kwargs)

    assert calls == []


def test_run_json_raises_on_timeout(monkeypatch) -> None:
    def fake_run(args, text, capture_output, check, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=300)

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)
    with pytest.raises(ExternalCommandError, match="timed out"):
        search("demo", limit=5)


def test_fetch_comments_uses_bounded_fixed_extractor_args(monkeypatch) -> None:
    captured = []
    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/usr/bin/yt-dlp")

    def fake_run(args, **kwargs):
        captured.extend(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"id":"abc123def45"}', stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    fetch_comments("abc123def45", limit=25)

    assert "--get-comments" in captured
    assert "youtube:max_comments=25" in captured


@pytest.mark.parametrize("limit", [0, 1001])
def test_fetch_comments_rejects_unbounded_limits(limit) -> None:
    with pytest.raises(InvalidInputError, match="between 1 and 1000"):
        fetch_comments("abc123def45", limit=limit)


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


def test_download_target_auto_subs_keeps_manual_subs_and_adds_auto_fallback(monkeypatch, tmp_path) -> None:
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
    assert "--write-subs" in args


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


def test_download_target_sponsorblock_marks_by_default(monkeypatch, tmp_path) -> None:
    captured = []
    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/usr/bin/yt-dlp")
    monkeypatch.setattr(
        "yt_agent.yt_dlp.subprocess.run",
        lambda args, **kwargs: (
            captured.extend(args)
            or subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        ),
    )

    download_target(_make_target(), _make_settings(tmp_path), sponsorblock=True)

    assert "--sponsorblock-mark" in captured
    assert "--sponsorblock-remove" not in captured


def test_download_target_sponsorblock_removal_is_explicit(monkeypatch, tmp_path) -> None:
    captured = []
    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/usr/bin/yt-dlp")
    monkeypatch.setattr(
        "yt_agent.yt_dlp.subprocess.run",
        lambda args, **kwargs: (
            captured.extend(args)
            or subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        ),
    )

    download_target(_make_target(), _make_settings(tmp_path), sponsorblock_remove=True)

    assert "--sponsorblock-remove" in captured
    assert "--sponsorblock-mark" not in captured


def test_download_target_rehardens_download_directory(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    protected: list[object] = []
    output_path = settings.download_root / "Channel" / "Demo.mp4"

    def fake_run(args, text, capture_output, check, **kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return subprocess.CompletedProcess(args, 0, stdout=f"{output_path}\n", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)
    monkeypatch.setattr("yt_agent.yt_dlp.protect_private_tree", lambda path: protected.append(path))

    result = download_target(_make_target(), settings)

    assert result is not None
    assert protected == [output_path.parent]


def test_download_target_rejects_output_outside_download_root(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    outside_path = tmp_path.parent / "escape.mp4"

    def fake_run(args, text, capture_output, check, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=f"{outside_path}\n", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    with pytest.raises(InvalidInputError, match="outside the download root"):
        download_target(_make_target(), settings)


def test_download_target_revalidates_persisted_webpage_url(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    target = _make_target()
    target = DownloadTarget(
        original_input=target.original_input,
        info=replace(target.info, webpage_url="https://example.com/watch?v=abc123def45"),
    )
    calls: list[list[str]] = []

    def fake_run(args, text, capture_output, check, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    with pytest.raises(InvalidInputError, match="Only YouTube URLs are supported"):
        download_target(target, settings)

    assert calls == []


def test_record_live_uses_only_typed_live_options(monkeypatch, tmp_path) -> None:
    settings = _make_settings(tmp_path)
    output_path = settings.download_root / "Channel" / "live.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"live")
    calls: list[list[str]] = []

    def fake_run(args, text, capture_output, check, **kwargs):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout=f"{output_path}\n", stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/usr/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)

    execution = record_live(
        _make_target(), settings, live_from_start=True, wait_seconds=30
    )

    assert execution.output_path == output_path
    args = calls[0]
    assert "--live-from-start" in args
    assert args[args.index("--wait-for-video") + 1] == "30"
    assert args[-1] == "https://www.youtube.com/watch?v=abc123def45"


def test_record_live_rejects_unbounded_wait(settings) -> None:
    with pytest.raises(InvalidInputError, match="between 0 and 86400"):
        record_live(_make_target(), settings, wait_seconds=86_401)
