import json
import subprocess
from dataclasses import replace

from yt_agent.catalog import CatalogStore, VideoUpsert
from yt_agent.verify import verify_library


def _video(settings, *, video_id: str = "abc123def45", output_path=None, info_path=None):
    return VideoUpsert(
        video_id=video_id,
        title="Demo",
        channel="Channel",
        upload_date=None,
        duration_seconds=42,
        extractor_key="Youtube",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
        requested_input=None,
        source_query=None,
        output_path=output_path,
        info_json_path=info_path,
        downloaded_at="2026-01-01T00:00:00Z" if output_path else None,
        indexed_at="2026-01-01T00:00:00Z",
    )


def test_verify_reports_manifest_and_missing_catalog(settings) -> None:
    settings.manifest_file.parent.mkdir(parents=True)
    settings.manifest_file.write_text('{bad json\n{"video_id":"abc123def45"}\n', encoding="utf-8")
    settings.archive_file.write_text("youtube abc123def45\n", encoding="utf-8")

    report = verify_library(settings)

    assert report.manifest_records == 1
    assert {item.code for item in report.findings} == {
        "manifest_invalid_json",
        "catalog_missing",
    }
    assert report.as_dict()["status"] == "issues"


def test_verify_audits_paths_fts_and_archive_drift(settings, tmp_path) -> None:
    settings.manifest_file.parent.mkdir(parents=True)
    settings.manifest_file.write_text("", encoding="utf-8")
    settings.archive_file.write_text("youtube archived123\n", encoding="utf-8")
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    store.upsert_video(
        _video(
            settings,
            output_path=tmp_path / "missing.mp4",
            info_path=tmp_path / "missing.info.json",
        )
    )
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO chapters (video_id, position, title, start_seconds) VALUES (?, ?, ?, ?)",
            ("abc123def45", 0, "Intro", 0.0),
        )
        conn.execute(
            "INSERT INTO chapter_fts (video_id, chapter_id, title) VALUES (?, ?, ?)",
            ("abc123def45", 999, "Orphan"),
        )
        conn.execute(
            "INSERT INTO subtitle_tracks (video_id, lang, source, format, file_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("abc123def45", "en", "manual", "vtt", str(tmp_path / "missing.vtt")),
        )

    report = verify_library(settings)
    codes = {item.code for item in report.findings}

    assert {
        "media_missing",
        "info_json_missing",
        "subtitle_missing",
        "chapter_fts_missing",
        "chapter_fts_orphan",
        "catalog_not_archived",
        "archive_not_cataloged",
    } <= codes
    assert report.catalog_videos == 1


def test_verify_deep_probes_existing_media(settings, tmp_path, monkeypatch) -> None:
    media = tmp_path / "demo.mp4"
    media.write_bytes(b"media")
    settings.manifest_file.parent.mkdir(parents=True)
    settings.manifest_file.write_text("", encoding="utf-8")
    settings.archive_file.write_text("youtube abc123def45\n", encoding="utf-8")
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    store.upsert_video(_video(settings, output_path=media))
    monkeypatch.setattr("yt_agent.verify.shutil.which", lambda _: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        "yt_agent.verify.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="invalid data"
        ),
    )

    report = verify_library(settings, deep=True)

    assert report.media_checked == 1
    corrupt = [item for item in report.findings if item.code == "media_corrupt"]
    assert len(corrupt) == 1
    assert corrupt[0].message == "invalid data"
    assert json.dumps(report.as_dict())


def test_verify_deep_reports_missing_ffprobe(settings, monkeypatch) -> None:
    settings.manifest_file.parent.mkdir(parents=True)
    settings.manifest_file.write_text("", encoding="utf-8")
    settings.archive_file.write_text("", encoding="utf-8")
    CatalogStore(settings.catalog_file).ensure_schema()
    monkeypatch.setattr("yt_agent.verify.shutil.which", lambda _: None)

    report = verify_library(settings, deep=True)

    assert "ffprobe_missing" in {item.code for item in report.findings}


def test_verify_reports_downloaded_catalog_row_without_media_path(settings) -> None:
    settings.manifest_file.parent.mkdir(parents=True)
    settings.manifest_file.write_text("", encoding="utf-8")
    settings.archive_file.write_text("youtube abc123def45\n", encoding="utf-8")
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    record = _video(settings)
    store.upsert_video(replace(record, downloaded_at="2026-01-01T00:00:00Z"))

    report = verify_library(settings)

    assert "media_path_missing" in {item.code for item in report.findings}
