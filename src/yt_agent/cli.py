"""Typer application for yt-agent."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import typer
from rich.console import Console
from rich.table import Table

from yt_agent import __version__, yt_dlp
from yt_agent.archive import ensure_archive_file, is_archived, load_archive_entries
from yt_agent.catalog import CatalogStore
from yt_agent.clips import extract_clip, extract_clip_for_range
from yt_agent.config import Settings, load_settings, render_default_config
from yt_agent.errors import DependencyError, ExitCode, ExternalCommandError, InvalidInputError, YtAgentError
from yt_agent.indexer import IndexSummary, index_manifest_record, index_refresh, index_target
from yt_agent.library import build_clip_output_path
from yt_agent.manifest import append_manifest_record, ensure_manifest_file, iter_manifest_records
from yt_agent.models import CatalogVideo, ClipSearchHit, DownloadTarget, ManifestRecord, VideoInfo
from yt_agent.security import ensure_private_file, operation_lock, sanitize_json_payload, sanitize_terminal_text
from yt_agent.selector import parse_selection, select_results
from yt_agent.tui import launch_tui

APP_HELP = "Terminal-first YouTube search, download, catalog, and clip tooling."
READ_OUTPUT_HELP = "Render output as table, json, or plain text."
OUTPUT_MODES = {"table", "json", "plain"}
MUTATION_SCHEMA_VERSION = 1

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


@dataclass(frozen=True)
class DownloadOperationItem:
    status: str
    info: VideoInfo
    requested_input: str
    reason: str | None = None
    output_path: Path | None = None
    info_json_path: Path | None = None
    indexed: bool = False
    index_summary: IndexSummary | None = None
    index_warning: str | None = None
    error_message: str | None = None
    stderr: str | None = None


def _normalize_optional_output_mode(value: str | None) -> str | None:
    if value is None:
        return None
    return _normalize_output_mode(value)


def _json_error_payload(
    *,
    exit_code: int,
    error_type: str,
    message: str,
    stderr: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": MUTATION_SCHEMA_VERSION,
        "status": "error",
        "exit_code": exit_code,
        "error_type": error_type,
        "message": sanitize_terminal_text(message),
    }
    if stderr:
        payload["stderr"] = sanitize_terminal_text(stderr)
    return payload


def _mutation_payload(
    *,
    command: str,
    status: str,
    summary: dict[str, Any],
    warnings: list[Any] | None = None,
    errors: list[Any] | None = None,
    **payload: Any,
) -> dict[str, Any]:
    return {
        "schema_version": MUTATION_SCHEMA_VERSION,
        "command": command,
        "status": status,
        "summary": summary,
        "warnings": warnings or [],
        "errors": errors or [],
        **payload,
    }


def _operation_lock_path(settings: Settings) -> Path:
    return settings.catalog_file.parent / "operation.lock"


def _video_info_payload(info: VideoInfo) -> dict[str, Any]:
    return {
        "video_id": info.video_id,
        "title": info.title,
        "channel": info.channel,
        "upload_date": info.upload_date or "undated",
        "duration": info.display_duration,
        "duration_seconds": info.duration_seconds,
        "webpage_url": info.webpage_url,
        "extractor_key": info.extractor_key,
    }


def _index_summary_payload(summary: IndexSummary) -> dict[str, int]:
    return {
        "videos": summary.videos,
        "playlists": summary.playlists,
        "chapters": summary.chapters,
        "transcript_segments": summary.transcript_segments,
    }


def _download_operation_item_payload(item: DownloadOperationItem) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": item.status,
        "requested_input": item.requested_input,
        **_video_info_payload(item.info),
    }
    if item.reason:
        payload["reason"] = sanitize_terminal_text(item.reason)
    if item.output_path is not None:
        payload["output_path"] = str(item.output_path)
    if item.info_json_path is not None:
        payload["info_json_path"] = str(item.info_json_path)
    payload["indexed"] = item.indexed
    if item.index_summary is not None:
        payload["index_summary"] = _index_summary_payload(item.index_summary)
    if item.index_warning:
        payload["index_warning"] = sanitize_terminal_text(item.index_warning)
    if item.error_message:
        payload["error_message"] = sanitize_terminal_text(item.error_message)
    if item.stderr:
        payload["stderr"] = sanitize_terminal_text(item.stderr)
    return payload


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
    # JSON mode still lands in a terminal, so sanitize nested strings before emission.
    console.file.write(json.dumps(sanitize_json_payload(payload), indent=2))
    console.file.write("\n")
    console.file.flush()


def _print_json_error(payload: object) -> None:
    error_console.file.write(json.dumps(sanitize_json_payload(payload), indent=2))
    error_console.file.write("\n")
    error_console.file.flush()


def _print_plain_mapping(rows: list[tuple[str, object]]) -> None:
    for key, value in rows:
        console.print(
            f"{sanitize_terminal_text(key)}\t{sanitize_terminal_text(value)}",
            markup=False,
        )


def _print_plain_rows(columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> None:
    console.print("\t".join(sanitize_terminal_text(label) for _, label in columns), markup=False)
    for row in rows:
        console.print(
            "\t".join(sanitize_terminal_text(row.get(key, "")) for key, _ in columns),
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


def _raise_cli_error(exc: YtAgentError, *, output_mode: str | None = None) -> None:
    normalized_output = _normalize_optional_output_mode(output_mode)
    if normalized_output == "json":
        payload = _json_error_payload(
            exit_code=int(getattr(exc, "exit_code", ExitCode.EXTERNAL)),
            error_type=exc.__class__.__name__,
            message=str(exc),
            stderr=exc.stderr if isinstance(exc, ExternalCommandError) else None,
        )
        _print_json_error(payload)
    elif isinstance(exc, ExternalCommandError) and exc.stderr:
        error_console.print(
            f"Error: {sanitize_terminal_text(exc)} {sanitize_terminal_text(exc.stderr)}",
            style="red",
            markup=False,
        )
    else:
        error_console.print(f"Error: {sanitize_terminal_text(exc)}", style="red", markup=False)
    raise typer.Exit(code=int(getattr(exc, "exit_code", ExitCode.EXTERNAL))) from exc


def _run_guarded(callback: Callable[[], None], *, output_mode: str | None = None) -> None:
    try:
        callback()
    except YtAgentError as exc:
        _raise_cli_error(exc, output_mode=output_mode)
    except sqlite3.Error as exc:
        normalized_output = _normalize_optional_output_mode(output_mode)
        if normalized_output == "json":
            payload = _json_error_payload(
                exit_code=int(ExitCode.STORAGE),
                error_type=exc.__class__.__name__,
                message=f"catalog database error: {exc}",
            )
            _print_json_error(payload)
        else:
            error_console.print(
                f"Error: catalog database error: {sanitize_terminal_text(exc)}",
                style="red",
                markup=False,
            )
        raise typer.Exit(code=int(ExitCode.STORAGE)) from exc
    except KeyboardInterrupt as exc:
        normalized_output = _normalize_optional_output_mode(output_mode)
        if normalized_output == "json":
            payload = _json_error_payload(
                exit_code=int(ExitCode.INTERRUPTED),
                error_type="KeyboardInterrupt",
                message="Interrupted.",
            )
            _print_json_error(payload)
        else:
            error_console.print("[red]Interrupted.[/red]")
        raise typer.Exit(code=int(ExitCode.INTERRUPTED)) from exc


def _read_targets_from_file(path: Path) -> list[str]:
    """Read URLs/IDs from a file, one per line. Blank lines and # comments are skipped."""
    if not path.exists():
        raise InvalidInputError(f"--from-file path not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


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


def _doctor_payload(settings: Settings) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
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
    yt_dlp_installed = any(tool["name"] == "yt-dlp" and tool["installed"] for tool in tools)
    ffmpeg_installed = any(tool["name"] == "ffmpeg" and tool["installed"] for tool in tools)
    if yt_dlp_installed and ffmpeg_installed:
        next_step = 'Ready: run yt-agent download URL or yt-agent grab "query".'
    elif yt_dlp_installed:
        next_step = 'Ready for search/download: run yt-agent download URL or yt-agent search "query".'
    else:
        next_step = "Install yt-dlp first, then rerun yt-agent doctor."
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
            "next_step": next_step,
        },
    }


