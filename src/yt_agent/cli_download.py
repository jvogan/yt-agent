"""Download coordination helpers shared by CLI commands."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from yt_agent import yt_dlp
from yt_agent.archive import is_archived, load_archive_entries
from yt_agent.cli_output import (
    DownloadOperationItem,
    _normalize_output_mode,
    _render_playlist_summary,
    _render_results,
    console,
    error_console,
)
from yt_agent.config import Settings
from yt_agent.errors import ExternalCommandError, InvalidInputError
from yt_agent.indexer import index_manifest_record
from yt_agent.manifest import append_manifest_record
from yt_agent.models import DownloadTarget, ManifestRecord, VideoInfo
from yt_agent.security import sanitize_terminal_text
from yt_agent.selector import parse_selection, select_results


def _read_targets_from_file(path: Path) -> list[str]:
    """Read URLs/IDs from a file, one per line. Blank lines and # comments are skipped."""
    if not path.exists():
        raise InvalidInputError(f"--from-file path not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _download_targets(
    targets: list[DownloadTarget],
    settings: Settings,
    *,
    mode: str = "video",
    fetch_subs: bool = False,
    auto_subs: bool = False,
    quiet: bool = False,
    show_failure_details: bool = True,
    append_manifest_record_fn: Callable[[Path, ManifestRecord], None] = append_manifest_record,
    index_manifest_record_fn: Callable[..., Any] = index_manifest_record,
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
                title = sanitize_terminal_text(target.info.title)
                video_id = sanitize_terminal_text(target.info.video_id)
                console.print(
                    f"Skipping archived: {title} [{video_id}]",
                    style="yellow",
                    markup=False,
                )
            continue
        if not quiet:
            console.print(
                f"Downloading: {sanitize_terminal_text(target.info.title)}",
                style="cyan",
                markup=False,
            )
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
                title = sanitize_terminal_text(target.info.title)
                video_id = sanitize_terminal_text(target.info.video_id)
                error_message = sanitize_terminal_text(exc)
                error_console.print(
                    f"Failed: {title} [{video_id}] {error_message}{detail}",
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
                title = sanitize_terminal_text(target.info.title)
                video_id = sanitize_terminal_text(target.info.video_id)
                console.print(
                    f"Skipping archived (detected by yt-dlp): {title} [{video_id}]",
                    style="yellow",
                    markup=False,
                )
            continue
        record = ManifestRecord.from_download(
            target,
            output_path=execution.output_path,
            info_json_path=execution.info_json_path,
        )
        append_manifest_record_fn(settings.manifest_file, record)
        archive_entries.add(target.info.archive_key)
        item = DownloadOperationItem(
            status="downloaded",
            info=target.info,
            requested_input=target.original_input,
            output_path=execution.output_path,
            info_json_path=execution.info_json_path,
        )
        if not quiet:
            console.print(
                f"Saved: {sanitize_terminal_text(execution.output_path)}",
                style="green",
                markup=False,
            )
        try:
            summary = index_manifest_record_fn(
                settings, record, fetch_subs=fetch_subs, auto_subs=auto_subs
            )
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


def _select_by_indexes(
    results: list[VideoInfo],
    selection: str,
    *,
    parse_selection_fn: Callable[[str, int], list[int]] = parse_selection,
) -> list[VideoInfo]:
    indexes = parse_selection_fn(selection, len(results))
    return [results[index - 1] for index in indexes]


def _choose_results(
    results: list[VideoInfo],
    *,
    selection: str | None = None,
    prefer_fzf: bool = False,
    configured_selector: str = "prompt",
    select_by_indexes_fn: Callable[[list[VideoInfo], str], list[VideoInfo]] | None = None,
    select_results_fn: Callable[..., list[VideoInfo]] = select_results,
) -> list[VideoInfo]:
    selector = select_by_indexes_fn or _select_by_indexes
    if selection is not None:
        return selector(results, selection)
    return select_results_fn(
        results,
        prefer_fzf=prefer_fzf,
        configured_selector=configured_selector,
    )


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
    choose_results_fn: Callable[..., list[VideoInfo]] | None = None,
) -> tuple[list[DownloadTarget], list[str]]:
    choose_results = choose_results_fn or _choose_results
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
                    (
                        "No downloadable entries found in playlist: "
                        f"{sanitize_terminal_text(user_input)}"
                    ),
                    style="yellow",
                    markup=False,
                )
            continue

        if render_selection and not quiet:
            selection_mode = (
                "table" if _normalize_output_mode(selection_output_mode) == "table" else "plain"
            )
            _render_playlist_summary(payload, len(resolution.targets), output_mode=selection_mode)
            _render_results(
                [target.info for target in resolution.targets],
                title="Playlist Entries",
                output_mode=selection_mode,
            )
        selected_infos = choose_results(
            [target.info for target in resolution.targets],
            selection=selection,
            prefer_fzf=use_fzf,
            configured_selector=settings.selector,
        )
        if not selected_infos:
            if render_selection and not quiet:
                console.print(
                    (
                        "No playlist selection made: "
                        f"{sanitize_terminal_text(payload.get('title') or user_input)}"
                    ),
                    style="yellow",
                    markup=False,
                )
            continue

        selected_ids = {item.video_id for item in selected_infos}
        all_targets.extend(
            [target for target in resolution.targets if target.info.video_id in selected_ids]
        )

    return all_targets, skipped_messages


