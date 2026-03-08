import json
from datetime import UTC, datetime
from pathlib import Path

from youtube_cli.manifest import append_manifest_record
from youtube_cli.models import DownloadTarget, ManifestRecord, VideoInfo


def test_append_manifest_record_writes_single_json_line(tmp_path: Path) -> None:
    target = DownloadTarget(
        original_input="https://www.youtube.com/watch?v=abc123def45",
        info=VideoInfo(
            video_id="abc123def45",
            title="Demo",
            channel="Channel",
            upload_date="2026-03-07",
            duration_seconds=91,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
        ),
    )
    record = ManifestRecord.from_download(
        target,
        output_path=tmp_path / "downloads" / "file.mp4",
        downloaded_at=datetime(2026, 3, 7, tzinfo=UTC),
    )
    manifest_path = tmp_path / "state" / "downloads.jsonl"
    append_manifest_record(manifest_path, record)
    rows = manifest_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    payload = json.loads(rows[0])
    assert payload["video_id"] == "abc123def45"
    assert payload["output_path"].endswith("file.mp4")
