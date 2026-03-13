import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yt_agent.catalog import CatalogStore, VideoUpsert
from yt_agent.clips import (
    _clip_bounds,
    _extract_resolved_clip,
    _ffmpeg_path,
    _run,
    _video_info_from_hit,
    extract_clip,
    extract_clip_for_range,
)
from yt_agent.errors import DependencyError, ExternalCommandError, InvalidInputError
from yt_agent.models import ChapterEntry, ClipSearchHit, SubtitleTrack, TranscriptSegment, VideoInfo


def _seed_video(settings, file_path: Path | None = None) -> None:
    store = CatalogStore(settings.catalog_file)
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
            output_path=file_path,
            info_json_path=None,
            downloaded_at=datetime.now(UTC).isoformat(),
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )


def _clip_hit(*, start_seconds: float = 5.0, end_seconds: float = 10.0, output_path: Path | None = None) -> ClipSearchHit:
    return ClipSearchHit(
        result_id="transcript:1",
        source="transcript",
        video_id="abc123def45",
        title="Demo Video",
        channel="Channel",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        score=0.5,
        match_text="clip me",
        context="before clip me after",
        output_path=output_path,
    )


def _video_info() -> VideoInfo:
    return VideoInfo(
        video_id="abc123def45",
        title="Demo Video",
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=120,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
        original_url="https://www.youtube.com/watch?v=abc123def45",
    )


def test_clip_bounds_applies_padding() -> None:
    start_seconds, end_seconds = _clip_bounds(_clip_hit(start_seconds=5.0, end_seconds=8.0), 1.5, 2.0)

    assert start_seconds == pytest.approx(3.5)
    assert end_seconds == pytest.approx(10.0)


def test_clip_bounds_clamps_negative_start_and_enforces_minimum_duration() -> None:
    start_seconds, end_seconds = _clip_bounds(_clip_hit(start_seconds=0.05, end_seconds=0.06), 0.5, 0.0)

    assert start_seconds == 0.0
    assert end_seconds == pytest.approx(0.1)


def test_video_info_from_hit_maps_fields_and_defaults() -> None:
    info = _video_info_from_hit(_clip_hit())

    assert info.video_id == "abc123def45"
    assert info.title == "Demo Video"
    assert info.channel == "Channel"
    assert info.upload_date is None
    assert info.duration_seconds is None
    assert info.extractor_key == "youtube"
    assert info.webpage_url == "https://www.youtube.com/watch?v=abc123def45"
    assert info.original_url == "https://www.youtube.com/watch?v=abc123def45"


def test_ffmpeg_path_raises_when_missing(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.clips.optional_tool_path", lambda name: None)

    with pytest.raises(DependencyError, match="ffmpeg is required"):
        _ffmpeg_path()


def test_run_raises_external_command_error_with_stderr(monkeypatch) -> None:
    def fake_run(args, text, capture_output, check):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr=" ffmpeg failed \n")

    monkeypatch.setattr("yt_agent.clips.subprocess.run", fake_run)

    with pytest.raises(ExternalCommandError, match="clip extraction failed") as excinfo:
        _run(["ffmpeg"], "clip extraction failed")

    assert excinfo.value.stderr == "ffmpeg failed"


@pytest.mark.parametrize("result_id", ["not-a-result-id", "chapter:not-a-number", "chapter:999"])
def test_extract_clip_raises_for_invalid_or_missing_result(settings, result_id: str) -> None:
    _seed_video(settings, None)

    with pytest.raises(InvalidInputError, match="Unknown clip result"):
        extract_clip(settings, result_id)


def test_extract_clip_raises_for_invalid_mode(settings) -> None:
    _seed_video(settings, None)
    store = CatalogStore(settings.catalog_file)
    store.replace_chapters(
        "abc123def45",
        [ChapterEntry(position=0, title="Intro", start_seconds=1.0, end_seconds=6.0)],
    )

    with pytest.raises(InvalidInputError, match="Clip mode must be 'fast' or 'accurate'"):
        extract_clip(settings, "chapter:1", mode="invalid")