def _video_row(info: VideoInfo, *, index: int | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "video_id": info.video_id,
        "title": info.title,
        "channel": info.channel,
        "duration": info.display_duration,
        "duration_seconds": info.duration_seconds,
        "upload_date": info.upload_date or "undated",
        "webpage_url": info.webpage_url,
        "extractor_key": info.extractor_key,
    }
    if index is not None:
        row["index"] = index
    return row


def _catalog_video_row(video: CatalogVideo) -> dict[str, Any]:
    return {
        "video_id": video.video_id,
        "title": video.title,
        "channel": video.channel,
        "upload_date": video.upload_date or "undated",
        "duration": video.display_duration,
        "duration_seconds": video.duration_seconds,
        "webpage_url": video.webpage_url,
        "output_path": str(video.output_path) if video.output_path else "",
        "has_local_media": video.has_local_media,
        "transcript_segments": video.transcript_segment_count,
        "chapters": video.chapter_count,
        "playlists": video.playlist_count,
    }


def _clip_hit_row(hit: ClipSearchHit) -> dict[str, Any]:
    base_url = getattr(hit, "webpage_url", "")
    start_seconds = float(getattr(hit, "start_seconds", 0.0) or 0.0)
    end_seconds = float(getattr(hit, "end_seconds", start_seconds) or start_seconds)
    timestamp_url = f"{base_url}&t={int(start_seconds)}" if base_url else ""
    return {
        "result_id": hit.result_id,
        "source": hit.source,
        "range": getattr(hit, "display_range", f"{start_seconds:.0f} - {end_seconds:.0f}"),
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
        "title": hit.title,
        "channel": hit.channel,
        "match": hit.match_text,
        "context": getattr(hit, "context", ""),
        "video_id": getattr(hit, "video_id", ""),
        "webpage_url": base_url,
        "timestamp_url": timestamp_url,
        "output_path": str(getattr(hit, "output_path", "") or ""),
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
                ("video_id", "video_id"),
                ("webpage_url", "webpage_url"),
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
            sanitize_terminal_text(row["index"]),
            sanitize_terminal_text(row["title"]),
            sanitize_terminal_text(row["channel"]),
            sanitize_terminal_text(row["duration"]),
            sanitize_terminal_text(row["upload_date"]),
            sanitize_terminal_text(row["video_id"]),
            sanitize_terminal_text(row["webpage_url"]),
        )
    console.print(table)


def _build_info_payload(
    payload: dict[str, Any],
    *,
    target: str,
    include_entries: bool = False,
) -> dict[str, Any]:
    entries = payload.get("entries")
    if isinstance(entries, list):
        result: dict[str, Any] = {
            "type": "playlist",
            "title": str(payload.get("title") or "Untitled"),
            "channel": str(payload.get("channel") or payload.get("uploader") or "Unknown"),
            "entries_count": len([entry for entry in entries if entry]),
            "webpage_url": str(payload.get("webpage_url") or payload.get("original_url") or target),
        }
        if include_entries:
            resolution = yt_dlp.resolve_payload(target, payload)
            result["entries"] = [_video_row(item.info) for item in resolution.targets]
            result["skipped_messages"] = resolution.skipped_messages
        return result

    info = VideoInfo.from_yt_dlp(payload, original_url=target)
    return {"type": "video", **_video_row(info)}


def _render_info_payload(payload: dict[str, Any], *, output_mode: str = "table") -> None:
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
            rows.insert(1, ("video_id", payload.get("video_id", "")))
            rows.append(("duration", payload.get("duration", "")))
            rows.append(("upload_date", payload.get("upload_date", "")))
        else:
            rows.append(("entries_count", payload.get("entries_count", "")))
        rows.append(("webpage_url", payload.get("webpage_url", "")))
        _print_plain_mapping(rows)
        entries = payload.get("entries")
        if isinstance(entries, list) and entries:
            console.print("", markup=False)
            _print_plain_rows(
                [
                    ("video_id", "video_id"),
                    ("title", "title"),
                    ("channel", "channel"),
                    ("duration", "duration"),
                    ("upload_date", "upload_date"),
                    ("webpage_url", "webpage_url"),
                ],
                [entry for entry in entries if isinstance(entry, dict)],
            )
        return

    table = Table(title="Metadata")
    table.add_column("Field")
    table.add_column("Value")
    if payload.get("type") == "playlist":
        table.add_row("type", "playlist")
        table.add_row("title", sanitize_terminal_text(payload.get("title") or "Untitled"))
        table.add_row("channel", sanitize_terminal_text(payload.get("channel") or "Unknown"))
        table.add_row("entries", sanitize_terminal_text(payload.get("entries_count") or 0))
        table.add_row("webpage_url", sanitize_terminal_text(payload.get("webpage_url") or ""))
    else:
        table.add_row("video_id", sanitize_terminal_text(payload.get("video_id") or ""))
        table.add_row("title", sanitize_terminal_text(payload.get("title") or "Untitled"))
        table.add_row("channel", sanitize_terminal_text(payload.get("channel") or "Unknown"))
        table.add_row("duration", sanitize_terminal_text(payload.get("duration") or "--:--"))
        table.add_row("upload_date", sanitize_terminal_text(payload.get("upload_date") or "undated"))
        table.add_row("webpage_url", sanitize_terminal_text(payload.get("webpage_url") or ""))
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
                sanitize_terminal_text(index),
                sanitize_terminal_text(entry["title"]),
                sanitize_terminal_text(entry["channel"]),
                sanitize_terminal_text(entry["duration"]),
                sanitize_terminal_text(entry["upload_date"]),
                sanitize_terminal_text(entry["video_id"]),
                sanitize_terminal_text(entry["webpage_url"]),
            )
        console.print(table)


