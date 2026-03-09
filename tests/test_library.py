from pathlib import Path

from yt_agent.library import (
    build_clip_output_path,
    build_output_template,
    discover_info_json,
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


def test_discover_info_json_finds_primary_or_alternate_sidecar(tmp_path: Path) -> None:
    media_path = tmp_path / "demo.mp4"
    primary = Path(f"{media_path}.info.json")
    primary.write_text("{}", encoding="utf-8")
    assert discover_info_json(media_path) == primary
