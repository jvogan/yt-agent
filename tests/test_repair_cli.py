import json

from typer.testing import CliRunner

from yt_agent.cli import app
from yt_agent.repair import RepairAction, RepairReport

runner = CliRunner()


def test_repair_defaults_to_preview_json(settings, monkeypatch) -> None:
    observed = []
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli.repair_library",
        lambda current, apply=False: (
            observed.append(apply)
            or RepairReport(False, (RepairAction("rebuild_fts", "planned"),))
        ),
    )

    result = runner.invoke(app, ["repair", "--output", "json"])

    assert result.exit_code == 0
    assert observed == [False]
    assert json.loads(result.stdout)["media_deleted"] == 0
