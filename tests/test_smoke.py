from typer.testing import CliRunner

from youtube_cli.cli import app


def test_doctor_smoke() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Bootstrap in progress." in result.stdout

