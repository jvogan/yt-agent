from typer.testing import CliRunner

from yt_agent.cli import app

runner = CliRunner()


def test_download_sponsorblock_remove_is_forwarded_explicitly(monkeypatch) -> None:
    observed = {}

    def fake_download(**kwargs):
        observed.update(kwargs)
        return {"summary": {"failed": 0}}

    monkeypatch.setattr("yt_agent.cli._download_command_impl", fake_download)

    result = runner.invoke(
        app,
        ["download", "abc123def45", "--sponsorblock-remove", "--dry-run", "--output", "json"],
    )

    assert result.exit_code == 0
    assert observed["sponsorblock"] is False
    assert observed["sponsorblock_remove"] is True


def test_grab_sponsorblock_mark_is_forwarded(monkeypatch) -> None:
    observed = {}

    def fake_grab(**kwargs):
        observed.update(kwargs)
        return {"summary": {"failed": 0}}

    monkeypatch.setattr("yt_agent.cli._grab_command_impl", fake_grab)

    result = runner.invoke(app, ["grab", "demo", "--sponsorblock", "--dry-run"])

    assert result.exit_code == 0
    assert observed["sponsorblock"] is True
    assert observed["sponsorblock_remove"] is False
