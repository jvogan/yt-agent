"""Textual catalog browser for yt-agent."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Input, Label, ListItem, ListView, Static

from yt_agent.catalog import CatalogStore, VideoDetails
from yt_agent.config import Settings
from yt_agent.job_queue import JobQueue, QueueJob
from yt_agent.models import CatalogVideo
from yt_agent.security import sanitize_terminal_text

__all__ = [
    "CatalogLike",
    "SourceItem",
    "YtAgentTui",
    "launch_tui",
    "open_with_system_default",
]



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
        offset: int = 0,
    ) -> list[CatalogVideo]: ...
    def search_videos(
        self,
        query: str,
        *,
        channel: str | None = None,
        playlist_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CatalogVideo]: ...
    def get_video_details(self, video_id: str) -> VideoDetails | None: ...


class QueueLike(Protocol):
    def ensure_schema(self) -> None: ...
    def add(
        self,
        operation: str,
        target: str,
        *,
        options: dict[str, Any] | None = None,
        max_retries: int = 2,
    ) -> QueueJob: ...


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

    #filter {
        margin: 0 1;
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
        ("n", "next_page", "Next Page"),
        ("p", "previous_page", "Previous Page"),
    ]

    selected_source: reactive[SourceItem | None] = reactive(None)
    selected_video_id: reactive[str | None] = reactive(None)
    filter_text: reactive[str] = reactive("")

    PAGE_SIZE = 50

    def __init__(
        self,
        catalog: CatalogLike,
        *,
        download_root: Path | None = None,
        queue: QueueLike | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self._download_root = download_root
        self._queue = queue
        self._source_items: list[SourceItem] = []
        self._videos: list[CatalogVideo] = []
        self._page = 0
        self._has_next_page = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder="Filter videos by title or channel", id="filter")
        with Horizontal(id="main"):
            yield ListView(id="sources")
            yield DataTable(id="videos")
            yield Static("Select a source to browse the catalog.", id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.catalog.initialize()
        if self._queue is not None:
            self._queue.ensure_schema()
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
        # Keep raw filter values for queries, but sanitize labels before they reach Textual widgets.
        self._source_items.extend(
            SourceItem("channel", sanitize_terminal_text(channel), channel)
            for channel in self.catalog.list_channels()
        )
        self._source_items.extend(
            SourceItem(
                "playlist",
                sanitize_terminal_text(playlist["title"]),
                str(playlist["playlist_id"]),
            )
            for playlist in self.catalog.list_playlists()
        )
        for item in self._source_items:
            sources.append(ListItem(Label(item.label)))

    def _load_videos_for_source(self, item: SourceItem) -> list[CatalogVideo]:
        kwargs: dict[str, Any] = {
            "limit": self.PAGE_SIZE + 1,
            "offset": self._page * self.PAGE_SIZE,
        }
        if item.kind == "channel":
            kwargs["channel"] = item.value
        elif item.kind == "playlist":
            kwargs["playlist_id"] = item.value
        query = self.filter_text.strip()
        if query:
            return self.catalog.search_videos(query, **kwargs)
        return self.catalog.list_videos(**kwargs)

    def _apply_source(self, item: SourceItem) -> None:
        self.selected_source = item
        self._page = 0
        self._load_page()

    def _load_page(self) -> None:
        if self.selected_source is None:
            self._videos = []
            self._has_next_page = False
        else:
            rows = self._load_videos_for_source(self.selected_source)
            self._has_next_page = len(rows) > self.PAGE_SIZE
            self._videos = rows[: self.PAGE_SIZE]
        self._render_videos()

    def _apply_filter(self) -> None:
        self._page = 0
        self._load_page()

    def _render_videos(self) -> None:
        table = self.query_one("#videos", DataTable)
        table.clear()
        for video in self._videos:
            table.add_row(
                sanitize_terminal_text(video.video_id),
                sanitize_terminal_text(video.title),
                sanitize_terminal_text(video.channel),
                sanitize_terminal_text(video.display_duration),
                str(video.transcript_count),
                str(video.chapter_count),
                key=video.video_id,
            )
        if self._videos:
            table.move_cursor(row=0, column=0)
            self._set_selected_video(self._videos[0].video_id)
        else:
            self._set_selected_video(None)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "filter":
            return
        self.filter_text = event.value
        self._apply_filter()

    def _set_selected_video(self, video_id: str | None) -> None:
        self.selected_video_id = video_id
        details = self.query_one("#details", Static)
        if video_id is None:
            if self.filter_text.strip():
                details.update("No videos match the current filter.")
            else:
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
            f"[b]{escape(sanitize_terminal_text(video.title))}[/b]",
            f"Channel: {escape(sanitize_terminal_text(video.channel))}",
            f"Video ID: {escape(sanitize_terminal_text(video.video_id))}",
            f"Duration: {escape(sanitize_terminal_text(video.display_duration))}",
            f"Upload Date: {escape(sanitize_terminal_text(video.upload_date or 'undated'))}",
            f"Path: {escape(sanitize_terminal_text(video.file_path or '-'))}",
            f"Chapters: {len(chapters)}",
            f"Subtitle Tracks: {len(tracks)}",
            "",
        ]
        if chapters:
            lines.append("Chapters:")
            lines.extend(
                f"- {escape(sanitize_terminal_text(chapter.title))}" for chapter in chapters[:5]
            )
        if preview:
            if chapters:
                lines.append("")
            lines.append("Transcript Preview:")
            lines.extend(
                f"- {escape(sanitize_terminal_text(segment.text))}" for segment in preview[:5]
            )
        details.update("\n".join(lines))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "sources":
            return
        index = event.list_view.index
        if index is None:
            return
        item = self._source_items[index]
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

    def action_next_page(self) -> None:
        if not self._has_next_page:
            self.notify("Already on the last page.")
            return
        self._page += 1
        self._load_page()
        self.notify(f"Page {self._page + 1}.")

    def action_previous_page(self) -> None:
        if self._page == 0:
            self.notify("Already on the first page.")
            return
        self._page -= 1
        self._load_page()
        self.notify(f"Page {self._page + 1}.")

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
        if self._download_root is not None:
            try:
                path.resolve().relative_to(self._download_root.resolve())
            except ValueError:
                self.notify("Media path is outside the download root.", severity="warning")
                return
        if not open_with_system_default(path):
            self.notify(
                "Opening local media is only supported on macOS, Linux, and Windows.",
                severity="warning",
            )
            return
        self.notify(f"Opened {sanitize_terminal_text(path.name)}")

    def action_clip_action(self) -> None:
        if self.selected_video_id is None:
            self.notify("No video selected.", severity="warning")
            return
        video_id = sanitize_terminal_text(self.selected_video_id)
        self.copy_to_clipboard(video_id)
        self.notify(f"Copied video ID {video_id} for clip search.")

    def action_download_action(self) -> None:
        if self.selected_video_id is None:
            self.notify("No video selected.", severity="warning")
            return
        if self._queue is None:
            self.notify("Download queue is unavailable.", severity="warning")
            return
        video_id = sanitize_terminal_text(self.selected_video_id)
        job = self._queue.add("download", video_id)
        self.notify(f"Queued download job {job.job_id} for {video_id}.")


def launch_tui(settings: Settings) -> None:
    store = CatalogStore(settings.catalog_file)
    queue = JobQueue(settings.catalog_file.parent / "jobs.sqlite")
    app = YtAgentTui(store, download_root=settings.download_root, queue=queue)
    app.run()


def open_with_system_default(path: Path) -> bool:
    if sys.platform == "darwin":
        launcher = shutil.which("open")
        if launcher is None:
            return False
        # Uses the platform launcher discovered on PATH and passes a single file path.
        subprocess.Popen([launcher, str(path)])  # noqa: S603
        return True
    if sys.platform.startswith("linux"):
        launcher = shutil.which("xdg-open")
        if launcher is None:
            return False
        # Uses the platform launcher discovered on PATH and passes a single file path.
        subprocess.Popen([launcher, str(path)])  # noqa: S603
        return True
    if sys.platform == "win32":
        # Windows startfile delegates to the OS shell for a user-selected local file only.
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        return True
    return False
