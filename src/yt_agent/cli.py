"""Typer application for yt-agent."""

from __future__ import annotations

import csv
import importlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import typer
from rich.console import Console
from rich.table import Table
from typer._completion_shared import get_completion_script
from typer._completion_shared import install as typer_completion_install

from yt_agent import __version__, yt_dlp
from yt_agent.archive import ensure_archive_file
from yt_agent.config import Settings, load_settings, render_default_config
from yt_agent.curation import CurationStore
from yt_agent.errors import (
    DependencyError,
    ExitCode,
    ExternalCommandError,
    InvalidInputError,
    StorageError,
    YtAgentError,
)
from yt_agent.job_queue import JOB_OPERATIONS, JobQueue, QueueJob, job_payload
from yt_agent.library import sanitize_file_id
from yt_agent.manifest import append_manifest_record, ensure_manifest_file, iter_manifest_records
from yt_agent.models import DownloadTarget, ManifestRecord, VideoInfo
from yt_agent.playback import launch_media
from yt_agent.security import (
    atomic_write_artifact_text,
    atomic_write_text,
    operation_lock,
    sanitize_terminal_text,
)
from yt_agent.selector import parse_selection, select_results

APP_HELP = "Terminal-first YouTube search, download, catalog, and clip tooling."
READ_OUTPUT_HELP = "Render output as table, json, or plain text."
DRY_RUN_HELP = "Preview the operation without writing changes."
OUTPUT_MODES = {"table", "json", "plain"}
MUTATION_SCHEMA_VERSION = 1

console = Console()
error_console = Console(stderr=True)

app = typer.Typer(help=APP_HELP, no_args_is_help=True)
index_app = typer.Typer(help="Catalog indexing commands.")
clips_app = typer.Typer(help="Transcript and chapter clip workflows.")
library_app = typer.Typer(help="Local library browsing commands.")
config_app = typer.Typer(help="Configuration helpers.")
completions_app = typer.Typer(help="Shell completion helpers.")
queue_app = typer.Typer(help="Persistent synchronous download and indexing jobs.")
live_app = typer.Typer(help="Typed live-stream recording workflows.")
curate_app = typer.Typer(help="Tags, notes, ratings, collections, and bookmarks.")
backup_app = typer.Typer(help="Lossless catalog backup and restore commands.")
transcripts_app = typer.Typer(help="Transcript export and local generation commands.")
preview_app = typer.Typer(help="Local contact-sheet and GIF preview commands.")
stats_app = typer.Typer(help="Optional yt-dlp-backed video statistics history.")
sync_app = typer.Typer(help="Saved channel and playlist synchronization.")
comments_app = typer.Typer(help="Opt-in bounded comment indexing and search.")
app.add_typer(index_app, name="index")
app.add_typer(clips_app, name="clips")
app.add_typer(library_app, name="library")
app.add_typer(config_app, name="config")
app.add_typer(completions_app, name="completions")
app.add_typer(queue_app, name="queue")
app.add_typer(live_app, name="live")
app.add_typer(curate_app, name="curate")
app.add_typer(backup_app, name="backup")
app.add_typer(transcripts_app, name="transcripts")
app.add_typer(preview_app, name="preview")
app.add_typer(stats_app, name="stats")
app.add_typer(sync_app, name="sync")
app.add_typer(comments_app, name="comments")

logger = logging.getLogger("yt_agent")
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


@dataclass(frozen=True)
class DownloadOperationItem:
    status: str
    info: VideoInfo
    requested_input: str
    reason: str | None = None
    output_path: Path | None = None
    info_json_path: Path | None = None
    indexed: bool = False
    index_summary: Any | None = None
    index_warning: str | None = None
    error_message: str | None = None
    stderr: str | None = None


_choose_results_impl = cast(Callable[..., Any], None)
_download_targets_impl = cast(Callable[..., Any], None)
_presence_flag_impl = cast(Callable[..., Any], None)
_read_targets_from_file_impl = cast(Callable[..., Any], None)
_require_noninteractive_json_selection_impl = cast(Callable[..., Any], None)
_resolve_download_inputs_impl = cast(Callable[..., Any], None)
_select_by_indexes_impl = cast(Callable[..., Any], None)
_validate_clip_mode_impl = cast(Callable[..., Any], None)
_validate_subtitle_flags_impl = cast(Callable[..., Any], None)
_download_command_impl = cast(Callable[..., Any], None)
_grab_command_impl = cast(Callable[..., Any], None)

_build_info_payload = cast(Callable[..., Any], None)
_build_formats_payload = cast(Callable[..., Any], None)
_catalog_video_row = cast(Callable[..., Any], None)
_cleanup_payload = cast(Callable[..., Any], None)
_clip_grab_payload = cast(Callable[..., Any], None)
_clip_hit_row = cast(Callable[..., Any], None)
_doctor_payload = cast(Callable[..., Any], None)
_download_operation_item_payload = cast(Callable[..., Any], None)
_download_operation_payload = cast(Callable[..., Any], None)
_history_row = cast(Callable[..., Any], None)
_index_payload = cast(Callable[..., Any], None)
_index_summary_payload = cast(Callable[..., Any], None)
_json_error_payload = cast(Callable[..., Any], None)
_library_detail_payload = cast(Callable[..., Any], None)
_library_remove_payload = cast(Callable[..., Any], None)
_mutation_payload = cast(Callable[..., Any], None)
_normalize_optional_output_mode = cast(Callable[..., Any], None)
_normalize_output_mode = cast(Callable[..., Any], None)
_pick_payload = cast(Callable[..., Any], None)
_platform_status = cast(Callable[..., Any], None)
_print_json = cast(Callable[..., Any], None)
_print_json_error = cast(Callable[..., Any], None)
_print_plain_mapping = cast(Callable[..., Any], None)
_print_plain_rows = cast(Callable[..., Any], None)
_render_cleanup_payload = cast(Callable[..., Any], None)
_render_clip_grab_payload = cast(Callable[..., Any], None)
_render_clip_hits = cast(Callable[..., Any], None)
_render_doctor = cast(Callable[..., Any], None)
_render_download_payload = cast(Callable[..., Any], None)
_render_history_rows = cast(Callable[..., Any], None)
_render_index_payload = cast(Callable[..., Any], None)
_render_index_summary = cast(Callable[..., Any], None)
_render_info_payload = cast(Callable[..., Any], None)
_render_formats_payload = cast(Callable[..., Any], None)
_render_library_detail = cast(Callable[..., Any], None)
_render_library_remove_payload = cast(Callable[..., Any], None)
_render_library_rows = cast(Callable[..., Any], None)
_render_pick_payload = cast(Callable[..., Any], None)
_render_playlist_summary = cast(Callable[..., Any], None)
_render_results = cast(Callable[..., Any], None)
_tool_install_hint = cast(Callable[..., Any], None)
_video_info_payload = cast(Callable[..., Any], None)
_video_row = cast(Callable[..., Any], None)

IndexSummary = cast(Callable[..., Any], None)
index_manifest_record = cast(Callable[..., Any], None)
index_refresh = cast(Callable[..., Any], None)
index_target = cast(Callable[..., Any], None)

extract_clip = cast(Callable[..., Any], None)
extract_clip_for_range = cast(Callable[..., Any], None)
plan_clip = cast(Callable[..., Any], None)
plan_clip_for_range = cast(Callable[..., Any], None)

CatalogStore = cast(Callable[..., Any], None)
launch_tui = cast(Callable[..., Any], None)
verify_library = cast(Callable[..., Any], None)
SourceStore = cast(Callable[..., Any], None)
run_sync = cast(Callable[..., Any], None)
source_store_path = cast(Callable[..., Any], None)
index_comments = cast(Callable[..., Any], None)
search_comments = cast(Callable[..., Any], None)
repair_library = cast(Callable[..., Any], None)

__all__ = [
    "DownloadOperationItem",
    "DRY_RUN_HELP",
    "MUTATION_SCHEMA_VERSION",
    "OUTPUT_MODES",
    "READ_OUTPUT_HELP",
    "_build_info_payload",
    "_build_formats_payload",
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
    "_pick_payload",
    "_platform_status",
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
    "_render_formats_payload",
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


def _load_symbol(module_name: str, attr_name: str) -> Any:
    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


def _lazy_callable(module_name: str, attr_name: str) -> Callable[..., Any]:
    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return _load_symbol(module_name, attr_name)(*args, **kwargs)

    _wrapper.__name__ = attr_name
    _wrapper.__qualname__ = attr_name
    _wrapper.__doc__ = f"Lazy wrapper for {module_name}.{attr_name}."
    return _wrapper


for _module_name, _bindings in (
    (
        "yt_agent.cli_download",
        (
            ("_choose_results_impl", "_choose_results"),
            ("_download_targets_impl", "_download_targets"),
            ("_presence_flag_impl", "_presence_flag"),
            ("_read_targets_from_file_impl", "_read_targets_from_file"),
            (
                "_require_noninteractive_json_selection_impl",
                "_require_noninteractive_json_selection",
            ),
            ("_resolve_download_inputs_impl", "_resolve_download_inputs"),
            ("_select_by_indexes_impl", "_select_by_indexes"),
            ("_validate_clip_mode_impl", "_validate_clip_mode"),
            ("_validate_subtitle_flags_impl", "_validate_subtitle_flags"),
            ("_download_command_impl", "download_command"),
            ("_grab_command_impl", "grab_command"),
        ),
    ),
    (
        "yt_agent.cli_output",
        (
            ("_build_info_payload", "_build_info_payload"),
            ("_build_formats_payload", "_build_formats_payload"),
            ("_catalog_video_row", "_catalog_video_row"),
            ("_cleanup_payload", "_cleanup_payload"),
            ("_clip_grab_payload", "_clip_grab_payload"),
            ("_clip_hit_row", "_clip_hit_row"),
            ("_doctor_payload", "_doctor_payload"),
            ("_download_operation_item_payload", "_download_operation_item_payload"),
            ("_download_operation_payload", "_download_operation_payload"),
            ("_history_row", "_history_row"),
            ("_index_payload", "_index_payload"),
            ("_index_summary_payload", "_index_summary_payload"),
            ("_json_error_payload", "_json_error_payload"),
            ("_library_detail_payload", "_library_detail_payload"),
            ("_library_remove_payload", "_library_remove_payload"),
            ("_mutation_payload", "_mutation_payload"),
            ("_normalize_optional_output_mode", "_normalize_optional_output_mode"),
            ("_normalize_output_mode", "_normalize_output_mode"),
            ("_pick_payload", "_pick_payload"),
            ("_platform_status", "_platform_status"),
            ("_print_json", "_print_json"),
            ("_print_json_error", "_print_json_error"),
            ("_print_plain_mapping", "_print_plain_mapping"),
            ("_print_plain_rows", "_print_plain_rows"),
            ("_render_cleanup_payload", "_render_cleanup_payload"),
            ("_render_clip_grab_payload", "_render_clip_grab_payload"),
            ("_render_clip_hits", "_render_clip_hits"),
            ("_render_doctor", "_render_doctor"),
            ("_render_download_payload", "_render_download_payload"),
            ("_render_history_rows", "_render_history_rows"),
            ("_render_index_payload", "_render_index_payload"),
            ("_render_index_summary", "_render_index_summary"),
            ("_render_info_payload", "_render_info_payload"),
            ("_render_formats_payload", "_render_formats_payload"),
            ("_render_library_detail", "_render_library_detail"),
            ("_render_library_remove_payload", "_render_library_remove_payload"),
            ("_render_library_rows", "_render_library_rows"),
            ("_render_pick_payload", "_render_pick_payload"),
            ("_render_playlist_summary", "_render_playlist_summary"),
            ("_render_results", "_render_results"),
            ("_tool_install_hint", "_tool_install_hint"),
            ("_video_info_payload", "_video_info_payload"),
            ("_video_row", "_video_row"),
        ),
    ),
    (
        "yt_agent.indexer",
        (
            ("IndexSummary", "IndexSummary"),
            ("index_manifest_record", "index_manifest_record"),
            ("index_refresh", "index_refresh"),
            ("index_target", "index_target"),
        ),
    ),
    (
        "yt_agent.clips",
        (
            ("extract_clip", "extract_clip"),
            ("extract_clip_for_range", "extract_clip_for_range"),
            ("plan_clip", "plan_clip"),
            ("plan_clip_for_range", "plan_clip_for_range"),
        ),
    ),
    ("yt_agent.catalog", (("CatalogStore", "CatalogStore"),)),
    ("yt_agent.tui", (("launch_tui", "launch_tui"),)),
    ("yt_agent.verify", (("verify_library", "verify_library"),)),
    (
        "yt_agent.sync",
        (
            ("SourceStore", "SourceStore"),
            ("run_sync", "run_sync"),
            ("source_store_path", "source_store_path"),
        ),
    ),
    (
        "yt_agent.comments",
        (("index_comments", "index_comments"), ("search_comments", "search_comments")),
    ),
    ("yt_agent.repair", (("repair_library", "repair_library"),)),
):
    for _local_name, _remote_name in _bindings:
        globals()[_local_name] = _lazy_callable(_module_name, _remote_name)

del _bindings
del _local_name
del _module_name
del _remote_name


def _operation_lock_path(settings: Settings) -> Path:
    return settings.catalog_file.parent / "operation.lock"


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"yt-agent {__version__}", markup=False)
        raise typer.Exit(code=int(ExitCode.OK))