def _presence_flag(enabled: bool, disabled: bool, *, label: str) -> bool | None:
    if enabled and disabled:
        raise InvalidInputError(f"Choose only one of --{label} or --no-{label}.")
    if enabled:
        return True
    if disabled:
        return False
    return None


def _run_download_flow(
    *,
    settings: Settings,
    mode: str,
    fetch_subs: bool,
    auto_subs: bool,
    dry_run: bool,
    quiet: bool,
    output_mode: str,
    resolve_targets: Callable[[], tuple[list[DownloadTarget], list[str]]],
    prepare_storage: Callable[[Settings], None],
    lock_factory: Callable[[Path], AbstractContextManager[object]],
    lock_path: Path,
    download_targets_fn: Callable[..., list[DownloadOperationItem]],
) -> tuple[list[DownloadTarget], list[str], list[DownloadOperationItem]]:
    normalized_output = _normalize_output_mode(output_mode)
    if dry_run:
        resolved_targets, skipped_messages = resolve_targets()
        return resolved_targets, skipped_messages, []

    with lock_factory(lock_path):
        resolved_targets, skipped_messages = resolve_targets()
        if not resolved_targets:
            return resolved_targets, skipped_messages, []
        prepare_storage(settings)
        items = download_targets_fn(
            resolved_targets,
            settings,
            mode=mode,
            fetch_subs=fetch_subs,
            auto_subs=auto_subs,
            quiet=quiet or normalized_output == "json",
            show_failure_details=normalized_output != "json",
        )
    return resolved_targets, skipped_messages, items


