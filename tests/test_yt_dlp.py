import json
import subprocess

import pytest

from yt_agent.errors import InvalidInputError
from yt_agent.models import VideoInfo
from yt_agent.yt_dlp import ResolutionResult, normalize_target, resolve_payload, resolve_targets, search


def test_normalize_target_wraps_bare_youtube_id() -> None:
    assert normalize_target("abc123def45") == "https://www.youtube.com/watch?v=abc123def45"


def test_normalize_target_rejects_free_form_text() -> None:
    with pytest.raises(InvalidInputError):
        normalize_target("not a url")


def test_search_parses_dump_single_json(monkeypatch) -> None:
    payload = {
        "entries": [
            {
                "id": "abc123def45",
                "title": "Demo",
                "channel": "Channel",
                "duration": 91,
                "upload_date": "20260307",
                "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
                "extractor_key": "youtube",
            }
        ]
    }

    def fake_run(args, text, capture_output, check):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("yt_agent.yt_dlp.shutil.which", lambda _: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr("yt_agent.yt_dlp.subprocess.run", fake_run)
    results = search("demo", limit=5)
    assert results == [
        VideoInfo(
            video_id="abc123def45",
            title="Demo",
            channel="Channel",
            upload_date="2026-03-07",
            duration_seconds=91,
            extractor_key="youtube",
            webpage_url="https://www.youtube.com/watch?v=abc123def45",
            original_url=None,
        )
    ]


def test_resolve_targets_expands_playlist(monkeypatch) -> None:
    payload = {
        "title": "Playlist",
        "entries": [
            {
                "id": "abc123def45",
                "title": "First",
                "channel": "Channel",
                "duration": 91,
                "upload_date": "20260307",
                "extractor_key": "youtube",
            },
            None,
        ],
    }
    monkeypatch.setattr("yt_agent.yt_dlp.fetch_info", lambda target: payload)
    result = resolve_targets(["https://www.youtube.com/playlist?list=PL123"])
    assert isinstance(result, ResolutionResult)
    assert [item.info.video_id for item in result.targets] == ["abc123def45"]
    assert result.targets[0].info.webpage_url == "https://www.youtube.com/watch?v=abc123def45"
    assert "Skipped unavailable playlist entry #2" in result.skipped_messages[0]


def test_resolve_payload_handles_single_video() -> None:
    payload = {
        "id": "abc123def45",
        "title": "Demo",
        "channel": "Channel",
        "duration": 91,
        "upload_date": "20260307",
        "webpage_url": "https://www.youtube.com/watch?v=abc123def45",
        "extractor_key": "youtube",
    }
    result = resolve_payload("https://www.youtube.com/watch?v=abc123def45", payload)
    assert [item.info.video_id for item in result.targets] == ["abc123def45"]
    assert result.skipped_messages == []
