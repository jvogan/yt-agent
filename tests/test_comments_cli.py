import json

from typer.testing import CliRunner

from yt_agent.cli import app
from yt_agent.comments import CommentIndexReport

runner = CliRunner()


def test_comments_index_dry_run_json(settings, monkeypatch) -> None:
    observed = {}
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    def fake_index(current_settings, target, **kwargs):
        observed.update({"target": target, **kwargs})
        return CommentIndexReport("abc123def45", 5, 3, True)

    monkeypatch.setattr("yt_agent.cli.index_comments", fake_index)

    result = runner.invoke(
        app,
        ["comments", "index", "abc123def45", "--limit", "3", "--dry-run", "--output", "json"],
    )

    assert result.exit_code == 0
    assert observed == {"target": "abc123def45", "limit": 3, "dry_run": True}
    assert json.loads(result.stdout)["network_fetch_attempted"] is True


def test_comments_search_json(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli.search_comments",
        lambda current_settings, query, limit: [
            {
                "comment_id": "c1",
                "video_id": "abc123def45",
                "author": "Alice",
                "text": "Useful",
                "title": "Demo",
                "channel": "Channel",
                "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                "like_count": 2,
                "score": -1.0,
            }
        ],
    )

    result = runner.invoke(app, ["comments", "search", "useful", "--output", "json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["comment_id"] == "c1"


def test_comments_limit_is_bounded_by_cli(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)

    result = runner.invoke(app, ["comments", "index", "abc123def45", "--limit", "1001"])

    assert result.exit_code == 2


def test_comments_search_plain_sanitizes_rows(settings, monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.cli._load_settings", lambda config=None: settings)
    monkeypatch.setattr(
        "yt_agent.cli.search_comments",
        lambda *args, **kwargs: [
            {
                "video_id": "abc123def45",
                "author": "Alice\x1b[31m",
                "text": "hello\nworld",
            }
        ],
    )

    result = runner.invoke(app, ["comments", "search", "hello", "--output", "plain"])

    assert result.exit_code == 0
    assert result.stdout == "abc123def45 Alice hello world\n"
    assert "\x1b" not in result.stdout
