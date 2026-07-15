import json

from typer.testing import CliRunner

from yt_agent.cli import app
from yt_agent.verify import VerifyFinding, VerifyReport

runner = CliRunner()


def _report() -> VerifyReport:
    return VerifyReport(
        deep=False,
        findings=(
            VerifyFinding(
                severity="error",
                code="media_missing",
                message="Catalog references a missing media file.",
                video_id="abc123def45",
                path="/tmp/missing.mp4",
            ),
        ),
        manifest_records=1,
        catalog_videos=1,
        media_checked=0,
    )


def test_verify_json_output(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.verify_library", lambda settings, deep=False: _report())

    result = runner.invoke(app, ["verify", "--output", "json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "issues"
    assert payload["summary"]["errors"] == 1
    assert payload["findings"][0]["code"] == "media_missing"


def test_verify_human_output(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr("yt_agent.cli.verify_library", lambda settings, deep=False: _report())

    result = runner.invoke(app, ["verify"])

    assert result.exit_code == 0
    assert "Verification Findings" in result.stdout
    assert "media_missing" in result.stdout
    assert "abc123def45" in result.stdout