def _render_playlist_summary(
    payload: dict[str, Any],
    entry_count: int,
    *,
    output_mode: str = "table",
) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "plain":
        _print_plain_mapping(
            [
                ("title", payload.get("title") or "Untitled"),
                ("channel", payload.get("channel") or payload.get("uploader") or "Unknown"),
                ("entries", entry_count),
                ("url", payload.get("webpage_url") or payload.get("original_url") or ""),
            ]
        )
        return
    table = Table(title="Playlist")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("title", sanitize_terminal_text(payload.get("title") or "Untitled"))
    table.add_row("channel", sanitize_terminal_text(payload.get("channel") or payload.get("uploader") or "Unknown"))
    table.add_row("entries", sanitize_terminal_text(entry_count))
    table.add_row("url", sanitize_terminal_text(payload.get("webpage_url") or payload.get("original_url") or ""))
    console.print(table)


def _render_doctor(settings: Settings, *, output_mode: str = "table") -> bool:
    payload = _doctor_payload(settings)
    missing_required = any(tool["required"] and not tool["installed"] for tool in payload["tools"])
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return missing_required
    if mode == "plain":
        rows: list[dict[str, Any]] = []
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
                ("next_step", payload["support"]["next_step"]),
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
            sanitize_terminal_text(tool["name"]),
            sanitize_terminal_text(tool["status"]),
            sanitize_terminal_text(tool["path"] or "-"),
            sanitize_terminal_text(tool["install_hint"] or "-"),
        )
    for key, value in payload["paths"].items():
        table.add_row(sanitize_terminal_text(key), "path", sanitize_terminal_text(value), "-")
    console.print(table)
    console.print(
        f"Platform: {sanitize_terminal_text(payload['support']['platform'])} ({sanitize_terminal_text(payload['support']['status'])}). Supported today: macOS and Linux; Windows is experimental.",
        markup=False,
    )
    console.print(sanitize_terminal_text(payload["support"]["notes"]), markup=False)
    console.print(sanitize_terminal_text(payload["support"]["next_step"]), markup=False)
    return missing_required


def _download_targets(
    targets: list[DownloadTarget],
    settings: Settings,
    *,
    mode: str = "video",
    fetch_subs: bool = False,
    auto_subs: bool = False,
    quiet: bool = False,
    show_failure_details: bool = True,
) -> list[DownloadOperationItem]:
    archive_entries = load_archive_entries(settings.archive_file)
    items: list[DownloadOperationItem] = []
    for target in targets:
        if is_archived(archive_entries, target.info):
            item = DownloadOperationItem(
                status="skipped",
                info=target.info,
                requested_input=target.original_input,
                reason="already archived",
            )
            items.append(item)
            if not quiet:
                console.print(
                    f"Skipping archived: {sanitize_terminal_text(target.info.title)} [{sanitize_terminal_text(target.info.video_id)}]",
                    style="yellow",
                    markup=False,
                )
            continue
        if not quiet:
            console.print(f"Downloading: {sanitize_terminal_text(target.info.title)}", style="cyan", markup=False)
        try:
            execution = yt_dlp.download_target(
                target, settings, mode=mode, fetch_subs=fetch_subs, auto_subs=auto_subs
            )
        except ExternalCommandError as exc:
            item = DownloadOperationItem(
                status="failed",
                info=target.info,
                requested_input=target.original_input,
                error_message=str(exc),
                stderr=exc.stderr,
            )
            items.append(item)
            if show_failure_details:
                detail = f" {sanitize_terminal_text(exc.stderr)}" if exc.stderr else ""
                error_console.print(
                    f"Failed: {sanitize_terminal_text(target.info.title)} [{sanitize_terminal_text(target.info.video_id)}] {sanitize_terminal_text(exc)}{detail}",
                    style="red",
                    markup=False,
                )
            continue
        if execution is None:
            item = DownloadOperationItem(
                status="skipped",
                info=target.info,
                requested_input=target.original_input,
                reason="already archived (reported by yt-dlp)",
            )
            items.append(item)
            if not quiet:
                console.print(
                    f"Skipping archived (detected by yt-dlp): {sanitize_terminal_text(target.info.title)} [{sanitize_terminal_text(target.info.video_id)}]",
                    style="yellow",
                    markup=False,
                )
            continue
        record = ManifestRecord.from_download(
            target,
            output_path=execution.output_path,
            info_json_path=execution.info_json_path,
        )
        append_manifest_record(settings.manifest_file, record)
        archive_entries.add(target.info.archive_key)
        item = DownloadOperationItem(
            status="downloaded",
            info=target.info,
            requested_input=target.original_input,
            output_path=execution.output_path,
            info_json_path=execution.info_json_path,
        )
        if not quiet:
            console.print(f"Saved: {sanitize_terminal_text(execution.output_path)}", style="green", markup=False)
        try:
            summary = index_manifest_record(settings, record, fetch_subs=fetch_subs, auto_subs=auto_subs)
            item = DownloadOperationItem(
                **{**item.__dict__, "indexed": True, "index_summary": summary}
            )
        except Exception as exc:  # pragma: no cover - indexing is a best-effort follow-up
            item = DownloadOperationItem(
                **{**item.__dict__, "index_warning": sanitize_terminal_text(exc)}
            )
            if not quiet:
                error_console.print(
                    f"Warning: post-download indexing failed: {sanitize_terminal_text(exc)}",
                    style="yellow",
                    markup=False,
                )
        items.append(item)
    return items


def _download_operation_payload(
    *,
    command: str,
    requested: list[str],
    resolved_targets: list[DownloadTarget],
    items: list[DownloadOperationItem],
    mode: str,
    fetch_subs: bool,
    auto_subs: bool,
    download_root: Path,
    dry_run: bool = False,
    skipped_messages: list[str] | None = None,
) -> dict[str, Any]:
    downloaded = [_download_operation_item_payload(item) for item in items if item.status == "downloaded"]
    skipped = [_download_operation_item_payload(item) for item in items if item.status == "skipped"]
    failed = [_download_operation_item_payload(item) for item in items if item.status == "failed"]
    warnings: list[Any] = [sanitize_terminal_text(message) for message in (skipped_messages or [])]
    warnings.extend(
        {"video_id": item.info.video_id, "warning": sanitize_terminal_text(item.index_warning)}
        for item in items
        if item.index_warning
    )
    errors = [
        {
            "video_id": item.info.video_id,
            "title": sanitize_terminal_text(item.info.title),
            "message": sanitize_terminal_text(item.error_message or "download failed"),
            "stderr": sanitize_terminal_text(item.stderr) if item.stderr else "",
        }
        for item in items
        if item.status == "failed"
    ]
    if dry_run or not resolved_targets:
        status = "noop"
    elif failed:
        status = "partial" if downloaded or skipped else "error"
    elif downloaded:
        status = "ok"
    else:
        status = "noop"
    return _mutation_payload(
        command=command,
        status=status,
        summary={
            "requested": len(requested),
            "resolved": len(resolved_targets),
            "downloaded": len(downloaded),
            "skipped": len(skipped),
            "failed": len(failed),
            "dry_run": dry_run,
        },
        warnings=warnings,
        errors=errors,
        requested=requested,
        resolved_targets=[_video_info_payload(target.info) for target in resolved_targets],
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
        mode=mode,
        fetch_subs=fetch_subs,
        auto_subs=auto_subs,
        download_root=str(download_root),
        dry_run=dry_run,
    )


