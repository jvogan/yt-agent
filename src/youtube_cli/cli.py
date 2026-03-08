"""Typer application for youtube_cli."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console
from rich.table import Table

from youtube_cli.archive import ensure_archive_file, is_archived, load_archive_entries
from youtube_cli.config import Settings, load_settings
from youtube_cli.errors import DependencyError, ExitCode, ExternalCommandError, YoutubeCliError
from youtube_cli.manifest import append_manifest_record, ensure_manifest_file
from youtube_cli.models import DownloadTarget, ManifestRecord, VideoInfo
from youtube_cli.selector import select_results
from youtube_cli import yt_dlp

app = typer.Typer(help="Terminal-first YouTube search and download workflow.")
console = Console()
error_console = Console(stderr=True)


def _load_settings(config_path: Path | None = None) -> Settings:
    return load_settings(config_path)


def _prepare_storage(settings: Settings) -> None:
    settings.ensure_storage_paths()
    ensure_archive_file(settings.archive_file)
    ensure_manifest_file(settings.manifest_file)


def _raise_cli_error(exc: YoutubeCliError) -> None:
    if isinstance(exc, ExternalCommandError) and exc.stderr:
        error_console.print(f"[red]Error:[/red] {exc} {exc.stderr}")
    else:
        error_console.print(f"[red]Error:[/red] {exc}")
    raise typer.Exit(code=int(getattr(exc, "exit_code", ExitCode.EXTERNAL))) from exc


def _run_guarded(callback: Callable[[], None]) -> None:
    try:
        callback()
    except YoutubeCliError as exc:
        _raise_cli_error(exc)
    except KeyboardInterrupt as exc:
        error_console.print("[red]Interrupted.[/red]")
        raise typer.Exit(code=int(ExitCode.INTERRUPTED)) from exc


def _render_results(results: list[VideoInfo]) -> None:
    table = Table(title="Search Results")
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Duration", justify="right")
    table.add_column("Upload Date")
    table.add_column("ID")
    table.add_column("URL", overflow="ignore", no_wrap=True)
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
        record = ManifestRecord.from_download(target, output_path=execution.output_path)
        append_manifest_record(settings.manifest_file, record)
        archive_entries.add(target.info.archive_key)
        downloaded += 1
        console.print(f"[green]Saved:[/green] {execution.output_path}")
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
        _render_results([target.info for target in resolution.targets])
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
        _render_results(results)

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
        _render_results(results)
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
                _render_results([item.info for item in resolution.targets])

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
        _render_results(results)
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


def main() -> None:
    """Run the app and map application errors to stable exit codes."""

    app()