def test_extract_clip_prefers_local_media(settings, monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "demo.mp4"
    source_path.write_bytes(b"video")
    _seed_video(settings, source_path)
    store = CatalogStore(settings.catalog_file)
    store.replace_chapters(
        "abc123def45",
        [ChapterEntry(position=0, title="Intro", start_seconds=1.0, end_seconds=6.0)],
    )

    recorded: list[list[str]] = []

    def fake_run(args: list[str], message: str) -> None:
        recorded.append(args)
        Path(args[-1]).write_bytes(b"clip")

    monkeypatch.setattr("yt_agent.clips._ffmpeg_path", lambda: "/opt/homebrew/bin/ffmpeg")
    monkeypatch.setattr("yt_agent.clips._run", fake_run)

    extraction = extract_clip(settings, "chapter:1", mode="fast")

    assert extraction.source == "local"
    assert extraction.used_remote_fallback is False
    assert extraction.output_path.suffix == ".mp4"
    assert extraction.output_path.exists()
    assert recorded == [
        [
            "/opt/homebrew/bin/ffmpeg",
            "-y",
            "-ss",
            "1.000",
            "-to",
            "6.000",
            "-i",
            str(source_path),
            "-c",
            "copy",
            str(extraction.output_path),
        ]
    ]


@pytest.mark.parametrize(
    ("mode", "expected_args", "expected_suffix"),
    [
        ("fast", ["-c", "copy"], ".mkv"),
        ("accurate", ["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"], ".mp4"),
    ],
)
def test_extract_resolved_clip_uses_local_ffmpeg_args(
    settings,
    monkeypatch,
    tmp_path: Path,
    mode: str,
    expected_args: list[str],
    expected_suffix: str,
) -> None:
    media_path = tmp_path / "source.mkv"
    media_path.write_bytes(b"video")
    recorded: list[list[str]] = []

    def fake_run(args: list[str], message: str) -> None:
        recorded.append(args)
        Path(args[-1]).write_bytes(b"clip")

    monkeypatch.setattr("yt_agent.clips._ffmpeg_path", lambda: "/usr/local/bin/ffmpeg")
    monkeypatch.setattr("yt_agent.clips._run", fake_run)

    extraction = _extract_resolved_clip(
        settings,
        _video_info(),
        media_path=media_path,
        label="segment",
        start_seconds=3.25,
        end_seconds=7.5,
        mode=mode,
        prefer_remote=False,
    )

    assert extraction.source == "local"
    assert extraction.used_remote_fallback is False
    assert extraction.output_path.suffix == expected_suffix
    assert extraction.output_path.exists()
    assert recorded[0][:8] == [
        "/usr/local/bin/ffmpeg",
        "-y",
        "-ss",
        "3.250",
        "-to",
        "7.500",
        "-i",
        str(media_path),
    ]
    assert recorded[0][8:-1] == expected_args
    assert recorded[0][-1] == str(extraction.output_path)


def test_extract_resolved_clip_prefers_remote_when_requested(settings, monkeypatch, tmp_path: Path) -> None:
    media_path = tmp_path / "source.mp4"
    media_path.write_bytes(b"video")
    recorded: list[list[str]] = []

    def fake_run(args: list[str], message: str) -> None:
        recorded.append(args)

    monkeypatch.setattr("yt_agent.clips.command_path", lambda: "/usr/local/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.clips._run", fake_run)

    extraction = _extract_resolved_clip(
        settings,
        _video_info(),
        media_path=media_path,
        label="segment",
        start_seconds=2.0,
        end_seconds=4.0,
        mode="fast",
        prefer_remote=True,
    )

    assert extraction.source == "remote"
    assert extraction.used_remote_fallback is True
    assert extraction.output_path.suffix == ".mp4"
    assert not extraction.output_path.exists()
    assert recorded == [
        [
            "/usr/local/bin/yt-dlp",
            "--quiet",
            "--no-warnings",
            "--force-overwrites",
            "--download-sections",
            "*2.000-4.000",
            "--output",
            str(extraction.output_path.with_suffix(".%(ext)s")),
            "https://www.youtube.com/watch?v=abc123def45",
        ]
    ]


def test_extract_resolved_clip_falls_back_to_default_output_when_remote_glob_misses(
    settings, monkeypatch
) -> None:
    recorded: list[list[str]] = []

    def fake_run(args: list[str], message: str) -> None:
        recorded.append(args)

    monkeypatch.setattr("yt_agent.clips.command_path", lambda: "/usr/local/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.clips._run", fake_run)

    extraction = _extract_resolved_clip(
        settings,
        _video_info(),
        media_path=None,
        label="segment",
        start_seconds=1.0,
        end_seconds=2.0,
        mode="fast",
        prefer_remote=False,
    )

    assert extraction.source == "remote"
    assert extraction.used_remote_fallback is True
    assert extraction.output_path.suffix == ".mp4"
    assert not extraction.output_path.exists()
    assert recorded == [
        [
            "/usr/local/bin/yt-dlp",
            "--quiet",
            "--no-warnings",
            "--force-overwrites",
            "--download-sections",
            "*1.000-2.000",
            "--output",
            str(extraction.output_path.with_suffix(".%(ext)s")),
            "https://www.youtube.com/watch?v=abc123def45",
        ]
    ]


def test_extract_clip_uses_remote_fallback(settings, monkeypatch) -> None:
    _seed_video(settings, None)
    store = CatalogStore(settings.catalog_file)
    store.replace_transcripts(
        "abc123def45",
        [
            (
                SubtitleTrack(
                    lang="en",
                    source="manual",
                    is_auto=False,
                    format="vtt",
                    file_path=settings.catalog_file.parent / "demo.en.vtt",
                ),
                [
                    TranscriptSegment(
                        segment_index=0,
                        start_seconds=5.0,
                        end_seconds=10.0,
                        text="clip me",
                    )
                ],
            )
        ],
    )

    def fake_run(args: list[str], message: str) -> None:
        output_template = Path(args[args.index("--output") + 1])
        created = output_template.parent / output_template.name.replace(".%(ext)s", ".mp4")
        created.parent.mkdir(parents=True, exist_ok=True)
        created.write_bytes(b"clip")

    monkeypatch.setattr("yt_agent.clips.command_path", lambda: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.clips._run", fake_run)

    extraction = extract_clip(settings, "transcript:1", padding_before=1, padding_after=1, mode="fast", prefer_remote=True)

    assert extraction.source == "remote"
    assert extraction.output_path.exists()


def test_extract_clip_for_range_raises_when_end_is_not_after_start(settings) -> None:
    with pytest.raises(InvalidInputError, match="greater than --start-seconds"):
        extract_clip_for_range(settings, video_id="abc123def45", start_seconds=4.0, end_seconds=4.0)


def test_extract_clip_for_range_raises_for_invalid_mode(settings) -> None:
    with pytest.raises(InvalidInputError, match="Clip mode must be 'fast' or 'accurate'"):
        extract_clip_for_range(
            settings,
            video_id="abc123def45",
            start_seconds=1.0,
            end_seconds=2.0,
            mode="invalid",
        )


def test_extract_clip_for_range_raises_for_missing_video(settings) -> None:
    with pytest.raises(InvalidInputError, match="is not in the catalog"):
        extract_clip_for_range(
            settings,
            video_id="missing-video",
            start_seconds=1.0,
            end_seconds=2.0,
        )


def test_extract_clip_for_range_clamps_negative_start_and_uses_catalog_video(settings, monkeypatch) -> None:
    source_path = settings.download_root / "demo.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"video")
    _seed_video(settings, source_path)
    recorded: dict[str, object] = {}

    def fake_extract_resolved_clip(settings_arg, info, **kwargs):
        recorded["settings"] = settings_arg
        recorded["info"] = info
        recorded["kwargs"] = kwargs
        return "sentinel"

    monkeypatch.setattr("yt_agent.clips._extract_resolved_clip", fake_extract_resolved_clip)

    result = extract_clip_for_range(
        settings,
        video_id="abc123def45",
        start_seconds=-5.0,
        end_seconds=8.0,
        mode="accurate",
        prefer_remote=True,
    )

    assert result == "sentinel"
    assert recorded["settings"] == settings
    assert recorded["info"] == VideoInfo(
        video_id="abc123def45",
        title="Demo Video",
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=120,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
        original_url="https://www.youtube.com/watch?v=abc123def45",
    )
    assert recorded["kwargs"] == {
        "media_path": source_path,
        "label": "range",
        "start_seconds": 0.0,
        "end_seconds": 8.0,
        "mode": "accurate",
        "prefer_remote": True,
    }
