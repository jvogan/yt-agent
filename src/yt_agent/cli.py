"""Typer application for yt-agent."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console
from rich.table import Table

from yt_agent import __version__, yt_dlp
from yt_agent.archive import ensure_archive_file, is_archived, load_archive_entries
from yt_agent.catalog import CatalogStore
from yt_agent.clips import extract_clip
from yt_agent.config import Settings, load_settings, render_default_config
from yt_agent.errors import DependencyError, ExitCode, ExternalCommandError, InvalidInputError, YtAgentError
from yt_agent.indexer import IndexSummary, index_manifest_record, index_refresh, index_target
from yt_agent.manifest import append_manifest_record, ensure_manifest_file
from yt_agent.models import CatalogVideo, DownloadTarget, ManifestRecord, VideoInfo
from yt_agent.selector import parse_selection, select_results
from yt_agent.tui import launch_tui

APP_HELP = "Terminal-first YouTube search, download, catalog, and clip tooling."
READ_OUTPUT_HELP = "Render output as table, json, or plain text."
OUTPUT_MODES = {"table", "json", "plain"}

app = typer.Typer(help=APP_HELP, no_args_is_help=True)
index_app = typer.Typer(help="Catalog indexing commands.")
clips_app = typer.Typer(help="Transcript and chapter clip workflows.")
library_app = typer.Typer(help="Local library browsing commands.")
config_app = typer.Typer(help="Configuration helpers.")
app.add_typer(index_app, name="index")
app.add_typer(clips_app, name="clips")
app.add_typer(library_app, name="library")
app.add_typer(config_app, name="config")

console = Console()
error_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"yt-agent {__version__}", markup=False)
        raise typer.Exit(code=int(ExitCode.OK))


@app.callback()
def _app_callback(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the yt-agent version and exit.",
    ),
) -> None:
    _ = version


def _normalize_output_mode(value: str) -> str:
    mode = value.casefold().strip()
    if mode not in OUTPUT_MODES:
        allowed = ", ".join(sorted(OUTPUT_MODES))
        raise InvalidInputError(f"Output mode must be one of: {allowed}")
    return mode


def _print_json(payload: object) -> None:
    console.print(json.dumps(payload, indent=2), markup=False)


def _print_plain_mapping(rows: list[tuple[str, object]]) -> None:
    for key, value in rows:
        console.print(f"{key}\t{value}", markup=False)


def _print_plain_rows(columns: list[tuple[str, str]], rows: list[dict[str, object]]) -> None:
    console.print("\t".join(label for _, label in columns), markup=False)
    for row in rows:
        console.print(
            "\t".join(str(row.get(key, "")) for key, _ in columns),
            markup=False,
        )


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


def _platform_status() -> str:
    if sys.platform in {"darwin", "linux"}:
        return "supported"
    return "experimental"


def _tool_install_hint(tool_name: str) -> str:
    if sys.platform == "darwin":
        hints = {
            "yt-dlp": "brew install yt-dlp",
            "ffmpeg": "brew install ffmpeg",
            "fzf": "brew install fzf",
            "mpv": "brew install mpv",
        }
        return hints.get(tool_name, "")
    if sys.platform.startswith("linux"):
        hints = {
            "yt-dlp": "python3 -m pip install -U yt-dlp",
            "ffmpeg": "sudo apt-get install -y ffmpeg",
            "fzf": "sudo apt-get install -y fzf",
            "mpv": "sudo apt-get install -y mpv",
        }
        return hints.get(tool_name, "")
    return ""


def _doctor_payload(settings: Settings) -> dict[str, object]:
    tools: list[dict[str, object]] = []
    for tool_name, required in (
        ("yt-dlp", True),
        ("ffmpeg", False),
        ("fzf", False),
        ("mpv", False),
    ):
        path = shutil.which(tool_name)
        tools.append(
            {
                "name": tool_name,
                "required": required,
                "installed": path is not None,
                "status": "ok" if path else ("missing" if required else "optional"),
                "path": path or "",
                "install_hint": _tool_install_hint(tool_name),
            }
        )
    return {
        "tools": tools,
        "paths": {
            "config": str(settings.config_path),
            "download_root": str(settings.download_root),
            "archive_file": str(settings.archive_file),
            "manifest_file": str(settings.manifest_file),
            "catalog_file": str(settings.catalog_file),
            "clips_root": str(settings.clips_root),
        },
        "support": {
            "platform": sys.platform,
            "status": _platform_status(),
            "supported_platforms": ["darwin", "linux"],
            "windows": "experimental",
            "tui": "read-mostly catalog browser",
            "notes": "Search, download, and clip behavior depend on external tools such as yt-dlp and ffmpeg.",
        },
    }


def _video_row(info: VideoInfo, *, index: int | None = None) -> dict[str, object]:
    row = {
        "id": info.video_id,
        "title": info.title,
        "channel": info.channel,
        "duration": info.display_duration,
        "duration_seconds": info.duration_seconds,
        "upload_date": info.upload_date or "undated",
        "url": info.webpage_url,
        "extractor_key": info.extractor_key,
    }
    if index is not None:
        row["index"] = index
    return row


def _catalog_video_row(video: CatalogVideo) -> dict[str, object]:
    return {
        "id": video.video_id,
        "title": video.title,
        "channel": video.channel,
        "upload_date": video.upload_date or "undated",
        "duration": video.display_duration,
        "duration_seconds": video.duration_seconds,
        "url": video.webpage_url,
        "local_path": str(video.output_path) if video.output_path else "",
        "has_local_media": video.has_local_media,
        "transcript_segments": video.transcript_segment_count,
        "chapters": video.chapter_count,
        "playlists": video.playlist_count,
    }


def _clip_hit_row(hit: object) -> dict[str, object]:
    return {
        "result_id": hit.result_id,
        "source": hit.source,
        "range": hit.display_range,
        "title": hit.title,
        "channel": hit.channel,
        "match": hit.match_text,
        "context": getattr(hit, "context", ""),
        "video_id": getattr(hit, "video_id", ""),
        "url": getattr(hit, "webpage_url", ""),
        "local_path": str(getattr(hit, "output_path", "") or ""),
    }


def _render_results(results: list[VideoInfo], *, title: str = "Results", output_mode: str = "table") -> None:
    mode = _normalize_output_mode(output_mode)
    rows = [_video_row(result, index=index) for index, result in enumerate(results, start=1)]
    if mode == "json":
        _print_json(rows)
        return
    if mode == "plain":
        _print_plain_rows(
            [
                ("index", "index"),
                ("title", "title"),
                ("channel", "channel"),
                ("duration", "duration"),
                ("upload_date", "upload_date"),
                ("id", "id"),
                ("url", "url"),
            ],
            rows,
        )
        return

    table = Table(title=title)
    table.add_column("#", justify="right")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Duration", justify="right")
    table.add_column("Upload Date")
    table.add_column("ID")
    table.add_column("URL", overflow="fold")
    for row in rows:
        table.add_row(
            str(row["index"]),
            str(row["title"]),
            str(row["channel"]),
            str(row["duration"]),
            str(row["upload_date"]),
            str(row["id"]),
            str(row["url"]),
        )
    console.print(table)


def _build_info_payload(
    payload: dict[str, object],
    *,
    target: str,
    include_entries: bool = False,
) -> dict[str, object]:
    entries = payload.get("entries")
    if isinstance(entries, list):
        result: dict[str, object] = {
            "type": "playlist",
            "title": str(payload.get("title") or "Untitled"),
            "channel": str(payload.get("channel") or payload.get("uploader") or "Unknown"),
            "entries_count": len([entry for entry in entries if entry]),
            "url": str(payload.get("webpage_url") or payload.get("original_url") or target),
        }
        if include_entries:
            resolution = yt_dlp.resolve_payload(target, payload)
            result["entries"] = [_video_row(item.info) for item in resolution.targets]
            result["skipped_messages"] = resolution.skipped_messages
        return result

    info = VideoInfo.from_yt_dlp(payload, original_url=target)
    return {"type": "video", **_video_row(info)}


def _render_info_payload(payload: dict[str, object], *, output_mode: str = "table") -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return
    if mode == "plain":
        rows = [
            ("type", payload.get("type", "")),
            ("title", payload.get("title", "")),
            ("channel", payload.get("channel", "")),
        ]
        if payload.get("type") == "video":
            rows.insert(1, ("id", payload.get("id", "")))
            rows.append(("duration", payload.get("duration", "")))
            rows.append(("upload_date", payload.get("upload_date", "")))
        else:
            rows.append(("entries_count", payload.get("entries_count", "")))
        rows.append(("url", payload.get("url", "")))
        _print_plain_mapping(rows)
        entries = payload.get("entries")
        if isinstance(entries, list) and entries:
            console.print("", markup=False)
            _print_plain_rows(
                [
                    ("id", "id"),
                    ("title", "title"),
                    ("channel", "channel"),
                    ("duration", "duration"),
                    ("upload_date", "upload_date"),
                    ("url", "url"),
                ],
                [entry for entry in entries if isinstance(entry, dict)],
            )
        return

    table = Table(title="Metadata")
    table.add_column("Field")
    table.add_column("Value")
    if payload.get("type") == "playlist":
        table.add_row("type", "playlist")
        table.add_row("title", str(payload.get("title") or "Untitled"))
        table.add_row("channel", str(payload.get("channel") or "Unknown"))
        table.add_row("entries", str(payload.get("entries_count") or 0))
        table.add_row("url", str(payload.get("url") or ""))
    else:
        table.add_row("id", str(payload.get("id") or ""))
        table.add_row("title", str(payload.get("title") or "Untitled"))
        table.add_row("channel", str(payload.get("channel") or "Unknown"))
        table.add_row("duration", str(payload.get("duration") or "--:--"))
        table.add_row("upload_date", str(payload.get("upload_date") or "undated"))
        table.add_row("url", str(payload.get("url") or ""))
    console.print(table)

    entries = payload.get("entries")
    if isinstance(entries, list) and entries:
        _print_plain_mapping([(f"skipped_message_{index}", message) for index, message in enumerate(payload.get("skipped_messages", []), start=1)])
        table = Table(title="Playlist Entries")
        table.add_column("#", justify="right")
        table.add_column("Title")
        table.add_column("Channel")
        table.add_column("Duration", justify="right")
        table.add_column("Upload Date")
        table.add_column("ID")
        table.add_column("URL", overflow="fold")
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                continue
            table.add_row(
                str(index),
                str(entry["title"]),
                str(entry["channel"]),
                str(entry["duration"]),
                str(entry["upload_date"]),
                str(entry["id"]),
                str(entry["url"]),
            )
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


def _render_doctor(settings: Settings, *, output_mode: str = "table") -> bool:
    payload = _doctor_payload(settings)
    missing_required = any(tool["required"] and not tool["installed"] for tool in payload["tools"])
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return missing_required
    if mode == "plain":
        rows: list[dict[str, object]] = []
        for tool in payload["tools"]:
            rows.append(
                {
                    "component": tool["name"],
                    "status": tool["status"],
                    "value": tool["path"],
                    "install_hint": tool["install_hint"],
                }
            )
        for key, value in payload["paths"].items():
            rows.append({"component": key, "status": "path", "value": value, "install_hint": ""})
        _print_plain_rows(
            [
                ("component", "component"),
                ("status", "status"),
                ("value", "value"),
                ("install_hint", "install_hint"),
            ],
            rows,
        )
        console.print("", markup=False)
        _print_plain_mapping(
            [
                ("platform", payload["support"]["platform"]),
                ("platform_status", payload["support"]["status"]),
                ("supported_platforms", ",".join(payload["support"]["supported_platforms"])),
                ("windows", payload["support"]["windows"]),
                ("tui", payload["support"]["tui"]),
                ("notes", payload["support"]["notes"]),
            ]
        )
        return missing_required

    table = Table(title="Doctor")
    table.add_column("Tool / Path")
    table.add_column("Status")
    table.add_column("Value")
    table.add_column("Install Hint")
    for tool in payload["tools"]:
        table.add_row(
            str(tool["name"]),
            str(tool["status"]),
            str(tool["path"] or "-"),
            str(tool["install_hint"] or "-"),
        )
    for key, value in payload["paths"].items():
        table.add_row(str(key), "path", str(value), "-")
    console.print(table)
    console.print(
        f"Platform: {payload['support']['platform']} ({payload['support']['status']}). Supported today: macOS and Linux; Windows is experimental.",
        markup=False,
    )
    console.print(str(payload["support"]["notes"]), markup=False)
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


def _select_by_indexes(results: list[VideoInfo], selection: str) -> list[VideoInfo]:
    indexes = parse_selection(selection, len(results))
    return [results[index - 1] for index in indexes]


def _choose_results(
    results: list[VideoInfo],
    *,
    selection: str | None = None,
    prefer_fzf: bool = False,
    configured_selector: str = "prompt",
) -> list[VideoInfo]:
    if selection is not None:
        return _select_by_indexes(results, selection)
    return select_results(results, prefer_fzf=prefer_fzf, configured_selector=configured_selector)


def _resolve_download_inputs(
    inputs: list[str],
    settings: Settings,
    *,
    source_query: str | None = None,
    select_playlist: bool = False,
    use_fzf: bool = False,
    selection: str | None = None,
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
        selected_infos = _choose_results(
            [target.info for target in resolution.targets],
            selection=selection,
            prefer_fzf=use_fzf,
            configured_selector=settings.selector,
        )
        if not selected_infos:
            console.print(f"[yellow]No playlist selection made:[/yellow] {payload.get('title') or user_input}")
            continue

        selected_ids = {item.video_id for item in selected_infos}
        all_targets.extend([target for target in resolution.targets if target.info.video_id in selected_ids])

    return all_targets, skipped_messages


def _render_clip_hits(hits: list[object], *, output_mode: str = "table") -> None:
    mode = _normalize_output_mode(output_mode)
    rows = [_clip_hit_row(hit) for hit in hits]
    if mode == "json":
        _print_json(rows)
        return
    if mode == "plain":
        _print_plain_rows(
            [
                ("result_id", "result_id"),
                ("source", "source"),
                ("range", "range"),
                ("title", "title"),
                ("channel", "channel"),
                ("match", "match"),
            ],
            rows,
        )
        return

    table = Table(title="Clip Hits")
    table.add_column("Result ID")
    table.add_column("Source")
    table.add_column("Range")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Match", overflow="fold")
    for row in rows:
        table.add_row(
            str(row["result_id"]),
            str(row["source"]),
            str(row["range"]),
            str(row["title"]),
            str(row["channel"]),
            str(row["match"]),
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


def _render_library_rows(
    videos: list[CatalogVideo],
    *,
    title: str = "Library",
    output_mode: str = "table",
) -> None:
    mode = _normalize_output_mode(output_mode)
    rows = [_catalog_video_row(video) for video in videos]
    if mode == "json":
        _print_json(rows)
        return
    if mode == "plain":
        _print_plain_rows(
            [
                ("id", "id"),
                ("title", "title"),
                ("channel", "channel"),
                ("upload_date", "upload_date"),
                ("duration", "duration"),
                ("transcript_segments", "transcript_segments"),
                ("chapters", "chapters"),
                ("has_local_media", "has_local_media"),
            ],
            rows,
        )
        return

    table = Table(title=title)
    table.add_column("ID")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Date")
    table.add_column("Duration")
    table.add_column("Transcript", justify="right")
    table.add_column("Chapters", justify="right")
    table.add_column("Local")
    for row in rows:
        table.add_row(
            str(row["id"]),
            str(row["title"]),
            str(row["channel"]),
            str(row["upload_date"]),
            str(row["duration"]),
            str(row["transcript_segments"]),
            str(row["chapters"]),
            "yes" if row["has_local_media"] else "no",
        )
    console.print(table)


def _library_detail_payload(store: CatalogStore, video_id: str) -> dict[str, object]:
    payload = store.get_video_details(video_id)
    if payload is None:
        raise InvalidInputError(f"Video id '{video_id}' is not in the catalog.")

    return {
        "video": _catalog_video_row(payload["video"]),
        "chapters": [
            {"position": chapter.position + 1, "title": chapter.title, "range": chapter.display_range}
            for chapter in payload["chapters"]
        ],
        "subtitle_tracks": [
            {
                "language": track.lang,
                "source": track.source,
                "auto": track.is_auto,
                "format": track.format,
                "file": str(track.file_path),
            }
            for track in payload["subtitle_tracks"]
        ],
        "transcript_preview": [
            {"range": segment.display_range, "text": segment.text}
            for segment in payload["transcript_preview"]
        ],
    }


def _render_library_detail(store: CatalogStore, video_id: str, *, output_mode: str = "table") -> None:
    detail = _library_detail_payload(store, video_id)
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(detail)
        return
    if mode == "plain":
        video = detail["video"]
        _print_plain_mapping([(key, value) for key, value in video.items()])
        chapters = detail["chapters"]
        if chapters:
            console.print("", markup=False)
            _print_plain_rows([("position", "position"), ("title", "title"), ("range", "range")], chapters)
        subtitle_tracks = detail["subtitle_tracks"]
        if subtitle_tracks:
            console.print("", markup=False)
            _print_plain_rows([("language", "language"), ("source", "source"), ("auto", "auto"), ("file", "file")], subtitle_tracks)
        transcript_preview = detail["transcript_preview"]
        if transcript_preview:
            console.print("", markup=False)
            _print_plain_rows([("range", "range"), ("text", "text")], transcript_preview)
        return

    video = detail["video"]
    metadata = Table(title="Video")
    metadata.add_column("Field")
    metadata.add_column("Value")
    metadata.add_row("id", str(video["id"]))
    metadata.add_row("title", str(video["title"]))
    metadata.add_row("channel", str(video["channel"]))
    metadata.add_row("upload_date", str(video["upload_date"]))
    metadata.add_row("duration", str(video["duration"]))
    metadata.add_row("url", str(video["url"]))
    metadata.add_row("local_path", str(video["local_path"] or "remote only"))
    metadata.add_row("transcript_segments", str(video["transcript_segments"]))
    metadata.add_row("chapters", str(video["chapters"]))
    console.print(metadata)

    chapters = detail["chapters"]
    if chapters:
        chapter_table = Table(title="Chapters")
        chapter_table.add_column("#", justify="right")
        chapter_table.add_column("Title")
        chapter_table.add_column("Range")
        for chapter in chapters:
            chapter_table.add_row(str(chapter["position"]), str(chapter["title"]), str(chapter["range"]))
        console.print(chapter_table)

    subtitle_tracks = detail["subtitle_tracks"]
    if subtitle_tracks:
        track_table = Table(title="Subtitle Tracks")
        track_table.add_column("Language")
        track_table.add_column("Source")
        track_table.add_column("Auto")
        track_table.add_column("File")
        for track in subtitle_tracks:
            track_table.add_row(
                str(track["language"]),
                str(track["source"]),
                "yes" if track["auto"] else "no",
                str(track["file"]),
            )
        console.print(track_table)

    transcript_preview = detail["transcript_preview"]
    if transcript_preview:
        transcript_table = Table(title="Transcript Preview")
        transcript_table.add_column("Range")
        transcript_table.add_column("Text", overflow="fold")
        for segment in transcript_preview:
            transcript_table.add_row(str(segment["range"]), str(segment["text"]))
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
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Check required and optional runtime dependencies."""

    def _command() -> None:
        settings = _load_settings(config)
        missing_required = _render_doctor(settings, output_mode=output)
        if missing_required:
            raise DependencyError("yt-dlp is required for this CLI.")

    _run_guarded(_command)


