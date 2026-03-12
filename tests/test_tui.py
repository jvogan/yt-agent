from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from yt_agent.models import CatalogVideo, ChapterEntry, SubtitleTrack, TranscriptSegment
from yt_agent.tui import SourceItem, YtAgentTui, launch_tui, open_with_system_default


def make_video(
    video_id: str,
    *,
    title: str | None = None,
    channel: str = "Channel",
    output_path: Path | None = None,
    transcript_count: int = 1,
    chapter_count: int = 1,
) -> CatalogVideo:
    return CatalogVideo(
        video_id=video_id,
        title=title or f"Video {video_id}",
        channel=channel,
        upload_date="2026-03-07",
        duration_seconds=120,
        extractor_key="youtube",
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
        requested_input=None,
        source_query=None,
        output_path=output_path,
        info_json_path=Path(f"/tmp/{video_id}.info.json"),
        downloaded_at="2026-03-08T00:00:00+00:00",
        chapter_count=chapter_count,
        transcript_segment_count=transcript_count,
        playlist_count=1,
    )


def make_details(
    video: CatalogVideo,
    *,
    chapter_count: int = 1,
    preview_count: int = 1,
) -> dict[str, Any]:
    return {
        "video": video,
        "chapters": [
            ChapterEntry(position=index, title=f"Chapter {index}", start_seconds=float(index), end_seconds=float(index + 1))
            for index in range(chapter_count)
        ],
        "subtitle_tracks": [
            SubtitleTrack(
                lang="en",
                source="manual",
                is_auto=False,
                format="vtt",
                file_path=Path(f"/tmp/{video.video_id}.en.vtt"),
            )
        ],
        "transcript_preview": [
            TranscriptSegment(
                segment_index=index,
                start_seconds=float(index),
                end_seconds=float(index + 1),
                text=f"preview {index}",
            )
            for index in range(preview_count)
        ],
    }


@dataclass
class FakeCatalog:
    channels: list[str] = field(default_factory=lambda: ["Alpha", "Beta"])
    playlists: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"playlist_id": "PL123", "title": "Playlist One", "entry_count": "1"},
            {"playlist_id": "PL999", "title": "Playlist Two", "entry_count": "2"},
        ]
    )
    all_videos: list[CatalogVideo] = field(default_factory=lambda: [make_video("all-video")])
    channel_videos: dict[str, list[CatalogVideo]] = field(default_factory=dict)
    playlist_videos: dict[str, list[CatalogVideo]] = field(default_factory=dict)
    details: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    initialize_calls: int = 0
    list_video_calls: list[dict[str, Any]] = field(default_factory=list)

    def initialize(self) -> None:
        self.initialize_calls += 1

    def list_channels(self) -> list[str]:
        return list(self.channels)

    def list_playlists(self) -> list[dict[str, Any]]:
        return list(self.playlists)

    def list_videos(
        self,
        *,
        channel: str | None = None,
        playlist_id: str | None = None,
        has_transcript: bool | None = None,
        has_chapters: bool | None = None,
        limit: int = 50,
    ) -> list[CatalogVideo]:
        self.list_video_calls.append(
            {
                "channel": channel,
                "playlist_id": playlist_id,
                "has_transcript": has_transcript,
                "has_chapters": has_chapters,
                "limit": limit,
            }
        )
        if channel is not None:
            return list(self.channel_videos.get(channel, []))
        if playlist_id is not None:
            return list(self.playlist_videos.get(playlist_id, []))
        return list(self.all_videos)

    def get_video_details(self, video_id: str) -> dict[str, Any] | None:
        return self.details.get(video_id)


class FakeListView:
    def __init__(self, *, view_id: str = "sources", index: int | None = None) -> None:
        self.id = view_id
        self.index = index
        self.children: list[Any] = []

    def clear(self) -> None:
        self.children.clear()

    def append(self, item: Any) -> None:
        self.children.append(item)


class FakeDataTable:
    def __init__(self, *, table_id: str = "videos") -> None:
        self.id = table_id
        self.cursor_type: str | None = None
        self.columns: list[str] = []
        self.rows: list[tuple[Any, ...]] = []
        self.row_keys: list[str | None] = []
        self.cursor_moves: list[tuple[int, int]] = []

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def add_columns(self, *columns: str) -> None:
        self.columns.extend(columns)

    def clear(self) -> None:
        self.rows.clear()
        self.row_keys.clear()

    def add_row(self, *values: Any, key: str | None = None) -> None:
        self.rows.append(values)
        self.row_keys.append(key)

    def move_cursor(self, *, row: int, column: int) -> None:
        self.cursor_moves.append((row, column))


