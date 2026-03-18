"""Output, render, and payload helpers for the yt-agent CLI."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from yt_agent import yt_dlp
from yt_agent.catalog import CatalogStore
from yt_agent.config import Settings
from yt_agent.errors import InvalidInputError
from yt_agent.indexer import IndexSummary
from yt_agent.models import CatalogVideo, ClipSearchHit, DownloadTarget, ManifestRecord, VideoInfo
from yt_agent.security import sanitize_terminal_text

READ_OUTPUT_HELP = "Render output as table, json, or plain text."
OUTPUT_MODES = {"table", "json", "plain"}
MUTATION_SCHEMA_VERSION = 1

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
        "message": message,
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
        payload["reason"] = item.reason
    if item.output_path is not None:
        payload["output_path"] = str(item.output_path)
    if item.info_json_path is not None:
        payload["info_json_path"] = str(item.info_json_path)
    payload["indexed"] = item.indexed
    if item.index_summary is not None:
        payload["index_summary"] = _index_summary_payload(item.index_summary)
    if item.index_warning:
        payload["index_warning"] = item.index_warning
    if item.error_message:
        payload["error_message"] = item.error_message
    if item.stderr:
        payload["stderr"] = sanitize_terminal_text(item.stderr)
    return payload


def _normalize_output_mode(value: str) -> str:
    mode = value.casefold().strip()
    if mode not in OUTPUT_MODES:
        allowed = ", ".join(sorted(OUTPUT_MODES))
        raise InvalidInputError(f"Output mode must be one of: {allowed}")
    return mode


def _print_json(payload: object) -> None:
    console.file.write(json.dumps(payload, indent=2))
    console.file.write("\n")
    console.file.flush()


def _print_json_error(payload: object) -> None:
    error_console.file.write(json.dumps(payload, indent=2))
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
        next_step = (
            'Ready for search/download: run yt-agent download URL or yt-agent search "query".'
        )
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
            "notes": (
                "Search, download, and clip behavior depend on external tools such as "
                "yt-dlp and ffmpeg."
            ),
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


def _render_results(
    results: list[VideoInfo], *, title: str = "Results", output_mode: str = "table"
) -> None:
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
        table.add_row(
            "upload_date", sanitize_terminal_text(payload.get("upload_date") or "undated")
        )
        table.add_row("webpage_url", sanitize_terminal_text(payload.get("webpage_url") or ""))
    console.print(table)

    entries = payload.get("entries")
    if isinstance(entries, list) and entries:
        _print_plain_mapping(
            [
                (f"skipped_message_{index}", message)
                for index, message in enumerate(payload.get("skipped_messages", []), start=1)
            ]
        )
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
    table.add_row(
        "channel",
        sanitize_terminal_text(payload.get("channel") or payload.get("uploader") or "Unknown"),
    )
    table.add_row("entries", sanitize_terminal_text(entry_count))
    table.add_row(
        "url",
        sanitize_terminal_text(payload.get("webpage_url") or payload.get("original_url") or ""),
    )
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
    platform = sanitize_terminal_text(payload["support"]["platform"])
    status = sanitize_terminal_text(payload["support"]["status"])
    console.print(
        (
            f"Platform: {platform} ({status}). "
            "Supported today: macOS and Linux; Windows is experimental."
        ),
        markup=False,
    )
    console.print(sanitize_terminal_text(payload["support"]["notes"]), markup=False)
    console.print(sanitize_terminal_text(payload["support"]["next_step"]), markup=False)
    return missing_required


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
    downloaded = [
        _download_operation_item_payload(item) for item in items if item.status == "downloaded"
    ]
    skipped = [_download_operation_item_payload(item) for item in items if item.status == "skipped"]
    failed = [_download_operation_item_payload(item) for item in items if item.status == "failed"]
    warnings: list[Any] = list(skipped_messages or [])
    warnings.extend(
        {"video_id": item.info.video_id, "warning": item.index_warning}
        for item in items
        if item.index_warning
    )
    errors = [
        {
            "video_id": item.info.video_id,
            "title": item.info.title,
            "message": item.error_message or "download failed",
            "stderr": sanitize_terminal_text(item.stderr or ""),
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


def _render_download_payload(
    payload: dict[str, Any], *, output_mode: str, quiet: bool = False
) -> None:
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
                        upload_date=None
                        if str(item["upload_date"]) == "undated"
                        else str(item["upload_date"]),
                        duration_seconds=item.get("duration_seconds")
                        if isinstance(item.get("duration_seconds"), int | type(None))
                        else None,
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
            (
                f"Dry run: would process {summary['resolved']} target(s) "
                f"in {sanitize_terminal_text(payload['mode'])} mode."
            ),
            style="cyan",
            markup=False,
        )
        return

    if quiet:
        return

    for warning in payload.get("warnings", []):
        if isinstance(warning, dict):
            warning_id = sanitize_terminal_text(warning.get("video_id", ""))
            warning_text = sanitize_terminal_text(warning.get("warning", ""))
            console.print(
                f"Warning: {warning_id} {warning_text}",
                style="yellow",
                markup=False,
            )
    console.print(
        (
            f"Completed: {summary['downloaded']} downloaded, "
            f"{summary['skipped']} skipped, {summary['failed']} failed."
        ),
        markup=False,
    )
    downloaded_rows = payload.get("downloaded", [])
    if downloaded_rows:
        last_output = downloaded_rows[-1].get("output_path")
        if last_output:
            console.print(
                f"Latest save: {sanitize_terminal_text(last_output)}",
                style="green",
                markup=False,
            )
        console.print(
            'Next: run yt-agent library stats or yt-agent clips search "query".',
            markup=False,
        )


def _pick_payload(
    query: str, results: list[VideoInfo], selected: list[VideoInfo]
) -> dict[str, Any]:
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
                    upload_date=None
                    if str(entry["upload_date"]) == "undated"
                    else str(entry["upload_date"]),
                    duration_seconds=entry.get("duration_seconds")
                    if isinstance(entry.get("duration_seconds"), int | type(None))
                    else None,
                    extractor_key=str(entry.get("extractor_key") or "youtube"),
                    webpage_url=str(entry["webpage_url"]),
                )
            )
        _render_results(
            rows, title="Search Results", output_mode="table" if mode == "table" else "plain"
        )
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
            {
                "position": chapter.position + 1,
                "title": chapter.title,
                "range": chapter.display_range,
            }
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


def _render_library_detail(
    store: CatalogStore, video_id: str, *, output_mode: str = "table"
) -> None:
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
            _print_plain_rows(
                [("position", "position"), ("title", "title"), ("range", "range")], chapters
            )
        subtitle_tracks = detail["subtitle_tracks"]
        if subtitle_tracks:
            console.print("", markup=False)
            _print_plain_rows(
                [
                    ("language", "language"),
                    ("source", "source"),
                    ("auto", "auto"),
                    ("file", "file"),
                ],
                subtitle_tracks,
            )
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


def _render_index_payload(
    payload: dict[str, Any], *, output_mode: str, quiet: bool = False
) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return
    summary = payload["summary"]
    if payload.get("dry_run"):
        videos = sanitize_terminal_text(summary["videos"])
        playlists = sanitize_terminal_text(summary["playlists"])
        console.print(
            f"Dry run: would index {videos} video(s) and {playlists} playlist(s).",
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
        output_path_is_template=bool(extraction_payload.get("output_path_is_template")),
        source=extraction_payload.get("source"),
        used_remote_fallback=extraction_payload.get("used_remote_fallback"),
        dry_run=dry_run,
    )


def _render_clip_grab_payload(
    payload: dict[str, Any], *, output_mode: str, quiet: bool = False
) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return
    if payload.get("dry_run"):
        locator = sanitize_terminal_text(payload["locator"])
        mode_label = sanitize_terminal_text(payload["mode"])
        console.print(
            f"Dry run: would extract {locator} as a {mode_label} clip.",
            style="cyan",
            markup=False,
        )
        return
    if quiet:
        return
    output_path = sanitize_terminal_text(payload["output_path"])
    source = sanitize_terminal_text(payload["source"])
    console.print(
        f"Saved clip: {output_path} ({source})",
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
        summary={
            "requested": len(requested),
            "removed": len(removed),
            "not_found": len(not_found),
            "dry_run": dry_run,
        },
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
        removed = sanitize_terminal_text(payload["summary"]["removed"])
        not_found = sanitize_terminal_text(payload["summary"]["not_found"])
        console.print(
            f"Dry run: would remove {removed} catalog entrie(s); {not_found} would remain missing.",
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


def _history_row(record: ManifestRecord) -> dict[str, str]:
    return {
        "video_id": record.video_id,
        "title": record.title,
        "channel": record.channel,
        "downloaded_at": record.downloaded_at,
    }


def _render_history_rows(rows: list[dict[str, str]], *, output_mode: str) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(rows)
        return
    if mode == "plain":
        _print_plain_rows(
            [
                ("video_id", "video_id"),
                ("title", "title"),
                ("channel", "channel"),
                ("downloaded_at", "downloaded_at"),
            ],
            rows,
        )
        return
    table = Table(title="Download History")
    table.add_column("Video ID")
    table.add_column("Title")
    table.add_column("Channel")
    table.add_column("Downloaded At")
    for row in rows:
        table.add_row(
            sanitize_terminal_text(row["video_id"]),
            sanitize_terminal_text(row["title"]),
            sanitize_terminal_text(row["channel"]),
            sanitize_terminal_text(row["downloaded_at"]),
        )
    console.print(table)


def _cleanup_payload(*, candidates: dict[str, list[Path]], dry_run: bool) -> dict[str, Any]:
    cache_dirs = [str(path) for path in candidates["removed_cache_dirs"]]
    empty_dirs = [str(path) for path in candidates["removed_empty_dirs"]]
    part_files = [str(path) for path in candidates["removed_part_files"]]
    total_removed = len(cache_dirs) + len(empty_dirs) + len(part_files)
    status = "noop" if dry_run or total_removed == 0 else "ok"
    return _mutation_payload(
        command="cleanup",
        status=status,
        summary={
            "removed_cache_dirs": len(cache_dirs),
            "removed_empty_dirs": len(empty_dirs),
            "removed_part_files": len(part_files),
            "dry_run": dry_run,
        },
        removed_cache_dirs=cache_dirs,
        removed_empty_dirs=empty_dirs,
        removed_part_files=part_files,
        dry_run=dry_run,
    )


def _render_cleanup_payload(
    payload: dict[str, Any], *, output_mode: str, quiet: bool = False
) -> None:
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json(payload)
        return
    if quiet:
        return

    summary = payload["summary"]
    rows = [
        {"kind": "cache_dir", "path": path} for path in payload["removed_cache_dirs"]
    ] + [{"kind": "empty_dir", "path": path} for path in payload["removed_empty_dirs"]] + [
        {"kind": "part_file", "path": path} for path in payload["removed_part_files"]
    ]

    if payload.get("dry_run"):
        console.print(
            "Dry run: "
            f"would remove {sanitize_terminal_text(summary['removed_cache_dirs'])} cache dir(s), "
            f"{sanitize_terminal_text(summary['removed_empty_dirs'])} empty dir(s), and "
            f"{sanitize_terminal_text(summary['removed_part_files'])} part file(s).",
            style="cyan",
            markup=False,
        )
    elif not rows:
        console.print("Nothing to clean.", markup=False)
        return
    else:
        console.print(
            "Removed "
            f"{sanitize_terminal_text(summary['removed_cache_dirs'])} cache dir(s), "
            f"{sanitize_terminal_text(summary['removed_empty_dirs'])} empty dir(s), and "
            f"{sanitize_terminal_text(summary['removed_part_files'])} part file(s).",
            style="green",
            markup=False,
        )

    if not rows:
        console.print("No orphaned artifacts found.", markup=False)
        return

    if mode == "plain":
        _print_plain_rows([("kind", "kind"), ("path", "path")], rows)
        return

    table = Table(title="Cleanup Preview" if payload.get("dry_run") else "Cleanup Result")
    table.add_column("Type")
    table.add_column("Path", overflow="fold")
    for row in rows:
        table.add_row(
            sanitize_terminal_text(row["kind"]),
            sanitize_terminal_text(row["path"]),
        )
    console.print(table)


__all__ = [
    "DownloadOperationItem",
    "MUTATION_SCHEMA_VERSION",
    "OUTPUT_MODES",
    "READ_OUTPUT_HELP",
    "_build_info_payload",
    "_catalog_video_row",
    "_cleanup_payload",
    "_clip_grab_payload",
    "_clip_hit_row",
    "_doctor_payload",
    "_download_operation_item_payload",
    "_download_operation_payload",
    "_history_row",
    "_index_payload",
    "_index_summary_payload",
    "_json_error_payload",
    "_library_detail_payload",
    "_library_remove_payload",
    "_mutation_payload",
    "_normalize_optional_output_mode",
    "_normalize_output_mode",
    "_platform_status",
    "_pick_payload",
    "_print_json",
    "_print_json_error",
    "_print_plain_mapping",
    "_print_plain_rows",
    "_render_cleanup_payload",
    "_render_clip_grab_payload",
    "_render_clip_hits",
    "_render_doctor",
    "_render_download_payload",
    "_render_history_rows",
    "_render_index_payload",
    "_render_index_summary",
    "_render_info_payload",
    "_render_library_detail",
    "_render_library_remove_payload",
    "_render_library_rows",
    "_render_pick_payload",
    "_render_playlist_summary",
    "_render_results",
    "_tool_install_hint",
    "_video_info_payload",
    "_video_row",
    "console",
    "error_console",
]
