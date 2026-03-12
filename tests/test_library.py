from pathlib import Path

from yt_agent.library import (
    build_clip_output_path,
    build_output_template,
    discover_info_json,
    discover_subtitle_files,
    sanitize_component,
)
from yt_agent.models import VideoInfo


def _video() -> VideoInfo:
    return VideoInfo(
        video_id="abc123def45",
        title="A / B",
        channel="Cool:Channel",
        upload_date="2026-03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )


def test_sanitize_component_normalizes_reserved_characters() -> None:
    assert sanitize_component('hello:/world*', "fallback") == "hello world"


def test_build_output_template_uses_channel_date_and_id(tmp_path: Path) -> None:
    output = build_output_template(tmp_path, _video())
    assert output == tmp_path / "Cool Channel" / "2026-03-07 - A B [abc123def45].%(ext)s"


def test_build_clip_output_path_includes_label_and_timerange(tmp_path: Path) -> None:
    output = build_clip_output_path(
        tmp_path,
        _video(),
        label="chapters",
        start_seconds=3,
        end_seconds=7,
    )
    assert output.parent == tmp_path / "Cool Channel"
    assert output.suffix == ".mp4"
    assert "[abc123def45]" in output.name


def test_build_output_template_sanitizes_video_id(tmp_path: Path) -> None:
    video = VideoInfo(
        video_id="../../escape",
        title="Demo",
        channel="Channel",
        upload_date="2026-03-10",
        duration_seconds=42,
        extractor_key="generic",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    output = build_output_template(tmp_path, video)
    assert output.parent == tmp_path / "Channel"
    assert output.name == "2026-03-10 - Demo [escape].%(ext)s"


def test_build_clip_output_path_sanitizes_video_id_and_extension(tmp_path: Path) -> None:
    video = VideoInfo(
        video_id="../../escape",
        title="Demo",
        channel="Channel",
        upload_date="2026-03-10",
        duration_seconds=42,
        extractor_key="generic",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    output = build_clip_output_path(
        tmp_path,
        video,
        label="chapters",
        start_seconds=1,
        end_seconds=5,
        extension="../mP4",
    )
    assert output.parent == tmp_path / "Channel"
    assert "[escape]" in output.name
    assert output.suffix == ".mp4"


def test_discover_info_json_finds_primary_or_alternate_sidecar(tmp_path: Path) -> None:
    media_path = tmp_path / "demo.mp4"
    primary = Path(f"{media_path}.info.json")
    primary.write_text("{}", encoding="utf-8")
    assert discover_info_json(media_path) == primary


def test_discover_subtitle_files_returns_vtt_and_srt(tmp_path: Path) -> None:
    media_path = tmp_path / "demo.mp4"
    media_path.write_bytes(b"video")
    vtt = tmp_path / "demo.en.vtt"
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    srt = tmp_path / "demo.en.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")
    unrelated = tmp_path / "other.txt"
    unrelated.write_text("nope", encoding="utf-8")

    result = discover_subtitle_files(media_path)
    assert set(result) == {vtt, srt}


def test_sanitize_component_truncates_at_180_chars() -> None:
    long_input = "x" * 300
    result = sanitize_component(long_input, "fallback")
    assert len(result) == 180