@app.command()
def search(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search YouTube and print normalized results."""

    def _command() -> None:
        settings = _load_settings(config)
        results = yt_dlp.search(query, limit=limit or settings.search_limit)
        if not results:
            if _normalize_output_mode(output) == "json":
                _print_json([])
            else:
                console.print("No matches found.")
            return
        _render_results(results, title="Search Results", output_mode=output)

    _run_guarded(_command)


@app.command()
def pick(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for interactive selection."),
    select: str | None = typer.Option(None, "--select", help="Choose result indexes without prompting, e.g. 1,3."),
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
        selected = _choose_results(
            results,
            selection=select,
            prefer_fzf=use_fzf,
            configured_selector=settings.selector,
        )
        if not selected:
            console.print("No selection made.")
            return
        for item in selected:
            console.print(item.webpage_url, markup=False)

    _run_guarded(_command)


@app.command()
def info(
    target: str,
    entries: bool = typer.Option(False, "--entries", help="For playlists, show individual entries."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Print normalized metadata for a target without downloading."""

    def _command() -> None:
        _ = _load_settings(config)
        payload = yt_dlp.fetch_info(target)
        info_payload = _build_info_payload(payload, target=target, include_entries=entries)
        _render_info_payload(info_payload, output_mode=output)

    _run_guarded(_command)


@app.command()
def download(
    targets: list[str] = typer.Argument(..., help="Video URLs, playlist URLs, or YouTube video ids."),
    select_playlist: bool = typer.Option(
        False,
        "--select-playlist",
        help="For playlist URLs, interactively choose which entries to download.",
    ),
    select: str | None = typer.Option(
        None,
        "--select",
        help="Choose playlist entry indexes without prompting, e.g. 1,3.",
    ),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for playlist entry selection."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Download videos into the organized local library."""

    def _command() -> None:
        settings = _load_settings(config)
        _prepare_storage(settings)
        resolve_kwargs: dict[str, object] = {
            "select_playlist": select_playlist or select is not None,
            "use_fzf": use_fzf,
        }
        if select is not None:
            resolve_kwargs["selection"] = select
        resolved_targets, skipped_messages = _resolve_download_inputs(
            targets,
            settings,
            **resolve_kwargs,
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
    select: str | None = typer.Option(None, "--select", help="Choose result indexes without prompting, e.g. 1,3."),
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
        selected = _choose_results(
            results,
            selection=select,
            prefer_fzf=use_fzf,
            configured_selector=settings.selector,
        )
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


@config_app.command("init")
def config_init_command(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Write a starter config file to the active config path."""

    def _command() -> None:
        settings = _load_settings(config)
        settings.config_path.parent.mkdir(parents=True, exist_ok=True)
        if settings.config_path.exists() and not force:
            raise InvalidInputError(
                f"Config already exists at {settings.config_path}. Use --force to overwrite it."
            )
        settings.config_path.write_text(render_default_config(), encoding="utf-8")
        console.print(f"[green]Wrote config:[/green] {settings.config_path}")

    _run_guarded(_command)


@config_app.command("path")
def config_path_command(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show the active config and data paths."""

    def _command() -> None:
        settings = _load_settings(config)
        payload = {
            "config": str(settings.config_path),
            "download_root": str(settings.download_root),
            "archive_file": str(settings.archive_file),
            "manifest_file": str(settings.manifest_file),
            "catalog_file": str(settings.catalog_file),
            "clips_root": str(settings.clips_root),
        }
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json(payload)
            return
        if mode == "plain":
            _print_plain_mapping(list(payload.items()))
            return
        table = Table(title="Config Paths")
        table.add_column("Field")
        table.add_column("Value")
        for key, value in payload.items():
            table.add_row(key, value)
        console.print(table)

    _run_guarded(_command)


@index_app.command("refresh")
def index_refresh_command(
    fetch_subs: bool = typer.Option(
        True,
        "--fetch-subs/--no-fetch-subs",
        help="Fetch missing subtitles during refresh.",
    ),
    auto_subs: bool = typer.Option(
        True,
        "--auto-subs/--manual-subs",
        help="Allow automatic subtitles when manuals are missing.",
    ),
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
    fetch_subs: bool = typer.Option(
        True,
        "--fetch-subs/--no-fetch-subs",
        help="Fetch subtitles while indexing the target.",
    ),
    auto_subs: bool = typer.Option(
        True,
        "--auto-subs/--manual-subs",
        help="Allow automatic subtitles when manuals are missing.",
    ),
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
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search indexed transcript segments and chapters for clip-worthy matches."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        hits = store.search_clips(query, source=source, channel=channel, language=lang, limit=limit)
        if not hits:
            if _normalize_output_mode(output) == "json":
                _print_json([])
            else:
                console.print("No clip hits found.")
            return
        _render_clip_hits(hits, output_mode=output)

    _run_guarded(_command)


@clips_app.command("show")
def clips_show_command(
    result_id: str,
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show a specific clip-search hit with context."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        hit = store.get_clip_hit(result_id)
        if hit is None:
            raise InvalidInputError(f"Unknown clip result '{result_id}'.")
        payload = _clip_hit_row(hit)
        payload["result_id_note"] = "Result ids are not durable across reindexing or catalog rebuilds."
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json(payload)
            return
        if mode == "plain":
            _print_plain_mapping(list(payload.items()))
            return
        table = Table(title="Clip Hit")
        table.add_column("Field")
        table.add_column("Value")
        for key in (
            "result_id",
            "source",
            "video_id",
            "title",
            "channel",
            "range",
            "match",
            "context",
            "url",
            "local_path",
            "result_id_note",
        ):
            value = payload[key]
            table.add_row(key, "remote only" if key == "local_path" and not value else str(value))
        console.print(table)

    _run_guarded(_command)


@clips_app.command("grab")
def clips_grab_command(
    result_id: str,
    padding_before: float = typer.Option(0.0, "--padding-before", min=0.0, help="Seconds to prepend."),
    padding_after: float = typer.Option(0.0, "--padding-after", min=0.0, help="Seconds to append."),
    mode: str = typer.Option("fast", "--mode", help="Extraction mode: fast or accurate."),
    remote_fallback: bool = typer.Option(
        False,
        "--remote-fallback",
        help="Fallback to yt-dlp section download if local media is missing.",
    ),
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
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
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
            if _normalize_output_mode(output) == "json":
                _print_json([])
            else:
                console.print("The catalog is empty.")
            return
        _render_library_rows(videos, output_mode=output)

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
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
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
            if _normalize_output_mode(output) == "json":
                _print_json([])
            else:
                console.print("No library matches found.")
            return
        _render_library_rows(videos, title="Library Search", output_mode=output)

    _run_guarded(_command)


@library_app.command("show")
def library_show_command(
    video_id: str,
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show one cataloged video with chapters and transcript preview."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        _render_library_detail(store, video_id, output_mode=output)

    _run_guarded(_command)


@library_app.command("stats")
def library_stats_command(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show high-level counts for the local catalog."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        payload = store.library_stats()
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json(payload)
            return
        if mode == "plain":
            _print_plain_mapping(list(payload.items()))
            return
        table = Table(title="Library Stats")
        table.add_column("Metric")
        table.add_column("Count", justify="right")
        for key, value in payload.items():
            table.add_row(key, str(value))
        console.print(table)

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
