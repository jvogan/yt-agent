import json
import sqlite3
import stat
import subprocess
import sys

import pytest
import typer
from typer.testing import CliRunner

from yt_agent.catalog import CatalogStore, VideoUpsert
from yt_agent.cli import _run_guarded, app
from yt_agent.errors import (
    ConfigError,
    DependencyError,
    ExternalCommandError,
    InvalidInputError,
    SelectionError,
    StateLockError,
    dependency_install_hint,
)
from yt_agent.models import (
    CatalogVideo,
    ChapterEntry,
    ClipSearchHit,
    DownloadTarget,
    ManifestRecord,
    SubtitleTrack,
    TranscriptSegment,
    VideoInfo,
)
from yt_agent.security import operation_lock
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


def _catalog_video(
    video_id: str = "abc123def45",
    *,
    title: str = "Demo",
    output_path=None,
) -> CatalogVideo:
    return CatalogVideo(
        video_id=video_id,
        title=title,
        channel="Channel",
        upload_date="2026-03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
        requested_input=f"https://www.youtube.com/watch?v={video_id}",
        source_query=None,
        output_path=output_path,
        info_json_path=None,
        downloaded_at="2026-03-08T00:00:00Z",
        chapter_count=2,
        transcript_segment_count=3,
        playlist_count=1,
    )


def _clip_hit(result_id: str = "transcript:12") -> ClipSearchHit:
    return ClipSearchHit(
        result_id=result_id,
        source="transcript",
        video_id="abc123def45",
        title="Demo",
        channel="Channel",
        webpage_url="https://www.youtube.com/watch?v=abc123def45",
        start_seconds=10.0,
        end_seconds=14.0,
        score=0.9,
        match_text="Intro",
        context="Intro context",
        output_path=None,
    )


def _manifest_record(
    video_id: str,
    *,
    title: str = "Demo",
    channel: str = "Channel",
    downloaded_at: str = "2026-03-08T00:00:00Z",
) -> ManifestRecord:
    return ManifestRecord(
        video_id=video_id,
        title=title,
        channel=channel,
        upload_date="2026-03-07",
        duration_seconds=91,
        extractor_key="youtube",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
        output_path=f"/tmp/{video_id}.mp4",
        requested_input=f"https://www.youtube.com/watch?v={video_id}",
        source_query=None,
        downloaded_at=downloaded_at,
        info_json_path=None,
    )


def _write_manifest(settings, records: list[ManifestRecord]) -> None:
    settings.manifest_file.parent.mkdir(parents=True, exist_ok=True)
    settings.manifest_file.write_text(
        "\n".join(json.dumps(record.as_dict(), sort_keys=True) for record in records) + "\n",
        encoding="utf-8",
    )


def _upsert_catalog_video(settings, video_id: str, *, channel: str = "Channel") -> None:
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    store.upsert_video(
        VideoUpsert(
            video_id=video_id,
            title="Demo",
            channel=channel,
            upload_date="2026-03-07",
            duration_seconds=91,
            extractor_key="youtube",
            webpage_url=f"https://www.youtube.com/watch?v={video_id}",
            requested_input=f"https://www.youtube.com/watch?v={video_id}",
            source_query=None,
            output_path=settings.download_root / channel / f"{video_id}.mp4",
            info_json_path=None,
            downloaded_at="2026-03-08T00:00:00Z",
            indexed_at="2026-03-08T00:00:00Z",
        )
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


def test_completions_install_detects_shell_from_env(monkeypatch, tmp_path) -> None:
    installed_path = tmp_path / "_yt-agent"
    observed: dict[str, str] = {}

    def fake_install(*, shell, prog_name, complete_var):
        observed["shell"] = shell
        observed["prog_name"] = prog_name
        observed["complete_var"] = complete_var
        return shell, installed_path

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr("yt_agent.cli.typer_completion_install", fake_install)

    result = runner.invoke(app, ["completions", "install"])

    assert result.exit_code == 0
    assert observed == {
        "shell": "zsh",
        "prog_name": "yt-agent",
        "complete_var": "_YT_AGENT_COMPLETE",
    }
    assert "Installed zsh completion" in result.stdout
    assert str(installed_path) in result.stdout.replace("\n", "")
    assert "Restart your terminal to enable it." in result.stdout
    assert result.stderr == ""


def test_completions_install_shell_flag_overrides_env(monkeypatch, tmp_path) -> None:
    installed_path = tmp_path / "yt-agent.bash"
    observed: dict[str, str] = {}

    def fake_install(*, shell, prog_name, complete_var):
        observed["shell"] = shell
        return shell, installed_path

    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr("yt_agent.cli.typer_completion_install", fake_install)

    result = runner.invoke(app, ["completions", "install", "--shell", "bash", "--output", "json"])

    assert result.exit_code == 0
    assert observed["shell"] == "bash"
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "command": "completions install",
        "status": "ok",
        "summary": {"installed": 1, "shell": "bash"},
        "warnings": [],
        "errors": [],
        "shell": "bash",
        "path": str(installed_path),
        "restart_required": True,
    }
    assert result.stderr == ""


def test_completions_show_prints_script(monkeypatch) -> None:
    script = "complete -F _yt_agent_completion yt-agent"
    observed: dict[str, str] = {}

    def fake_get_completion_script(*, prog_name, complete_var, shell):
        observed["prog_name"] = prog_name
        observed["complete_var"] = complete_var
        observed["shell"] = shell
        return script

    monkeypatch.setenv("SHELL", "/usr/local/bin/fish")
    monkeypatch.setattr("yt_agent.cli.get_completion_script", fake_get_completion_script)

    result = runner.invoke(app, ["completions", "show"])

    assert result.exit_code == 0
    assert observed == {
        "prog_name": "yt-agent",
        "complete_var": "_YT_AGENT_COMPLETE",
        "shell": "fish",
    }
    assert result.stdout == f"{script}\n"
    assert result.stderr == ""


