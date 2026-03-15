import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from yt_agent.catalog import CatalogStore, VideoUpsert
from yt_agent.cli import app
from yt_agent.errors import StateLockError
from yt_agent.models import DownloadTarget, ManifestRecord, VideoInfo
from yt_agent.yt_dlp import DownloadExecution

runner = CliRunner()


def _video(video_id: str = "abc123def45", *, title: str = "Demo") -> VideoInfo:
    return VideoInfo(
        video_id=video_id,
        title=title,
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
        original_url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _seed_catalog_video(settings, *, output_path: Path | None = None) -> None:
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    store.upsert_video(
        VideoUpsert(
            video_id="abc123def45",
            title="Demo",
            channel="Channel",
            upload_date="2026-03-07",
            duration_seconds=91,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            requested_input="https://www.youtube.com/watch?v=abc123def45",
            source_query=None,
            output_path=output_path,
            info_json_path=None,
            downloaded_at=datetime.now(UTC).isoformat(),
            indexed_at=datetime.now(UTC).isoformat(),
        )
    )


def test_download_json_envelope_reports_downloaded_items(settings, monkeypatch) -> None:
    target = DownloadTarget(original_input="abc123def45", info=_video())
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._resolve_download_inputs", lambda *args, **kwargs: ([target], []))
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.download_target",
        lambda target, current_settings, **kwargs: DownloadExecution(
            output_path=current_settings.download_root / "Channel" / "file.mp4",
            stdout="",
            info_json_path=current_settings.download_root / "Channel" / "file.mp4.info.json",
        ),
    )
    monkeypatch.setattr("yt_agent.cli.index_manifest_record", lambda *args, **kwargs: None)

    result = runner.invoke(app, ["download", "--output", "json", "abc123def45"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "download"
    assert payload["status"] == "ok"
    assert payload["summary"]["downloaded"] == 1
    assert payload["resolved_targets"][0]["video_id"] == "abc123def45"
    assert payload["downloaded"][0]["output_path"].endswith("file.mp4")


def test_download_dry_run_does_not_write_state(settings, monkeypatch) -> None:
    target = DownloadTarget(original_input="abc123def45", info=_video())
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._resolve_download_inputs", lambda *args, **kwargs: ([target], []))
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.download_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download should not run during dry-run")),
    )

    result = runner.invoke(app, ["download", "--dry-run", "--output", "json", "abc123def45"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "noop"
    assert payload["summary"]["resolved"] == 1
    assert not settings.archive_file.exists()
    assert not settings.manifest_file.exists()
    assert not settings.catalog_file.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()


def test_download_json_error_payload_is_used_for_busy_lock(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    @contextmanager
    def busy_lock(path):
        raise StateLockError("Another yt-agent operation is already running.")
        yield

    monkeypatch.setattr("yt_agent.cli.operation_lock", busy_lock)

    result = runner.invoke(app, ["download", "--output", "json", "abc123def45"])

    assert result.exit_code == 7
    payload = json.loads(result.stderr)
    assert payload["status"] == "error"
    assert payload["error_type"] == "StateLockError"
    assert payload["exit_code"] == 7


def test_pick_json_without_select_returns_noop_when_no_results(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [])

    result = runner.invoke(app, ["pick", "--output", "json", "demo"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "pick"
    assert payload["status"] == "noop"
    assert payload["results"] == []


def test_grab_json_without_select_returns_noop_when_no_results(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [])

    result = runner.invoke(app, ["grab", "--dry-run", "--output", "json", "demo"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "grab"
    assert payload["status"] == "noop"
    assert payload["summary"]["resolved"] == 0


def test_grab_json_requires_select_when_results_exist(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [_video()])

    result = runner.invoke(app, ["grab", "--dry-run", "--output", "json", "demo"])

    assert result.exit_code == 4
    payload = json.loads(result.stderr)
    assert payload["status"] == "error"
    assert payload["message"] == "grab with --output json requires --select."


def test_download_json_error_payload_for_invalid_subtitle_flags(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["download", "--output", "json", "--auto-subs", "abc123def45"])

    assert result.exit_code == 4
    payload = json.loads(result.stderr)
    assert payload["error_type"] == "InvalidInputError"
    assert payload["message"] == "--auto-subs requires --fetch-subs."


def test_index_refresh_dry_run_is_local_only(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    settings.manifest_file.parent.mkdir(parents=True, exist_ok=True)
    settings.manifest_file.write_text(
        json.dumps(
            ManifestRecord(
                video_id="abc123def45",
                title="Demo",
                channel="Channel",
                upload_date="2026-03-07",
                duration_seconds=91,
                extractor_key="youtube",
                webpage_url="https://www.youtube.com/watch?v=abc123def45",
                requested_input="https://www.youtube.com/watch?v=abc123def45",
                source_query=None,
                output_path=None,
                info_json_path=None,
                downloaded_at=datetime.now(UTC).isoformat(),
            ).as_dict()
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "yt_agent.cli.index_refresh",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("index_refresh should not run during dry-run")),
    )

    result = runner.invoke(app, ["index", "refresh", "--dry-run", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "index refresh"
    assert payload["status"] == "noop"
    assert payload["summary"]["videos"] == 1
    assert payload["network_fetch_attempted"] is False
    assert not settings.catalog_file.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()


def test_index_add_requires_fetch_subs_for_auto_subs(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(
        app,
        ["index", "add", "abc123def45", "--output", "json", "--auto-subs"],
    )

    assert result.exit_code == 4
    payload = json.loads(result.stderr)
    assert payload["message"] == "--auto-subs requires --fetch-subs."


def test_clips_grab_supports_explicit_range_dry_run(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    media_path = settings.download_root / "Channel" / "file.mp4"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"video")
    _seed_catalog_video(settings, output_path=media_path)

    result = runner.invoke(
        app,
        [
            "clips",
            "grab",
            "--dry-run",
            "--output",
            "json",
            "--video-id",
            "abc123def45",
            "--start-seconds",
            "5",
            "--end-seconds",
            "12",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "clips grab"
    assert payload["status"] == "noop"
    assert payload["locator"] == "abc123def45:5.000-12.000"
    assert payload["start_seconds"] == 5.0
    assert payload["end_seconds"] == 12.0
    assert payload["output_path"].endswith(".mp4")
    assert not any(settings.clips_root.rglob("*")) if settings.clips_root.exists() else True


def test_read_only_catalog_commands_do_not_create_catalog(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    library_result = runner.invoke(app, ["library", "list", "--output", "json"])
    clips_result = runner.invoke(app, ["clips", "search", "demo", "--output", "json"])

    assert library_result.exit_code == 0
    assert json.loads(library_result.stdout) == []
    assert clips_result.exit_code == 0
    assert json.loads(clips_result.stdout) == []
    assert not settings.catalog_file.exists()


def test_library_show_missing_catalog_does_not_create_catalog(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["library", "show", "abc123def45", "--output", "json"])

    assert result.exit_code == 4
    payload = json.loads(result.stderr)
    assert payload["message"] == "Video id 'abc123def45' is not in the catalog."
    assert not settings.catalog_file.exists()


def test_clips_grab_rejects_mixed_locator_modes(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(
        app,
        [
            "clips",
            "grab",
            "chapter:1",
            "--output",
            "json",
            "--video-id",
            "abc123def45",
            "--start-seconds",
            "5",
            "--end-seconds",
            "12",
        ],
    )

    assert result.exit_code == 4
    payload = json.loads(result.stderr)
    assert "Use either RESULT_ID" in payload["message"]


def test_library_remove_dry_run_does_not_create_catalog(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["library", "remove", "--dry-run", "--output", "json", "abc123def45"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "library remove"
    assert payload["status"] == "noop"
    assert payload["removed"] == []
    assert payload["not_found"] == ["abc123def45"]
    assert not settings.catalog_file.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()
