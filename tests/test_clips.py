import subprocess
from datetime import UTC, datetime
from pathlib import Path

from yt_agent.catalog import CatalogStore, VideoUpsert
from yt_agent.clips import extract_clip
from yt_agent.models import ChapterEntry, SubtitleTrack, TranscriptSegment


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


def test_extract_clip_prefers_local_media(settings, monkeypatch, tmp_path: Path) -> None:
    source_path = tmp_path / "demo.mp4"
    source_path.write_bytes(b"video")
    _seed_video(settings, source_path)
    store = CatalogStore(settings.catalog_file)
    store.replace_chapters(
        "abc123def45",
        [ChapterEntry(position=0, title="Intro", start_seconds=1.0, end_seconds=6.0)],
    )

    def fake_run(args, text, capture_output, check):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.clips.optional_tool_path", lambda name: "/opt/homebrew/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr("yt_agent.clips.subprocess.run", fake_run)
    extraction = extract_clip(settings, "chapter:1", padding_before=0, padding_after=0, mode="fast")
    assert extraction.source == "local"
    assert extraction.output_path.name.endswith(".mp4")


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

    def fake_run(args, text, capture_output, check):
        output_template = Path(args[args.index("--output") + 1])
        created = output_template.parent / output_template.name.replace(".%(ext)s", ".mp4")
        created.parent.mkdir(parents=True, exist_ok=True)
        created.write_bytes(b"clip")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("yt_agent.clips.command_path", lambda: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.clips.subprocess.run", fake_run)
    extraction = extract_clip(settings, "transcript:1", padding_before=1, padding_after=1, mode="fast", prefer_remote=True)
    assert extraction.source == "remote"
    assert extraction.output_path.exists()