def test_completions_show_json_output(monkeypatch) -> None:
    script = "line1\nline2"
    monkeypatch.setattr(
        "yt_agent.cli.get_completion_script",
        lambda *, prog_name, complete_var, shell: script,
    )

    result = runner.invoke(app, ["completions", "show", "--shell", "zsh", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "schema_version": 1,
        "command": "completions show",
        "status": "ok",
        "summary": {"lines": 2, "shell": "zsh"},
        "warnings": [],
        "errors": [],
        "shell": "zsh",
        "script": script,
    }
    assert result.stderr == ""


def test_doctor_json_output_is_machine_readable(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.shutil.which", lambda _: "/usr/bin/tool")
    result = runner.invoke(app, ["doctor", "--output", "json"])
    assert result.exit_code == 0
    assert '"tools"' in result.stdout
    assert '"paths"' in result.stdout


@pytest.mark.parametrize(
    ("scenario", "argv", "expected_exit_code", "stderr_fragment"),
    [
        ("ok", ["--version"], 0, None),
        ("dependency", ["doctor"], 3, "yt-dlp is required"),
        ("input", ["info", "bad"], 4, "bad target"),
        ("config", ["config", "validate"], 5, "bad config"),
        ("external", ["info", "abc123def45"], 6, "boom"),
        ("busy", ["index", "refresh"], 7, "Another yt-agent operation is already running."),
        ("storage", ["library", "stats"], 8, "catalog database error: database is locked"),
        ("interrupted", ["search", "demo"], 130, "Interrupted."),
    ],
)
def test_cli_exit_code_matrix(settings, monkeypatch, scenario, argv, expected_exit_code, stderr_fragment) -> None:
    if scenario != "ok":
        monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    if scenario == "dependency":
        monkeypatch.setattr("yt_agent.cli.shutil.which", lambda _: None)
    elif scenario == "input":
        monkeypatch.setattr(
            "yt_agent.cli.yt_dlp.fetch_info",
            lambda target: (_ for _ in ()).throw(InvalidInputError("bad target")),
        )
    elif scenario == "config":
        monkeypatch.setattr(
            "yt_agent.cli._load_settings",
            lambda config=None: (_ for _ in ()).throw(ConfigError("bad config")),
        )
    elif scenario == "external":
        monkeypatch.setattr(
            "yt_agent.cli.yt_dlp.fetch_info",
            lambda target: (_ for _ in ()).throw(ExternalCommandError("boom", stderr="bad stderr")),
        )
    elif scenario == "storage":
        monkeypatch.setattr(
            "yt_agent.cli._catalog",
            lambda current_settings, readonly=False: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
        )
    elif scenario == "interrupted":
        monkeypatch.setattr(
            "yt_agent.cli.yt_dlp.search",
            lambda query, limit=None: (_ for _ in ()).throw(KeyboardInterrupt),
        )

    if scenario == "busy":
        with operation_lock(settings.catalog_file.parent / "operation.lock"):
            result = runner.invoke(app, argv)
    else:
        result = runner.invoke(app, argv)

    assert result.exit_code == expected_exit_code
    if stderr_fragment is None:
        assert result.stderr == ""
    else:
        assert stderr_fragment in result.stderr.replace("\n", " ")


def test_search_with_no_results_exits_zero(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [])
    result = runner.invoke(app, ["search", "demo"])
    assert result.exit_code == 0
    assert "No matches found." in result.stdout


def test_verbose_search_json_logs_debug_to_stderr(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.yt_dlp.command_path", lambda: "/usr/bin/yt-dlp")
    monkeypatch.setattr(
        "yt_agent.yt_dlp.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(
                {
                    "entries": [
                        {
                            "id": "abc123def45",
                            "title": "Demo",
                            "channel": "Channel",
                            "duration": 91,
                            "extractor_key": "youtube",
                            "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                            "upload_date": "20260307",
                        }
                    ]
                }
            ),
            stderr="",
        ),
    )

    result = runner.invoke(app, ["--verbose", "search", "demo", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["video_id"] == "abc123def45"
    assert "Running subprocess:" in result.stderr
    assert "/usr/bin/yt-dlp --dump-single-json --no-warnings ytsearch10:demo" in result.stderr
    assert "Finished command callback=_command elapsed_ms=" in result.stderr


def test_history_table_shows_recent_manifest_downloads_newest_first(settings, monkeypatch) -> None:
    _write_manifest(
        settings,
        [
            _manifest_record("old123abc45", title="Old", downloaded_at="2026-03-08T00:00:00Z"),
            _manifest_record("new123abc45", title="New", downloaded_at="2026-03-09T00:00:00Z"),
        ],
    )
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["history"])

    assert result.exit_code == 0
    assert "Download History" in result.stdout
    assert result.stdout.index("New") < result.stdout.index("Old")
    assert "new123abc45" in result.stdout
    assert "old123abc45" in result.stdout


def test_history_json_output_returns_structured_array(settings, monkeypatch) -> None:
    _write_manifest(
        settings,
        [_manifest_record("abc123def45", title="Demo", downloaded_at="2026-03-08T00:00:00Z")],
    )
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["history", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == [
        {
            "video_id": "abc123def45",
            "title": "Demo",
            "channel": "Channel",
            "downloaded_at": "2026-03-08T00:00:00Z",
        }
    ]


def test_history_limit_applies(settings, monkeypatch) -> None:
    _write_manifest(
        settings,
        [
            _manifest_record(
                f"video{i:08d}"[-11:],
                title=f"Video {i}",
                downloaded_at=f"2026-03-{i + 1:02d}T00:00:00Z",
            )
            for i in range(1, 7)
        ],
    )
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["history", "--limit", "5", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 5
    assert payload[0]["title"] == "Video 6"
    assert payload[-1]["title"] == "Video 2"


def test_history_channel_filter_applies(settings, monkeypatch) -> None:
    _write_manifest(
        settings,
        [
            _manifest_record("abc123def45", channel="Alpha"),
            _manifest_record("def123abc45", channel="Beta"),
            _manifest_record("ghi123abc45", channel="Alpha"),
        ],
    )
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["history", "--channel", "Alpha", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [row["channel"] for row in payload] == ["Alpha", "Alpha"]
    assert [row["video_id"] for row in payload] == ["ghi123abc45", "abc123def45"]


def test_search_json_output_is_machine_readable(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [_video()])
    result = runner.invoke(app, ["search", "demo", "--output", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["video_id"] == "abc123def45"
    assert payload[0]["webpage_url"] == "https://www.youtube.com/watch?v=abc123def45"
    assert payload[0]["index"] == 1


def test_search_plain_output_strips_control_characters(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.search",
        lambda query, limit: [
            VideoInfo(
                video_id="abc123def45",
                title="bad\nline\x1b[31m",
                channel="chan\r\nnel",
                upload_date="2026-03-07",
                duration_seconds=91,
                extractor_key="youtube",
                webpage_url="https://www.youtube.com/watch?v=abc123def45",
            )
        ],
    )
    result = runner.invoke(app, ["search", "demo", "--output", "plain"])
    assert result.exit_code == 0
    assert "\x1b" not in result.stdout
    assert "bad line" in result.stdout
    assert "chan nel" in result.stdout


def test_info_playlist_plain_output_includes_entries(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="playlist", info=_video())
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
        lambda target, current_payload: ResolutionResult([first, second], ["Skipped private entry"]),
    )

    result = runner.invoke(app, ["info", "playlist", "--entries", "--output", "plain"])
    normalized_output = result.stdout.replace("\n", "")

    assert result.exit_code == 0
    assert "type" in normalized_output
    assert "playlist" in normalized_output
    assert "entries_count" in normalized_output
    assert "abc123def45" in normalized_output
    assert "def123abc45" in normalized_output


def test_info_playlist_table_output_includes_entry_table(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="playlist", info=_video())
    payload = {
        "title": "Playlist",
        "channel": "Channel",
        "webpage_url": "https://www.youtube.com/playlist?list=PL123",
        "entries": [{"id": "abc123def45"}],
    }

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.fetch_info", lambda target: payload)
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.resolve_payload",
        lambda target, current_payload: ResolutionResult([first], ["Skipped private entry"]),
    )

    result = runner.invoke(app, ["info", "playlist", "--entries"])

    assert result.exit_code == 0
    assert "Metadata" in result.stdout
    assert "Playlist Entries" in result.stdout
    assert "Skipped private entry" in result.stdout
    assert "abc123def45" in result.stdout


def test_info_invalid_input_returns_input_exit_code(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.fetch_info", lambda target: (_ for _ in ()).throw(InvalidInputError("bad target")))
    result = runner.invoke(app, ["info", "bad"])
    assert result.exit_code == 4
    assert "bad target" in result.stderr


def test_external_error_output_strips_terminal_escapes(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.fetch_info",
        lambda target: (_ for _ in ()).throw(ExternalCommandError("boom", stderr="bad\x1b[31m\nnext")),
    )
    result = runner.invoke(app, ["info", "abc123def45"])
    assert result.exit_code == 6
    assert "\x1b" not in result.stderr
    assert "bad next" in result.stderr


def test_download_continues_after_single_failure(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="first", info=_video("abc123def45", title="First"))
    second = DownloadTarget(original_input="second", info=_video("def123abc45", title="Second"))

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda targets, current_settings, source_query=None, select_playlist=False, use_fzf=False, selection=None, **kwargs: (
            [first, second],
            [],
        ),
    )

    def fake_download(target, current_settings, **kwargs):
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


@pytest.mark.parametrize(
    ("error", "expected_payload"),
    [
        (
            DependencyError("missing yt-dlp"),
            {
                "schema_version": 1,
                "status": "error",
                "exit_code": 3,
                "error_type": "DependencyError",
                "message": f"missing yt-dlp. Install it with `{dependency_install_hint('yt-dlp')}` and retry.",
            },
        ),
        (
            InvalidInputError("bad target"),
            {
                "schema_version": 1,
                "status": "error",
                "exit_code": 4,
                "error_type": "InvalidInputError",
                "message": "bad target",
            },
        ),
        (
            ConfigError("bad config"),
            {
                "schema_version": 1,
                "status": "error",
                "exit_code": 5,
                "error_type": "ConfigError",
                "message": "bad config",
            },
        ),
        (
            SelectionError("pick one result"),
            {
                "schema_version": 1,
                "status": "error",
                "exit_code": 4,
                "error_type": "SelectionError",
                "message": "pick one result",
            },
        ),
        (
            ExternalCommandError("boom", stderr="bad\x1b[31m\nnext"),
            {
                "schema_version": 1,
                "status": "error",
                "exit_code": 6,
                "error_type": "ExternalCommandError",
                "message": "boom. Retry the command. If it keeps failing, try again later.",
                "stderr": "bad next",
            },
        ),
        (
            StateLockError("Another yt-agent operation is already running."),
            {
                "schema_version": 1,
                "status": "error",
                "exit_code": 7,
                "error_type": "StateLockError",
                "message": "Another yt-agent operation is already running.",
            },
        ),
    ],
)
def test_run_guarded_emits_json_error_envelope_for_all_yt_agent_errors(capsys, error, expected_payload) -> None:
    def boom() -> None:
        raise error

    with pytest.raises(typer.Exit) as exc_info:
        _run_guarded(boom, output_mode="json")

    assert exc_info.value.exit_code == expected_payload["exit_code"]
    assert json.loads(capsys.readouterr().err) == expected_payload


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

    def fake_download(target, current_settings, **kwargs):
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

    def fake_download(target, current_settings, **kwargs):
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
    monkeypatch.setattr("yt_agent.cli._catalog", lambda settings, readonly=False: FakeStore())
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
    monkeypatch.setattr("yt_agent.cli._catalog", lambda settings, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "stats", "--output", "json"])
    assert result.exit_code == 0
    assert '"videos": 3' in result.stdout


def test_verbose_library_stats_logs_sql_queries(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    _upsert_catalog_video(settings, "abc123def45")

    result = runner.invoke(app, ["--verbose", "library", "stats", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["videos"] == 1
    assert "SQL: PRAGMA foreign_keys = ON" in result.stderr
    assert "SQL:" in result.stderr
    assert "SELECT" in result.stderr


def test_config_init_writes_starter_file(settings, monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings.__class__(**{**settings.__dict__, "config_path": config_path}))
    result = runner.invoke(app, ["config", "init"])
    assert result.exit_code == 0
    assert config_path.exists()
    assert 'download_root = "' in config_path.read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission modes only")
def test_config_init_applies_private_mode(settings, monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings.__class__(**{**settings.__dict__, "config_path": config_path}))

    result = runner.invoke(app, ["config", "init"])

    assert result.exit_code == 0
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_download_counts_yt_dlp_skip_as_skipped(settings, monkeypatch) -> None:
    target = DownloadTarget(original_input="first", info=_video("abc123def45", title="First"))

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda targets, current_settings, source_query=None, select_playlist=False, use_fzf=False, selection=None, **kwargs: (
            [target],
            [],
        ),
    )
    monkeypatch.setattr("yt_agent.cli.yt_dlp.download_target", lambda target, current_settings, **kwargs: None)
    result = runner.invoke(app, ["download", "first"])
    assert result.exit_code == 0
    assert "0 downloaded, 1 skipped, 0 failed" in result.stdout
    assert "Skipping archived (detected by yt-dlp)" in result.stdout


def test_run_guarded_catches_sqlite_error(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._catalog",
        lambda s, readonly=False: (_ for _ in ()).throw(sqlite3.DatabaseError("database disk image is malformed")),
    )
    result = runner.invoke(app, ["library", "stats"])
    assert result.exit_code == 8
    assert "catalog database error" in result.stderr


# --- config validate ---


def test_config_validate_exits_zero_for_valid_config(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    result = runner.invoke(app, ["config", "validate"])
    assert result.exit_code == 0
    assert "Config is valid" in result.stdout


def test_config_validate_exits_config_code_for_invalid_config(monkeypatch) -> None:
    from yt_agent.errors import ConfigError

    monkeypatch.setattr(
        "yt_agent.cli._load_settings",
        lambda config=None: (_ for _ in ()).throw(ConfigError("bad key")),
    )
    result = runner.invoke(app, ["config", "validate"])
    assert result.exit_code == 5


# --- library channels ---


def test_library_channels_table_output(settings, monkeypatch) -> None:
    class FakeStore:
        def list_channels(self):
            return ["Alpha", "Zeta"]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "channels"])
    assert result.exit_code == 0
    assert "Alpha" in result.stdout
    assert "Zeta" in result.stdout


def test_library_channels_json_output(settings, monkeypatch) -> None:
    class FakeStore:
        def list_channels(self):
            return ["Alpha", "Zeta"]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "channels", "--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data == [{"channel": "Alpha"}, {"channel": "Zeta"}]


def test_library_channels_empty_exits_zero(settings, monkeypatch) -> None:
    class FakeStore:
        def list_channels(self):
            return []

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "channels"])
    assert result.exit_code == 0
    assert "No channels found." in result.stdout


# --- library playlists ---


def test_library_playlists_table_output(settings, monkeypatch) -> None:
    class FakeStore:
        def list_playlists(self):
            return [{"playlist_id": "PL123", "title": "My Playlist", "channel": "Channel", "entry_count": "3"}]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "playlists"])
    assert result.exit_code == 0
    assert "My Playlist" in result.stdout


def test_library_playlists_json_output(settings, monkeypatch) -> None:
    class FakeStore:
        def list_playlists(self):
            return [{"playlist_id": "PL123", "title": "My Playlist", "channel": "Channel", "entry_count": "3"}]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "playlists", "--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data[0]["playlist_id"] == "PL123"


def test_library_playlists_json_output_preserves_machine_readable_strings(settings, monkeypatch) -> None:
    class FakeStore:
        def list_playlists(self):
            return [
                {
                    "playlist_id": "PL123",
                    "title": "Bad\nTitle\x1b[31m",
                    "channel": "Chan\tName",
                    "entry_count": "3",
                }
            ]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "playlists", "--output", "json"])

    assert result.exit_code == 0
    assert "\\u001b" in result.stdout
    data = json.loads(result.stdout)
    assert data == [
        {
            "playlist_id": "PL123",
            "title": "Bad\nTitle\x1b[31m",
            "channel": "Chan\tName",
            "entry_count": "3",
        }
    ]


