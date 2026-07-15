import json
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from yt_agent.catalog import CatalogStore, VideoUpsert
from yt_agent.cli import app
from yt_agent.errors import InvalidInputError
from yt_agent.media_tools import execute_preview, plan_preview, smart_clip_bounds
from yt_agent.models import SubtitleTrack, TranscriptSegment

runner = CliRunner()


def _seed(settings) -> Path:
    media = settings.download_root / "Channel" / "demo.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video")
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo",
            channel="Channel",
            upload_date="2026-07-10",
            duration_seconds=100,
            extractor_key="Youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input=None,
            source_query=None,
            output_path=media,
            info_json_path=None,
            downloaded_at=datetime.now(UTC).isoformat(),
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )
    store.replace_transcripts(
        "abc123def45",
        [
            (
                SubtitleTrack("en", "manual", False, "vtt", settings.catalog_file.parent / "en.vtt"),
                [TranscriptSegment(0, 10.0, 20.0, "smart boundary")],
            )
        ],
    )
    return media


def test_smart_clip_bounds_snaps_to_nearby_silence(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(settings)
    monkeypatch.setattr("yt_agent.media_tools.optional_tool_path", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        "yt_agent.media_tools.subprocess.run",
        lambda args, text, capture_output, check: subprocess.CompletedProcess(
            args,
            0,
            stdout="",
            stderr=(
                "[silencedetect] silence_start: 0.5\n"
                "[silencedetect] silence_end: 1.5\n"
                "[silencedetect] silence_start: 12.4\n"
                "[silencedetect] silence_end: 13.0\n"
            ),
        ),
    )

    bounds = smart_clip_bounds(settings, "transcript:1", window_seconds=2.0)

    assert bounds.original_start == 10.0
    assert bounds.original_end == 20.0
    assert bounds.start_seconds == pytest.approx(9.5)
    assert bounds.end_seconds == pytest.approx(20.4)


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"window_seconds": 20.0}, "window"),
        ({"noise_db": -100.0}, "threshold"),
        ({"min_silence": 0.0}, "Minimum silence"),
    ],
)
def test_smart_clip_bounds_rejects_unbounded_options(settings, kwargs, match: str) -> None:
    with pytest.raises(InvalidInputError, match=match):
        smart_clip_bounds(settings, "transcript:1", **kwargs)


def test_plan_and_execute_contact_sheet_and_gif(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(settings)
    monkeypatch.setattr("yt_agent.media_tools.optional_tool_path", lambda name: "/usr/bin/ffmpeg")
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    sibling = shared / "sibling.txt"
    sibling.write_text("keep\n", encoding="utf-8")
    sibling.chmod(0o644)
    plan = plan_preview(
        settings,
        "abc123def45",
        dest=shared / "sheet.jpg",
        frames=12,
        columns=4,
        width=400,
        gif=True,
        gif_start=3.0,
        gif_duration=5.0,
        gif_fps=10,
    )
    calls: list[list[str]] = []

    def fake_run(args, text, capture_output, check):
        calls.append(args)
        Path(args[-1]).write_bytes(b"output")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.media_tools.subprocess.run", fake_run)
    execute_preview(plan)

    assert "tile=4x3:nb_frames=12" in plan.contact_sheet_args[-4]
    assert plan.gif_args is not None
    assert "fps=10" in plan.gif_args[-2]
    assert plan.contact_sheet_path.is_file()
    assert plan.gif_path is not None and plan.gif_path.is_file()
    assert len(calls) == 2
    if sys.platform != "win32":
        assert stat.S_IMODE(shared.stat().st_mode) == 0o755
        assert stat.S_IMODE(sibling.stat().st_mode) == 0o644
        assert stat.S_IMODE(plan.contact_sheet_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(plan.gif_path.stat().st_mode) == 0o600


def test_preview_and_smart_cli_dry_runs(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(settings)
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.media_tools.optional_tool_path", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        "yt_agent.media_tools.subprocess.run",
        lambda args, text, capture_output, check: subprocess.CompletedProcess(
            args, 0, stdout="", stderr=""
        ),
    )

    preview = runner.invoke(
        app,
        [
            "preview",
            "contact-sheet",
            "abc123def45",
            "--dest",
            str(tmp_path / "sheet.jpg"),
            "--gif",
            "--dry-run",
            "--output",
            "json",
        ],
    )
    smart = runner.invoke(
        app,
        ["clips", "smart", "transcript:1", "--dry-run", "--output", "json"],
    )

    assert preview.exit_code == 0, preview.stdout
    preview_payload = json.loads(preview.stdout)
    assert preview_payload["summary"]["dry_run"] is True
    assert not Path(preview_payload["path"]).exists()
    assert smart.exit_code == 0, smart.stdout
    smart_payload = json.loads(smart.stdout)
    assert smart_payload["summary"]["start_seconds"] == 10.0
