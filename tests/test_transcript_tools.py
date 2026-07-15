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
from yt_agent.models import ChapterEntry, SubtitleTrack, TranscriptSegment
from yt_agent.transcript_tools import (
    execute_local_transcription,
    load_transcript_document,
    plan_local_transcription,
    render_transcript,
)

runner = CliRunner()


def _seed(settings) -> tuple[CatalogStore, Path, Path]:
    media = settings.download_root / "Channel" / "demo.mp4"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"video")
    manual = settings.catalog_file.parent / "subs" / "demo.en.vtt"
    manual.parent.mkdir(parents=True)
    manual.write_text("WEBVTT\n", encoding="utf-8")
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo Video",
            channel="Channel",
            upload_date="2026-07-10",
            duration_seconds=120,
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
    store.replace_chapters(
        "abc123def45",
        [
            ChapterEntry(0, "Opening", 0.0, 5.0),
            ChapterEntry(1, "Main", 5.0, 20.0),
        ],
    )
    store.replace_transcripts(
        "abc123def45",
        [
            (
                SubtitleTrack("en", "youtube-manual", False, "vtt", manual),
                [
                    TranscriptSegment(0, 1.0, 2.5, "Hello world"),
                    TranscriptSegment(1, 6.25, 8.0, "Main point"),
                ],
            )
        ],
    )
    return store, media, manual


def test_render_transcript_supports_all_formats_and_chapter_grouping(settings) -> None:
    store, _, _ = _seed(settings)
    document = load_transcript_document(store, "abc123def45")

    text = render_transcript(document, "txt", timestamps=False, group_chapters=True)
    markdown = render_transcript(document, "md", group_chapters=True)
    json_payload = json.loads(render_transcript(document, "json", group_chapters=True))
    vtt = render_transcript(document, "vtt")
    srt = render_transcript(document, "srt")

    assert text == "# Opening\n\nHello world\n\n# Main\n\nMain point\n"
    assert "## Opening" in markdown
    assert "[00:00:06.250] Main point" in markdown
    assert json_payload["segments"][1]["chapter"] == "Main"
    assert "00:00:01.000 --> 00:00:02.500" in vtt
    assert "00:00:01,000 --> 00:00:02,500" in srt


def test_plan_local_transcription_rejects_indexed_manual_output_without_force(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, _, manual = _seed(settings)
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    monkeypatch.setattr(
        "yt_agent.transcript_tools.optional_tool_path", lambda name: f"/usr/bin/{name}"
    )

    with pytest.raises(InvalidInputError, match="already exists|indexed track"):
        plan_local_transcription(
            settings,
            "abc123def45",
            model_path=model,
            output_path=manual,
        )

    with pytest.raises(InvalidInputError, match="language"):
        plan_local_transcription(
            settings,
            "abc123def45",
            model_path=model,
            language="../../escape",
        )


def test_execute_local_transcription_writes_provenance_and_appends_track(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, _, _ = _seed(settings)
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    monkeypatch.setattr(
        "yt_agent.transcript_tools.optional_tool_path", lambda name: f"/usr/bin/{name}"
    )
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    sibling = shared / "sibling.txt"
    sibling.write_text("keep\n", encoding="utf-8")
    sibling.chmod(0o644)
    plan = plan_local_transcription(
        settings,
        "abc123def45",
        model_path=model,
        language="auto",
        output_path=shared / "generated.vtt",
    )
    calls: list[list[str]] = []

    def fake_run(args, text, capture_output, check):
        calls.append(args)
        if args[0].endswith("ffmpeg"):
            Path(args[-1]).write_bytes(b"wav")
        else:
            prefix = Path(args[args.index("-of") + 1])
            prefix.with_suffix(".vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nlocally generated\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.transcript_tools.subprocess.run", fake_run)

    result = execute_local_transcription(settings, plan)

    assert result.segment_count == 1
    assert plan.output_path.is_file()
    provenance = json.loads(plan.provenance_path.read_text(encoding="utf-8"))
    assert provenance["generator"] == "whisper-cli"
    assert provenance["segment_count"] == 1
    assert len(calls) == 2
    tracks = store.subtitle_tracks("abc123def45")
    assert {track.source for track in tracks} == {"youtube-manual", "local-whisper"}
    assert store.search_clips("locally", source="transcript")[0].video_id == "abc123def45"
    if sys.platform != "win32":
        assert stat.S_IMODE(shared.stat().st_mode) == 0o755
        assert stat.S_IMODE(sibling.stat().st_mode) == 0o644
        assert stat.S_IMODE(plan.output_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(plan.provenance_path.stat().st_mode) == 0o600


def test_transcripts_export_and_generate_dry_run_cli(
    settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(settings)
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    sibling = shared / "sibling.txt"
    sibling.write_text("keep\n", encoding="utf-8")
    sibling.chmod(0o644)
    export_path = shared / "transcript.md"
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.transcript_tools.optional_tool_path", lambda name: f"/usr/bin/{name}"
    )

    exported = runner.invoke(
        app,
        [
            "transcripts",
            "export",
            "abc123def45",
            "--dest",
            str(export_path),
            "--chapters",
            "--output",
            "json",
        ],
    )
    generated = runner.invoke(
        app,
        [
            "transcripts",
            "generate",
            "abc123def45",
            "--model",
            str(model),
            "--dry-run",
            "--output",
            "json",
        ],
    )

    assert exported.exit_code == 0, exported.stdout
    assert json.loads(exported.stdout)["summary"]["format"] == "md"
    assert "## Opening" in export_path.read_text(encoding="utf-8")
    assert generated.exit_code == 0, generated.stdout
    payload = json.loads(generated.stdout)
    assert payload["summary"]["dry_run"] is True
    assert not Path(payload["path"]).exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(shared.stat().st_mode) == 0o755
        assert stat.S_IMODE(sibling.stat().st_mode) == 0o644
        assert stat.S_IMODE(export_path.stat().st_mode) == 0o600