class FakeStatic:
    def __init__(self) -> None:
        self.content = ""

    def update(self, content: str) -> None:
        self.content = content


@dataclass
class TuiHarness:
    app: YtAgentTui
    list_view: FakeListView
    table: FakeDataTable
    details: FakeStatic
    notifications: list[tuple[str, str | None]]


def make_harness(catalog: FakeCatalog) -> TuiHarness:
    app = YtAgentTui(catalog)
    list_view = FakeListView()
    table = FakeDataTable()
    details = FakeStatic()
    notifications: list[tuple[str, str | None]] = []
    widgets = {"#sources": list_view, "#videos": table, "#details": details}

    def fake_query_one(selector: str, expected_type: Any) -> Any:
        return widgets[selector]

    app.query_one = fake_query_one  # type: ignore[method-assign]
    app.notify = lambda message, severity=None: notifications.append((message, severity))  # type: ignore[method-assign]
    return TuiHarness(app=app, list_view=list_view, table=table, details=details, notifications=notifications)


def test_on_mount_populates_sources_and_initial_video() -> None:
    catalog = FakeCatalog(details={"all-video": make_details(make_video("all-video"), chapter_count=2, preview_count=2)})
    harness = make_harness(catalog)

    harness.app.on_mount()

    assert catalog.initialize_calls == 1
    assert harness.table.columns == ["Video ID", "Title", "Channel", "Duration", "Transcripts", "Chapters"]
    assert harness.list_view.index == 0
    assert len(harness.list_view.children) == 5
    assert harness.table.row_count == 1
    assert harness.table.cursor_moves == [(0, 0)]
    assert harness.app.selected_source == SourceItem("all", "All Videos")
    assert harness.app.selected_video_id == "all-video"
    assert "Transcript Preview:" in harness.details.content


def test_populate_sources_sanitizes_catalog_labels() -> None:
    catalog = FakeCatalog(
        channels=["Alpha\n\x1b[31mBeta"],
        playlists=[{"playlist_id": "PL123", "title": "Play\tlist\x1b[31m", "entry_count": "1"}],
    )
    harness = make_harness(catalog)

    harness.app._populate_sources()

    assert harness.app._source_items == [
        SourceItem("all", "All Videos"),
        SourceItem("channel", "Alpha Beta", "Alpha\n\x1b[31mBeta"),
        SourceItem("playlist", "Play list", "PL123"),
    ]


def test_load_videos_for_each_source_kind_uses_expected_filters() -> None:
    catalog = FakeCatalog(
        channel_videos={"Alpha": [make_video("channel-video")]},
        playlist_videos={"PL123": [make_video("playlist-video")]},
    )
    harness = make_harness(catalog)

    assert harness.app._load_videos_for_source(SourceItem("channel", "Alpha", "Alpha"))[0].video_id == "channel-video"
    assert harness.app._load_videos_for_source(SourceItem("playlist", "Playlist One", "PL123"))[0].video_id == "playlist-video"
    assert harness.app._load_videos_for_source(SourceItem("all", "All Videos"))[0].video_id == "all-video"
    assert catalog.list_video_calls == [
        {"channel": "Alpha", "playlist_id": None, "has_transcript": None, "has_chapters": None, "limit": 100},
        {"channel": None, "playlist_id": "PL123", "has_transcript": None, "has_chapters": None, "limit": 100},
        {"channel": None, "playlist_id": None, "has_transcript": None, "has_chapters": None, "limit": 100},
    ]


def test_apply_source_sets_empty_state_when_catalog_has_no_results() -> None:
    catalog = FakeCatalog(all_videos=[], details={})
    harness = make_harness(catalog)

    harness.app._apply_source(SourceItem("all", "All Videos"))

    assert harness.table.row_count == 0
    assert harness.app.selected_video_id is None
    assert harness.details.content == "No videos found for this source."


def test_apply_source_renders_large_result_sets_and_selects_first_video() -> None:
    videos = [make_video(f"video-{index:03d}") for index in range(120)]
    catalog = FakeCatalog(all_videos=videos, details={videos[0].video_id: make_details(videos[0])})
    harness = make_harness(catalog)

    harness.app._apply_source(SourceItem("all", "All Videos"))

    assert harness.table.row_count == 120
    assert harness.table.row_keys[0] == "video-000"
    assert harness.app.selected_video_id == "video-000"