def test_library_playlists_empty_exits_zero(settings, monkeypatch) -> None:
    class FakeStore:
        def list_playlists(self):
            return []

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "playlists"])
    assert result.exit_code == 0
    assert "No playlists found." in result.stdout


# --- library remove ---


def test_library_remove_prints_removed_and_not_found(settings, monkeypatch) -> None:
    class FakeStore:
        def delete_video(self, video_id):
            return video_id == "abc123def45"

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "remove", "abc123def45", "unknownvid00"])
    assert result.exit_code == 0
    assert "Removed" in result.stdout
    assert "abc123def45" in result.stdout
    assert "Not found" in result.stdout
    assert "unknownvid00" in result.stdout


def test_library_remove_json_output(settings, monkeypatch) -> None:
    class FakeStore:
        def delete_video(self, video_id):
            return True

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda s, readonly=False: FakeStore())
    result = runner.invoke(app, ["library", "remove", "abc123def45", "--output", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "abc123def45" in data["removed"]
    assert data["not_found"] == []


# --- download --audio ---


def test_download_audio_flag_passes_audio_mode(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="first", info=_video("abc123def45"))
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda inputs, s, **kw: ([first], []),
    )
    captured_kwargs: list[dict] = []

    def fake_download(target, current_settings, **kwargs):
        captured_kwargs.append(kwargs)
        return DownloadExecution(
            output_path=current_settings.download_root / "Channel" / "file.mp3",
            stdout="",
            info_json_path=None,
        )

    monkeypatch.setattr("yt_agent.cli.yt_dlp.download_target", fake_download)
    monkeypatch.setattr("yt_agent.cli.index_manifest_record", lambda *args, **kwargs: None)
    result = runner.invoke(app, ["download", "--audio", "abc123def45"])
    assert result.exit_code == 0
    assert captured_kwargs[0]["mode"] == "audio"


# --- download --fetch-subs ---


def test_download_fetch_subs_flag_passes_through(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="first", info=_video("abc123def45"))
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda inputs, s, **kw: ([first], []),
    )
    captured_kwargs: list[dict] = []

    def fake_download(target, current_settings, **kwargs):
        captured_kwargs.append(kwargs)
        return DownloadExecution(
            output_path=current_settings.download_root / "Channel" / "file.mp4",
            stdout="",
            info_json_path=None,
        )

    monkeypatch.setattr("yt_agent.cli.yt_dlp.download_target", fake_download)
    monkeypatch.setattr("yt_agent.cli.index_manifest_record", lambda *args, **kwargs: None)
    result = runner.invoke(app, ["download", "--fetch-subs", "abc123def45"])
    assert result.exit_code == 0
    assert captured_kwargs[0]["fetch_subs"] is True