def download_command(
    *,
    targets: list[str],
    from_file: Path | None,
    select_playlist: bool,
    select: str | None,
    audio: bool,
    fetch_subs: bool,
    auto_subs: bool,
    dry_run: bool,
    quiet: bool,
    use_fzf: bool,
    output: str,
    config: Path | None,
    load_settings: Callable[[Path | None], Settings],
    read_targets_from_file: Callable[[Path], list[str]],
    resolve_download_inputs: Callable[..., tuple[list[DownloadTarget], list[str]]],
    prepare_storage: Callable[[Settings], None],
    operation_lock_path: Callable[[Settings], Path],
    lock_factory: Callable[[Path], AbstractContextManager[object]],
    download_targets_fn: Callable[..., list[DownloadOperationItem]],
    build_download_payload: Callable[..., dict[str, Any]],
    render_download_payload: Callable[..., None],
) -> dict[str, Any]:
    settings = load_settings(config)
    _validate_subtitle_flags(fetch_subs, auto_subs)
    if _normalize_output_mode(output) == "json" and select_playlist and select is None:
        raise InvalidInputError("--select-playlist with --output json requires --select.")
    all_inputs: list[str] = list(targets)
    if from_file is not None:
        all_inputs.extend(read_targets_from_file(from_file))
    if not all_inputs:
        raise InvalidInputError(
            "No targets provided. Pass video URLs as arguments or use --from-file."
        )
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
    resolved_targets, skipped_messages, items = _run_download_flow(
        settings=settings,
        mode=mode,
        fetch_subs=fetch_subs,
        auto_subs=auto_subs,
        dry_run=dry_run,
        quiet=quiet,
        output_mode=output,
        resolve_targets=lambda: resolve_download_inputs(
            all_inputs,
            settings,
            **resolve_kwargs,
        ),
        prepare_storage=prepare_storage,
        lock_factory=lock_factory,
        lock_path=operation_lock_path(settings),
        download_targets_fn=download_targets_fn,
    )
    payload = build_download_payload(
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
    render_download_payload(payload, output_mode=output, quiet=quiet)
    return payload


def grab_command(
    *,
    query: str,
    limit: int | None,
    use_fzf: bool,
    select: str | None,
    audio: bool,
    fetch_subs: bool,
    auto_subs: bool,
    dry_run: bool,
    quiet: bool,
    output: str,
    config: Path | None,
    load_settings: Callable[[Path | None], Settings],
    choose_results: Callable[..., list[VideoInfo]],
    prepare_storage: Callable[[Settings], None],
    operation_lock_path: Callable[[Settings], Path],
    lock_factory: Callable[[Path], AbstractContextManager[object]],
    download_targets_fn: Callable[..., list[DownloadOperationItem]],
    build_download_payload: Callable[..., dict[str, Any]],
    render_download_payload: Callable[..., None],
) -> dict[str, Any] | None:
    settings = load_settings(config)
    _validate_subtitle_flags(fetch_subs, auto_subs)
    mode = "audio" if audio or settings.default_mode == "audio" else "video"
    results: list[VideoInfo] | None = None

    def _resolve_targets() -> tuple[list[DownloadTarget], list[str]]:
        nonlocal results
        results = yt_dlp.search(query, limit=limit or settings.search_limit)
        if not results:
            return [], []
        _require_noninteractive_json_selection(output_mode=output, selection=select, action="grab")
        if not quiet and _normalize_output_mode(output) != "json":
            _render_results(
                results,
                title="Search Results",
                output_mode="table" if _normalize_output_mode(output) == "table" else "plain",
            )
        selected = choose_results(
            results,
            selection=select,
            prefer_fzf=use_fzf,
            configured_selector=settings.selector,
        )
        return (
            [
                DownloadTarget(
                    original_input=item.webpage_url,
                    info=item,
                    source_query=query,
                )
                for item in selected
            ],
            [],
        )

    resolved_targets, _, items = _run_download_flow(
        settings=settings,
        mode=mode,
        fetch_subs=fetch_subs,
        auto_subs=auto_subs,
        dry_run=dry_run,
        quiet=quiet,
        output_mode=output,
        resolve_targets=_resolve_targets,
        prepare_storage=prepare_storage,
        lock_factory=lock_factory,
        lock_path=operation_lock_path(settings),
        download_targets_fn=download_targets_fn,
    )
    if results == [] and _normalize_output_mode(output) != "json":
        console.print("No matches found.")
        return None
    payload = build_download_payload(
        command="grab",
        requested=[query],
        resolved_targets=resolved_targets,
        items=items,
        mode=mode,
        fetch_subs=fetch_subs,
        auto_subs=auto_subs,
        download_root=settings.download_root,
        dry_run=dry_run,
    )
    render_download_payload(payload, output_mode=output, quiet=quiet)
    return payload
