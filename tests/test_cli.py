from typer.testing import CliRunner

from youtube_cli.cli import app
from youtube_cli.errors import ExternalCommandError, InvalidInputError
from youtube_cli.models import DownloadTarget, VideoInfo
from youtube_cli.yt_dlp import DownloadExecution, ResolutionResult


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
    )


def test_doctor_returns_dependency_exit_code_when_yt_dlp_missing(settings, monkeypatch) -> None:
    monkeypatch.setattr("youtube_cli.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("youtube_cli.cli.shutil.which", lambda _: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3
    assert "yt-dlp is required" in result.stderr


def test_search_with_no_results_exits_zero(settings, monkeypatch) -> None:
    monkeypatch.setattr("youtube_cli.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("youtube_cli.cli.yt_dlp.search", lambda query, limit: [])
    result = runner.invoke(app, ["search", "demo"])
    assert result.exit_code == 0
    assert "No matches found." in result.stdout


def test_search_renders_url_column(settings, monkeypatch) -> None:
    monkeypatch.setattr("youtube_cli.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("youtube_cli.cli.yt_dlp.search", lambda query, limit: [_video()])
    result = runner.invoke(app, ["search", "demo"])
    assert result.exit_code == 0
    assert "URL" in result.stdout
    assert "https://www.youtube.com/watch?v=abc123def45" in result.stdout


def test_info_invalid_input_returns_input_exit_code(settings, monkeypatch) -> None:
    monkeypatch.setattr("youtube_cli.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("youtube_cli.cli.yt_dlp.fetch_info", lambda target: (_ for _ in ()).throw(InvalidInputError("bad target")))
    result = runner.invoke(app, ["info", "bad"])
    assert result.exit_code == 4
    assert "bad target" in result.stderr


def test_info_playlist_entries_renders_entry_table(settings, monkeypatch) -> None:
    payload = {
        "title": "Playlist",
        "channel": "Channel",
        "webpage_url": "https://www.youtube.com/playlist?list=PL123",
        "entries": [{"id": "abc123def45"}],
    }
    resolution = ResolutionResult([DownloadTarget(original_input="playlist", info=_video())], [])
    monkeypatch.setattr("youtube_cli.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("youtube_cli.cli.yt_dlp.fetch_info", lambda target: payload)
    monkeypatch.setattr("youtube_cli.cli.yt_dlp.resolve_payload", lambda target, current_payload, source_query=None: resolution)
    result = runner.invoke(app, ["info", "https://www.youtube.com/playlist?list=PL123", "--entries"])
    assert result.exit_code == 0
    assert "Playlist" in result.stdout
    assert "abc123def45" in result.stdout


def test_download_continues_after_single_failure(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="first", info=_video("abc123def45", title="First"))
    second = DownloadTarget(original_input="second", info=_video("def123abc45", title="Second"))

    monkeypatch.setattr("youtube_cli.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "youtube_cli.cli._resolve_download_inputs",
        lambda targets, current_settings, source_query=None, select_playlist=False, use_fzf=False: (
            [first, second],
            [],
        ),
    )

    def fake_download(target, current_settings):
        if target.info.video_id == "def123abc45":
            raise ExternalCommandError("yt-dlp download failed.", stderr="simulated")
        return DownloadExecution(output_path=current_settings.download_root / "Channel" / "file.mp4", stdout="")

    monkeypatch.setattr("youtube_cli.cli.yt_dlp.download_target", fake_download)
    result = runner.invoke(app, ["download", "first", "second"])
    assert result.exit_code == 6
    assert "1 downloaded, 0 skipped, 1 failed" in result.stdout
    manifest_rows = settings.manifest_file.read_text(encoding="utf-8").splitlines()
    assert len(manifest_rows) == 1


def test_download_select_playlist_downloads_only_selected_entries(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="playlist", info=_video("abc123def45", title="First"))
    second = DownloadTarget(original_input="playlist", info=_video("def123abc45", title="Second"))
    payload = {
        "title": "Playlist",
        "channel": "Channel",
        "webpage_url": "https://www.youtube.com/playlist?list=PL123",
        "entries": [{"id": "abc123def45"}, {"id": "def123abc45"}],
    }

    monkeypatch.setattr("youtube_cli.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("youtube_cli.cli.yt_dlp.fetch_info", lambda target: payload)
    monkeypatch.setattr(
        "youtube_cli.cli.yt_dlp.resolve_payload",
        lambda target, current_payload, source_query=None: ResolutionResult([first, second], []),
    )
    monkeypatch.setattr("youtube_cli.cli.select_results", lambda results, prefer_fzf, configured_selector: [second.info])

    downloaded_ids: list[str] = []

    def fake_download(target, current_settings):
        downloaded_ids.append(target.info.video_id)
        return DownloadExecution(output_path=current_settings.download_root / "Channel" / "file.mp4", stdout="")

    monkeypatch.setattr("youtube_cli.cli.yt_dlp.download_target", fake_download)
    result = runner.invoke(
        app,
        ["download", "https://www.youtube.com/playlist?list=PL123", "--select-playlist"],
    )
    assert result.exit_code == 0
    assert downloaded_ids == ["def123abc45"]
    assert "1 downloaded, 0 skipped, 0 failed" in result.stdout


def test_download_skips_archived_items(settings, monkeypatch) -> None:
    target = DownloadTarget(original_input="first", info=_video())

    monkeypatch.setattr("youtube_cli.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "youtube_cli.cli._resolve_download_inputs",
        lambda targets, current_settings, source_query=None, select_playlist=False, use_fzf=False: (
            [target],
            [],
        ),
    )

    settings.archive_file.parent.mkdir(parents=True, exist_ok=True)
    settings.archive_file.write_text(f"{target.info.archive_key}\n", encoding="utf-8")

    def unexpected_download(target, current_settings):
        raise AssertionError("download should not be invoked for archived items")

    monkeypatch.setattr("youtube_cli.cli.yt_dlp.download_target", unexpected_download)
    result = runner.invoke(app, ["download", "first"])
    assert result.exit_code == 0
    assert "1 skipped" in result.stdout
