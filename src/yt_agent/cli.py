"""Typer application for yt-agent."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console
from rich.table import Table

from yt_agent import yt_dlp
from yt_agent.archive import ensure_archive_file, is_archived, load_archive_entries
from yt_agent.catalog import CatalogStore
from yt_agent.clips import extract_clip
from yt_agent.config import Settings, load_settings
from yt_agent.errors import DependencyError, ExitCode, ExternalCommandError, InvalidInputError, YtAgentError
from yt_agent.indexer import IndexSummary, index_manifest_record, index_refresh, index_target
from yt_agent.manifest import append_manifest_record, ensure_manifest_file
from yt_agent.models import CatalogVideo, DownloadTarget, ManifestRecord, VideoInfo
from yt_agent.selector import select_results
from yt_agent.tui import launch_tui

app = typer.Typer(help="Agentic YouTube search, download, catalog, and clip tooling.")
index_app = typer.Typer(help="Catalog indexing commands.")
clips_app = typer.Typer(help="Transcript and chapter clip workflows.")
library_app = typer.Typer(help="Local library browsing commands.")
app.add_typer(index_app, name="index")
app.add_typer(clips_app, name="clips")
app.add_typer(library_app, name="library")

console = Console()
error_console = Console(stderr=True)


def _load_settings(config_path: Path | None = None) -> Settings:
    return load_settings(config_path)


def _prepare_storage(settings: Settings) -> None:
    settings.ensure_storage_paths()
    ensure_archive_file(settings.archive_file)
    ensure_manifest_file(settings.manifest_file)


def _catalog(settings: Settings) -> CatalogStore:
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    return store


def _raise_cli_error(exc: YtAgentError) -> None:
    if isinstance(exc, ExternalCommandError) and exc.stderr:
        error_console.print(f"[red]Error:[/red] {exc} {exc.stderr}")
    else:
        error_console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(code=int(getattr(exc, "exit_code", ExitCode.EXTERNAL))) from exc


def _run_guarded(callback: Callable[[], None]) -> None:
    try:
        callback()
    except YtAgentError as exc:
        _raise_cli_error(exc)
    except KeyboardInterrupt as exc:
        error_console.print("[red]Interrupted.[/red]")
        raise typer.Exit(code=int(ExitCode.INTERRUPTED)) from exc


def _render_results(results: list[VideoInfo], *, title: str = "Results") -> None:
    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Duration", justify="right")
    table.add_column("Upload Date")
    table.add_column("ID")
    table.add_column("URL", overflow="fold")
    for index, result in enumerate(results, start=1):
        table.add_row(
            str(index),
            result.title,
            result.channel,
            result.display_duration,
            result.upload_date or "undated",
            result.video_id,
            result.webpage_url,
        )
    console.print(table)


def _render_info(payload: dict[str, object]) -> None:
    table = Table(title="Metadata")
    table.add_column("Field")
    table.add_column("Value")
    entries = payload.get("entries")
    if isinstance(entries, list):
        table.add_row("type", "playlist")
        table.add_row("title", str(payload.get("title") or "Untitled"))
        table.add_row("channel", str(payload.get("channel") or payload.get("uploader") or "Unknown"))
        table.add_row("entries", str(len([entry for entry in entries if entry])))
        table.add_row("url", str(payload.get("webpage_url") or payload.get("original_url") or ""))
    else:
        info = VideoInfo.from_yt_dlp(payload)
        table.add_row("id", info.video_id)
        table.add_row("title", info.title)
        table.add_row("channel", info.channel)
        table.add_row("duration", info.display_duration)
        table.add_row("upload_date", info.upload_date or "undated")
        table.add_row("url", info.webpage_url)
    console.print(table)


def _render_playlist_summary(payload: dict[str, object], entry_count: int) -> None:
    table = Table(title="Playlist")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("title", str(payload.get("title") or "Untitled"))
    table.add_row("channel", str(payload.get("channel") or payload.get("uploader") or "Unknown"))
    table.add_row("entries", str(entry_count))
    table.add_row("url", str(payload.get("webpage_url") or payload.get("original_url") or ""))
    console.print(table)


def _render_doctor(settings: Settings) -> bool:
    table = Table(title="Doctor")
    table.add_column("Tool / Path")
    table.add_column("Status")
    table.add_column("Value")
    missing_required = False
    for tool_name, required in (
        ("yt-dlp", True),
        ("ffmpeg", False),
        ("fzf", False),
        ("mpv", False),
    ):
        path = shutil.which(tool_name)
        status = "ok" if path else ("missing" if required else "optional")
        table.add_row(tool_name, status, path or "-")
        if required and path is None:
            missing_required = True
    table.add_row("config", "path", str(settings.config_path))
    table.add_row("download_root", "path", str(settings.download_root))
    table.add_row("archive_file", "path", str(settings.archive_file))
    table.add_row("manifest_file", "path", str(settings.manifest_file))
    table.add_row("catalog_file", "path", str(settings.catalog_file))
    table.add_row("clips_root", "path", str(settings.clips_root))
    console.print(table)
    return missing_required


def _download_targets(targets: list[DownloadTarget], settings: Settings) -> tuple[int, int, int]:
    archive_entries = load_archive_entries(settings.archive_file)
    downloaded = 0
    skipped = 0
    failed = 0
    for target in targets:
        if is_archived(archive_entries, target.info):
            skipped += 1
            console.print(f"[yellow]Skipping archived:[/yellow] {target.info.title} [{target.info.video_id}]")
            continue
        console.print(f"[cyan]Downloading:[/cyan] {target.info.title}")
        try:
            execution = yt_dlp.download_target(target, settings)
        except ExternalCommandError as exc:
            failed += 1
            detail = f" {exc.stderr}" if exc.stderr else ""
            error_console.print(
                f"[red]Failed:[/red] {target.info.title} [{target.info.video_id}] {exc}{detail}"
            )
            continue
        record = ManifestRecord.from_download(
            target,
            output_path=execution.output_path,
            info_json_path=execution.info_json_path,
        )
        append_manifest_record(settings.manifest_file, record)
        archive_entries.add(target.info.archive_key)
        downloaded += 1
        console.print(f"[green]Saved:[/green] {execution.output_path}")
        try:
            index_manifest_record(settings, record, fetch_subs=False, auto_subs=False)
        except Exception as exc:  # pragma: no cover - indexing is a best-effort follow-up
            console.print(f"[yellow]Indexed download with warning:[/yellow] {exc}")
    return downloaded, skipped, failed


def _resolve_download_inputs(
    inputs: list[str],
    settings: Settings,
    *,
    source_query: str | None = None,
    select_playlist: bool = False,
    use_fzf: bool = False,
) -> tuple[list[DownloadTarget], list[str]]:
    all_targets: list[DownloadTarget] = []
    skipped_messages: list[str] = []
    for user_input in inputs:
        payload = yt_dlp.fetch_info(user_input)
        resolution = yt_dlp.resolve_payload(user_input, payload, source_query=source_query)
        skipped_messages.extend(resolution.skipped_messages)

        if not isinstance(payload.get("entries"), list) or not select_playlist:
            all_targets.extend(resolution.targets)
            continue

        if not resolution.targets:
            console.print(f"[yellow]No downloadable entries found in playlist:[/yellow] {user_input}")
            continue

        _render_playlist_summary(payload, len(resolution.targets))
        _render_results([target.info for target in resolution.targets], title="Playlist Entries")
        selected_infos = select_results(
            [target.info for target in resolution.targets],
            prefer_fzf=use_fzf,
            configured_selector=settings.selector,
        )
        if not selected_infos:
            console.print(f"[yellow]No playlist selection made:[/yellow] {payload.get('title') or user_input}")
            continue

        selected_ids = {item.video_id for item in selected_infos}
        all_targets.extend([target for target in resolution.targets if target.info.video_id in selected_ids])

    return all_targets, skipped_messages


def _render_clip_hits(hits: list[object]) -> None:
    table = Table(title="Clip Hits")
    table.add_column("Result ID")
    table.add_column("Source")
    table.add_column("Range")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Match", overflow="fold")
    for hit in hits:
        table.add_row(
            hit.result_id,
            hit.source,
            hit.display_range,
            hit.title,
            hit.channel,
            hit.match_text,
        )
    console.print(table)


def _presence_flag(enabled: bool, disabled: bool, *, label: str) -> bool | None:
    if enabled and disabled:
        raise InvalidInputError(f"Choose only one of --{label} or --no-{label}.")
    if enabled:
        return True
    if disabled:
        return False
    return None


def _render_library_rows(videos: list[CatalogVideo], *, title: str = "Library") -> None:
    table = Table(title=title)
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Date")
    table.add_column("Duration")
    table.add_column("Transcript", justify="right")
    table.add_column("Chapters", justify="right")
    table.add_column("Local")
    for video in videos:
        table.add_row(
            video.video_id,
            video.title,
            video.channel,
            video.upload_date or "undated",
            video.display_duration,
            str(video.transcript_segment_count),
            str(video.chapter_count),
            "yes" if video.has_local_media else "no",
        )
    console.print(table)


def _render_library_detail(store: CatalogStore, video_id: str) -> None:
    payload = store.get_video_details(video_id)
    if payload is None:
        raise InvalidInputError(f"Video id '{video_id}' is not in the catalog.")

    video = payload["video"]
    chapters = payload["chapters"]
    subtitle_tracks = payload["subtitle_tracks"]
    transcript_preview = payload["transcript_preview"]

    metadata = Table(title="Video")
    metadata.add_column("Field")
    metadata.add_column("Value")
    metadata.add_row("id", video.video_id)
    metadata.add_row("title", video.title)
    metadata.add_row("channel", video.channel)
    metadata.add_row("upload_date", video.upload_date or "undated")
    metadata.add_row("duration", video.display_duration)
    metadata.add_row("url", video.webpage_url)
    metadata.add_row("local_path", str(video.output_path) if video.output_path else "remote only")
    metadata.add_row("transcript_segments", str(video.transcript_segment_count))
    metadata.add_row("chapters", str(video.chapter_count))
    console.print(metadata)

    if chapters:
        chapter_table = Table(title="Chapters")
        chapter_table.add_column("#", justify="right")
        chapter_table.add_column("Title")
        chapter_table.add_column("Range")
        for chapter in chapters:
            chapter_table.add_row(str(chapter.position + 1), chapter.title, chapter.display_range)
        console.print(chapter_table)

    if subtitle_tracks:
        track_table = Table(title="Subtitle Tracks")
        track_table.add_column("Language")
        track_table.add_column("Source")
        track_table.add_column("Auto")
        track_table.add_column("File")
        for track in subtitle_tracks:
            track_table.add_row(track.lang, track.source, "yes" if track.is_auto else "no", str(track.file_path))
        console.print(track_table)

    if transcript_preview:
        transcript_table = Table(title="Transcript Preview")
        transcript_table.add_column("Range")
        transcript_table.add_column("Text", overflow="fold")
        for segment in transcript_preview:
            transcript_table.add_row(segment.display_range, segment.text)
        console.print(transcript_table)


def _render_index_summary(summary: IndexSummary, *, title: str) -> None:
    table = Table(title=title)
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_row("videos", str(summary.videos))
    table.add_row("playlists", str(summary.playlists))
    table.add_row("chapters", str(summary.chapters))
    table.add_row("transcript_segments", str(summary.transcript_segments))
    console.print(table)


@app.command()
def doctor(
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Check required and optional runtime dependencies."""

    def _command() -> None:
        settings = _load_settings(config)
        missing_required = _render_doctor(settings)
        if missing_required:
            raise DependencyError("yt-dlp is required for this CLI.")

    _run_guarded(_command)