def _render_download_payload(payload: dict[str, Any], *, output_mode: str, quiet: bool = False) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return

    summary = payload["summary"]
    if not payload.get("resolved_targets") and not payload.get("dry_run"):
        if not quiet:
            for warning in payload.get("warnings", []):
                console.print(sanitize_terminal_text(warning), style="yellow", markup=False)
            console.print("Nothing to download.", markup=False)
        return
    if payload.get("dry_run"):
        resolved_targets = payload.get("resolved_targets", [])
        if resolved_targets:
            _render_results(
                [
                    VideoInfo(
                        video_id=str(item["video_id"]),
                        title=str(item["title"]),
                        channel=str(item["channel"]),
                        upload_date=None if str(item["upload_date"]) == "undated" else str(item["upload_date"]),
                        duration_seconds=item.get("duration_seconds") if isinstance(item.get("duration_seconds"), int | type(None)) else None,
                        extractor_key=str(item["extractor_key"]),
                        webpage_url=str(item["webpage_url"]),
                    )
                    for item in resolved_targets
                    if isinstance(item, dict)
                ],
                title="Download Preview",
                output_mode="table" if mode == "table" else "plain",
            )
        console.print(
            f"Dry run: would process {summary['resolved']} target(s) in {sanitize_terminal_text(payload['mode'])} mode.",
            style="cyan",
            markup=False,
        )
        return

    if quiet:
        return

    for warning in payload.get("warnings", []):
        if isinstance(warning, dict):
            console.print(
                f"Warning: {sanitize_terminal_text(warning.get('video_id', ''))} {sanitize_terminal_text(warning.get('warning', ''))}",
                style="yellow",
                markup=False,
            )
    console.print(
        f"Completed: {summary['downloaded']} downloaded, {summary['skipped']} skipped, {summary['failed']} failed.",
        markup=False,
    )
    downloaded_rows = payload.get("downloaded", [])
    if downloaded_rows:
        last_output = downloaded_rows[-1].get("output_path")
        if last_output:
            console.print(f"Latest save: {sanitize_terminal_text(last_output)}", style="green", markup=False)
        console.print("Next: run yt-agent library stats or yt-agent clips search \"query\".", markup=False)


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


def _validate_subtitle_flags(fetch_subs: bool, auto_subs: bool) -> None:
    if auto_subs and not fetch_subs:
        raise InvalidInputError("--auto-subs requires --fetch-subs.")


def _validate_clip_mode(mode: str) -> str:
    normalized = mode.casefold().strip()
    if normalized not in {"fast", "accurate"}:
        raise InvalidInputError("--mode must be 'fast' or 'accurate'.")
    return normalized


def _require_noninteractive_json_selection(
    *,
    output_mode: str,
    selection: str | None,
    action: str,
) -> None:
    if _normalize_output_mode(output_mode) == "json" and selection is None:
        raise InvalidInputError(f"{action} with --output json requires --select.")


def _pick_payload(query: str, results: list[VideoInfo], selected: list[VideoInfo]) -> dict[str, Any]:
    return _mutation_payload(
        command="pick",
        status="noop" if not selected else "ok",
        summary={"results": len(results), "selected": len(selected)},
        query=query,
        results=[_video_row(result, index=index) for index, result in enumerate(results, start=1)],
        selected=[_video_info_payload(item) for item in selected],
        selected_urls=[item.webpage_url for item in selected],
    )


def _render_pick_payload(payload: dict[str, Any], *, output_mode: str) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return
    results = payload.get("results", [])
    if results:
        rows: list[VideoInfo] = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            rows.append(
                VideoInfo(
                    video_id=str(entry["video_id"]),
                    title=str(entry["title"]),
                    channel=str(entry["channel"]),
                    upload_date=None if str(entry["upload_date"]) == "undated" else str(entry["upload_date"]),
                    duration_seconds=entry.get("duration_seconds") if isinstance(entry.get("duration_seconds"), int | type(None)) else None,
                    extractor_key=str(entry.get("extractor_key") or "youtube"),
                    webpage_url=str(entry["webpage_url"]),
                )
            )
        _render_results(rows, title="Search Results", output_mode="table" if mode == "table" else "plain")
    selected_urls = payload.get("selected_urls", [])
    if not selected_urls:
        console.print("No selection made.")
        return
    if mode == "plain":
        for url in selected_urls:
            console.print(sanitize_terminal_text(url), markup=False)
        return
    console.print("Selected URLs:", markup=False)
    for url in selected_urls:
        console.print(sanitize_terminal_text(url), markup=False)


