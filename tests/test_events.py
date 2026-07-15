import json
import sys

import pytest

from yt_agent.cli_download import _download_targets, _validate_events_path
from yt_agent.errors import InvalidInputError
from yt_agent.events import JsonlEventWriter
from yt_agent.models import DownloadTarget, VideoInfo
from yt_agent.yt_dlp import DownloadExecution


def _target():
    info = VideoInfo(
        video_id="abc123def45",
        title="Demo\nTitle",
        channel="Channel",
        upload_date=None,
        duration_seconds=10,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    return DownloadTarget(info.webpage_url, info)


def test_jsonl_event_writer_sequences_and_sanitizes(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    writer = JsonlEventWriter(path)
    writer.emit("one", message="hello\nworld")
    writer.emit("two")

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert rows[0]["message"] == "hello world"
    assert rows[0]["schema_version"] == 1


def test_download_targets_emits_lifecycle_events(settings, monkeypatch) -> None:
    output = settings.download_root / "Channel" / "demo.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"media")
    monkeypatch.setattr(
        "yt_agent.cli_download.yt_dlp.download_target",
        lambda *args, **kwargs: DownloadExecution(output, f"{output}\n"),
    )
    events = settings.catalog_file.parent / "events.jsonl"

    items = _download_targets(
        [_target()],
        settings,
        quiet=True,
        events_jsonl=events,
        index_manifest_record_fn=lambda *args, **kwargs: object(),
    )

    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert items[0].status == "downloaded"
    assert [row["event"] for row in rows] == [
        "download.started",
        "download.completed",
        "index.completed",
    ]
    assert rows[0]["title"] == "Demo Title"


def test_events_path_rejects_state_and_media_collisions(settings) -> None:
    unsafe_paths = (
        settings.archive_file,
        settings.manifest_file,
        settings.catalog_file,
        settings.download_root / "video.mp4",
        settings.clips_root / "clip.mp4",
    )
    for path in unsafe_paths:
        with pytest.raises(InvalidInputError):
            _validate_events_path(settings, path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink semantics")
def test_event_writer_refuses_symlink_swap(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    target = tmp_path / "target.txt"
    target.write_text("untouched", encoding="utf-8")
    writer = JsonlEventWriter(path)
    path.unlink()
    path.symlink_to(target)

    with pytest.raises(OSError):
        writer.emit("unsafe")

    assert target.read_text(encoding="utf-8") == "untouched"
