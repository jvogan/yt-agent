from pathlib import Path

from youtube_cli.library import build_output_template, sanitize_component
from youtube_cli.models import VideoInfo


def test_sanitize_component_removes_invalid_path_chars() -> None:
    assert sanitize_component('bad:/\\\\name*?', "fallback") == "bad name"


def test_build_output_template_uses_fallbacks(tmp_path: Path) -> None:
    info = VideoInfo(
        video_id="abc123def45",
        title="",
        channel="",
        upload_date=None,
        duration_seconds=None,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    template = build_output_template(tmp_path, info)
    assert template == tmp_path / "Unknown Channel" / "undated - Untitled [abc123def45].%(ext)s"