@app.command()
def search(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search YouTube and print normalized results."""

    def _command() -> None:
        settings = _load_settings(config)
        results = yt_dlp.search(query, limit=limit or settings.search_limit)
        if not results:
            console.print("No matches found.")
            return
        _render_results(results, title="Search Results")

    _run_guarded(_command)


@app.command()
def pick(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for interactive selection."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search YouTube and interactively select result URLs."""

    def _command() -> None:
        settings = _load_settings(config)
        results = yt_dlp.search(query, limit=limit or settings.search_limit)
        if not results:
            console.print("No matches found.")
            return
        _render_results(results, title="Search Results")
        selected = select_results(results, prefer_fzf=use_fzf, configured_selector=settings.selector)
        if not selected:
            console.print("No selection made.")
            return
        for item in selected:
            console.print(item.webpage_url)

    _run_guarded(_command)


@app.command()
def info(
    target: str,
    entries: bool = typer.Option(False, "--entries", help="For playlists, show individual entries."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Print normalized metadata for a target without downloading."""

    def _command() -> None:
        _ = _load_settings(config)
        payload = yt_dlp.fetch_info(target)
        _render_info(payload)
        if entries and isinstance(payload.get("entries"), list):
            resolution = yt_dlp.resolve_payload(target, payload)
            for message in resolution.skipped_messages:
                console.print(f"[yellow]{message}[/yellow]")
            if resolution.targets:
                _render_results([item.info for item in resolution.targets], title="Playlist Entries")

    _run_guarded(_command)


@app.command()
def download(
    targets: list[str] = typer.Argument(..., help="Video URLs, playlist URLs, or YouTube video ids."),
    select_playlist: bool = typer.Option(
        False,
        "--select-playlist",
        help="For playlist URLs, interactively choose which entries to download.",
    ),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for playlist entry selection."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Download videos into the organized local library."""

    def _command() -> None:
        settings = _load_settings(config)
        _prepare_storage(settings)
        resolved_targets, skipped_messages = _resolve_download_inputs(
            targets,
            settings,
            select_playlist=select_playlist,
            use_fzf=use_fzf,
        )
        for message in skipped_messages:
            console.print(f"[yellow]{message}[/yellow]")
        if not resolved_targets:
            console.print("Nothing to download.")
            return
        downloaded, skipped, failed = _download_targets(resolved_targets, settings)
        console.print(f"Completed: {downloaded} downloaded, {skipped} skipped, {failed} failed.")
        if failed:
            raise ExternalCommandError(f"{failed} download(s) failed.")

    _run_guarded(_command)


@app.command()
def grab(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for interactive selection."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search, select, and download in one flow."""

    def _command() -> None:
        settings = _load_settings(config)
        results = yt_dlp.search(query, limit=limit or settings.search_limit)
        if not results:
            console.print("No matches found.")
            return
        _render_results(results, title="Search Results")
        selected = select_results(results, prefer_fzf=use_fzf, configured_selector=settings.selector)
        if not selected:
            console.print("No selection made.")
            return
        _prepare_storage(settings)
        targets = [
            DownloadTarget(original_input=item.webpage_url, info=item, source_query=query)
            for item in selected
        ]
        downloaded, skipped, failed = _download_targets(targets, settings)
        console.print(f"Completed: {downloaded} downloaded, {skipped} skipped, {failed} failed.")
        if failed:
            raise ExternalCommandError(f"{failed} download(s) failed.")

    _run_guarded(_command)


@index_app.command("refresh")
def index_refresh_command(
    fetch_subs: bool = typer.Option(True, "--fetch-subs/--no-fetch-subs", help="Fetch missing subtitles during refresh."),
    auto_subs: bool = typer.Option(True, "--auto-subs/--manual-subs", help="Allow automatic subtitles when manuals are missing."),
    lang: str | None = typer.Option(None, "--lang", help="Preferred subtitle language expression, e.g. en.*"),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Backfill or refresh the local catalog from the download manifest."""

    def _command() -> None:
        settings = _load_settings(config)
        _prepare_storage(settings)
        summary = index_refresh(settings, fetch_subs=fetch_subs, auto_subs=auto_subs, lang=lang)
        _render_index_summary(summary, title="Index Refresh")

    _run_guarded(_command)


@index_app.command("add")
def index_add_command(
    target: str,
    fetch_subs: bool = typer.Option(True, "--fetch-subs/--no-fetch-subs", help="Fetch subtitles while indexing the target."),
    auto_subs: bool = typer.Option(True, "--auto-subs/--manual-subs", help="Allow automatic subtitles when manuals are missing."),
    lang: str | None = typer.Option(None, "--lang", help="Preferred subtitle language expression, e.g. en.*"),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Index a specific video or playlist target into the local catalog."""

    def _command() -> None:
        settings = _load_settings(config)
        _prepare_storage(settings)
        summary = index_target(settings, target, fetch_subs=fetch_subs, auto_subs=auto_subs, lang=lang)
        _render_index_summary(summary, title="Index Add")

    _run_guarded(_command)


@clips_app.command("search")
def clips_search_command(
    query: str,
    source: str = typer.Option("all", "--source", help="Search source: transcript, chapters, or all."),
    channel: str | None = typer.Option(None, "--channel", help="Limit results to one channel."),
    lang: str | None = typer.Option(None, "--lang", help="Optional transcript language filter, e.g. en% or en.*"),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum clip hits to show."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search indexed transcript segments and chapters for clip-worthy matches."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        hits = store.search_clips(query, source=source, channel=channel, language=lang, limit=limit)
        if not hits:
            console.print("No clip hits found.")
            return
        _render_clip_hits(hits)

    _run_guarded(_command)


@clips_app.command("show")
def clips_show_command(
    result_id: str,
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show a specific clip-search hit with context."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        hit = store.get_clip_hit(result_id)
        if hit is None:
            raise InvalidInputError(f"Unknown clip result '{result_id}'.")
        table = Table(title="Clip Hit")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("result_id", hit.result_id)
        table.add_row("source", hit.source)
        table.add_row("video_id", hit.video_id)
        table.add_row("title", hit.title)
        table.add_row("channel", hit.channel)
        table.add_row("range", hit.display_range)
        table.add_row("match", hit.match_text)
        table.add_row("context", hit.context)
        table.add_row("url", hit.webpage_url)
        table.add_row("local_path", str(hit.output_path) if hit.output_path else "remote only")
        console.print(table)

    _run_guarded(_command)


@clips_app.command("grab")
def clips_grab_command(
    result_id: str,
    padding_before: float = typer.Option(0.0, "--padding-before", min=0.0, help="Seconds to prepend."),
    padding_after: float = typer.Option(0.0, "--padding-after", min=0.0, help="Seconds to append."),
    mode: str = typer.Option("fast", "--mode", help="Extraction mode: fast or accurate."),
    remote_fallback: bool = typer.Option(False, "--remote-fallback", help="Fallback to yt-dlp section download if local media is missing."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Extract a clip from a cataloged chapter or transcript hit."""

    def _command() -> None:
        settings = _load_settings(config)
        _prepare_storage(settings)
        extraction = extract_clip(
            settings,
            result_id,
            padding_before=padding_before,
            padding_after=padding_after,
            mode=mode,
            prefer_remote=remote_fallback,
        )
        console.print(f"[green]Saved clip:[/green] {extraction.output_path} ({extraction.source})")

    _run_guarded(_command)


@library_app.command("list")
def library_list_command(
    channel: str | None = typer.Option(None, "--channel", help="Only show videos from one channel."),
    playlist: str | None = typer.Option(None, "--playlist", help="Filter by playlist id or title."),
    has_transcript: bool = typer.Option(False, "--has-transcript", help="Only show videos with indexed transcripts."),
    no_transcript: bool = typer.Option(False, "--no-transcript", help="Only show videos without indexed transcripts."),
    has_chapters: bool = typer.Option(False, "--has-chapters", help="Only show videos with indexed chapters."),
    no_chapters: bool = typer.Option(False, "--no-chapters", help="Only show videos without indexed chapters."),
    limit: int = typer.Option(25, "--limit", min=1, help="Maximum videos to show."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """List cataloged library entries."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        videos = store.list_videos(
            channel=channel,
            playlist=playlist,
            has_transcript=_presence_flag(has_transcript, no_transcript, label="has-transcript"),
            has_chapters=_presence_flag(has_chapters, no_chapters, label="has-chapters"),
            limit=limit,
        )
        if not videos:
            console.print("The catalog is empty.")
            return
        _render_library_rows(videos)

    _run_guarded(_command)


@library_app.command("search")
def library_search_command(
    query: str,
    channel: str | None = typer.Option(None, "--channel", help="Only search one channel."),
    playlist: str | None = typer.Option(None, "--playlist", help="Filter by playlist id or title."),
    has_transcript: bool = typer.Option(False, "--has-transcript", help="Only show videos with indexed transcripts."),
    no_transcript: bool = typer.Option(False, "--no-transcript", help="Only show videos without indexed transcripts."),
    has_chapters: bool = typer.Option(False, "--has-chapters", help="Only show videos with indexed chapters."),
    no_chapters: bool = typer.Option(False, "--no-chapters", help="Only show videos without indexed chapters."),
    limit: int = typer.Option(25, "--limit", min=1, help="Maximum videos to show."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search the local library catalog by title, channel, or video id."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        videos = store.search_videos(
            query,
            channel=channel,
            playlist=playlist,
            has_transcript=_presence_flag(has_transcript, no_transcript, label="has-transcript"),
            has_chapters=_presence_flag(has_chapters, no_chapters, label="has-chapters"),
            limit=limit,
        )
        if not videos:
            console.print("No library matches found.")
            return
        _render_library_rows(videos, title="Library Search")

    _run_guarded(_command)


@library_app.command("show")
def library_show_command(
    video_id: str,
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show one cataloged video with chapters and transcript preview."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        _render_library_detail(store, video_id)

    _run_guarded(_command)


@app.command()
def tui(
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Launch the Textual catalog browser."""

    def _command() -> None:
        settings = _load_settings(config)
        _prepare_storage(settings)
        launch_tui(settings)

    _run_guarded(_command)


def main() -> None:
    """Run the app and map application errors to stable exit codes."""

    app()
