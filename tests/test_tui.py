from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import DataTable

from yt_agent.models import CatalogVideo, ChapterEntry, SubtitleTrack, TranscriptSegment
from yt_agent.tui import YtAgentTui, open_with_system_default


@dataclass
class FakeCatalog:
    def initialize(self) -> None:
        return None

    def list_channels(self) -> list[str]:
        return ["Channel"]

    def list_playlists(self) -> list[dict[str, Any]]:
        return [{"playlist_id": "PL123", "title": "Playlist", "entry_count": "1"}]

    def list_videos(
        self,
        *,
        channel: str | None = None,
        playlist_id: str | None = None,
        has_transcript: bool | None = None,
        has_chapters: bool | None = None,
        limit: int = 50,
    ) -> list[CatalogVideo]:
        return [
            CatalogVideo(
                video_id="abc123def45",
                title="Demo Video",
                channel="Channel",
                upload_date="2026-03-07",
                duration_seconds=120,
                extractor_key="youtube",
                webpage_url="https://www.youtube.com/watch?v=abc123def45",
                requested_input=None,
                source_query=None,
                output_path=Path("/tmp/demo.mp4"),
                info_json_path=Path("/tmp/demo.info.json"),
                downloaded_at="2026-03-08T00:00:00+00:00",
                chapter_count=1,
                transcript_segment_count=1,
                playlist_count=1,
            )
        ]

    def get_video_details(self, video_id: str) -> dict[str, Any] | None:
        return {
            "video": self.list_videos()[0],
            "chapters": [ChapterEntry(position=0, title="Intro", start_seconds=0.0, end_seconds=10.0)],
            "subtitle_tracks": [
                SubtitleTrack(
                    lang="en",
                    source="manual",
                    is_auto=False,
                    format="vtt",
                    file_path=Path("/tmp/demo.en.vtt"),
                )
            ],
            "transcript_preview": [
                TranscriptSegment(segment_index=0, start_seconds=1.0, end_seconds=4.0, text="hello world")
            ],
        }


@pytest.mark.asyncio
async def test_tui_renders_catalog_data() -> None:
    app = YtAgentTui(FakeCatalog())
    async with app.run_test() as pilot:
        table = app.query_one(DataTable)
        assert table.row_count == 1
        assert app.selected_video_id == "abc123def45"
        await pilot.press("r")
        assert table.row_count == 1


def test_open_with_system_default_returns_false_on_unsupported_platform(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("yt_agent.tui.sys.platform", "plan9")
    assert open_with_system_default(tmp_path / "demo.mp4") is False


def test_open_with_system_default_uses_linux_launcher(monkeypatch, tmp_path: Path) -> None:
    launched: list[list[str]] = []

    def fake_popen(args):
        launched.append(args)
        return None

    monkeypatch.setattr("yt_agent.tui.sys.platform", "linux")
    monkeypatch.setattr("yt_agent.tui.subprocess.Popen", fake_popen)
    path = tmp_path / "demo.mp4"
    assert open_with_system_default(path) is True
    assert launched == [["xdg-open", str(path)]]