def _resolve_download_inputs(
    inputs: list[str],
    settings: Settings,
    *,
    source_query: str | None = None,
    select_playlist: bool = False,
    use_fzf: bool = False,
    selection: str | None = None,
    render_selection: bool = True,
    selection_output_mode: str = "table",
    quiet: bool = False,
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
            if render_selection and not quiet:
                console.print(
                    f"No downloadable entries found in playlist: {sanitize_terminal_text(user_input)}",
                    style="yellow",
                    markup=False,
                )
            continue

        if render_selection and not quiet:
            selection_mode = "table" if _normalize_output_mode(selection_output_mode) == "table" else "plain"
            _render_playlist_summary(payload, len(resolution.targets), output_mode=selection_mode)
            _render_results(
                [target.info for target in resolution.targets],
                title="Playlist Entries",
                output_mode=selection_mode,
            )
        selected_infos = _choose_results(
            [target.info for target in resolution.targets],
            selection=selection,
            prefer_fzf=use_fzf,
            configured_selector=settings.selector,
        )
        if not selected_infos:
            if render_selection and not quiet:
                console.print(
                    f"No playlist selection made: {sanitize_terminal_text(payload.get('title') or user_input)}",
                    style="yellow",
                    markup=False,
                )
            continue

        selected_ids = {item.video_id for item in selected_infos}
        all_targets.extend([target for target in resolution.targets if target.info.video_id in selected_ids])

    return all_targets, skipped_messages


def _render_clip_hits(hits: list[ClipSearchHit], *, output_mode: str = "table") -> None:
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
                ("timestamp_url", "timestamp_url"),
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
    table.add_column("Timestamp URL", overflow="fold")
    for row in rows:
        table.add_row(
            sanitize_terminal_text(row["result_id"]),
            sanitize_terminal_text(row["source"]),
            sanitize_terminal_text(row["range"]),
            sanitize_terminal_text(row["title"]),
            sanitize_terminal_text(row["channel"]),
            sanitize_terminal_text(row["match"]),
            sanitize_terminal_text(row["timestamp_url"]),
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
                ("video_id", "video_id"),
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
            sanitize_terminal_text(row["video_id"]),
            sanitize_terminal_text(row["title"]),
            sanitize_terminal_text(row["channel"]),
            sanitize_terminal_text(row["upload_date"]),
            sanitize_terminal_text(row["duration"]),
            sanitize_terminal_text(row["transcript_segments"]),
            sanitize_terminal_text(row["chapters"]),
            "yes" if row["has_local_media"] else "no",
        )
    console.print(table)


def _library_detail_payload(store: CatalogStore, video_id: str) -> dict[str, Any]:
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
    metadata.add_row("video_id", sanitize_terminal_text(video["video_id"]))
    metadata.add_row("title", sanitize_terminal_text(video["title"]))
    metadata.add_row("channel", sanitize_terminal_text(video["channel"]))
    metadata.add_row("upload_date", sanitize_terminal_text(video["upload_date"]))
    metadata.add_row("duration", sanitize_terminal_text(video["duration"]))
    metadata.add_row("webpage_url", sanitize_terminal_text(video["webpage_url"]))
    metadata.add_row("output_path", sanitize_terminal_text(video["output_path"] or "remote only"))
    metadata.add_row("transcript_segments", sanitize_terminal_text(video["transcript_segments"]))
    metadata.add_row("chapters", sanitize_terminal_text(video["chapters"]))
    console.print(metadata)

    chapters = detail["chapters"]
    if chapters:
        chapter_table = Table(title="Chapters")
        chapter_table.add_column("#", justify="right")
        chapter_table.add_column("Title")
        chapter_table.add_column("Range")
        for chapter in chapters:
            chapter_table.add_row(
                sanitize_terminal_text(chapter["position"]),
                sanitize_terminal_text(chapter["title"]),
                sanitize_terminal_text(chapter["range"]),
            )
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
                sanitize_terminal_text(track["language"]),
                sanitize_terminal_text(track["source"]),
                "yes" if track["auto"] else "no",
                sanitize_terminal_text(track["file"]),
            )
        console.print(track_table)

    transcript_preview = detail["transcript_preview"]
    if transcript_preview:
        transcript_table = Table(title="Transcript Preview")
        transcript_table.add_column("Range")
        transcript_table.add_column("Text", overflow="fold")
        for segment in transcript_preview:
            transcript_table.add_row(
                sanitize_terminal_text(segment["range"]),
                sanitize_terminal_text(segment["text"]),
            )
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


def _index_payload(
    *,
    command: str,
    requested: list[str],
    summary: IndexSummary,
    fetch_subs: bool,
    auto_subs: bool,
    dry_run: bool = False,
    network_fetch_attempted: bool = False,
) -> dict[str, Any]:
    status = "noop" if dry_run or not any(_index_summary_payload(summary).values()) else "ok"
    return _mutation_payload(
        command=command,
        status=status,
        summary=_index_summary_payload(summary),
        requested=requested,
        fetch_subs=fetch_subs,
        auto_subs=auto_subs,
        network_fetch_attempted=network_fetch_attempted,
        dry_run=dry_run,
    )


def _render_index_payload(payload: dict[str, Any], *, output_mode: str, quiet: bool = False) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return
    summary = payload["summary"]
    if payload.get("dry_run"):
        console.print(
            f"Dry run: would index {sanitize_terminal_text(summary['videos'])} video(s) and {sanitize_terminal_text(summary['playlists'])} playlist(s).",
            style="cyan",
            markup=False,
        )
        return
    if quiet:
        return
    _render_index_summary(
        IndexSummary(
            videos=int(summary["videos"]),
            playlists=int(summary["playlists"]),
            chapters=int(summary["chapters"]),
            transcript_segments=int(summary["transcript_segments"]),
        ),
        title="Index Result",
    )


def _clip_grab_payload(
    *,
    locator: str,
    extraction: Any | None,
    mode: str,
    padding_before: float,
    padding_after: float,
    dry_run: bool = False,
) -> dict[str, Any]:
    extraction_payload = extraction if isinstance(extraction, dict) else {}
    status = "noop" if dry_run else "ok"
    return _mutation_payload(
        command="clips grab",
        status=status,
        summary={"saved": 0 if dry_run else 1, "dry_run": dry_run},
        locator=locator,
        start_seconds=extraction_payload.get("start_seconds"),
        end_seconds=extraction_payload.get("end_seconds"),
        padding_before=padding_before,
        padding_after=padding_after,
        mode=mode,
        output_path=extraction_payload.get("output_path"),
        source=extraction_payload.get("source"),
        used_remote_fallback=extraction_payload.get("used_remote_fallback"),
        dry_run=dry_run,
    )


def _render_clip_grab_payload(payload: dict[str, Any], *, output_mode: str, quiet: bool = False) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return
    if payload.get("dry_run"):
        console.print(
            f"Dry run: would extract {sanitize_terminal_text(payload['locator'])} as a {sanitize_terminal_text(payload['mode'])} clip.",
            style="cyan",
            markup=False,
        )
        return
    if quiet:
        return
    console.print(
        f"Saved clip: {sanitize_terminal_text(payload['output_path'])} ({sanitize_terminal_text(payload['source'])})",
        style="green",
        markup=False,
    )
    if not quiet:
        console.print("Next: run yt-agent library show VIDEO_ID or yt-agent tui.", markup=False)