def _configure_logging(*, verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
        force=True,
    )


@app.callback()
def _app_callback(
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable debug logging on stderr.",
    ),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the yt-agent version and exit.",
    ),
) -> None:
    _configure_logging(verbose=verbose)
    _ = version


def _load_settings(config_path: Path | None = None) -> Settings:
    return load_settings(config_path)


def _prepare_storage(settings: Settings) -> None:
    settings.ensure_storage_paths()
    ensure_archive_file(settings.archive_file)
    ensure_manifest_file(settings.manifest_file)


def _catalog(settings: Settings, *, readonly: bool = False) -> Any:
    store = CatalogStore(settings.catalog_file, readonly=readonly)
    if not readonly:
        store.ensure_schema()
    return store


def _csv_safe_cell(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    if stripped.startswith(CSV_FORMULA_PREFIXES):
        return f"'{value}"
    return value


def _csv_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _csv_safe_cell(value) for key, value in row.items()}


def _atomic_write_public_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError(f"Refusing to operate on symlink: {path}")
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


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
    callback_name = getattr(callback, "__name__", callback.__class__.__name__)
    start_time = time.perf_counter()
    logger.debug("Starting command callback=%s output_mode=%s", callback_name, output_mode)
    try:
        callback()
    except YtAgentError as exc:
        _raise_cli_error(exc, output_mode=output_mode)
    except OSError as exc:
        _raise_cli_error(StorageError(f"Storage operation failed: {exc}"), output_mode=output_mode)
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
    except Exception as exc:
        if exc.__class__.__module__ != "sqlite3":
            raise
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
    finally:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug("Finished command callback=%s elapsed_ms=%.2f", callback_name, elapsed_ms)


class CompletionShell(StrEnum):
    bash = "bash"
    zsh = "zsh"
    fish = "fish"


def _completion_prog_name() -> str:
    return "yt-agent"


def _completion_env_var(prog_name: str) -> str:
    return f"_{prog_name.replace('-', '_').upper()}_COMPLETE"


def _resolve_completion_shell(shell: CompletionShell | None) -> str:
    if shell is not None:
        return shell.value
    shell_path = os.getenv("SHELL", "").strip()
    detected_shell = Path(shell_path).name.casefold()
    if detected_shell in {item.value for item in CompletionShell}:
        return detected_shell
    raise InvalidInputError("Unable to detect shell from $SHELL. Use --shell bash, zsh, or fish.")


def _read_targets_from_file(path: Path) -> list[str]:
    return cast(list[str], _read_targets_from_file_impl(path))


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
    return cast(
        list[DownloadOperationItem],
        _download_targets_impl(
            targets,
            settings,
            mode=mode,
            fetch_subs=fetch_subs,
            auto_subs=auto_subs,
            quiet=quiet,
            show_failure_details=show_failure_details,
            append_manifest_record_fn=append_manifest_record,
            index_manifest_record_fn=index_manifest_record,
        ),
    )


def _select_by_indexes(results: list[VideoInfo], selection: str) -> list[VideoInfo]:
    return cast(
        list[VideoInfo],
        _select_by_indexes_impl(results, selection, parse_selection_fn=parse_selection),
    )


def _choose_results(
    results: list[VideoInfo],
    *,
    selection: str | None = None,
    prefer_fzf: bool = False,
    configured_selector: str = "prompt",
) -> list[VideoInfo]:
    return cast(
        list[VideoInfo],
        _choose_results_impl(
            results,
            selection=selection,
            prefer_fzf=prefer_fzf,
            configured_selector=configured_selector,
            select_by_indexes_fn=_select_by_indexes,
            select_results_fn=select_results,
        ),
    )


def _validate_subtitle_flags(fetch_subs: bool, auto_subs: bool) -> None:
    _validate_subtitle_flags_impl(fetch_subs, auto_subs)


def _validate_clip_mode(mode: str) -> str:
    return cast(str, _validate_clip_mode_impl(mode))


def _require_noninteractive_json_selection(
    *,
    output_mode: str,
    selection: str | None,
    action: str,
) -> None:
    _require_noninteractive_json_selection_impl(
        output_mode=output_mode,
        selection=selection,
        action=action,
    )


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
    return cast(
        tuple[list[DownloadTarget], list[str]],
        _resolve_download_inputs_impl(
            inputs,
            settings,
            source_query=source_query,
            select_playlist=select_playlist,
            use_fzf=use_fzf,
            selection=selection,
            render_selection=render_selection,
            selection_output_mode=selection_output_mode,
            quiet=quiet,
            choose_results_fn=_choose_results,
        ),
    )


def _presence_flag(enabled: bool, disabled: bool, *, label: str) -> bool | None:
    return cast(bool | None, _presence_flag_impl(enabled, disabled, label=label))


