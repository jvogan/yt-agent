import json

from typer.testing import CliRunner

from yt_agent.cli import app
from yt_agent.sync import SyncItem, SyncReport, source_store_path

runner = CliRunner()


def test_sync_add_and_list_json(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    added = runner.invoke(
        app,
        [
            "sync",
            "add",
            "research",
            "https://www.youtube.com/@example/videos",
            "--kind",
            "channel",
            "--output",
            "json",
        ],
    )
    listed = runner.invoke(app, ["sync", "list", "--output", "json"])

    assert added.exit_code == 0
    assert json.loads(added.stdout)["source"]["name"] == "research"
    assert listed.exit_code == 0
    assert json.loads(listed.stdout)[0]["seen_count"] == 0
    assert source_store_path(settings).exists()


def test_sync_run_json_passes_incremental_controls(settings, monkeypatch) -> None:
    observed = {}
    report = SyncReport(
        dry_run=True,
        index=True,
        download=True,
        sources=1,
        items=(SyncItem("research", "abc123def45", "Demo", "would_download"),),
    )
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    def fake_run(current_settings, **kwargs):
        observed.update(kwargs)
        return report

    monkeypatch.setattr("yt_agent.cli.run_sync", fake_run)

    result = runner.invoke(
        app,
        [
            "sync",
            "run",
            "research",
            "--since",
            "2026-01-01",
            "--latest",
            "3",
            "--download",
            "--dry-run",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert observed == {
        "names": ["research"],
        "since": "2026-01-01",
        "latest": 3,
        "index": True,
        "download": True,
        "dry_run": True,
    }
    assert json.loads(result.stdout)["summary"]["would_download"] == 1


def test_sync_remove_missing_is_input_error(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["sync", "remove", "missing", "--output", "json"])

    assert result.exit_code == 4
    assert json.loads(result.stderr)["message"] == "Saved source not found: missing"