def _library_remove_payload(
    *,
    requested: list[str],
    removed: list[str],
    not_found: list[str],
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        status = "noop"
    elif removed and not_found:
        status = "partial"
    elif removed:
        status = "ok"
    else:
        status = "noop"
    return _mutation_payload(
        command="library remove",
        status=status,
        summary={"requested": len(requested), "removed": len(removed), "not_found": len(not_found), "dry_run": dry_run},
        requested=requested,
        removed=removed,
        not_found=not_found,
        dry_run=dry_run,
    )


def _render_library_remove_payload(payload: dict[str, Any], *, output_mode: str) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return
    if payload.get("dry_run"):
        console.print(
            f"Dry run: would remove {sanitize_terminal_text(payload['summary']['removed'])} catalog entrie(s); {sanitize_terminal_text(payload['summary']['not_found'])} would remain missing.",
            style="cyan",
            markup=False,
        )
        return
    if mode == "plain":
        for vid in payload["removed"]:
            console.print(f"removed\t{sanitize_terminal_text(vid)}", markup=False)
        for vid in payload["not_found"]:
            console.print(f"not_found\t{sanitize_terminal_text(vid)}", markup=False)
        return
    for vid in payload["removed"]:
        console.print(f"Removed: {sanitize_terminal_text(vid)}", style="green", markup=False)
    for vid in payload["not_found"]:
        console.print(f"Not found: {sanitize_terminal_text(vid)}", style="yellow", markup=False)


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

    _run_guarded(_command, output_mode=output)


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

    _run_guarded(_command, output_mode=output)


@app.command()
def pick(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for interactive selection."),
    select: str | None = typer.Option(None, "--select", help="Choose result indexes without prompting, e.g. 1,3."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search YouTube and interactively select result URLs."""

    def _command() -> None:
        settings = _load_settings(config)
        results = yt_dlp.search(query, limit=limit or settings.search_limit)
        if not results:
            payload = _pick_payload(query, results, [])
            if _normalize_output_mode(output) == "json":
                _print_json(payload)
            else:
                console.print("No matches found.")
            return
        _require_noninteractive_json_selection(output_mode=output, selection=select, action="pick")
        selected = _choose_results(
            results,
            selection=select,
            prefer_fzf=use_fzf,
            configured_selector=settings.selector,
        )
        payload = _pick_payload(query, results, selected)
        _render_pick_payload(payload, output_mode=output)

    _run_guarded(_command, output_mode=output)


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

    _run_guarded(_command, output_mode=output)


@app.command()
def download(
    targets: list[str] = typer.Argument(default=None, help="Video URLs, playlist URLs, or YouTube video ids."),
    from_file: Path | None = typer.Option(
        None,
        "--from-file",
        help="Read targets from a file (one per line, # comments ignored).",
    ),
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
    audio: bool = typer.Option(False, "--audio", help="Download audio only instead of video."),
    fetch_subs: bool = typer.Option(False, "--fetch-subs", help="Fetch subtitles during download."),
    auto_subs: bool = typer.Option(
        False,
        "--auto-subs",
        help="Include auto-generated subtitles (requires --fetch-subs).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview resolved downloads without writing files."),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for playlist entry selection."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Download videos into the organized local library."""

    def _command() -> None:
        settings = _load_settings(config)
        _validate_subtitle_flags(fetch_subs, auto_subs)
        if _normalize_output_mode(output) == "json" and select_playlist and select is None:
            raise InvalidInputError("--select-playlist with --output json requires --select.")
        all_inputs: list[str] = list(targets or [])
        if from_file is not None:
            all_inputs.extend(_read_targets_from_file(from_file))
        if not all_inputs:
            raise InvalidInputError("No targets provided. Pass video URLs as arguments or use --from-file.")
        mode = "audio" if audio or settings.default_mode == "audio" else "video"
        resolve_kwargs: dict[str, Any] = {
            "select_playlist": select_playlist or select is not None,
            "use_fzf": use_fzf,
            "render_selection": _normalize_output_mode(output) != "json",
            "selection_output_mode": output,
            "quiet": quiet or _normalize_output_mode(output) == "json",
        }
        if select is not None:
            resolve_kwargs["selection"] = select
        if dry_run:
            resolved_targets, skipped_messages = _resolve_download_inputs(
                all_inputs,
                settings,
                **resolve_kwargs,
            )
            items: list[DownloadOperationItem] = []
        else:
            with operation_lock(_operation_lock_path(settings)):
                _prepare_storage(settings)
                resolved_targets, skipped_messages = _resolve_download_inputs(
                    all_inputs,
                    settings,
                    **resolve_kwargs,
                )
                items = _download_targets(
                    resolved_targets,
                    settings,
                    mode=mode,
                    fetch_subs=fetch_subs,
                    auto_subs=auto_subs,
                    quiet=quiet or _normalize_output_mode(output) == "json",
                    show_failure_details=_normalize_output_mode(output) != "json",
                ) if resolved_targets else []
        payload = _download_operation_payload(
            command="download",
            requested=all_inputs,
            resolved_targets=resolved_targets,
            items=items,
            mode=mode,
            fetch_subs=fetch_subs,
            auto_subs=auto_subs,
            download_root=settings.download_root,
            dry_run=dry_run,
            skipped_messages=skipped_messages,
        )
        _render_download_payload(payload, output_mode=output, quiet=quiet)
        if not dry_run and int(payload["summary"]["failed"]) > 0:
            raise typer.Exit(code=int(ExitCode.EXTERNAL))

    _run_guarded(_command, output_mode=output)


@app.command()
def grab(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for interactive selection."),
    select: str | None = typer.Option(None, "--select", help="Choose result indexes without prompting, e.g. 1,3."),
    audio: bool = typer.Option(False, "--audio", help="Download audio only instead of video."),
    fetch_subs: bool = typer.Option(False, "--fetch-subs", help="Fetch subtitles during download."),
    auto_subs: bool = typer.Option(
        False,
        "--auto-subs",
        help="Include auto-generated subtitles (requires --fetch-subs).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview selected downloads without writing files."),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search, select, and download in one flow."""

    def _command() -> None:
        settings = _load_settings(config)
        _validate_subtitle_flags(fetch_subs, auto_subs)
        mode = "audio" if audio or settings.default_mode == "audio" else "video"
        if dry_run:
            results = yt_dlp.search(query, limit=limit or settings.search_limit)
            if not results:
                if _normalize_output_mode(output) != "json":
                    console.print("No matches found.")
                    return
                payload = _download_operation_payload(
                    command="grab",
                    requested=[query],
                    resolved_targets=[],
                    items=[],
                    mode=mode,
                    fetch_subs=fetch_subs,
                    auto_subs=auto_subs,
                    download_root=settings.download_root,
                    dry_run=dry_run,
                )
                _render_download_payload(payload, output_mode=output, quiet=quiet)
                return
            _require_noninteractive_json_selection(output_mode=output, selection=select, action="grab")
            if not quiet and _normalize_output_mode(output) != "json":
                _render_results(
                    results,
                    title="Search Results",
                    output_mode="table" if _normalize_output_mode(output) == "table" else "plain",
                )
            selected = _choose_results(
                results,
                selection=select,
                prefer_fzf=use_fzf,
                configured_selector=settings.selector,
            )
            targets = [
                DownloadTarget(original_input=item.webpage_url, info=item, source_query=query)
                for item in selected
            ]
            items: list[DownloadOperationItem] = []
        else:
            with operation_lock(_operation_lock_path(settings)):
                results = yt_dlp.search(query, limit=limit or settings.search_limit)
                if not results:
                    if _normalize_output_mode(output) != "json":
                        console.print("No matches found.")
                        return
                    payload = _download_operation_payload(
                        command="grab",
                        requested=[query],
                        resolved_targets=[],
                        items=[],
                        mode=mode,
                        fetch_subs=fetch_subs,
                        auto_subs=auto_subs,
                        download_root=settings.download_root,
                        dry_run=dry_run,
                    )
                    _render_download_payload(payload, output_mode=output, quiet=quiet)
                    return
                _require_noninteractive_json_selection(output_mode=output, selection=select, action="grab")
                if not quiet and _normalize_output_mode(output) != "json":
                    _render_results(
                        results,
                        title="Search Results",
                        output_mode="table" if _normalize_output_mode(output) == "table" else "plain",
                    )
                selected = _choose_results(
                    results,
                    selection=select,
                    prefer_fzf=use_fzf,
                    configured_selector=settings.selector,
                )
                targets = [
                    DownloadTarget(original_input=item.webpage_url, info=item, source_query=query)
                    for item in selected
                ]
                _prepare_storage(settings)
                items = _download_targets(
                    targets,
                    settings,
                    mode=mode,
                    fetch_subs=fetch_subs,
                    auto_subs=auto_subs,
                    quiet=quiet or _normalize_output_mode(output) == "json",
                    show_failure_details=_normalize_output_mode(output) != "json",
                ) if targets else []
        payload = _download_operation_payload(
            command="grab",
            requested=[query],
            resolved_targets=targets,
            items=items,
            mode=mode,
            fetch_subs=fetch_subs,
            auto_subs=auto_subs,
            download_root=settings.download_root,
            dry_run=dry_run,
        )
        _render_download_payload(payload, output_mode=output, quiet=quiet)
        if not dry_run and int(payload["summary"]["failed"]) > 0:
            raise typer.Exit(code=int(ExitCode.EXTERNAL))

    _run_guarded(_command, output_mode=output)


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
        ensure_private_file(settings.config_path)
        console.print(f"Wrote config: {sanitize_terminal_text(settings.config_path)}", style="green", markup=False)

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
            table.add_row(sanitize_terminal_text(key), sanitize_terminal_text(value))
        console.print(table)

    _run_guarded(_command, output_mode=output)


@config_app.command("validate")
def config_validate_command(
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Validate the active config file and report any errors."""

    def _command() -> None:
        settings = _load_settings(config)
        console.print(
            f"Config is valid: {sanitize_terminal_text(settings.config_path)}",
            style="green",
            markup=False,
        )

    _run_guarded(_command)


@index_app.command("refresh")
def index_refresh_command(
    fetch_subs: bool = typer.Option(
        False,
        "--fetch-subs/--no-fetch-subs",
        help="Fetch missing subtitles during refresh.",
    ),
    auto_subs: bool = typer.Option(
        False,
        "--auto-subs/--manual-subs",
        help="Allow automatic subtitles when manuals are missing.",
    ),
    lang: str | None = typer.Option(None, "--lang", help="Preferred subtitle language expression, e.g. en.*"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview refresh work without writing the catalog."),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Backfill or refresh the local catalog from the download manifest."""

    def _command() -> None:
        settings = _load_settings(config)
        _validate_subtitle_flags(fetch_subs, auto_subs)
        if dry_run:
            summary = IndexSummary(videos=len(iter_manifest_records(settings.manifest_file)))
            payload = _index_payload(
                command="index refresh",
                requested=["manifest"],
                summary=summary,
                fetch_subs=fetch_subs,
                auto_subs=auto_subs,
                dry_run=True,
                network_fetch_attempted=False,
            )
        else:
            with operation_lock(_operation_lock_path(settings)):
                _prepare_storage(settings)
                summary = index_refresh(settings, fetch_subs=fetch_subs, auto_subs=auto_subs, lang=lang)
            payload = _index_payload(
                command="index refresh",
                requested=["manifest"],
                summary=summary,
                fetch_subs=fetch_subs,
                auto_subs=auto_subs,
                dry_run=False,
                network_fetch_attempted=fetch_subs,
            )
        _render_index_payload(payload, output_mode=output, quiet=quiet)

    _run_guarded(_command, output_mode=output)


@index_app.command("add")
def index_add_command(
    target: str,
    fetch_subs: bool = typer.Option(
        False,
        "--fetch-subs/--no-fetch-subs",
        help="Fetch subtitles while indexing the target.",
    ),
    auto_subs: bool = typer.Option(
        False,
        "--auto-subs/--manual-subs",
        help="Allow automatic subtitles when manuals are missing.",
    ),
    lang: str | None = typer.Option(None, "--lang", help="Preferred subtitle language expression, e.g. en.*"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview target indexing without writing the catalog."),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Index a specific video or playlist target into the local catalog."""

    def _command() -> None:
        settings = _load_settings(config)
        _validate_subtitle_flags(fetch_subs, auto_subs)
        if dry_run:
            payload = yt_dlp.fetch_info(target)
            resolution = yt_dlp.resolve_payload(target, payload)
            summary = IndexSummary(
                videos=len(resolution.targets),
                playlists=1 if isinstance(payload.get("entries"), list) else 0,
            )
            result = _index_payload(
                command="index add",
                requested=[target],
                summary=summary,
                fetch_subs=fetch_subs,
                auto_subs=auto_subs,
                dry_run=True,
                network_fetch_attempted=False,
            )
        else:
            with operation_lock(_operation_lock_path(settings)):
                _prepare_storage(settings)
                summary = index_target(settings, target, fetch_subs=fetch_subs, auto_subs=auto_subs, lang=lang)
            result = _index_payload(
                command="index add",
                requested=[target],
                summary=summary,
                fetch_subs=fetch_subs,
                auto_subs=auto_subs,
                dry_run=False,
                network_fetch_attempted=fetch_subs,
            )
        _render_index_payload(result, output_mode=output, quiet=quiet)

    _run_guarded(_command, output_mode=output)


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

    _run_guarded(_command, output_mode=output)


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
            "webpage_url",
            "timestamp_url",
            "output_path",
            "result_id_note",
        ):
            value = payload[key]
            table.add_row(
                sanitize_terminal_text(key),
                sanitize_terminal_text("remote only" if key == "output_path" and not value else value),
            )
        console.print(table)

    _run_guarded(_command, output_mode=output)


@clips_app.command("grab")
def clips_grab_command(
    result_id: str | None = typer.Argument(None, help="Clip search result id like transcript:12 or chapter:3."),
    video_id: str | None = typer.Option(None, "--video-id", help="Cataloged video id for explicit clip extraction."),
    start_seconds: float | None = typer.Option(None, "--start-seconds", help="Explicit clip start in seconds."),
    end_seconds: float | None = typer.Option(None, "--end-seconds", help="Explicit clip end in seconds."),
    padding_before: float = typer.Option(0.0, "--padding-before", min=0.0, help="Seconds to prepend."),
    padding_after: float = typer.Option(0.0, "--padding-after", min=0.0, help="Seconds to append."),
    mode: str = typer.Option("fast", "--mode", help="Extraction mode: fast or accurate."),
    remote_fallback: bool = typer.Option(
        False,
        "--remote-fallback",
        help="Fallback to yt-dlp section download if local media is missing.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the clip extraction without writing files."),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Extract a clip from a cataloged chapter or transcript hit."""

    def _command() -> None:
        settings = _load_settings(config)
        normalized_mode = _validate_clip_mode(mode)
        using_explicit_range = any(value is not None for value in (video_id, start_seconds, end_seconds))
        if using_explicit_range and result_id is not None:
            raise InvalidInputError("Use either RESULT_ID or --video-id/--start-seconds/--end-seconds, not both.")
        if using_explicit_range:
            if video_id is None or start_seconds is None or end_seconds is None:
                raise InvalidInputError("--video-id, --start-seconds, and --end-seconds are required together.")
            if end_seconds <= start_seconds:
                raise InvalidInputError("--end-seconds must be greater than --start-seconds.")
            locator = f"{video_id}:{start_seconds:.3f}-{end_seconds:.3f}"
        elif result_id is not None:
            locator = result_id
        else:
            raise InvalidInputError("Pass a RESULT_ID or --video-id/--start-seconds/--end-seconds.")

        if dry_run:
            store = CatalogStore(settings.catalog_file)
            if using_explicit_range:
                assert video_id is not None
                assert start_seconds is not None
                assert end_seconds is not None
                video = store.get_video(video_id, readonly=True)
                if video is None:
                    raise InvalidInputError(f"Video id '{video_id}' is not in the catalog.")
                info = VideoInfo(
                    video_id=video.video_id,
                    title=video.title,
                    channel=video.channel,
                    upload_date=video.upload_date,
                    duration_seconds=video.duration_seconds,
                    extractor_key=video.extractor_key,
                    webpage_url=video.webpage_url,
                    original_url=video.requested_input,
                )
                preview_start = max(0.0, start_seconds)
                preview_end = end_seconds
                preview_output = build_clip_output_path(
                    settings.clips_root,
                    info,
                    label="range",
                    start_seconds=preview_start,
                    end_seconds=preview_end,
                    extension="mp4",
                )
            else:
                assert result_id is not None
                hit = store.get_clip_hit(result_id, readonly=True)
                if hit is None:
                    raise InvalidInputError(f"Unknown clip result '{result_id}'.")
                info = VideoInfo(
                    video_id=hit.video_id,
                    title=hit.title,
                    channel=hit.channel,
                    upload_date=None,
                    duration_seconds=None,
                    extractor_key="youtube",
                    webpage_url=hit.webpage_url,
                    original_url=hit.webpage_url,
                )
                preview_start = max(0.0, hit.start_seconds - padding_before)
                preview_end = max(preview_start + 0.1, hit.end_seconds + padding_after)
                preview_output = build_clip_output_path(
                    settings.clips_root,
                    info,
                    label=hit.source,
                    start_seconds=preview_start,
                    end_seconds=preview_end,
                    extension="mp4",
                )
            payload = _clip_grab_payload(
                locator=locator,
                extraction={
                    "output_path": str(preview_output),
                    "source": "remote" if remote_fallback else "local",
                    "start_seconds": preview_start,
                    "end_seconds": preview_end,
                    "used_remote_fallback": remote_fallback,
                },
                mode=normalized_mode,
                padding_before=padding_before,
                padding_after=padding_after,
                dry_run=True,
            )
        else:
            with operation_lock(_operation_lock_path(settings)):
                _prepare_storage(settings)
                if using_explicit_range:
                    assert video_id is not None
                    assert start_seconds is not None
                    assert end_seconds is not None
                    extraction = extract_clip_for_range(
                        settings,
                        video_id=video_id,
                        start_seconds=start_seconds,
                        end_seconds=end_seconds,
                        mode=normalized_mode,
                        prefer_remote=remote_fallback,
                    )
                else:
                    assert result_id is not None
                    extraction = extract_clip(
                        settings,
                        result_id,
                        padding_before=padding_before,
                        padding_after=padding_after,
                        mode=normalized_mode,
                        prefer_remote=remote_fallback,
                    )
            payload = _clip_grab_payload(
                locator=locator,
                extraction={
                    "output_path": str(extraction.output_path),
                    "source": extraction.source,
                    "start_seconds": extraction.start_seconds,
                    "end_seconds": extraction.end_seconds,
                    "used_remote_fallback": extraction.used_remote_fallback,
                },
                mode=normalized_mode,
                padding_before=padding_before,
                padding_after=padding_after,
                dry_run=False,
            )
        _render_clip_grab_payload(payload, output_mode=output, quiet=quiet)

    _run_guarded(_command, output_mode=output)


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
            playlist_id=playlist,
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

    _run_guarded(_command, output_mode=output)


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
            playlist_id=playlist,
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

    _run_guarded(_command, output_mode=output)


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

    _run_guarded(_command, output_mode=output)


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
            table.add_row(sanitize_terminal_text(key), sanitize_terminal_text(value))
        console.print(table)

    _run_guarded(_command, output_mode=output)


@library_app.command("channels")
def library_channels_command(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """List distinct channels in the catalog."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        channels = store.list_channels()
        mode = _normalize_output_mode(output)
        if not channels:
            if mode == "json":
                _print_json([])
            else:
                console.print("No channels found.")
            return
        rows = [{"channel": channel_name} for channel_name in channels]
        if mode == "json":
            _print_json(rows)
            return
        if mode == "plain":
            _print_plain_rows([("channel", "channel")], rows)
            return
        table = Table(title="Channels")
        table.add_column("Channel")
        for row in rows:
            table.add_row(sanitize_terminal_text(row["channel"]))
        console.print(table)

    _run_guarded(_command, output_mode=output)


@library_app.command("playlists")
def library_playlists_command(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """List indexed playlists with video counts."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings)
        playlists = store.list_playlists()
        mode = _normalize_output_mode(output)
        if not playlists:
            if mode == "json":
                _print_json([])
            else:
                console.print("No playlists found.")
            return
        if mode == "json":
            _print_json(playlists)
            return
        if mode == "plain":
            _print_plain_rows(
                [
                    ("playlist_id", "Playlist ID"),
                    ("title", "Title"),
                    ("channel", "Channel"),
                    ("entry_count", "Videos"),
                ],
                playlists,
            )
            return
        table = Table(title="Playlists")
        table.add_column("Playlist ID")
        table.add_column("Title")
        table.add_column("Channel")
        table.add_column("Videos", justify="right")
        for row in playlists:
            table.add_row(
                sanitize_terminal_text(row["playlist_id"]),
                sanitize_terminal_text(row["title"]),
                sanitize_terminal_text(row["channel"]),
                sanitize_terminal_text(row["entry_count"]),
            )
        console.print(table)

    _run_guarded(_command, output_mode=output)


@library_app.command("remove")
def library_remove_command(
    video_ids: list[str] = typer.Argument(..., help="One or more video IDs to remove from the catalog."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview catalog removals without writing changes."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Remove videos from the catalog. Media files on disk are not affected."""

    def _command() -> None:
        settings = _load_settings(config)
        if dry_run:
            store = CatalogStore(settings.catalog_file)
            removed = [vid for vid in video_ids if store.get_video(vid, readonly=True) is not None]
            not_found = [vid for vid in video_ids if vid not in set(removed)]
        else:
            with operation_lock(_operation_lock_path(settings)):
                store = _catalog(settings)
                removed = []
                not_found = []
                for vid in video_ids:
                    if store.delete_video(vid):
                        removed.append(vid)
                    else:
                        not_found.append(vid)
        payload = _library_remove_payload(requested=video_ids, removed=removed, not_found=not_found, dry_run=dry_run)
        _render_library_remove_payload(payload, output_mode=output)

    _run_guarded(_command, output_mode=output)


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
