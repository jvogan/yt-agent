"""Textual catalog browser for yt-agent."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, ListItem, ListView, Static

from yt_agent.catalog import CatalogStore
from yt_agent.config import Settings
from yt_agent.models import CatalogVideo


class CatalogLike(Protocol):
    def initialize(self) -> None: ...
    def list_channels(self) -> list[str]: ...
    def list_playlists(self) -> list[dict[str, Any]]: ...
    def list_videos(
        self,
        *,
        channel: str | None = None,
        playlist_id: str | None = None,
        has_transcript: bool | None = None,
        has_chapters: bool | None = None,
        limit: int = 50,
    ) -> list[CatalogVideo]: ...
    def get_video_details(self, video_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class SourceItem:
    kind: str
    label: str
    value: str | None = None


class YtAgentTui(App[None]):
    """Read-mostly TUI backed by the local catalog."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #main {
        height: 1fr;
    }

    #sources {
        width: 28;
        border: solid $panel;
    }

    #videos {
        width: 2fr;
        border: solid $panel;
    }

    #details {
        width: 1fr;
        border: solid $panel;
        padding: 1 2;
        overflow-y: auto;
    }
    """

    BINDINGS = [
        ("r", "refresh_catalog", "Refresh"),
        ("o", "open_media", "Open Media"),
        ("c", "clip_action", "Clip"),
        ("d", "download_action", "Download"),
    ]

    selected_source: reactive[SourceItem | None] = reactive(None)
    selected_video_id: reactive[str | None] = reactive(None)

    def __init__(self, catalog: CatalogLike) -> None:
        super().__init__()
        self.catalog = catalog
        self._source_items: list[SourceItem] = []
        self._videos: list[CatalogVideo] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield ListView(id="sources")
            yield DataTable(id="videos")
            yield Static("Select a source to browse the catalog.", id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.catalog.initialize()
        sources = self.query_one("#sources", ListView)
        table = self.query_one("#videos", DataTable)
        table.cursor_type = "row"
        table.add_columns("Video ID", "Title", "Channel", "Duration", "Transcripts", "Chapters")
        self._populate_sources()
        if sources.children:
            sources.index = 0
            self._apply_source(self._source_items[0])

    def _populate_sources(self) -> None:
        sources = self.query_one("#sources", ListView)
        sources.clear()
        self._source_items = [SourceItem("all", "All Videos")]
        self._source_items.extend(SourceItem("channel", channel, channel) for channel in self.catalog.list_channels())
        self._source_items.extend(
            SourceItem("playlist", playlist["title"], str(playlist["playlist_id"]))
            for playlist in self.catalog.list_playlists()
        )
        for item in self._source_items:
            sources.append(ListItem(Label(item.label)))

    def _load_videos_for_source(self, item: SourceItem) -> list[CatalogVideo]:
        if item.kind == "channel":
            return self.catalog.list_videos(channel=item.value, limit=100)
        if item.kind == "playlist":
            return self.catalog.list_videos(playlist_id=item.value, limit=100)
        return self.catalog.list_videos(limit=100)

    def _apply_source(self, item: SourceItem) -> None:
        self.selected_source = item
        self._videos = self._load_videos_for_source(item)
        table = self.query_one("#videos", DataTable)
        table.clear()
        for video in self._videos:
            table.add_row(
                video.video_id,
                video.title,
                video.channel,
                video.display_duration,
                str(video.transcript_count),
                str(video.chapter_count),
                key=video.video_id,
            )
        if self._videos:
            table.move_cursor(row=0, column=0)
            self._set_selected_video(self._videos[0].video_id)
        else:
            self._set_selected_video(None)

    def _set_selected_video(self, video_id: str | None) -> None:
        self.selected_video_id = video_id
        details = self.query_one("#details", Static)
        if video_id is None:
            details.update("No videos found for this source.")
            return
        payload = self.catalog.get_video_details(video_id)
        if payload is None:
            details.update("Video details are unavailable.")
            return
        video = payload["video"]
        chapters = payload["chapters"]
        tracks = payload["subtitle_tracks"]
        preview = payload["transcript_preview"]
        lines = [
            f"[b]{video.title}[/b]",
            f"Channel: {video.channel}",
            f"Video ID: {video.video_id}",
            f"Duration: {video.display_duration}",
            f"Upload Date: {video.upload_date or 'undated'}",
            f"Path: {video.file_path or '-'}",
            f"Chapters: {len(chapters)}",
            f"Subtitle Tracks: {len(tracks)}",
            "",
        ]
        if chapters:
            lines.append("Chapters:")
            lines.extend(f"- {chapter.title}" for chapter in chapters[:5])
        if preview:
            if chapters:
                lines.append("")
            lines.append("Transcript Preview:")
            lines.extend(f"- {segment.text}" for segment in preview[:5])
        details.update("\n".join(lines))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "sources":
            return
        item = self._source_items[event.list_view.index]
        self._apply_source(item)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "videos":
            return
        if not self._videos:
            return
        row_index = event.cursor_row
        if 0 <= row_index < len(self._videos):
            self._set_selected_video(self._videos[row_index].video_id)

    def action_refresh_catalog(self) -> None:
        self.catalog.initialize()
        self._populate_sources()
        if self.selected_source is not None:
            self._apply_source(self.selected_source)
        self.notify("Catalog view refreshed.")

    def action_open_media(self) -> None:
        if self.selected_video_id is None:
            self.notify("No video selected.", severity="warning")
            return
        payload = self.catalog.get_video_details(self.selected_video_id)
        if payload is None or payload["video"].file_path is None:
            self.notify("Selected video has no local media path.", severity="warning")
            return
        path = Path(payload["video"].file_path)
        if not path.exists():
            self.notify("Local media path is missing on disk.", severity="warning")
            return
        subprocess.Popen(["open", str(path)])
        self.notify(f"Opened {path.name}")

    def action_clip_action(self) -> None:
        if self.selected_video_id is None:
            self.notify("No video selected.", severity="warning")
            return
        self.notify("Use `yt-agent clips search` or `yt-agent clips grab` for precise clip extraction.")

    def action_download_action(self) -> None:
        self.notify("Use `yt-agent download ...` or `yt-agent grab ...` from the CLI to add media.")


def launch_tui(settings: Settings) -> None:
    store = CatalogStore(settings.catalog_file)
    app = YtAgentTui(store)
    app.run()
