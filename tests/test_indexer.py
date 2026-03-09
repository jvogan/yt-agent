import json
from pathlib import Path

from yt_agent.catalog import CatalogStore
from yt_agent.indexer import refresh_catalog
from yt_agent.manifest import append_manifest_record
from yt_agent.models import DownloadTarget, ManifestRecord, VideoInfo


def _target() -> DownloadTarget:
    info = VideoInfo(
        video_id="abc123def45",
        title="Demo Video",
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=120,
        extractor_key="youtube",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
    )
    return DownloadTarget(original_input=info.webpage_url, info=info)


def test_refresh_catalog_backfills_from_manifest_and_sidecars(settings) -> None:
    output_path = settings.download_root / "Channel" / "2026-03-07 - Demo Video [abc123def45].mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"video")
    info_json_path = Path(f"{output_path}.info.json")
    info_json_path.write_text(
        json.dumps(
            {
                "id": "abc123def45",
                "title": "Demo Video",
                "channel": "Channel",
                "duration": 120,
                "upload_date": "20260307",
                "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                "extractor_key": "youtube",
                "chapters": [{"title": "Intro", "start_time": 0, "end_time": 10}],
            }
        ),
        encoding="utf-8",
    )
    Path(f"{output_path}.en.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello there\n",
        encoding="utf-8",
    )

    record = ManifestRecord.from_download(_target(), output_path=output_path, info_json_path=info_json_path)
    append_manifest_record(settings.manifest_file, record)

    summary = refresh_catalog(settings)
    assert summary.videos == 1
    assert summary.chapters == 1
    assert summary.transcript_segments == 1

    summary = refresh_catalog(settings)
    assert summary.videos == 1

    store = CatalogStore(settings.catalog_file)
    details = store.get_video_details("abc123def45")
    assert details is not None
    assert len(details["chapters"]) == 1
    assert len(details["subtitle_tracks"]) == 1