def test_set_selected_video_handles_missing_payload_and_limits_preview_sections() -> None:
    video = make_video("video-123", title="Video [Title]")
    catalog = FakeCatalog(details={"video-123": make_details(video, chapter_count=7, preview_count=7), "missing": None})
    harness = make_harness(catalog)

    harness.app._set_selected_video("missing")
    assert harness.details.content == "Video details are unavailable."

    harness.app._set_selected_video("video-123")

    assert harness.app.selected_video_id == "video-123"
    assert "[b]Video [Title][/b]" in harness.details.content
    assert harness.details.content.count("- Chapter ") == 5
    assert harness.details.content.count("- preview ") == 5
    assert "Subtitle Tracks: 1" in harness.details.content


def test_selection_handlers_ignore_irrelevant_events_and_apply_valid_selection() -> None:
    first = make_video("video-1")
    second = make_video("video-2")
    catalog = FakeCatalog(
        all_videos=[first, second],
        channel_videos={"Alpha": [first, second]},
        details={first.video_id: make_details(first), second.video_id: make_details(second)},
    )
    harness = make_harness(catalog)
    harness.app._source_items = [SourceItem("all", "All Videos"), SourceItem("channel", "Alpha", "Alpha")]
    harness.app._videos = [first, second]

    harness.app.on_list_view_selected(SimpleNamespace(list_view=FakeListView(view_id="other", index=0)))
    harness.app.on_list_view_selected(SimpleNamespace(list_view=FakeListView(index=None)))
    assert not catalog.list_video_calls

    harness.app.on_list_view_selected(SimpleNamespace(list_view=FakeListView(index=1)))
    assert harness.app.selected_source == SourceItem("channel", "Alpha", "Alpha")
    assert harness.app.selected_video_id == "video-1"

    harness.app.on_data_table_row_highlighted(SimpleNamespace(data_table=FakeDataTable(table_id="other"), cursor_row=1))
    assert harness.app.selected_video_id == "video-1"

    harness.app.on_data_table_row_highlighted(SimpleNamespace(data_table=FakeDataTable(), cursor_row=5))
    assert harness.app.selected_video_id == "video-1"

    harness.app.on_data_table_row_highlighted(SimpleNamespace(data_table=FakeDataTable(), cursor_row=1))
    assert harness.app.selected_video_id == "video-2"


def test_row_highlight_ignores_events_when_no_videos_loaded() -> None:
    harness = make_harness(FakeCatalog())

    harness.app.on_data_table_row_highlighted(SimpleNamespace(data_table=FakeDataTable(), cursor_row=0))

    assert harness.app.selected_video_id is None


def test_refresh_catalog_reloads_selected_source_and_notifies() -> None:
    catalog = FakeCatalog(details={"all-video": make_details(make_video("all-video"))})
    harness = make_harness(catalog)
    harness.app.selected_source = SourceItem("all", "All Videos")

    harness.app.action_refresh_catalog()

    assert catalog.initialize_calls == 1
    assert harness.table.row_count == 1
    assert harness.notifications == [("Catalog view refreshed.", None)]


@pytest.mark.parametrize(
    ("selected_video_id", "details_payload", "file_exists", "open_result", "expected"),
    [
        (None, None, False, False, ("No video selected.", "warning")),
        ("video-1", None, False, False, ("Selected video has no local media path.", "warning")),
        ("video-1", make_details(make_video("video-1", output_path=None)), False, False, ("Selected video has no local media path.", "warning")),
        ("video-1", make_details(make_video("video-1", output_path=Path("/tmp/missing.mp4"))), False, False, ("Local media path is missing on disk.", "warning")),
        ("video-1", make_details(make_video("video-1", output_path=Path("/tmp/demo.mp4"))), True, False, ("Opening local media is only supported on macOS, Linux, and Windows.", "warning")),
        ("video-1", make_details(make_video("video-1", output_path=Path("/tmp/demo.mp4"))), True, True, ("Opened demo.mp4", None)),
    ],
)
def test_action_open_media_reports_expected_status(
    monkeypatch: pytest.MonkeyPatch,
    selected_video_id: str | None,
    details_payload: dict[str, Any] | None,
    file_exists: bool,
    open_result: bool,
    expected: tuple[str, str | None],
) -> None:
    catalog = FakeCatalog(details={"video-1": details_payload} if selected_video_id else {})
    harness = make_harness(catalog)
    harness.app.selected_video_id = selected_video_id

    monkeypatch.setattr("yt_agent.tui.Path.exists", lambda self: file_exists)
    monkeypatch.setattr("yt_agent.tui.open_with_system_default", lambda path: open_result)

    harness.app.action_open_media()

    assert harness.notifications == [expected]