# --- download --from-file ---


def test_download_from_file_reads_targets(settings, monkeypatch, tmp_path) -> None:
    target_file = tmp_path / "urls.txt"
    target_file.write_text(
        "https://www.youtube.com/watch?v=abc123def45\n# comment\n\nabc123def45\n",
        encoding="utf-8",
    )
    collected: list[list[str]] = []

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda inputs, s, **kw: (collected.append(list(inputs)) or ([], [])),
    )
    result = runner.invoke(app, ["download", "--from-file", str(target_file)])
    assert result.exit_code == 0
    assert collected[0] == ["https://www.youtube.com/watch?v=abc123def45", "abc123def45"]


def test_download_from_file_merges_with_positional(settings, monkeypatch, tmp_path) -> None:
    target_file = tmp_path / "urls.txt"
    target_file.write_text("def123abc45\n", encoding="utf-8")
    collected: list[list[str]] = []

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda inputs, s, **kw: (collected.append(list(inputs)) or ([], [])),
    )
    result = runner.invoke(app, ["download", "abc123def45", "--from-file", str(target_file)])
    assert result.exit_code == 0
    assert "abc123def45" in collected[0]
    assert "def123abc45" in collected[0]


def test_download_from_file_not_found_exits_input_error(settings, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    result = runner.invoke(app, ["download", "--from-file", str(tmp_path / "nonexistent.txt")])
    assert result.exit_code == 4


def test_download_no_targets_exits_input_error(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    result = runner.invoke(app, ["download"])
    assert result.exit_code == 4


def test_download_dry_run_json_envelope_does_not_write(settings, monkeypatch) -> None:
    target = DownloadTarget(original_input="abc123def45", info=_video())

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda inputs, current_settings, **kwargs: ([target], ["Skipped unavailable playlist entry #2 from abc123def45."]),
    )
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.download_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("download_target should not run during dry-run")),
    )
    monkeypatch.setattr(
        "yt_agent.cli.append_manifest_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("manifest writes should not happen during dry-run")),
    )
    monkeypatch.setattr(
        "yt_agent.cli.index_manifest_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("indexing should not happen during dry-run")),
    )

    result = runner.invoke(app, ["download", "abc123def45", "--dry-run", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "download"
    assert payload["status"] == "noop"
    assert payload["summary"] == {
        "requested": 1,
        "resolved": 1,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "dry_run": True,
    }
    assert payload["requested"] == ["abc123def45"]
    assert payload["resolved_targets"][0]["video_id"] == "abc123def45"
    assert payload["warnings"] == ["Skipped unavailable playlist entry #2 from abc123def45."]
    assert payload["errors"] == []
    assert payload["downloaded"] == []
    assert payload["skipped"] == []
    assert payload["failed"] == []
    assert payload["mode"] == "video"
    assert payload["fetch_subs"] is False
    assert payload["auto_subs"] is False
    assert payload["download_root"] == str(settings.download_root)
    assert payload["dry_run"] is True
    assert result.stderr == ""
    assert not settings.download_root.exists()
    assert not settings.archive_file.exists()
    assert not settings.manifest_file.exists()
    assert not settings.catalog_file.exists()
    assert not settings.clips_root.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()


def test_index_refresh_dry_run_json_envelope_does_not_write(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.iter_manifest_records", lambda path: [object(), object()])
    monkeypatch.setattr(
        "yt_agent.cli.index_refresh",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("index_refresh should not run during dry-run")),
    )

    result = runner.invoke(app, ["index", "refresh", "--dry-run", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "schema_version": 1,
        "command": "index refresh",
        "status": "noop",
        "summary": {
            "videos": 2,
            "playlists": 0,
            "chapters": 0,
            "transcript_segments": 0,
        },
        "warnings": [],
        "errors": [],
        "requested": ["manifest"],
        "fetch_subs": False,
        "auto_subs": False,
        "network_fetch_attempted": False,
        "dry_run": True,
    }
    assert result.stderr == ""
    assert not settings.archive_file.exists()
    assert not settings.manifest_file.exists()
    assert not settings.catalog_file.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()


def test_library_remove_dry_run_json_envelope_does_not_write(settings, monkeypatch) -> None:
    class FakeStore:
        def __init__(self, path, *, readonly=False):
            self.path = path

        def get_video(self, video_id, readonly=False):
            if video_id == "abc123def45":
                return object()
            return None

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.CatalogStore", FakeStore)

    result = runner.invoke(
        app,
        ["library", "remove", "abc123def45", "missingvid00", "--dry-run", "--output", "json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "schema_version": 1,
        "command": "library remove",
        "status": "noop",
        "summary": {
            "requested": 2,
            "removed": 1,
            "not_found": 1,
            "dry_run": True,
        },
        "warnings": [],
        "errors": [],
        "requested": ["abc123def45", "missingvid00"],
        "removed": ["abc123def45"],
        "not_found": ["missingvid00"],
        "dry_run": True,
    }
    assert result.stderr == ""
    assert not settings.catalog_file.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()


def test_clips_grab_dry_run_json_uses_result_locator(settings, monkeypatch) -> None:
    media_path = settings.download_root / "Channel" / "demo.mp4"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"video")

    class FakeStore:
        def __init__(self, path, *, readonly=False):
            self.path = path

        def get_clip_hit(self, result_id, readonly=False):
            assert result_id == "transcript:12"
            return type(
                "Hit",
                (),
                {
                    "video_id": "abc123def45",
                    "title": "Demo",
                    "channel": "Channel",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                    "start_seconds": 10.0,
                    "end_seconds": 14.0,
                    "source": "transcript",
                    "output_path": media_path,
                },
            )()

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.clips.CatalogStore", FakeStore)

    result = runner.invoke(app, ["clips", "grab", "transcript:12", "--dry-run", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "clips grab"
    assert payload["status"] == "noop"
    assert payload["summary"] == {"saved": 0, "dry_run": True}
    assert payload["locator"] == "transcript:12"
    assert payload["start_seconds"] == 10.0
    assert payload["end_seconds"] == 14.0
    assert payload["padding_before"] == 0.0
    assert payload["padding_after"] == 0.0
    assert payload["mode"] == "fast"
    assert payload["source"] == "local"
    assert payload["used_remote_fallback"] is False
    assert payload["dry_run"] is True
    assert payload["output_path"].endswith("transcript.mp4")
    assert result.stderr == ""
    assert not settings.clips_root.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()


def test_clips_grab_dry_run_json_uses_explicit_range_locator(settings, monkeypatch) -> None:
    media_path = settings.download_root / "Channel" / "demo.mkv"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"video")

    class FakeStore:
        def __init__(self, path, *, readonly=False):
            self.path = path

        def get_video(self, video_id, readonly=False):
            assert video_id == "abc123def45"
            return type(
                "Video",
                (),
                {
                    "video_id": "abc123def45",
                    "title": "Demo",
                    "channel": "Channel",
                    "upload_date": "2026-03-07",
                    "duration_seconds": 91,
                    "extractor_key": "youtube",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                    "requested_input": "https://www.youtube.com/watch?v=abc123def45",
                    "output_path": media_path,
                },
            )()

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.clips.CatalogStore", FakeStore)

    result = runner.invoke(
        app,
        [
            "clips",
            "grab",
            "--video-id",
            "abc123def45",
            "--start-seconds",
            "12.5",
            "--end-seconds",
            "18",
            "--dry-run",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "clips grab"
    assert payload["status"] == "noop"
    assert payload["summary"] == {"saved": 0, "dry_run": True}
    assert payload["locator"] == "abc123def45:12.500-18.000"
    assert payload["start_seconds"] == 12.5
    assert payload["end_seconds"] == 18.0
    assert payload["mode"] == "fast"
    assert payload["source"] == "local"
    assert payload["used_remote_fallback"] is False
    assert payload["dry_run"] is True
    assert payload["output_path"].endswith("range.mkv")
    assert result.stderr == ""
    assert not settings.clips_root.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()


def test_clips_grab_dry_run_requires_remote_fallback_when_local_media_is_missing(settings, monkeypatch) -> None:
    class FakeStore:
        def __init__(self, path, *, readonly=False):
            self.path = path

        def get_video(self, video_id, readonly=False):
            return type(
                "Video",
                (),
                {
                    "video_id": video_id,
                    "title": "Demo",
                    "channel": "Channel",
                    "upload_date": "2026-03-07",
                    "duration_seconds": 91,
                    "extractor_key": "youtube",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                    "requested_input": "https://www.youtube.com/watch?v=abc123def45",
                    "output_path": settings.download_root / "Channel" / "missing.mp4",
                },
            )()

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.clips.CatalogStore", FakeStore)

    result = runner.invoke(
        app,
        [
            "clips",
            "grab",
            "--video-id",
            "abc123def45",
            "--start-seconds",
            "12.5",
            "--end-seconds",
            "18",
            "--dry-run",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 4
    payload = json.loads(result.stderr)
    assert payload["message"] == "Local media is unavailable for this clip. Re-run with --remote-fallback."


def test_clips_grab_dry_run_remote_fallback_uses_template_path(settings, monkeypatch) -> None:
    class FakeStore:
        def __init__(self, path, *, readonly=False):
            self.path = path

        def get_video(self, video_id, readonly=False):
            return type(
                "Video",
                (),
                {
                    "video_id": video_id,
                    "title": "Demo",
                    "channel": "Channel",
                    "upload_date": "2026-03-07",
                    "duration_seconds": 91,
                    "extractor_key": "youtube",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                    "requested_input": "https://www.youtube.com/watch?v=abc123def45",
                    "output_path": None,
                },
            )()

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.clips.CatalogStore", FakeStore)

    result = runner.invoke(
        app,
        [
            "clips",
            "grab",
            "--video-id",
            "abc123def45",
            "--start-seconds",
            "12.5",
            "--end-seconds",
            "18",
            "--remote-fallback",
            "--dry-run",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source"] == "remote"
    assert payload["used_remote_fallback"] is True
    assert payload["output_path_is_template"] is True
    assert payload["output_path"].endswith(".%(ext)s")


@pytest.mark.parametrize(
    "argv",
    [
        ["download", "abc123def45", "--auto-subs", "--output", "json"],
        ["grab", "demo", "--select", "1", "--auto-subs", "--output", "json"],
        ["index", "refresh", "--auto-subs", "--output", "json"],
        ["index", "add", "abc123def45", "--auto-subs", "--output", "json"],
    ],
)
def test_mutating_commands_reject_auto_subs_without_fetch_subs(settings, monkeypatch, argv) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, argv)

    assert result.exit_code == 4
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload == {
        "schema_version": 1,
        "status": "error",
        "exit_code": 4,
        "error_type": "InvalidInputError",
        "message": "--auto-subs requires --fetch-subs.",
    }


def test_mutating_command_returns_json_busy_error_when_operation_lock_is_held(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    with operation_lock(settings.catalog_file.parent / "operation.lock"):
        result = runner.invoke(app, ["index", "refresh", "--output", "json"])

    assert result.exit_code == 7
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload == {
        "schema_version": 1,
        "status": "error",
        "exit_code": 7,
        "error_type": "StateLockError",
        "message": "Another yt-agent operation is already running.",
    }
    assert not settings.catalog_file.exists()


def test_cleanup_dry_run_lists_orphans_without_removing(settings, monkeypatch) -> None:
    _upsert_catalog_video(settings, "abc123def45")
    valid_cache_dir = settings.catalog_file.parent / "subtitle-cache" / "abc123def45"
    orphan_cache_dir = settings.catalog_file.parent / "subtitle-cache" / "orphan987654"
    valid_cache_dir.mkdir(parents=True, exist_ok=True)
    orphan_cache_dir.mkdir(parents=True, exist_ok=True)
    empty_dir = settings.download_root / "Empty Channel"
    empty_dir.mkdir(parents=True, exist_ok=True)
    part_file = settings.download_root / "Channel" / "video.mp4.part"
    part_file.parent.mkdir(parents=True, exist_ok=True)
    part_file.write_text("partial", encoding="utf-8")
    complete_file = settings.download_root / "Channel" / "video.mp4"
    complete_file.write_text("complete", encoding="utf-8")
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["cleanup", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry run:" in result.stdout
    assert "Cleanup Preview" in result.stdout
    # Check kind labels rather than full paths to avoid Rich fold-truncation on long tmp paths
    assert "cache_dir" in result.stdout
    assert "empty_dir" in result.stdout
    assert "part_file" in result.stdout
    assert orphan_cache_dir.exists()
    assert empty_dir.exists()
    assert part_file.exists()
    assert valid_cache_dir.exists()
    assert complete_file.exists()


def test_cleanup_dry_run_json_reports_orphans(settings, monkeypatch) -> None:
    orphan_cache_dir = settings.catalog_file.parent / "subtitle-cache" / "orphan987654"
    orphan_cache_dir.mkdir(parents=True, exist_ok=True)
    empty_dir = settings.download_root / "Empty Channel"
    empty_dir.mkdir(parents=True, exist_ok=True)
    part_file = settings.download_root / "Channel" / "video.mp4.part"
    part_file.parent.mkdir(parents=True, exist_ok=True)
    part_file.write_text("partial", encoding="utf-8")
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["cleanup", "--dry-run", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["command"] == "cleanup"
    assert payload["status"] == "noop"
    assert payload["summary"] == {
        "removed_cache_dirs": 1,
        "removed_empty_dirs": 1,
        "removed_part_files": 1,
        "dry_run": True,
    }
    assert payload["removed_cache_dirs"] == [str(orphan_cache_dir)]
    assert payload["removed_empty_dirs"] == [str(empty_dir)]
    assert payload["removed_part_files"] == [str(part_file)]
    assert payload["dry_run"] is True
    assert orphan_cache_dir.exists()
    assert empty_dir.exists()
    assert part_file.exists()


def test_cleanup_removes_orphaned_artifacts(settings, monkeypatch) -> None:
    _upsert_catalog_video(settings, "abc123def45")
    valid_cache_dir = settings.catalog_file.parent / "subtitle-cache" / "abc123def45"
    orphan_cache_dir = settings.catalog_file.parent / "subtitle-cache" / "orphan987654"
    valid_cache_dir.mkdir(parents=True, exist_ok=True)
    orphan_cache_dir.mkdir(parents=True, exist_ok=True)
    empty_dir = settings.download_root / "Empty Channel"
    empty_dir.mkdir(parents=True, exist_ok=True)
    part_file = settings.download_root / "Channel" / "video.mp4.part"
    part_file.parent.mkdir(parents=True, exist_ok=True)
    part_file.write_text("partial", encoding="utf-8")
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["cleanup", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["summary"] == {
        "removed_cache_dirs": 1,
        "removed_empty_dirs": 1,
        "removed_part_files": 1,
        "dry_run": False,
    }
    assert not orphan_cache_dir.exists()
    assert not empty_dir.exists()
    assert not part_file.exists()
    assert valid_cache_dir.exists()


def test_cleanup_returns_json_busy_error_when_operation_lock_is_held(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    settings.catalog_file.parent.mkdir(parents=True, exist_ok=True)

    with operation_lock(settings.catalog_file.parent / "operation.lock"):
        result = runner.invoke(app, ["cleanup", "--output", "json"])

    assert result.exit_code == 7
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload == {
        "schema_version": 1,
        "status": "error",
        "exit_code": 7,
        "error_type": "StateLockError",
        "message": "Another yt-agent operation is already running.",
    }


def test_run_guarded_catches_yt_agent_error_json(capsys) -> None:
    def boom() -> None:
        raise InvalidInputError("bad target")

    with pytest.raises(typer.Exit) as exc_info:
        _run_guarded(boom, output_mode="json")

    assert exc_info.value.exit_code == 4
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "schema_version": 1,
        "status": "error",
        "exit_code": 4,
        "error_type": "InvalidInputError",
        "message": "bad target",
    }


def test_run_guarded_catches_sqlite_error_json(capsys) -> None:
    def boom() -> None:
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(typer.Exit) as exc_info:
        _run_guarded(boom, output_mode="json")

    assert exc_info.value.exit_code == 8
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "schema_version": 1,
        "status": "error",
        "exit_code": 8,
        "error_type": "OperationalError",
        "message": "catalog database error: database is locked",
    }


def test_run_guarded_catches_keyboard_interrupt_json(capsys) -> None:
    def boom() -> None:
        raise KeyboardInterrupt

    with pytest.raises(typer.Exit) as exc_info:
        _run_guarded(boom, output_mode="json")

    assert exc_info.value.exit_code == 130
    payload = json.loads(capsys.readouterr().err)
    assert payload == {
        "schema_version": 1,
        "status": "error",
        "exit_code": 130,
        "error_type": "KeyboardInterrupt",
        "message": "Interrupted.",
    }


@pytest.mark.parametrize(
    ("argv", "setup"),
    [
        (
            ["download", "abc123def45", "--quiet"],
            "download",
        ),
        (
            ["grab", "demo", "--select", "1", "--quiet"],
            "grab",
        ),
        (
            ["index", "refresh", "--quiet"],
            "index-refresh",
        ),
        (
            ["index", "add", "abc123def45", "--quiet"],
            "index-add",
        ),
        (
            ["clips", "grab", "transcript:12", "--quiet"],
            "clips-grab",
        ),
    ],
)
def test_mutating_commands_quiet_suppress_success_output(settings, monkeypatch, argv, setup) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    if setup == "download":
        monkeypatch.setattr(
            "yt_agent.cli._resolve_download_inputs",
            lambda inputs, current_settings, **kwargs: ([DownloadTarget(original_input="abc123def45", info=_video())], []),
        )
        monkeypatch.setattr(
            "yt_agent.cli.yt_dlp.download_target",
            lambda *args, **kwargs: DownloadExecution(
                output_path=settings.download_root / "Channel" / "file.mp4",
                stdout="",
                info_json_path=settings.download_root / "Channel" / "file.mp4.info.json",
            ),
        )
        monkeypatch.setattr("yt_agent.cli.index_manifest_record", lambda *args, **kwargs: None)
    elif setup == "grab":
        monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [_video()])
        monkeypatch.setattr(
            "yt_agent.cli.yt_dlp.download_target",
            lambda *args, **kwargs: DownloadExecution(
                output_path=settings.download_root / "Channel" / "file.mp4",
                stdout="",
                info_json_path=settings.download_root / "Channel" / "file.mp4.info.json",
            ),
        )
        monkeypatch.setattr("yt_agent.cli.index_manifest_record", lambda *args, **kwargs: None)
    elif setup == "index-refresh":
        monkeypatch.setattr("yt_agent.cli.index_refresh", lambda *args, **kwargs: type("Summary", (), {"videos": 1, "playlists": 0, "chapters": 0, "transcript_segments": 0})())
    elif setup == "index-add":
        monkeypatch.setattr("yt_agent.cli.index_target", lambda *args, **kwargs: type("Summary", (), {"videos": 1, "playlists": 0, "chapters": 0, "transcript_segments": 0})())
    elif setup == "clips-grab":
        monkeypatch.setattr(
            "yt_agent.cli.extract_clip",
            lambda *args, **kwargs: type(
                "Extraction",
                (),
                {
                    "output_path": settings.clips_root / "clip.mp4",
                    "source": "local",
                    "start_seconds": 10.0,
                    "end_seconds": 14.0,
                    "used_remote_fallback": False,
                },
            )(),
        )

    result = runner.invoke(app, argv)

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_download_quiet_plain_keeps_failure_details_on_stderr(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda inputs, current_settings, **kwargs: ([DownloadTarget(original_input="abc123def45", info=_video())], []),
    )
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.download_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ExternalCommandError("yt-dlp download failed.", stderr="bad\x1b[31m\nnext")
        ),
    )

    result = runner.invoke(app, ["download", "abc123def45", "--quiet", "--output", "plain"])

    assert result.exit_code == 6
    assert result.stdout == ""
    assert "Failed: Demo [abc123def45] yt-dlp download failed. Retry the command." in result.stderr.replace("\n", " ")


def test_download_json_failure_sanitizes_stderr(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli._resolve_download_inputs",
        lambda inputs, current_settings, **kwargs: ([DownloadTarget(original_input="abc123def45", info=_video())], []),
    )
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.download_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ExternalCommandError("yt-dlp download failed.", stderr="bad\x1b[31m\nnext")
        ),
    )

    result = runner.invoke(app, ["download", "abc123def45", "--output", "json"])

    assert result.exit_code == 6
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["errors"][0]["stderr"] == "bad next"


def test_download_json_requires_select_for_select_playlist(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["download", "playlist", "--select-playlist", "--output", "json"])

    assert result.exit_code == 4
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["message"] == "--select-playlist with --output json requires --select."


def test_grab_dry_run_json_requires_select(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.search", lambda query, limit: [_video()])

    result = runner.invoke(app, ["grab", "demo", "--dry-run", "--output", "json"])

    assert result.exit_code == 4
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["message"] == "grab with --output json requires --select."


def test_grab_dry_run_json_envelope_does_not_write(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.search",
        lambda query, limit: [_video(), _video("def123abc45", title="Second")],
    )

    result = runner.invoke(app, ["grab", "demo", "--dry-run", "--select", "2", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "grab"
    assert payload["status"] == "noop"
    assert payload["summary"] == {
        "requested": 1,
        "resolved": 1,
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
        "dry_run": True,
    }
    assert payload["requested"] == ["demo"]
    assert payload["resolved_targets"][0]["video_id"] == "def123abc45"
    assert payload["dry_run"] is True
    assert result.stderr == ""
    assert not settings.download_root.exists()
    assert not settings.archive_file.exists()
    assert not settings.manifest_file.exists()
    assert not settings.catalog_file.exists()


def test_config_init_requires_force_to_overwrite(settings, monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("download_root = \"/tmp/existing\"\n", encoding="utf-8")
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings.__class__(**{**settings.__dict__, "config_path": config_path}))

    result = runner.invoke(app, ["config", "init"])

    assert result.exit_code == 4
    normalized = " ".join(result.stderr.split())
    assert "Use --force to overwrite" in normalized


def test_config_path_plain_output(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["config", "path", "--output", "plain"])
    normalized_output = result.stdout.replace("\n", "")

    assert result.exit_code == 0
    assert "config" in normalized_output
    assert str(settings.config_path) in normalized_output
    assert "catalog_file" in normalized_output
    assert str(settings.catalog_file) in normalized_output


def test_config_path_json_output(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["config", "path", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["config"] == str(settings.config_path)
    assert payload["clips_root"] == str(settings.clips_root)


def test_index_add_dry_run_json_envelope_does_not_write(settings, monkeypatch) -> None:
    first = DownloadTarget(original_input="playlist", info=_video())
    second = DownloadTarget(original_input="playlist", info=_video("def123abc45", title="Second"))
    payload = {
        "title": "Playlist",
        "entries": [{"id": "abc123def45"}, {"id": "def123abc45"}],
    }

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.yt_dlp.fetch_info", lambda target: payload)
    monkeypatch.setattr(
        "yt_agent.cli.yt_dlp.resolve_payload",
        lambda target, current_payload: ResolutionResult([first, second], []),
    )
    monkeypatch.setattr(
        "yt_agent.cli.index_target",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("index_target should not run during dry-run")),
    )

    result = runner.invoke(app, ["index", "add", "playlist", "--dry-run", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload == {
        "schema_version": 1,
        "command": "index add",
        "status": "noop",
        "summary": {
            "videos": 2,
            "playlists": 1,
            "chapters": 0,
            "transcript_segments": 0,
        },
        "warnings": [],
        "errors": [],
        "requested": ["playlist"],
        "fetch_subs": False,
        "auto_subs": False,
        "network_fetch_attempted": False,
        "dry_run": True,
    }
    assert result.stderr == ""
    assert not settings.catalog_file.exists()
    assert not (settings.catalog_file.parent / "operation.lock").exists()


def test_clips_search_json_empty(settings, monkeypatch) -> None:
    class FakeStore:
        def search_clips(self, query, source="all", channel=None, language=None, limit=10):
            return []

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["clips", "search", "missing", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_clips_show_json_output(settings, monkeypatch) -> None:
    class FakeStore:
        def get_clip_hit(self, result_id):
            assert result_id == "transcript:12"
            return _clip_hit(result_id)

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["clips", "show", "transcript:12", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["result_id"] == "transcript:12"
    assert payload["timestamp_url"].endswith("&t=10")
    assert payload["result_id_note"] == "Result ids are not durable across reindexing or catalog rebuilds."


def test_clips_grab_json_success_with_remote_fallback(settings, monkeypatch) -> None:
    extraction = type(
        "Extraction",
        (),
        {
            "output_path": settings.clips_root / "clip.mp4",
            "source": "remote",
            "start_seconds": 10.0,
            "end_seconds": 14.0,
            "used_remote_fallback": True,
        },
    )()

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli.extract_clip",
        lambda current_settings, result_id, **kwargs: extraction,
    )

    result = runner.invoke(app, ["clips", "grab", "transcript:12", "--remote-fallback", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["locator"] == "transcript:12"
    assert payload["source"] == "remote"
    assert payload["used_remote_fallback"] is True
    assert payload["output_path"].endswith("clip.mp4")


def test_library_list_json_empty(settings, monkeypatch) -> None:
    class FakeStore:
        def list_videos(self, **kwargs):
            return []

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "list", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_library_search_json_empty(settings, monkeypatch) -> None:
    class FakeStore:
        def search_videos(self, query, **kwargs):
            return []

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "search", "missing", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_library_list_rejects_conflicting_transcript_flags(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["library", "list", "--has-transcript", "--no-transcript", "--output", "json"])

    assert result.exit_code == 4
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["message"] == "Choose only one of --has-transcript or --no-has-transcript."


def test_library_show_json_output(settings, monkeypatch) -> None:
    class FakeStore:
        def get_video_details(self, video_id):
            assert video_id == "abc123def45"
            return {
                "video": _catalog_video(video_id),
                "chapters": [ChapterEntry(position=0, title="Intro", start_seconds=0.0, end_seconds=5.0)],
                "subtitle_tracks": [
                    SubtitleTrack(
                        lang="en",
                        source="manual",
                        is_auto=False,
                        format="vtt",
                        file_path=settings.download_root / "Demo.en.vtt",
                    )
                ],
                "transcript_preview": [TranscriptSegment(segment_index=0, start_seconds=0.0, end_seconds=2.0, text="Hello")],
            }

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "show", "abc123def45", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["video"]["video_id"] == "abc123def45"
    assert payload["chapters"][0]["title"] == "Intro"
    assert payload["subtitle_tracks"][0]["language"] == "en"
    assert payload["transcript_preview"][0]["text"] == "Hello"


def test_library_list_table_output(settings, monkeypatch) -> None:
    class FakeStore:
        def list_videos(self, **kwargs):
            return [_catalog_video()]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "list"])

    assert result.exit_code == 0
    assert "Library" in result.stdout
    assert "Demo" in result.stdout
    assert "Channel" in result.stdout


def test_library_search_plain_output(settings, monkeypatch) -> None:
    class FakeStore:
        def search_videos(self, query, **kwargs):
            return [_catalog_video("def123abc45", title="Second")]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "search", "demo", "--output", "plain"])
    normalized_output = result.stdout.replace("\n", "")

    assert result.exit_code == 0
    assert "def123abc45" in normalized_output
    assert "Second" in normalized_output
    assert "transcript_segments" in normalized_output


def test_library_show_plain_output(settings, monkeypatch) -> None:
    class FakeStore:
        def get_video_details(self, video_id):
            return {
                "video": _catalog_video(video_id),
                "chapters": [ChapterEntry(position=0, title="Intro", start_seconds=0.0, end_seconds=5.0)],
                "subtitle_tracks": [
                    SubtitleTrack(
                        lang="en",
                        source="manual",
                        is_auto=False,
                        format="vtt",
                        file_path=settings.download_root / "Demo.en.vtt",
                    )
                ],
                "transcript_preview": [TranscriptSegment(segment_index=0, start_seconds=0.0, end_seconds=2.0, text="Hello")],
            }

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "show", "abc123def45", "--output", "plain"])
    normalized_output = result.stdout.replace("\n", "")

    assert result.exit_code == 0
    assert "title" in normalized_output
    assert "Intro" in normalized_output
    assert "manual" in normalized_output
    assert "Hello" in normalized_output


def test_library_show_table_output(settings, monkeypatch) -> None:
    class FakeStore:
        def get_video_details(self, video_id):
            return {
                "video": _catalog_video(video_id),
                "chapters": [ChapterEntry(position=0, title="Intro", start_seconds=0.0, end_seconds=5.0)],
                "subtitle_tracks": [
                    SubtitleTrack(
                        lang="en",
                        source="manual",
                        is_auto=False,
                        format="vtt",
                        file_path=settings.download_root / "Demo.en.vtt",
                    )
                ],
                "transcript_preview": [TranscriptSegment(segment_index=0, start_seconds=0.0, end_seconds=2.0, text="Hello")],
            }

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "show", "abc123def45"])

    assert result.exit_code == 0
    assert "Video" in result.stdout
    assert "Chapters" in result.stdout
    assert "Subtitle Tracks" in result.stdout
    assert "Transcript Preview" in result.stdout


def test_library_stats_plain_output(settings, monkeypatch) -> None:
    class FakeStore:
        def library_stats(self):
            return {"videos": 3, "channels": 1}

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "stats", "--output", "plain"])
    normalized_output = result.stdout.replace("\n", "")

    assert result.exit_code == 0
    assert "videos" in normalized_output
    assert "3" in normalized_output
    assert "channels" in normalized_output


def test_library_stats_table_output(settings, monkeypatch) -> None:
    class FakeStore:
        def library_stats(self):
            return {"videos": 3, "channels": 1}

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "stats"])

    assert result.exit_code == 0
    assert "Library Stats" in result.stdout
    assert "videos" in result.stdout
    assert "channels" in result.stdout


def test_library_channels_plain_output(settings, monkeypatch) -> None:
    class FakeStore:
        def list_channels(self):
            return ["Alpha", "Zeta"]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "channels", "--output", "plain"])

    assert result.exit_code == 0
    assert "Alpha" in result.stdout
    assert "Zeta" in result.stdout


def test_library_playlists_plain_output(settings, monkeypatch) -> None:
    class FakeStore:
        def list_playlists(self):
            return [{"playlist_id": "PL123", "title": "My Playlist", "channel": "Channel", "entry_count": "3"}]

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["library", "playlists", "--output", "plain"])

    assert result.exit_code == 0
    assert "PL123" in result.stdout
    assert "My Playlist" in result.stdout


def test_clips_show_plain_output(settings, monkeypatch) -> None:
    class FakeStore:
        def get_clip_hit(self, result_id):
            return _clip_hit(result_id)

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["clips", "show", "transcript:12", "--output", "plain"])
    normalized_output = result.stdout.replace("\n", "")

    assert result.exit_code == 0
    assert "result_id_note" in normalized_output
    assert "Intro context" in normalized_output
    assert "remote only" not in normalized_output


def test_clips_show_table_output(settings, monkeypatch) -> None:
    class FakeStore:
        def get_clip_hit(self, result_id):
            return _clip_hit(result_id)

    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli._catalog", lambda current_settings, readonly=False: FakeStore())

    result = runner.invoke(app, ["clips", "show", "transcript:12"])

    assert result.exit_code == 0
    assert "Clip Hit" in result.stdout
    assert "result_id_note" in result.stdout
    assert "remote only" in result.stdout
