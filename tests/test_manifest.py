from pathlib import Path

from yt_agent.manifest import append_manifest_record, iter_manifest_records
from yt_agent.models import DownloadTarget, ManifestRecord, VideoInfo


def _target() -> DownloadTarget:
    info = VideoInfo(
        video_id="abc123def45",
        title="Demo",
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    return DownloadTarget(original_input=info.webpage_url, info=info, source_query="demo")


def test_append_and_iter_manifest_records(tmp_path: Path) -> None:
    manifest_path = tmp_path / "downloads.jsonl"
    output_path = tmp_path / "demo.mp4"
    info_json_path = output_path.with_suffix(".info.json")
    record = ManifestRecord.from_download(_target(), output_path=output_path, info_json_path=info_json_path)
    append_manifest_record(manifest_path, record)

    rows = iter_manifest_records(manifest_path)
    assert rows == [record]


def test_iter_manifest_records_skips_corrupt_lines(tmp_path: Path) -> None:
    manifest_path = tmp_path / "downloads.jsonl"
    output_path = tmp_path / "demo.mp4"
    info_json_path = output_path.with_suffix(".info.json")
    record = ManifestRecord.from_download(_target(), output_path=output_path, info_json_path=info_json_path)
    append_manifest_record(manifest_path, record)

    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write("{corrupt json line\n")
        handle.write("\n")

    rows = iter_manifest_records(manifest_path)
    assert rows == [record]