def test_clip_and_download_actions_require_selection() -> None:
    harness = make_harness(FakeCatalog())

    harness.app.action_clip_action()
    harness.app.action_download_action()
    harness.app.selected_video_id = "abc123def45"
    harness.app.action_clip_action()
    harness.app.action_download_action()

    assert harness.notifications == [
        ("No video selected.", "warning"),
        ("No video selected.", "warning"),
        ("Run: yt-agent clips search --source transcript 'query'  (video abc123def45)", None),
        ("Run: yt-agent download abc123def45", None),
    ]


def test_clip_and_download_actions_sanitize_selected_video_id() -> None:
    harness = make_harness(FakeCatalog())
    harness.app.selected_video_id = "abc123\n\x1b[31mdef45"

    harness.app.action_clip_action()
    harness.app.action_download_action()

    assert harness.notifications == [
        ("Run: yt-agent clips search --source transcript 'query'  (video abc123 def45)", None),
        ("Run: yt-agent download abc123 def45", None),
    ]


def test_launch_tui_constructs_catalog_store_and_runs_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    created: dict[str, Any] = {}

    class FakeStore:
        def __init__(self, catalog_file: Path) -> None:
            created["catalog_file"] = catalog_file

    class FakeApp:
        def __init__(self, store: Any) -> None:
            created["store"] = store

        def run(self) -> None:
            created["ran"] = True

    settings = SimpleNamespace(catalog_file=tmp_path / "catalog.sqlite")
    monkeypatch.setattr("yt_agent.tui.CatalogStore", FakeStore)
    monkeypatch.setattr("yt_agent.tui.YtAgentTui", FakeApp)

    launch_tui(settings)

    assert created == {
        "catalog_file": settings.catalog_file,
        "store": created["store"],
        "ran": True,
    }
    assert isinstance(created["store"], FakeStore)


def test_open_with_system_default_returns_false_on_unsupported_platform(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("yt_agent.tui.sys.platform", "plan9")
    assert open_with_system_default(tmp_path / "demo.mp4") is False


def test_open_with_system_default_uses_darwin_launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    launched: list[list[str]] = []

    monkeypatch.setattr("yt_agent.tui.sys.platform", "darwin")
    monkeypatch.setattr("yt_agent.tui.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("yt_agent.tui.subprocess.Popen", lambda args: launched.append(args))

    path = tmp_path / "demo.mp4"
    assert open_with_system_default(path) is True
    assert launched == [["/usr/bin/open", str(path)]]


def test_open_with_system_default_returns_false_when_darwin_launcher_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("yt_agent.tui.sys.platform", "darwin")
    monkeypatch.setattr("yt_agent.tui.shutil.which", lambda name: None)

    assert open_with_system_default(tmp_path / "demo.mp4") is False


def test_open_with_system_default_returns_false_when_linux_launcher_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("yt_agent.tui.sys.platform", "linux")
    monkeypatch.setattr("yt_agent.tui.shutil.which", lambda name: None)

    assert open_with_system_default(tmp_path / "demo.mp4") is False


def test_open_with_system_default_uses_linux_launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    launched: list[list[str]] = []

    monkeypatch.setattr("yt_agent.tui.sys.platform", "linux")
    monkeypatch.setattr("yt_agent.tui.shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("yt_agent.tui.subprocess.Popen", lambda args: launched.append(args))

    path = tmp_path / "demo.mp4"
    assert open_with_system_default(path) is True
    assert launched == [["/usr/bin/xdg-open", str(path)]]


def test_open_with_system_default_uses_windows_startfile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    started: list[str] = []

    monkeypatch.setattr("yt_agent.tui.sys.platform", "win32")
    monkeypatch.setattr("yt_agent.tui.os", SimpleNamespace(startfile=lambda path: started.append(path)))

    path = tmp_path / "demo.mp4"
    assert open_with_system_default(path) is True
    assert started == [str(path)]