def _history_rows(
    settings: Settings,
    *,
    limit: int,
    channel: str | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in reversed(iter_manifest_records(settings.manifest_file)):
        if channel is not None and record.channel != channel:
            continue
        rows.append(_history_row(record))
        if len(rows) >= limit:
            break
    return rows


def _catalog_video_ids(settings: Settings) -> set[str]:
    store = _catalog(settings, readonly=True)
    try:
        with store.connect() as conn:
            rows = conn.execute("SELECT video_id FROM videos").fetchall()
    except FileNotFoundError:
        return set()
    return {sanitize_file_id(str(row["video_id"])) for row in rows}


def _cleanup_candidates(settings: Settings) -> dict[str, list[Path]]:
    catalog_ids = _catalog_video_ids(settings)
    cache_dirs: list[Path] = []
    empty_dirs: list[Path] = []
    part_files: list[Path] = []

    subtitle_cache_root = settings.catalog_file.parent / "subtitle-cache"
    if subtitle_cache_root.exists():
        cache_dirs = [
            path
            for path in sorted(subtitle_cache_root.iterdir())
            if path.is_dir() and path.name not in catalog_ids
        ]

    if settings.download_root.exists():
        empty_dirs = [
            path
            for path in sorted(settings.download_root.iterdir())
            if path.is_dir() and path != settings.clips_root and not any(path.iterdir())
        ]
        part_files = [
            path for path in sorted(settings.download_root.rglob("*.part")) if path.is_file()
        ]

    return {
        "removed_cache_dirs": cache_dirs,
        "removed_empty_dirs": empty_dirs,
        "removed_part_files": part_files,
    }


def _remove_cleanup_candidates(candidates: dict[str, list[Path]]) -> None:
    for path in candidates["removed_cache_dirs"]:
        shutil.rmtree(path, ignore_errors=True)
    for path in candidates["removed_empty_dirs"]:
        try:
            path.rmdir()
        except FileNotFoundError:
            continue
    for path in candidates["removed_part_files"]:
        try:
            path.unlink()
        except FileNotFoundError:
            continue


_EXPORT_FORMAT_CHOICES = {"json", "csv"}


@app.command(help="Export the local catalog to a JSON or CSV file.")
def export(
    dest: Path | None = typer.Option(
        None, "--dest", help="Output file path (.json or .csv). Defaults to stdout."
    ),
    format: str | None = typer.Option(  # noqa: A002
        None,
        "--format",
        help="Export format: json or csv. Inferred from file extension; defaults to json.",
    ),
    limit: int = typer.Option(10000, "--limit", min=1, help="Maximum catalog entries to export."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Export the local catalog to a JSON or CSV file, or stdout when no --dest is given."""

    def _command() -> None:
        settings = _load_settings(config)
        resolved_format = format
        if resolved_format is None:
            if dest is not None:
                suffix = dest.suffix.lower().lstrip(".")
                resolved_format = suffix if suffix in _EXPORT_FORMAT_CHOICES else "json"
            else:
                resolved_format = "json"
        resolved_format = resolved_format.lower().strip()
        if resolved_format not in _EXPORT_FORMAT_CHOICES:
            raise InvalidInputError(
                f"Export format must be one of: {', '.join(sorted(_EXPORT_FORMAT_CHOICES))}"
            )
        store = _catalog(settings, readonly=True)
        videos = store.list_videos(limit=limit)
        rows = [_catalog_video_row(video) for video in videos]
        if resolved_format == "json":
            content = json.dumps(rows, indent=2)
        else:
            if rows:
                fieldnames = list(rows[0].keys())
            else:
                fieldnames = [
                    "video_id",
                    "title",
                    "channel",
                    "upload_date",
                    "duration",
                    "duration_seconds",
                    "webpage_url",
                    "output_path",
                    "has_local_media",
                    "transcript_segments",
                    "chapters",
                    "playlists",
                ]
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(_csv_safe_row(row) for row in rows)
            content = buf.getvalue()
        if dest is None:
            sys.stdout.write(content)
            if not content.endswith("\n"):
                sys.stdout.write("\n")
            sys.stdout.flush()
            return
        try:
            _atomic_write_public_text(dest, content, encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"Could not write export file: {exc}") from exc
        payload = _mutation_payload(
            command="export",
            status="ok",
            summary={"exported": len(rows), "format": resolved_format},
            path=str(dest),
        )
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json(payload)
            return
        console.print(
            f"Exported {len(rows)} catalog entries to {sanitize_terminal_text(str(dest))}.",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@app.command(name="import", help="Import catalog entries from a JSON file.")
def import_catalog(
    src: Path = typer.Argument(..., help="JSON file previously created by 'yt-agent export'."),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Import catalog entries from a JSON file created by 'yt-agent export'."""

    def _command() -> None:
        from yt_agent.catalog import VideoUpsert  # lazy to match module-level pattern

        settings = _load_settings(config)
        if not src.exists():
            raise InvalidInputError(f"Import file not found: {src}")
        try:
            raw = src.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidInputError(f"Could not read import file: {exc}") from exc
        if not isinstance(data, list):
            raise InvalidInputError("Import file must contain a top-level JSON array.")
        indexed_at = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        upserted = 0
        skipped = 0
        warnings: list[str] = []

        def _parse_entry(entry: object) -> VideoUpsert | None:
            nonlocal skipped
            if not isinstance(entry, dict):
                skipped += 1
                warnings.append("Skipped entry that is not a JSON object.")
                return None
            video_id = str(entry.get("video_id") or "").strip()
            if not video_id:
                skipped += 1
                warnings.append("Skipped entry with missing video_id.")
                return None
            try:
                return VideoUpsert(
                    video_id=video_id,
                    title=str(entry.get("title") or ""),
                    channel=str(entry.get("channel") or ""),
                    upload_date=str(entry["upload_date"])
                    if entry.get("upload_date") and entry["upload_date"] != "undated"
                    else None,
                    duration_seconds=int(entry["duration_seconds"])
                    if entry.get("duration_seconds") is not None
                    else None,
                    extractor_key=str(entry.get("extractor_key") or "Youtube"),
                    webpage_url=str(entry.get("webpage_url") or ""),
                    requested_input=str(entry["requested_input"])
                    if entry.get("requested_input")
                    else None,
                    source_query=str(entry["source_query"]) if entry.get("source_query") else None,
                    output_path=Path(str(entry["output_path"]))
                    if entry.get("output_path")
                    else None,
                    info_json_path=Path(str(entry["info_json_path"]))
                    if entry.get("info_json_path")
                    else None,
                    downloaded_at=str(entry["downloaded_at"])
                    if entry.get("downloaded_at")
                    else None,
                    indexed_at=indexed_at,
                )
            except (KeyError, TypeError, ValueError) as exc:
                skipped += 1
                warnings.append(f"Skipped {sanitize_terminal_text(video_id)}: {exc}")
                return None

        records = [record for entry in data if (record := _parse_entry(entry)) is not None]
        upserted = len(records)
        if not dry_run:
            with operation_lock(_operation_lock_path(settings)):
                store = _catalog(settings)
                for record in records:
                    store.upsert_video(record)
        payload = _mutation_payload(
            command="import",
            status="ok",
            summary={"imported": upserted, "skipped": skipped, "dry_run": dry_run},
            warnings=warnings,
        )
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json(payload)
            return
        prefix = "[dry-run] " if dry_run else ""
        console.print(
            f"{prefix}Imported {upserted} catalog entries ({skipped} skipped).",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@backup_app.command("create", help="Back up core indexed content, comments, and user curation.")
def backup_create_command(
    dest: Path = typer.Argument(..., help="Destination JSON backup file."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing backup file."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Back up all catalog metadata and search source records, but not media bytes."""

    def _command() -> None:
        from yt_agent.backup import BackupSummary, create_catalog_backup

        settings = _load_settings(config)
        if dest.exists() and not force:
            raise InvalidInputError(
                f"Backup file already exists: {dest}. Use --force to replace it."
            )
        bundle = create_catalog_backup(_catalog(settings, readonly=True))
        catalog = bundle["catalog"]
        summary = BackupSummary(
            videos=len(catalog["videos"]),
            chapters=len(catalog["chapters"]),
            subtitle_tracks=len(catalog["subtitle_tracks"]),
            transcript_segments=len(catalog["transcript_segments"]),
            playlists=len(catalog["playlists"]),
            playlist_entries=len(catalog["playlist_entries"]),
        )
        try:
            atomic_write_artifact_text(
                dest,
                json.dumps(bundle, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        except (OSError, ValueError) as exc:
            raise StorageError(f"Could not write backup file: {exc}") from exc
        payload = _mutation_payload(
            command="backup create",
            status="ok",
            summary=summary.as_dict(),
            path=str(dest),
        )
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
            return
        console.print(
            f"Backed up {summary.videos} videos to {sanitize_terminal_text(str(dest))}.",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@backup_app.command("restore", help="Validate and restore a versioned catalog backup.")
def backup_restore_command(
    src: Path = typer.Argument(..., help="Versioned JSON backup file to restore."),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Replace catalog metadata from a fully validated backup bundle."""

    def _command() -> None:
        from yt_agent.backup import restore_catalog_backup, validate_catalog_backup

        settings = _load_settings(config)
        if not src.is_file():
            raise InvalidInputError(f"Backup file not found: {src}")
        try:
            bundle = json.loads(src.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidInputError(f"Could not read backup file: {exc}") from exc

        # Validation is deliberately completed before storage preparation or a
        # write connection, so malformed backups cannot partially mutate state.
        summary = validate_catalog_backup(bundle)
        if not dry_run:
            with operation_lock(_operation_lock_path(settings)):
                _prepare_storage(settings)
                store = _catalog(settings)
                summary = restore_catalog_backup(store, bundle)
        payload = _mutation_payload(
            command="backup restore",
            status="ok",
            summary={**summary.as_dict(), "dry_run": dry_run},
            path=str(src),
        )
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
            return
        prefix = "[dry-run] " if dry_run else ""
        console.print(
            f"{prefix}Restored {summary.videos} videos from {sanitize_terminal_text(str(src))}.",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@app.command(help="Show recent downloads from the manifest.")
def history(
    limit: int = typer.Option(20, "--limit", min=1, help="Maximum downloads to show."),
    channel: str | None = typer.Option(None, "--channel", help="Only show one channel."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show recent downloads from the manifest."""

    def _command() -> None:
        settings = _load_settings(config)
        rows = _history_rows(settings, limit=limit, channel=channel)
        if not rows:
            if _normalize_output_mode(output) == "json":
                _print_json([])
            else:
                console.print("No download history found.")
            return
        _render_history_rows(rows, output_mode=output)

    _run_guarded(_command, output_mode=output)


@app.command(help="Remove orphaned caches, empty directories, and partial downloads.")
def cleanup(
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Remove orphaned subtitle cache directories, empty channel directories, and part files."""

    def _command() -> None:
        settings = _load_settings(config)
        if dry_run:
            candidates = _cleanup_candidates(settings)
        else:
            with operation_lock(_operation_lock_path(settings)):
                candidates = _cleanup_candidates(settings)
                _remove_cleanup_candidates(candidates)
        payload = _cleanup_payload(candidates=candidates, dry_run=dry_run)
        _render_cleanup_payload(payload, output_mode=output, quiet=quiet)

    _run_guarded(_command, output_mode=output)


@app.command(help="Check required and optional runtime dependencies.")
def doctor(
    deep: bool = typer.Option(
        False,
        "--deep",
        help=(
            "Check versions and optional extraction, verification, and transcription capabilities."
        ),
    ),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Check required and optional runtime dependencies."""

    def _command() -> None:
        settings = _load_settings(config)
        missing_required = _render_doctor(settings, output_mode=output, deep=deep)
        if missing_required:
            raise DependencyError("yt-dlp is required for this CLI.")

    _run_guarded(_command, output_mode=output)


@app.command(help="Audit local manifest, catalog, archive, and media consistency.")
def verify(
    deep: bool = typer.Option(
        False,
        "--deep",
        help="Use ffprobe to check each existing catalog media file.",
    ),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Report local-state inconsistencies without modifying or repairing files."""

    def _command() -> None:
        mode = _normalize_output_mode(output)
        report = verify_library(_load_settings(config), deep=deep)
        payload = report.as_dict()
        if mode == "json":
            _print_json(payload)
            return
        if mode == "plain":
            if not report.findings:
                console.print("ok")
                return
            for finding in report.findings:
                parts = [finding.severity, finding.code, finding.message]
                if finding.video_id:
                    parts.append(f"video_id={finding.video_id}")
                if finding.path:
                    parts.append(f"path={finding.path}")
                console.print(sanitize_terminal_text("\t".join(parts)), markup=False)
            return

        summary = payload["summary"]
        console.print(
            "Verify: "
            f"{summary['errors']} error(s), {summary['warnings']} warning(s); "
            f"{summary['catalog_videos']} catalog video(s), "
            f"{summary['manifest_records']} manifest record(s).",
            markup=False,
        )
        if not report.findings:
            console.print("No consistency problems found.", style="green", markup=False)
            return
        table = Table(title="Verification Findings")
        table.add_column("Severity")
        table.add_column("Code")
        table.add_column("Video")
        table.add_column("Message")
        table.add_column("Path")
        for finding in report.findings:
            table.add_row(
                sanitize_terminal_text(finding.severity),
                sanitize_terminal_text(finding.code),
                sanitize_terminal_text(finding.video_id or ""),
                sanitize_terminal_text(finding.message),
                sanitize_terminal_text(finding.path or ""),
            )
        console.print(table)

    _run_guarded(_command, output_mode=output)


@app.command(help="Preview or apply safe repairs to derived local state.")
def repair(
    apply: bool = typer.Option(
        False, "--apply", help="Apply the plan. Without this flag repair is a dry run."
    ),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Repair FTS, manifest indexing, and stale caches without deleting media."""

    def _command() -> None:
        mode = _normalize_output_mode(output)
        settings = _load_settings(config)
        if apply:
            with operation_lock(_operation_lock_path(settings)):
                report = repair_library(settings, apply=True)
        else:
            report = repair_library(settings)
        payload = report.as_dict()
        if mode == "json":
            _print_json(payload)
            return
        prefix = "Applied" if apply else "Planned"
        console.print(f"{prefix} {len(report.actions)} safe repair action(s).", markup=False)
        for action in report.actions:
            message = f"{action.status}: {action.action}"
            if action.path:
                message += f" — {action.path}"
            console.print(sanitize_terminal_text(message), markup=False)
        console.print("Media files are never deleted by repair.", markup=False)

    _run_guarded(_command, output_mode=output)


def _saved_source_payload(source: Any) -> dict[str, Any]:
    return {
        "name": source.name,
        "kind": source.kind,
        "url": source.url,
        "created_at": source.created_at,
        "last_synced_at": source.last_synced_at,
        "seen_count": len(source.seen_video_ids),
    }


@sync_app.command("add", help="Save a channel or playlist source.")
def sync_add(
    name: str,
    url: str,
    kind: str = typer.Option(..., "--kind", help="Source kind: channel or playlist."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Persist a named source definition in the private local source store."""

    def _command() -> None:
        mode = _normalize_output_mode(output)
        settings = _load_settings(config)
        with operation_lock(_operation_lock_path(settings)):
            source = SourceStore(source_store_path(settings)).add(name, kind, url)
        payload = {
            "schema_version": 1,
            "command": "sync add",
            "status": "ok",
            "source": _saved_source_payload(source),
        }
        if mode == "json":
            _print_json(payload)
        else:
            console.print(
                f"Saved {sanitize_terminal_text(source.kind)} source "
                f"'{sanitize_terminal_text(source.name)}'.",
                style="green",
                markup=False,
            )

    _run_guarded(_command, output_mode=output)


@sync_app.command("list", help="List saved channel and playlist sources.")
def sync_list(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """List saved source definitions and incremental sync state."""

    def _command() -> None:
        mode = _normalize_output_mode(output)
        settings = _load_settings(config)
        rows = [
            _saved_source_payload(item) for item in SourceStore(source_store_path(settings)).list()
        ]
        if mode == "json":
            _print_json(rows)
            return
        if mode == "plain":
            for row in rows:
                console.print(
                    sanitize_terminal_text(f"{row['name']}\t{row['kind']}\t{row['url']}"),
                    markup=False,
                )
            return
        if not rows:
            console.print("No saved sources found.")
            return
        table = Table(title="Saved Sources")
        for label in ("Name", "Kind", "URL", "Seen", "Last synced"):
            table.add_column(label)
        for row in rows:
            table.add_row(
                sanitize_terminal_text(row["name"]),
                sanitize_terminal_text(row["kind"]),
                sanitize_terminal_text(row["url"]),
                str(row["seen_count"]),
                sanitize_terminal_text(row["last_synced_at"] or ""),
            )
        console.print(table)

    _run_guarded(_command, output_mode=output)


@sync_app.command("remove", help="Remove a saved source definition.")
def sync_remove(
    name: str,
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Remove one saved source without touching downloaded or indexed videos."""

    def _command() -> None:
        mode = _normalize_output_mode(output)
        settings = _load_settings(config)
        with operation_lock(_operation_lock_path(settings)):
            removed = SourceStore(source_store_path(settings)).remove(name)
        if not removed:
            raise InvalidInputError(f"Saved source not found: {name}")
        payload = {
            "schema_version": 1,
            "command": "sync remove",
            "status": "ok",
            "removed": name,
        }
        if mode == "json":
            _print_json(payload)
        else:
            console.print(f"Removed saved source '{sanitize_terminal_text(name)}'.", markup=False)

    _run_guarded(_command, output_mode=output)


@sync_app.command("run", help="Incrementally index or download saved sources.")
def sync_run(
    names: list[str] | None = typer.Argument(None, help="Saved source names; default is all."),
    since: str | None = typer.Option(None, "--since", help="Only videos on/after YYYY-MM-DD."),
    latest: int | None = typer.Option(None, "--latest", min=1, help="Newest items per source."),
    index: bool = typer.Option(True, "--index/--no-index", help="Index selected new videos."),
    download: bool = typer.Option(False, "--download", help="Download selected new videos."),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Fetch saved sources and process video IDs not completed by earlier runs."""

    def _command() -> None:
        mode = _normalize_output_mode(output)
        settings = _load_settings(config)
        kwargs = {
            "names": names,
            "since": since,
            "latest": latest,
            "index": index,
            "download": download,
            "dry_run": dry_run,
        }
        if dry_run:
            report = run_sync(settings, **kwargs)
        else:
            _prepare_storage(settings)
            with operation_lock(_operation_lock_path(settings)):
                report = run_sync(settings, **kwargs)
        payload = report.as_dict()
        if mode == "json":
            _print_json(payload)
            return
        if quiet and not any(item.status == "failed" for item in report.items):
            return
        summary = payload["summary"]
        console.print(
            f"Sync: {summary['sources']} source(s), {summary['items']} item(s).",
            markup=False,
        )
        for item in report.items:
            message = f"{item.source}: {item.status}"
            if item.title:
                message += f" — {item.title} [{item.video_id}]"
            if item.message:
                message += f" — {item.message}"
            console.print(sanitize_terminal_text(message), markup=False)

    _run_guarded(_command, output_mode=output)


@comments_app.command("index", help="Fetch and index a bounded comment set for one video.")
def comments_index(
    target: str,
    limit: int = typer.Option(100, "--limit", min=1, max=1000, help="Maximum comments."),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Opt in to bounded, sanitized comment fetching for a single video."""

    def _command() -> None:
        mode = _normalize_output_mode(output)
        settings = _load_settings(config)
        if dry_run:
            report = index_comments(settings, target, limit=limit, dry_run=True)
        else:
            with operation_lock(_operation_lock_path(settings)):
                report = index_comments(settings, target, limit=limit)
        payload = report.as_dict()
        if mode == "json":
            _print_json(payload)
        else:
            console.print(
                f"Comments: fetched {report.fetched}, indexed {report.indexed} "
                f"for {sanitize_terminal_text(report.video_id)}"
                + (" (dry-run)" if dry_run else ""),
                markup=False,
            )

    _run_guarded(_command, output_mode=output)


@comments_app.command("search", help="Search locally indexed comments.")
def comments_search(
    query: str,
    limit: int = typer.Option(20, "--limit", min=1, max=1000, help="Maximum results."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search the separate local comment FTS index."""

    def _command() -> None:
        mode = _normalize_output_mode(output)
        rows = search_comments(_load_settings(config), query, limit=limit)
        if mode == "json":
            _print_json(rows)
            return
        if not rows:
            console.print("No comment matches found.")
            return
        if mode == "plain":
            for row in rows:
                console.print(
                    sanitize_terminal_text(f"{row['video_id']}\t{row['author']}\t{row['text']}"),
                    markup=False,
                )
            return
        table = Table(title="Comment Search")
        for label in ("Video", "Author", "Comment", "Likes"):
            table.add_column(label)
        for row in rows:
            table.add_row(
                sanitize_terminal_text(row["title"]),
                sanitize_terminal_text(row["author"]),
                sanitize_terminal_text(row["text"]),
                str(row["like_count"]),
            )
        console.print(table)

    _run_guarded(_command, output_mode=output)


@app.command(help="Search YouTube and print normalized results.")
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


@app.command(help="Search YouTube and interactively select result URLs.")
def pick(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for interactive selection."),
    select: str | None = typer.Option(
        None, "--select", help="Choose result indexes without prompting, e.g. 1,3."
    ),
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


@app.command(help="Print normalized metadata for a target without downloading.")
def info(
    target: str,
    entries: bool = typer.Option(
        False, "--entries", help="For playlists, show individual entries."
    ),
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


@app.command(help="List normalized formats and safe preset recommendations for a video.")
def formats(
    target: str,
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Inspect available formats without downloading or accepting raw yt-dlp arguments."""

    def _command() -> None:
        _ = _load_settings(config)
        payload = yt_dlp.fetch_info(target)
        formats_payload = _build_formats_payload(payload, target=target)
        _render_formats_payload(formats_payload, output_mode=output)

    _run_guarded(_command, output_mode=output)


@live_app.command("record", help="Record a live stream with bounded wait options.")
def live_record_command(
    target: str,
    live_from_start: bool = typer.Option(
        True, "--live-from-start/--from-now", help="Record from the stream start when supported."
    ),
    wait_seconds: int = typer.Option(
        0, "--wait-seconds", min=0, max=86_400, help="Wait for a scheduled stream."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Synchronously record, manifest, and index one live stream."""

    def _command() -> None:
        settings = _load_settings(config)
        normalized_target = yt_dlp.normalize_target(target)
        if dry_run:
            payload = {
                "status": "planned",
                "dry_run": True,
                "target": normalized_target,
                "live_from_start": live_from_start,
                "wait_seconds": wait_seconds,
            }
        else:
            info_payload = yt_dlp.fetch_info(normalized_target)
            if isinstance(info_payload.get("entries"), list):
                raise InvalidInputError("Live record requires a single video target.")
            info = VideoInfo.from_yt_dlp(info_payload, original_url=normalized_target)
            download_target = DownloadTarget(original_input=target, info=info)
            with operation_lock(_operation_lock_path(settings)):
                _prepare_storage(settings)
                execution = yt_dlp.record_live(
                    download_target,
                    settings,
                    live_from_start=live_from_start,
                    wait_seconds=wait_seconds,
                )
                record = ManifestRecord.from_download(
                    download_target,
                    output_path=execution.output_path,
                    info_json_path=execution.info_json_path,
                )
                append_manifest_record(settings.manifest_file, record)
                summary = index_manifest_record(settings, record)
            payload = {
                "status": "completed",
                "dry_run": False,
                "target": normalized_target,
                "video_id": info.video_id,
                "output_path": str(execution.output_path),
                "info_json_path": str(execution.info_json_path or ""),
                "indexed_videos": summary.videos,
            }
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
        else:
            _print_plain_mapping(list(payload.items()))

    _run_guarded(_command, output_mode=output)


def _resolve_playback_reference(
    settings: Settings, reference: str
) -> tuple[str | Path, float | None]:
    def _catalog_media_path(path: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(settings.download_root.resolve())
        except ValueError as exc:
            raise InvalidInputError(
                "Catalog media path is outside the configured download root."
            ) from exc
        return resolved

    path = Path(reference).expanduser()
    if path.exists():
        return path, None
    store = _catalog(settings, readonly=True)
    if reference.startswith(("chapter:", "transcript:")):
        hit = store.get_clip_hit(reference)
        if hit is None:
            raise InvalidInputError(f"Unknown clip result '{reference}'.")
        if hit.output_path is not None and hit.output_path.exists():
            return _catalog_media_path(hit.output_path), hit.start_seconds
        separator = "&" if "?" in hit.webpage_url else "?"
        return f"{hit.webpage_url}{separator}t={int(hit.start_seconds)}", None
    video = store.get_video(reference)
    if video is not None and video.output_path is not None and video.output_path.exists():
        return _catalog_media_path(video.output_path), None
    return reference, None


def _playback_command(
    *, reference: str, dry_run: bool, output: str, config: Path | None, action: str
) -> None:
    def _command() -> None:
        resolved, start_seconds = _resolve_playback_reference(_load_settings(config), reference)
        args = launch_media(resolved, start_seconds=start_seconds, dry_run=dry_run)
        payload = {
            "action": action,
            "dry_run": dry_run,
            "reference": reference,
            "resolved": str(resolved),
            "start_seconds": start_seconds,
            "launcher": Path(args[0]).name,
        }
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
        else:
            _print_plain_mapping(list(payload.items()))

    _run_guarded(_command, output_mode=output)


@app.command("play", help="Play local media, a clip result, or a YouTube timestamp.")
def play_command(
    reference: str,
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    _playback_command(
        reference=reference, dry_run=dry_run, output=output, config=config, action="play"
    )


@app.command("open", help="Open local media, a clip result, or a YouTube timestamp.")
def open_command(
    reference: str,
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    _playback_command(
        reference=reference, dry_run=dry_run, output=output, config=config, action="open"
    )


@app.command(help="Download videos into the organized local library.")
def download(
    targets: list[str] = typer.Argument(
        default=None, help="Video URLs, playlist URLs, or YouTube video IDs."
    ),
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
    sponsorblock: bool = typer.Option(
        False, "--sponsorblock", help="Mark SponsorBlock segments without removing media."
    ),
    sponsorblock_remove: bool = typer.Option(
        False,
        "--sponsorblock-remove",
        help="Explicitly remove all SponsorBlock segments instead of only marking them.",
    ),
    events_jsonl: Path | None = typer.Option(
        None, "--events-jsonl", help="Append stable lifecycle events to a JSONL file."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for playlist entry selection."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Download videos into the organized local library."""

    def _command() -> None:
        payload = _download_command_impl(
            targets=list(targets or []),
            from_file=from_file,
            select_playlist=select_playlist,
            select=select,
            audio=audio,
            fetch_subs=fetch_subs,
            auto_subs=auto_subs,
            sponsorblock=sponsorblock,
            sponsorblock_remove=sponsorblock_remove,
            events_jsonl=events_jsonl,
            dry_run=dry_run,
            quiet=quiet,
            use_fzf=use_fzf,
            output=output,
            config=config,
            load_settings=_load_settings,
            read_targets_from_file=_read_targets_from_file,
            resolve_download_inputs=_resolve_download_inputs,
            prepare_storage=_prepare_storage,
            operation_lock_path=_operation_lock_path,
            lock_factory=operation_lock,
            download_targets_fn=_download_targets,
            build_download_payload=_download_operation_payload,
            render_download_payload=_render_download_payload,
        )
        if not dry_run and int(payload["summary"]["failed"]) > 0:
            raise typer.Exit(code=int(ExitCode.EXTERNAL))

    _run_guarded(_command, output_mode=output)


@app.command(help="Search, select, and download in one flow.")
def grab(
    query: str,
    limit: int | None = typer.Option(None, "--limit", min=1, help="Maximum result count."),
    use_fzf: bool = typer.Option(False, "--fzf", help="Use fzf for interactive selection."),
    select: str | None = typer.Option(
        None, "--select", help="Choose result indexes without prompting, e.g. 1,3."
    ),
    audio: bool = typer.Option(False, "--audio", help="Download audio only instead of video."),
    fetch_subs: bool = typer.Option(False, "--fetch-subs", help="Fetch subtitles during download."),
    auto_subs: bool = typer.Option(
        False,
        "--auto-subs",
        help="Include auto-generated subtitles (requires --fetch-subs).",
    ),
    sponsorblock: bool = typer.Option(
        False, "--sponsorblock", help="Mark SponsorBlock segments without removing media."
    ),
    sponsorblock_remove: bool = typer.Option(
        False,
        "--sponsorblock-remove",
        help="Explicitly remove all SponsorBlock segments instead of only marking them.",
    ),
    events_jsonl: Path | None = typer.Option(
        None, "--events-jsonl", help="Append stable lifecycle events to a JSONL file."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search, select, and download in one flow."""

    def _command() -> None:
        payload = _grab_command_impl(
            query=query,
            limit=limit,
            use_fzf=use_fzf,
            select=select,
            audio=audio,
            fetch_subs=fetch_subs,
            auto_subs=auto_subs,
            sponsorblock=sponsorblock,
            sponsorblock_remove=sponsorblock_remove,
            events_jsonl=events_jsonl,
            dry_run=dry_run,
            quiet=quiet,
            output=output,
            config=config,
            load_settings=_load_settings,
            choose_results=_choose_results,
            prepare_storage=_prepare_storage,
            operation_lock_path=_operation_lock_path,
            lock_factory=operation_lock,
            download_targets_fn=_download_targets,
            build_download_payload=_download_operation_payload,
            render_download_payload=_render_download_payload,
        )
        if payload is None:
            return
        if not dry_run and int(payload["summary"]["failed"]) > 0:
            raise typer.Exit(code=int(ExitCode.EXTERNAL))

    _run_guarded(_command, output_mode=output)


@config_app.command("init", help="Write a starter config file to the active config path.")
def config_init_command(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Write a starter config file to the active config path."""

    def _command() -> None:
        settings = _load_settings(config)
        if settings.config_path.exists() and not force:
            raise InvalidInputError(
                f"Config already exists at {settings.config_path}. Use --force to overwrite it."
            )
        atomic_write_text(settings.config_path, render_default_config(), encoding="utf-8")
        message = f"Wrote config: {settings.config_path}"
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json(
                {
                    "status": "ok",
                    "config_path": str(settings.config_path),
                    "message": message,
                }
            )
            return
        console.print(
            sanitize_terminal_text(message),
            style="green",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@config_app.command("path", help="Show the active config and data paths.")
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


@config_app.command("validate", help="Validate the active config file and report any errors.")
def config_validate_command(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Validate the active config file and report any errors."""

    def _command() -> None:
        settings = _load_settings(config)
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json(
                {
                    "status": "ok",
                    "config_path": str(settings.config_path),
                    "valid": True,
                }
            )
            return
        console.print(
            f"Config is valid: {sanitize_terminal_text(settings.config_path)}",
            style="green",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


def _queue_path(settings: Settings) -> Path:
    return settings.catalog_file.parent / "jobs.sqlite"


def _queue_store(settings: Settings) -> JobQueue:
    store = JobQueue(_queue_path(settings))
    store.ensure_schema()
    return store


def _curation_store(settings: Settings) -> CurationStore:
    catalog = _catalog(settings)
    return CurationStore(catalog)


def _render_curation(payload: dict[str, Any], output: str) -> None:
    if _normalize_output_mode(output) == "json":
        _print_json(payload)
    else:
        _print_plain_mapping(list(payload.items()))


@curate_app.command("set", help="Set a video's note and optional 1-5 rating.")
def curate_set_command(
    video_id: str,
    note: str = typer.Option("", "--note"),
    rating: int | None = typer.Option(None, "--rating", min=1, max=5),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        if not dry_run:
            _curation_store(_load_settings(config)).set_annotation(
                video_id, note=note, rating=rating
            )
        _render_curation(
            {
                "action": "set",
                "video_id": video_id,
                "note": note,
                "rating": rating,
                "dry_run": dry_run,
            },
            output,
        )

    _run_guarded(_command, output_mode=output)


@curate_app.command("clear", help="Remove a video's note and rating.")
def curate_clear_command(
    video_id: str,
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        if not dry_run:
            _curation_store(_load_settings(config)).clear_annotation(video_id)
        _render_curation({"action": "clear", "video_id": video_id, "dry_run": dry_run}, output)

    _run_guarded(_command, output_mode=output)


@curate_app.command("tag", help="Add or remove a tag from a video.")
def curate_tag_command(
    video_id: str,
    tag: str,
    remove: bool = typer.Option(False, "--remove"),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        if not dry_run:
            store = _curation_store(_load_settings(config))
            store.remove_tag(video_id, tag) if remove else store.add_tag(video_id, tag)
        _render_curation(
            {
                "action": "remove-tag" if remove else "add-tag",
                "video_id": video_id,
                "tag": tag,
                "dry_run": dry_run,
            },
            output,
        )

    _run_guarded(_command, output_mode=output)


@curate_app.command("collection-create", help="Create a named collection.")
def curate_collection_create_command(
    name: str,
    description: str = typer.Option("", "--description"),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        collection_id = (
            None
            if dry_run
            else _curation_store(_load_settings(config)).create_collection(name, description)
        )
        _render_curation(
            {
                "action": "collection-create",
                "collection_id": collection_id,
                "name": name,
                "description": description,
                "dry_run": dry_run,
            },
            output,
        )

    _run_guarded(_command, output_mode=output)


@curate_app.command("collection-video", help="Add or remove a video from a collection.")
def curate_collection_video_command(
    collection_id: int,
    video_id: str,
    remove: bool = typer.Option(False, "--remove"),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        if not dry_run:
            _curation_store(_load_settings(config)).set_collection_video(
                collection_id, video_id, add=not remove
            )
        _render_curation(
            {
                "action": "collection-remove" if remove else "collection-add",
                "collection_id": collection_id,
                "video_id": video_id,
                "dry_run": dry_run,
            },
            output,
        )

    _run_guarded(_command, output_mode=output)


@curate_app.command("collection-delete", help="Delete a collection.")
def curate_collection_delete_command(
    collection_id: int,
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        if not dry_run:
            _curation_store(_load_settings(config)).delete_collection(collection_id)
        _render_curation(
            {"action": "collection-delete", "collection_id": collection_id, "dry_run": dry_run},
            output,
        )

    _run_guarded(_command, output_mode=output)


@curate_app.command("bookmark", help="Add a timestamp bookmark.")
def curate_bookmark_command(
    video_id: str,
    timestamp_seconds: float,
    label: str = typer.Option("", "--label"),
    note: str = typer.Option("", "--note"),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        bookmark_id = (
            None
            if dry_run
            else _curation_store(_load_settings(config)).add_bookmark(
                video_id, timestamp_seconds, label=label, note=note
            )
        )
        _render_curation(
            {
                "action": "bookmark",
                "bookmark_id": bookmark_id,
                "video_id": video_id,
                "timestamp_seconds": timestamp_seconds,
                "label": label,
                "note": note,
                "dry_run": dry_run,
            },
            output,
        )

    _run_guarded(_command, output_mode=output)


@curate_app.command("bookmark-remove", help="Remove a timestamp bookmark.")
def curate_bookmark_remove_command(
    bookmark_id: int,
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        if not dry_run:
            _curation_store(_load_settings(config)).remove_bookmark(bookmark_id)
        _render_curation(
            {"action": "bookmark-remove", "bookmark_id": bookmark_id, "dry_run": dry_run}, output
        )

    _run_guarded(_command, output_mode=output)


@curate_app.command("show", help="List curation records, optionally for one video.")
def curate_show_command(
    video_id: str | None = typer.Argument(None),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        payload = _curation_store(_load_settings(config)).list_all(video_id=video_id)
        _render_curation(payload, output)

    _run_guarded(_command, output_mode=output)


@curate_app.command("search", help="Search titles and user-owned curation text.")
def curate_search_command(
    query: str,
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config"),
) -> None:
    def _command() -> None:
        _render_curation({"results": _curation_store(_load_settings(config)).search(query)}, output)

    _run_guarded(_command, output_mode=output)


def _render_queue_jobs(
    jobs: list[QueueJob], *, output_mode: str, action: str, dry_run: bool = False
) -> None:
    rows = [job_payload(job) for job in jobs]
    mode = _normalize_output_mode(output_mode)
    if mode == "json":
        _print_json({"action": action, "dry_run": dry_run, "jobs": rows})
        return
    if mode == "plain":
        _print_plain_rows(
            [
                ("job_id", "job_id"),
                ("operation", "operation"),
                ("status", "status"),
                ("attempts", "attempts"),
                ("max_attempts", "max_attempts"),
                ("target", "target"),
                ("last_error", "last_error"),
            ],
            rows,
        )
        return
    table = Table(title=f"Queue: {action}{' (dry run)' if dry_run else ''}")
    for heading in ("ID", "Operation", "Status", "Attempts", "Target", "Last error"):
        table.add_column(heading)
    for row in rows:
        table.add_row(
            sanitize_terminal_text(row["job_id"]),
            sanitize_terminal_text(row["operation"]),
            sanitize_terminal_text(row["status"]),
            f"{row['attempts']}/{row['max_attempts']}",
            sanitize_terminal_text(row["target"]),
            sanitize_terminal_text(row["last_error"] or ""),
        )
    console.print(table)


def _execute_queue_job(job: QueueJob, settings: Settings) -> None:
    options = job.options
    if job.operation == "download":
        payload = _download_command_impl(
            targets=[job.target],
            from_file=None,
            select_playlist=False,
            select=None,
            audio=bool(options.get("audio", False)),
            fetch_subs=bool(options.get("fetch_subs", False)),
            auto_subs=bool(options.get("auto_subs", False)),
            dry_run=False,
            quiet=True,
            use_fzf=False,
            output="json",
            config=None,
            load_settings=lambda config=None: settings,
            read_targets_from_file=_read_targets_from_file,
            resolve_download_inputs=_resolve_download_inputs,
            prepare_storage=_prepare_storage,
            operation_lock_path=_operation_lock_path,
            lock_factory=operation_lock,
            download_targets_fn=_download_targets,
            build_download_payload=_download_operation_payload,
            render_download_payload=lambda *args, **kwargs: None,
        )
        if int(payload["summary"]["failed"]) > 0:
            raise ExternalCommandError("Queued download failed.")
        return

    with operation_lock(_operation_lock_path(settings)):
        _prepare_storage(settings)
        index_target(
            settings,
            job.target,
            fetch_subs=bool(options.get("fetch_subs", False)),
            auto_subs=bool(options.get("auto_subs", False)),
            lang=str(options["lang"]) if options.get("lang") else None,
        )


@queue_app.command("add", help="Add a download, index, or sync job.")
def queue_add_command(
    operation: str,
    target: str,
    audio: bool = typer.Option(False, "--audio", help="Download audio only."),
    fetch_subs: bool = typer.Option(False, "--fetch-subs", help="Fetch subtitles."),
    auto_subs: bool = typer.Option(False, "--auto-subs", help="Allow automatic subtitles."),
    lang: str | None = typer.Option(None, "--lang", help="Subtitle language expression."),
    max_retries: int = typer.Option(2, "--max-retries", min=0, max=10),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Persist a narrowly typed synchronous job."""

    def _command() -> None:
        settings = _load_settings(config)
        normalized_operation = operation.casefold().strip()
        if normalized_operation not in JOB_OPERATIONS:
            raise InvalidInputError("Queue operation must be download, index, or sync.")
        _validate_subtitle_flags(fetch_subs, auto_subs)
        if normalized_operation != "download" and audio:
            raise InvalidInputError("--audio is only valid for download jobs.")
        options = {
            "audio": audio,
            "fetch_subs": fetch_subs,
            "auto_subs": auto_subs,
            "lang": lang,
        }
        if dry_run:
            mode = _normalize_output_mode(output)
            payload = {
                "action": "add",
                "dry_run": True,
                "operation": normalized_operation,
                "target": target,
                "options": options,
                "max_attempts": max_retries + 1,
            }
            if mode == "json":
                _print_json(payload)
            else:
                _print_plain_mapping(list(payload.items()))
            return
        job = _queue_store(settings).add(
            normalized_operation, target, options=options, max_retries=max_retries
        )
        _render_queue_jobs([job], output_mode=output, action="add")

    _run_guarded(_command, output_mode=output)


@queue_app.command("list", help="List persistent jobs.")
def queue_list_command(
    status: str | None = typer.Option(None, "--status", help="Filter by job status."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    def _command() -> None:
        settings = _load_settings(config)
        jobs = _queue_store(settings).list(status=status) if _queue_path(settings).exists() else []
        _render_queue_jobs(jobs, output_mode=output, action="list")

    _run_guarded(_command, output_mode=output)


@queue_app.command("show", help="Show one persistent job.")
def queue_show_command(
    job_id: int,
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    def _command() -> None:
        settings = _load_settings(config)
        job = _queue_store(settings).get(job_id) if _queue_path(settings).exists() else None
        if job is None:
            raise InvalidInputError(f"Queue job {job_id} was not found.")
        _render_queue_jobs([job], output_mode=output, action="show")

    _run_guarded(_command, output_mode=output)


def _queue_transition_command(
    *, job_id: int, action: str, dry_run: bool, output: str, config: Path | None
) -> None:
    def _command() -> None:
        settings = _load_settings(config)
        if not _queue_path(settings).exists():
            raise InvalidInputError(f"Queue job {job_id} was not found.")
        store = _queue_store(settings)
        existing = store.get(job_id)
        if existing is None:
            raise InvalidInputError(f"Queue job {job_id} was not found.")
        if dry_run:
            _render_queue_jobs([existing], output_mode=output, action=action, dry_run=True)
            return
        job = store.cancel(job_id) if action == "cancel" else store.retry(job_id)
        _render_queue_jobs([job], output_mode=output, action=action)

    _run_guarded(_command, output_mode=output)


@queue_app.command("cancel", help="Cancel a pending or failed job.")
def queue_cancel_command(
    job_id: int,
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    _queue_transition_command(
        job_id=job_id, action="cancel", dry_run=dry_run, output=output, config=config
    )


@queue_app.command("retry", help="Retry a failed or cancelled job.")
def queue_retry_command(
    job_id: int,
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    _queue_transition_command(
        job_id=job_id, action="retry", dry_run=dry_run, output=output, config=config
    )


@queue_app.command("run-next", help="Synchronously run the next available job.")
def queue_run_next_command(
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    def _command() -> None:
        settings = _load_settings(config)
        if dry_run:
            job = _queue_store(settings).peek_next() if _queue_path(settings).exists() else None
            _render_queue_jobs(
                [job] if job else [], output_mode=output, action="run-next", dry_run=True
            )
            return
        store = _queue_store(settings)
        with operation_lock(settings.catalog_file.parent / "queue-worker.lock"):
            store.recover_running()
            job = store.claim_next()
            if job is None:
                _render_queue_jobs([], output_mode=output, action="run-next")
                return
            try:
                _execute_queue_job(job, settings)
            except Exception as exc:
                failed = store.fail(job.job_id, sanitize_terminal_text(exc))
                _render_queue_jobs([failed], output_mode=output, action="run-next")
                raise typer.Exit(code=int(ExitCode.EXTERNAL)) from exc
            completed = store.complete(job.job_id)
            _render_queue_jobs([completed], output_mode=output, action="run-next")

    _run_guarded(_command, output_mode=output)


@index_app.command(
    "refresh",
    help="Backfill or refresh the local catalog from the download manifest.",
)
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
    lang: str | None = typer.Option(
        None, "--lang", help="Preferred subtitle language expression, such as en.*."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
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
                summary = index_refresh(
                    settings, fetch_subs=fetch_subs, auto_subs=auto_subs, lang=lang
                )
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


@index_app.command("add", help="Index a specific video or playlist target into the local catalog.")
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
    lang: str | None = typer.Option(
        None, "--lang", help="Preferred subtitle language expression, such as en.*."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
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
                network_fetch_attempted=True,
            )
        else:
            with operation_lock(_operation_lock_path(settings)):
                _prepare_storage(settings)
                summary = index_target(
                    settings, target, fetch_subs=fetch_subs, auto_subs=auto_subs, lang=lang
                )
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


@transcripts_app.command("export", help="Export one indexed transcript track.")
def transcripts_export_command(
    video_id: str,
    dest: Path | None = typer.Option(None, "--dest", help="Destination file; defaults to stdout."),
    format: str | None = typer.Option(  # noqa: A002
        None, "--format", help="Export format: txt, md, json, vtt, or srt."
    ),
    language: str | None = typer.Option(
        None, "--language", "--lang", help="Select an exact indexed track language."
    ),
    timestamps: bool = typer.Option(
        True, "--timestamps/--no-timestamps", help="Include timestamps in text exports."
    ),
    chapters: bool = typer.Option(
        False, "--chapters", help="Group text/Markdown or annotate JSON by chapter."
    ),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Export a preferred or language-selected transcript without fetching the network."""

    def _command() -> None:
        from yt_agent.transcript_tools import (
            TRANSCRIPT_FORMATS,
            load_transcript_document,
            render_transcript,
        )

        settings = _load_settings(config)
        inferred_format = dest.suffix.lstrip(".").casefold() if dest else "txt"
        resolved_format = (format or inferred_format).casefold()
        if format is None and resolved_format not in TRANSCRIPT_FORMATS:
            resolved_format = "txt"
        if resolved_format not in TRANSCRIPT_FORMATS:
            raise InvalidInputError(
                f"Transcript format must be one of: {', '.join(sorted(TRANSCRIPT_FORMATS))}."
            )
        document = load_transcript_document(
            _catalog(settings, readonly=True), video_id, language=language
        )
        content = render_transcript(
            document,
            resolved_format,
            timestamps=timestamps,
            group_chapters=chapters,
        )
        if dest is None:
            sys.stdout.write(content)
            sys.stdout.flush()
            return
        try:
            atomic_write_artifact_text(dest, content, encoding="utf-8")
        except OSError as exc:
            raise StorageError(f"Could not write transcript export: {exc}") from exc
        payload = _mutation_payload(
            command="transcripts export",
            status="ok",
            summary={
                "segments": len(document.segments),
                "format": resolved_format,
                "language": document.track.lang,
            },
            path=str(dest),
        )
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
            return
        console.print(
            f"Exported {len(document.segments)} transcript segments to "
            f"{sanitize_terminal_text(str(dest))}.",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@transcripts_app.command("generate", help="Generate and index a transcript with local whisper-cli.")
def transcripts_generate_command(
    video_id: str,
    model: Path = typer.Option(..., "--model", help="Existing whisper.cpp model file."),
    language: str = typer.Option(
        "auto", "--language", "--lang", help="Spoken language code or 'auto'."
    ),
    dest: Path | None = typer.Option(None, "--dest", help="Generated .vtt destination."),
    force: bool = typer.Option(
        False, "--force", help="Replace an existing generated/indexed transcript path."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Run fully local ASR; this command never downloads a model."""

    def _command() -> None:
        from yt_agent.transcript_tools import (
            execute_local_transcription,
            plan_local_transcription,
        )

        settings = _load_settings(config)
        plan = plan_local_transcription(
            settings,
            video_id,
            model_path=model,
            language=language,
            output_path=dest,
            force=force,
        )
        segment_count = 0
        if not dry_run:
            with operation_lock(_operation_lock_path(settings)):
                _prepare_storage(settings)
                result = execute_local_transcription(settings, plan, force=force)
                segment_count = result.segment_count
        payload = _mutation_payload(
            command="transcripts generate",
            status="ok",
            summary={
                "segments": segment_count,
                "language": plan.language,
                "dry_run": dry_run,
            },
            path=str(plan.output_path),
            provenance_path=str(plan.provenance_path),
            model_path=str(plan.model_path),
            media_path=str(plan.media_path),
        )
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
            return
        prefix = "[dry-run] " if dry_run else ""
        console.print(
            f"{prefix}Generated {segment_count} transcript segments at "
            f"{sanitize_terminal_text(str(plan.output_path))}.",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@clips_app.command(
    "search",
    help="Search indexed transcript segments and chapters for clip-worthy matches.",
)
def clips_search_command(
    query: str,
    source: str = typer.Option(
        "all", "--source", help="Search source: transcript, chapters, or all."
    ),
    channel: str | None = typer.Option(None, "--channel", help="Limit results to one channel."),
    lang: str | None = typer.Option(
        None, "--lang", help="Optional transcript language filter, such as en% or en.*."
    ),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum clip hits to show."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search indexed transcript segments and chapters for clip-worthy matches."""

    def _command() -> None:
        settings = _load_settings(config)
        normalized_source = source.casefold().strip()
        if normalized_source not in {"all", "transcript", "chapters"}:
            raise InvalidInputError("--source must be 'transcript', 'chapters', or 'all'.")
        store = _catalog(settings, readonly=True)
        hits = store.search_clips(
            query,
            source=normalized_source,
            channel=channel,
            language=lang,
            limit=limit,
        )
        if not hits:
            if _normalize_output_mode(output) == "json":
                _print_json([])
            else:
                console.print("No clip hits found.")
            return
        _render_clip_hits(hits, output_mode=output)

    _run_guarded(_command, output_mode=output)


@clips_app.command("smart", help="Extract a clip with boundaries snapped to nearby silence.")
def clips_smart_command(
    result_id: str = typer.Argument(..., help="Transcript or chapter search result ID."),
    window: float = typer.Option(2.0, "--window", help="Silence search radius in seconds."),
    noise_db: float = typer.Option(-35.0, "--noise-db", help="Silence threshold in dB."),
    min_silence: float = typer.Option(
        0.25, "--min-silence", help="Minimum silence duration in seconds."
    ),
    mode: str = typer.Option("accurate", "--mode", help="Clip mode: fast or accurate."),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Analyze local audio for silence and extract at the nearest safe boundaries."""

    def _command() -> None:
        from yt_agent.media_tools import smart_clip_bounds

        if mode not in {"fast", "accurate"}:
            raise InvalidInputError("Clip mode must be 'fast' or 'accurate'.")
        settings = _load_settings(config)
        bounds = smart_clip_bounds(
            settings,
            result_id,
            window_seconds=window,
            noise_db=noise_db,
            min_silence=min_silence,
        )
        output_path: Path | None = None
        if not dry_run:
            with operation_lock(_operation_lock_path(settings)):
                extraction = extract_clip_for_range(
                    settings,
                    video_id=bounds.video_id,
                    start_seconds=bounds.start_seconds,
                    end_seconds=bounds.end_seconds,
                    mode=mode,
                )
                output_path = extraction.output_path
        payload = _mutation_payload(
            command="clips smart",
            status="ok",
            summary={
                "dry_run": dry_run,
                "original_start": bounds.original_start,
                "original_end": bounds.original_end,
                "start_seconds": bounds.start_seconds,
                "end_seconds": bounds.end_seconds,
            },
            locator=result_id,
            video_id=bounds.video_id,
            path=str(output_path) if output_path else None,
        )
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
            return
        prefix = "[dry-run] " if dry_run else ""
        console.print(
            f"{prefix}Smart clip {bounds.start_seconds:.3f}-{bounds.end_seconds:.3f}s"
            + (f" -> {sanitize_terminal_text(str(output_path))}" if output_path else ""),
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@preview_app.command("contact-sheet", help="Create an evenly sampled contact sheet and GIF.")
def preview_contact_sheet_command(
    video_id: str,
    dest: Path | None = typer.Option(None, "--dest", help="Contact sheet image destination."),
    frames: int = typer.Option(12, "--frames", help="Number of sampled frames (4-25)."),
    columns: int = typer.Option(4, "--columns", help="Grid columns (1-5)."),
    width: int = typer.Option(320, "--width", help="Width of each frame (160-1920)."),
    gif: bool = typer.Option(False, "--gif", help="Also create a short GIF preview."),
    gif_start: float = typer.Option(0.0, "--gif-start", help="GIF start time."),
    gif_duration: float = typer.Option(4.0, "--gif-duration", help="GIF duration (0.5-15)."),
    gif_fps: int = typer.Option(8, "--gif-fps", help="GIF frame rate (1-20)."),
    force: bool = typer.Option(False, "--force", help="Replace existing preview outputs."),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Create bounded, local-only visual previews with ffmpeg."""

    def _command() -> None:
        from yt_agent.media_tools import execute_preview, plan_preview

        settings = _load_settings(config)
        plan = plan_preview(
            settings,
            video_id,
            dest=dest,
            frames=frames,
            columns=columns,
            width=width,
            gif=gif,
            gif_start=gif_start,
            gif_duration=gif_duration,
            gif_fps=gif_fps,
            force=force,
        )
        if not dry_run:
            with operation_lock(_operation_lock_path(settings)):
                execute_preview(plan)
        payload = _mutation_payload(
            command="preview contact-sheet",
            status="ok",
            summary={"dry_run": dry_run, "frames": frames, "gif": gif},
            video_id=video_id,
            path=str(plan.contact_sheet_path),
            gif_path=str(plan.gif_path) if plan.gif_path else None,
        )
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
            return
        prefix = "[dry-run] " if dry_run else ""
        console.print(
            f"{prefix}Contact sheet: {sanitize_terminal_text(str(plan.contact_sheet_path))}",
            markup=False,
        )

    _run_guarded(_command, output_mode=output)


@stats_app.command("refresh", help="Fetch and store current public video statistics.")
def stats_refresh_command(
    video_ids: list[str] | None = typer.Argument(
        None, help="Catalog video IDs; defaults to the newest bounded catalog batch."
    ),
    limit: int = typer.Option(25, "--limit", help="Maximum videos to refresh (1-100)."),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Refresh public counts through yt-dlp without Google API credentials."""

    def _command() -> None:
        from yt_agent.stats import refresh_stats, snapshot_payload

        settings = _load_settings(config)
        if dry_run:
            snapshots = refresh_stats(
                _catalog(settings, readonly=True), video_ids, limit=limit, dry_run=True
            )
        else:
            with operation_lock(_operation_lock_path(settings)):
                store = _catalog(settings)
                snapshots = refresh_stats(store, video_ids, limit=limit)
        payload = _mutation_payload(
            command="stats refresh",
            status="ok",
            summary={"videos": len(snapshots), "dry_run": dry_run, "provider": "yt-dlp"},
            snapshots=[snapshot_payload(snapshot) for snapshot in snapshots],
        )
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
            return
        prefix = "[dry-run] " if dry_run else ""
        console.print(f"{prefix}Refreshed statistics for {len(snapshots)} videos.", markup=False)

    _run_guarded(_command, output_mode=output)


@stats_app.command("show", help="Show stored statistics history for one video.")
def stats_show_command(
    video_id: str,
    limit: int = typer.Option(20, "--limit", help="Maximum snapshots (1-1000)."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show newest-first time-series snapshots for a catalog video."""

    def _command() -> None:
        from yt_agent.stats import snapshot_payload, stats_history

        settings = _load_settings(config)
        snapshots = stats_history(_catalog(settings, readonly=True), video_id, limit=limit)
        rows = [snapshot_payload(snapshot) for snapshot in snapshots]
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json({"video_id": video_id, "snapshots": rows})
            return
        if not rows:
            console.print("No statistics snapshots found.", markup=False)
            return
        if mode == "plain":
            _print_plain_rows(
                [
                    ("fetched_at", "fetched_at"),
                    ("view_count", "views"),
                    ("like_count", "likes"),
                    ("comment_count", "comments"),
                    ("provider", "provider"),
                ],
                rows,
            )
            return
        table = Table(title=f"Statistics: {sanitize_terminal_text(video_id)}")
        for heading in ("Fetched", "Views", "Likes", "Comments", "Provider"):
            table.add_column(heading)
        for row in rows:
            table.add_row(
                sanitize_terminal_text(row["fetched_at"]),
                str(row["view_count"] if row["view_count"] is not None else "-"),
                str(row["like_count"] if row["like_count"] is not None else "-"),
                str(row["comment_count"] if row["comment_count"] is not None else "-"),
                sanitize_terminal_text(row["provider"]),
            )
        console.print(table)

    _run_guarded(_command, output_mode=output)


@stats_app.command("trends", help="Show latest count deltas for catalog videos.")
def stats_trends_command(
    video_ids: list[str] | None = typer.Argument(None, help="Catalog video IDs; default is all."),
    limit: int = typer.Option(25, "--limit", help="Maximum videos (1-100)."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Compare each video's newest two stored snapshots."""

    def _command() -> None:
        from yt_agent.stats import stats_trends, trend_payload

        settings = _load_settings(config)
        trends = stats_trends(_catalog(settings, readonly=True), video_ids, limit=limit)
        rows = [trend_payload(trend) for trend in trends]
        mode = _normalize_output_mode(output)
        if mode == "json":
            _print_json({"trends": rows})
            return
        display_rows = [
            {
                "video_id": row["video_id"],
                "view_delta": row["view_delta"],
                "like_delta": row["like_delta"],
                "comment_delta": row["comment_delta"],
                "fetched_at": row["current"]["fetched_at"],
            }
            for row in rows
        ]
        if mode == "plain":
            _print_plain_rows(
                [
                    ("video_id", "video_id"),
                    ("view_delta", "view_delta"),
                    ("like_delta", "like_delta"),
                    ("comment_delta", "comment_delta"),
                    ("fetched_at", "fetched_at"),
                ],
                display_rows,
            )
            return
        table = Table(title="Statistics Trends")
        for heading in ("Video ID", "Views Δ", "Likes Δ", "Comments Δ", "Fetched"):
            table.add_column(heading)
        for row in display_rows:
            table.add_row(
                sanitize_terminal_text(row["video_id"]),
                str(row["view_delta"] if row["view_delta"] is not None else "-"),
                str(row["like_delta"] if row["like_delta"] is not None else "-"),
                str(row["comment_delta"] if row["comment_delta"] is not None else "-"),
                sanitize_terminal_text(row["fetched_at"]),
            )
        console.print(table)

    _run_guarded(_command, output_mode=output)


@clips_app.command("show", help="Show a specific clip-search hit with context.")
def clips_show_command(
    result_id: str,
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show a specific clip-search hit with context."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings, readonly=True)
        hit = store.get_clip_hit(result_id)
        if hit is None:
            raise InvalidInputError(f"Unknown clip result '{result_id}'.")
        payload = _clip_hit_row(hit)
        payload["result_id_note"] = (
            "Result ids are not durable across reindexing or catalog rebuilds."
        )
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
                sanitize_terminal_text(
                    "remote only" if key == "output_path" and not value else value
                ),
            )
        console.print(table)

    _run_guarded(_command, output_mode=output)


@clips_app.command("grab", help="Extract a clip from a cataloged chapter or transcript hit.")
def clips_grab_command(
    result_id: str | None = typer.Argument(
        None, help="Clip search result ID like transcript:12 or chapter:3."
    ),
    video_id: str | None = typer.Option(
        None, "--video-id", help="Cataloged video ID for explicit clip extraction."
    ),
    start_seconds: float | None = typer.Option(
        None, "--start-seconds", help="Explicit clip start in seconds."
    ),
    end_seconds: float | None = typer.Option(
        None, "--end-seconds", help="Explicit clip end in seconds."
    ),
    padding_before: float = typer.Option(
        0.0, "--padding-before", min=0.0, help="Seconds to prepend."
    ),
    padding_after: float = typer.Option(0.0, "--padding-after", min=0.0, help="Seconds to append."),
    mode: str = typer.Option("fast", "--mode", help="Extraction mode: fast or accurate."),
    remote_fallback: bool = typer.Option(
        False,
        "--remote-fallback",
        help="Fallback to yt-dlp section download if local media is missing.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
    quiet: bool = typer.Option(False, "--quiet", help="Reduce non-essential output."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Extract a clip from a cataloged chapter or transcript hit."""

    def _command() -> None:
        settings = _load_settings(config)
        normalized_mode = _validate_clip_mode(mode)
        using_explicit_range = any(
            value is not None for value in (video_id, start_seconds, end_seconds)
        )
        if using_explicit_range and result_id is not None:
            raise InvalidInputError(
                "Use either RESULT_ID or --video-id/--start-seconds/--end-seconds, not both."
            )
        if using_explicit_range:
            if video_id is None or start_seconds is None or end_seconds is None:
                raise InvalidInputError(
                    "--video-id, --start-seconds, and --end-seconds are required together."
                )
            if end_seconds <= start_seconds:
                raise InvalidInputError("--end-seconds must be greater than --start-seconds.")
            locator = f"{video_id}:{start_seconds:.3f}-{end_seconds:.3f}"
        elif result_id is not None:
            locator = result_id
        else:
            raise InvalidInputError("Pass a RESULT_ID or --video-id/--start-seconds/--end-seconds.")

        if dry_run:
            if using_explicit_range:
                if video_id is None or start_seconds is None or end_seconds is None:
                    raise RuntimeError(
                        "Clip range validation should guarantee explicit range values."
                    )
                preview = plan_clip_for_range(
                    settings,
                    video_id=video_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    mode=normalized_mode,
                    prefer_remote=remote_fallback,
                )
            else:
                if result_id is None:
                    raise RuntimeError("Clip validation should guarantee a result id.")
                preview = plan_clip(
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
                    "output_path": str(preview.output_template or preview.output_path),
                    "output_path_is_template": preview.output_template is not None,
                    "source": preview.source,
                    "start_seconds": preview.start_seconds,
                    "end_seconds": preview.end_seconds,
                    "used_remote_fallback": preview.used_remote_fallback,
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
                    if video_id is None or start_seconds is None or end_seconds is None:
                        raise RuntimeError(
                            "Clip range validation should guarantee explicit range values."
                        )
                    extraction = extract_clip_for_range(
                        settings,
                        video_id=video_id,
                        start_seconds=start_seconds,
                        end_seconds=end_seconds,
                        mode=normalized_mode,
                        prefer_remote=remote_fallback,
                    )
                else:
                    if result_id is None:
                        raise RuntimeError("Clip validation should guarantee a result id.")
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


@library_app.command("list", help="List cataloged library entries.")
def library_list_command(
    channel: str | None = typer.Option(
        None, "--channel", help="Only show videos from one channel."
    ),
    playlist: str | None = typer.Option(None, "--playlist", help="Filter by playlist ID or title."),
    has_transcript: bool = typer.Option(
        False, "--has-transcript", help="Only show videos with indexed transcripts."
    ),
    no_transcript: bool = typer.Option(
        False, "--no-transcript", help="Only show videos without indexed transcripts."
    ),
    has_chapters: bool = typer.Option(
        False, "--has-chapters", help="Only show videos with indexed chapters."
    ),
    no_chapters: bool = typer.Option(
        False, "--no-chapters", help="Only show videos without indexed chapters."
    ),
    limit: int = typer.Option(25, "--limit", min=1, help="Maximum videos to show."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """List cataloged library entries."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings, readonly=True)
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


@library_app.command(
    "search",
    help="Search the local library catalog by title, channel, or video ID.",
)
def library_search_command(
    query: str,
    channel: str | None = typer.Option(None, "--channel", help="Only search one channel."),
    playlist: str | None = typer.Option(None, "--playlist", help="Filter by playlist ID or title."),
    has_transcript: bool = typer.Option(
        False, "--has-transcript", help="Only show videos with indexed transcripts."
    ),
    no_transcript: bool = typer.Option(
        False, "--no-transcript", help="Only show videos without indexed transcripts."
    ),
    has_chapters: bool = typer.Option(
        False, "--has-chapters", help="Only show videos with indexed chapters."
    ),
    no_chapters: bool = typer.Option(
        False, "--no-chapters", help="Only show videos without indexed chapters."
    ),
    limit: int = typer.Option(25, "--limit", min=1, help="Maximum videos to show."),
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Search the local library catalog by title, channel, or video ID."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings, readonly=True)
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


@library_app.command("show", help="Show one cataloged video with chapters and transcript preview.")
def library_show_command(
    video_id: str,
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show one cataloged video with chapters and transcript preview."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings, readonly=True)
        _render_library_detail(store, video_id, output_mode=output)

    _run_guarded(_command, output_mode=output)


@library_app.command("stats", help="Show high-level counts for the local catalog.")
def library_stats_command(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Show high-level counts for the local catalog."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings, readonly=True)
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


@library_app.command("channels", help="List distinct channels in the catalog.")
def library_channels_command(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """List distinct channels in the catalog."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings, readonly=True)
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


@library_app.command("playlists", help="List indexed playlists with video counts.")
def library_playlists_command(
    output: str = typer.Option("table", "--output", help=READ_OUTPUT_HELP),
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """List indexed playlists with video counts."""

    def _command() -> None:
        settings = _load_settings(config)
        store = _catalog(settings, readonly=True)
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


@library_app.command(
    "remove",
    help="Remove videos from the catalog. Media files on disk are not affected.",
)
def library_remove_command(
    video_ids: list[str] = typer.Argument(
        ..., help="One or more video IDs to remove from the catalog."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help=DRY_RUN_HELP),
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
        payload = _library_remove_payload(
            requested=video_ids, removed=removed, not_found=not_found, dry_run=dry_run
        )
        _render_library_remove_payload(payload, output_mode=output)

    _run_guarded(_command, output_mode=output)


@app.command(help="Launch the Textual catalog browser.")
def tui(
    config: Path | None = typer.Option(None, "--config", help="Path to config.toml override."),
) -> None:
    """Launch the Textual catalog browser."""

    def _command() -> None:
        settings = _load_settings(config)
        _prepare_storage(settings)
        launch_tui(settings)

    _run_guarded(_command)


@completions_app.command("install")
def completions_install_command(
    shell: CompletionShell | None = typer.Option(
        None, "--shell", help="Shell to install completion for."
    ),
    output: str = typer.Option("plain", "--output", help=READ_OUTPUT_HELP),
) -> None:
    """Install shell completion for yt-agent."""

    def _command() -> None:
        resolved_shell = _resolve_completion_shell(shell)
        prog_name = _completion_prog_name()
        installed_shell, installed_path = typer_completion_install(
            shell=resolved_shell,
            prog_name=prog_name,
            complete_var=_completion_env_var(prog_name),
        )
        payload = _mutation_payload(
            command="completions install",
            status="ok",
            summary={"installed": 1, "shell": installed_shell},
            shell=installed_shell,
            path=str(installed_path),
            restart_required=True,
        )
        if _normalize_output_mode(output) == "json":
            _print_json(payload)
            return
        console.print(
            f"Installed {sanitize_terminal_text(installed_shell)} completion: "
            f"{sanitize_terminal_text(installed_path)}",
            style="green",
            markup=False,
        )
        console.print("Restart your terminal to enable it.", markup=False)

    _run_guarded(_command, output_mode=output)


@completions_app.command("show")
def completions_show_command(
    shell: CompletionShell | None = typer.Option(
        None, "--shell", help="Shell to print completion for."
    ),
    output: str = typer.Option("plain", "--output", help=READ_OUTPUT_HELP),
) -> None:
    """Print the shell completion script for yt-agent."""

    def _command() -> None:
        resolved_shell = _resolve_completion_shell(shell)
        prog_name = _completion_prog_name()
        script = get_completion_script(
            prog_name=prog_name,
            complete_var=_completion_env_var(prog_name),
            shell=resolved_shell,
        )
        if _normalize_output_mode(output) == "json":
            payload = _mutation_payload(
                command="completions show",
                status="ok",
                summary={"lines": len(script.splitlines()), "shell": resolved_shell},
                shell=resolved_shell,
                script=script,
            )
            _print_json(payload)
            return
        console.file.write(script)
        console.file.write("\n")
        console.file.flush()

    _run_guarded(_command, output_mode=output)


def main() -> None:
    """Run the app and map application errors to stable exit codes."""

    app()
