from typer.testing import CliRunner

from yt_agent.cli import app
from yt_agent.errors import ExternalCommandError, InvalidInputError
from yt_agent.models import DownloadTarget, VideoInfo
from yt_agent.yt_dlp import DownloadExecution, ResolutionResult


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
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.shutil.which", lambda _: None)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 3
    assert "yt-dlp is required" in result.stderr


def test_version_flag_prints_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "yt-agent" in result.stdout


def test_doctor_json_output_is_machine_readable(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.shutil.which", lambda _: "/usr/bin/tool")
    result = runner.invoke(app, ["doctor", "--output", "json"])
    assert result.exit_code == 0
    assert '"tools"' in result.stdout
    assert '"paths"' in result.stdout


def test_search_with_no_results_exits_zero(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [])
    result = runner.invoke(app, ["search", "demo"])
    assert result.exit_code == 0
    assert "No matches found." in result.stdout


def test_search_json_output_is_machine_readable(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [_video()])
    result = runner.invoke(app, ["search", "demo", "--output", "json"])
    assert result.exit_code == 0
    assert '"id": "abc123def45"' in result.stdout
    assert '"index": 1' in result.stdout


def test_info_invalid_input_returns_input_exit_code(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.fetch_info", lambda target: (_ for _ in ()).throw(InvalidInputError("bad target")))
    result = runner.invoke(app, ["info", "bad"])
    assert result.exit_code == 4
    assert "bad target" in result.stderr


def test_download_continues_after_single_failure(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="first", info=_video("abc123def45", title="First"))
    second = DownloadTarget(original_input="second", info=_video("def123abc45", title="Second"))

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda targets, current_settings, source_query=None, select_playlist=False, use_fzf=False, selection=None: (
            [first, second],
            [],
        ),
    )

    def fake_download(target, current_settings):
        if target.info.video_id == "def123abc45":
            raise ExternalCommandError("yt-dlp download failed.", stderr="simulated")
        return DownloadExecution(
            output_path=current_settings.download_root / "Channel" / "file.mp4",
            stdout="",
            info_json_path=current_settings.download_root / "Channel" / "file.mp4.info.json",
        )

    monkeypatch.setattr("yt_agent.cli.yt_dlp.download_target", fake_download)
    monkeypatch.setattr("yt_agent.cli.index_manifest_record", lambda *args, **kwargs: None)
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

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.fetch_info", lambda target: payload)
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.resolve_payload",
        lambda target, current_payload, source_query=None: ResolutionResult([first, second], []),
    )
    monkeypatch.setattr("yt_agent.cli.select_results", lambda results, prefer_fzf, configured_selector: [second.info])

    downloaded_ids: list[str] = []

    def fake_download(target, current_settings):
        downloaded_ids.append(target.info.video_id)
        return DownloadExecution(
            output_path=current_settings.download_root / "Channel" / "file.mp4",
            stdout="",
            info_json_path=current_settings.download_root / "Channel" / "file.mp4.info.json",
        )

    monkeypatch.setattr("yt_agent.cli.yt_dlp.download_target", fake_download)
    monkeypatch.setattr("yt_agent.cli.index_manifest_record", lambda *args, **kwargs: None)
    result = runner.invoke(
        app,
        ["download", "https://www.youtube.com/playlist?list=PL123", "--select-playlist"],
    )
    assert result.exit_code == 0
    assert downloaded_ids == ["def123abc45"]
    assert "1 downloaded, 0 skipped, 0 failed" in result.stdout


def test_download_select_option_downloads_only_requested_playlist_entries(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="playlist", info=_video("abc123def45", title="First"))
    second = DownloadTarget(original_input="playlist", info=_video("def123abc45", title="Second"))
    payload = {
        "title": "Playlist",
        "channel": "Channel",
        "webpage_url": "https://www.youtube.com/playlist?list=PL123",
        "entries": [{"id": "abc123def45"}, {"id": "def123abc45"}],
    }

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.fetch_info", lambda target: payload)
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.resolve_payload",
        lambda target, current_payload, source_query=None: ResolutionResult([first, second], []),
    )

    downloaded_ids: list[str] = []

    def fake_download(target, current_settings):
        downloaded_ids.append(target.info.video_id)
        return DownloadExecution(
            output_path=current_settings.download_root / "Channel" / "file.mp4",
            stdout="",
            info_json_path=current_settings.download_root / "Channel" / "file.mp4.info.json",
        )

    monkeypatch.setattr("yt_agent.cli.yt_dlp.download_target", fake_download)
    monkeypatch.setattr("yt_agent.cli.index_manifest_record", lambda *args, **kwargs: None)
    result = runner.invoke(
        app,
        ["download", "https://www.youtube.com/playlist?list=PL123", "--select", "2"],
    )
    assert result.exit_code == 0
    assert downloaded_ids == ["def123abc45"]


def test_pick_select_outputs_selected_urls(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [_video(), _video("def123abc45")])
    result = runner.invoke(app, ["pick", "demo", "--select", "2"])
    assert result.exit_code == 0
    assert "https://www.youtube.com/watch?v=def123abc45" in result.stdout


def test_clips_search_renders_result_ids(settings, monkeypatch) -> None:
    class FakeStore:
        def search_clips(self, query, source="all", channel=None, language=None, limit=10):
            return [
                type(
                    "Hit",
                    (),
                    {
                        "result_id": "chapter:1",
                        "source": "chapters",
                        "display_range": "00:00 - 00:05",
                        "title": "Demo",
                        "channel": "Channel",
                        "match_text": "Intro",
                    },
                )()
            ]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda settings: FakeStore())
    result = runner.invoke(app, ["clips", "search", "Intro"])
    assert result.exit_code == 0
    assert "chapter:1" in result.stdout


def test_library_stats_json_output(settings, monkeypatch) -> None:
    class FakeStore:
        def library_stats(self):
            return {
                "videos": 3,
                "local_media": 2,
                "playlists": 1,
                "chapters": 4,
                "subtitle_tracks": 2,
                "transcript_segments": 8,
                "channels": 1,
            }

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda settings: FakeStore())
    result = runner.invoke(app, ["library", "stats", "--output", "json"])
    assert result.exit_code == 0
    assert '"videos": 3' in result.stdout


def test_config_init_writes_starter_file(settings, monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings.__class__(**{**settings.__dict__, "config_path": config_path}))
    result = runner.invoke(app, ["config", "init"])
    assert result.exit_code == 0
    assert config_path.exists()
    assert 'download_root = "' in config_path.read_text(encoding="utf-8")
