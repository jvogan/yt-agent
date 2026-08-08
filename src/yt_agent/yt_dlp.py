"""Subprocess wrapper around yt-dlp."""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from yt_agent.config import Settings
from yt_agent.errors import DependencyError, ExternalCommandError, InvalidInputError
from yt_agent.library import build_output_template, discover_info_json
from yt_agent.models import DownloadTarget, VideoInfo
from yt_agent.security import protect_private_tree

_URL_RE = re.compile(r"https?://\S+")
_SEARCH_TARGET_RE = re.compile(r"^(ytsearch(?:date)?\d*):", re.IGNORECASE)

__all__ = [
    "YOUTUBE_ID_RE",
    "ALLOWED_YOUTUBE_HOSTS",
    "DownloadExecution",
    "ResolutionResult",
    "command_path",
    "optional_tool_path",
    "normalize_target",
    "search",
    "fetch_info",
    "resolve_payload",
    "resolve_targets",
    "download_target",
    "fetch_comments",
    "record_live",
]


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SUBPROCESS_TIMEOUT_SECONDS = 300  # 5 minutes
_MAX_CAPTURED_STDERR_CHARS = 4000
ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}
logger = logging.getLogger("yt_agent")

# Allowlisted config values passed to yt-dlp CLI arguments.
_FORMAT_ALLOWLIST_RE = re.compile(r"^[A-Za-z0-9+*/\[\],._()\- ]+$")
_SUBTITLE_LANG_RE = re.compile(r"^[A-Za-z0-9,.*\- ]+$")


@dataclass(frozen=True)
class DownloadExecution:
    """Successful yt-dlp invocation details."""

    output_path: Path
    stdout: str
    info_json_path: Path | None = None


@dataclass(frozen=True)
class ResolutionResult:
    """Expanded download targets and skipped playlist entries."""

    targets: list[DownloadTarget]
    skipped_messages: list[str]


def command_path() -> str:
    path = shutil.which("yt-dlp")
    if path is None:
        raise DependencyError("Required tool 'yt-dlp' is not installed or not on PATH.")
    return path


def optional_tool_path(name: str) -> str | None:
    return shutil.which(name)


def normalize_target(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise InvalidInputError("Target cannot be empty.")
    if stripped.startswith(("http://", "https://")):
        try:
            parsed = urlsplit(stripped)
            host = (parsed.hostname or "").rstrip(".").casefold()
        except ValueError as exc:
            raise InvalidInputError("Target must be a valid YouTube URL.") from exc
        if (
            host not in ALLOWED_YOUTUBE_HOSTS
            and not host.endswith(".youtube.com")
            and not host.endswith(".youtube-nocookie.com")
        ):
            raise InvalidInputError("Only YouTube URLs are supported.")
        return stripped
    if YOUTUBE_ID_RE.fullmatch(stripped):
        return f"https://www.youtube.com/watch?v={stripped}"
    raise InvalidInputError("Target must be a full URL or an 11-character YouTube video id.")


def _redact_args(args: list[str]) -> str:
    """Render command arguments for logs without targets or search text."""
    redacted: list[str] = []
    for arg in args:
        search_target = _SEARCH_TARGET_RE.match(arg)
        if search_target is not None:
            redacted.append(f"{search_target.group(1)}:<query>")
        else:
            redacted.append(_URL_RE.sub("<url>", arg))
    return shlex.join(redacted)


def _bounded_stderr(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= _MAX_CAPTURED_STDERR_CHARS:
        return stripped
    return f"{stripped[:_MAX_CAPTURED_STDERR_CHARS]}..."


def _valid_subtitle_languages(value: str) -> bool:
    if not _SUBTITLE_LANG_RE.fullmatch(value):
        return False
    return all(not item.strip().startswith("-") for item in value.split(",") if item.strip())


def _run_json(args: list[str]) -> dict[str, Any]:
    command = _redact_args(args)
    start_time = time.perf_counter()
    logger.debug("Running subprocess: %s", command)
    try:
        # Uses a resolved yt-dlp path and normalized arguments without invoking a shell.
        completed = subprocess.run(  # noqa: S603
            args, text=True, capture_output=True, check=False, timeout=_SUBPROCESS_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug("Subprocess timed out after %.2fms", elapsed_ms)
        raise ExternalCommandError("yt-dlp timed out.") from exc
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    logger.debug(
        "Subprocess completed returncode=%s elapsed_ms=%.2f command=%s",
        completed.returncode,
        elapsed_ms,
        command,
    )
    if completed.returncode != 0:
        stderr = _bounded_stderr(completed.stderr)
        raise ExternalCommandError("yt-dlp failed while extracting metadata.", stderr=stderr)
    try:
        result: dict[str, Any] = json.loads(completed.stdout)
        return result
    except json.JSONDecodeError as exc:
        raise ExternalCommandError("yt-dlp returned invalid JSON metadata.") from exc


def _run_download(args: list[str]) -> DownloadExecution | None:
    command = _redact_args(args)
    start_time = time.perf_counter()
    logger.debug("Running subprocess: %s", command)
    try:
        # Uses a resolved yt-dlp path and normalized arguments without invoking a shell.
        completed = subprocess.run(  # noqa: S603
            args, text=True, capture_output=True, check=False
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug("Subprocess timed out after %.2fms", elapsed_ms)
        raise ExternalCommandError("yt-dlp timed out.") from exc
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    logger.debug(
        "Subprocess completed returncode=%s elapsed_ms=%.2f command=%s",
        completed.returncode,
        elapsed_ms,
        command,
    )
    if completed.returncode != 0:
        stderr = _bounded_stderr(completed.stderr)
        raise ExternalCommandError("yt-dlp download failed.", stderr=stderr)

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    output_path = next((Path(line) for line in reversed(lines) if not line.startswith("[")), None)
    if output_path is None:
        return None  # yt-dlp exited 0 with no output — archive skip
    return DownloadExecution(output_path=output_path, stdout=completed.stdout)


def search(query: str, *, limit: int) -> list[VideoInfo]:
    if "://" in query:
        raise InvalidInputError("Search query must not contain a URL.")
    yt_dlp = command_path()
    payload = _run_json([yt_dlp, "--dump-single-json", "--no-warnings", f"ytsearch{limit}:{query}"])
    entries = payload.get("entries") or []
    return [VideoInfo.from_yt_dlp(entry) for entry in entries if entry]


def fetch_info(target: str) -> dict[str, Any]:
    yt_dlp = command_path()
    normalized = normalize_target(target)
    return _run_json([yt_dlp, "--dump-single-json", "--no-warnings", normalized])


def fetch_comments(target: str, *, limit: int) -> dict[str, Any]:
    """Fetch a bounded comment payload without accepting raw extractor arguments."""
    if limit < 1 or limit > 1000:
        raise InvalidInputError("Comment limit must be between 1 and 1000.")
    yt_dlp = command_path()
    normalized = normalize_target(target)
    return _run_json(
        [
            yt_dlp,
            "--dump-single-json",
            "--no-warnings",
            "--get-comments",
            "--extractor-args",
            f"youtube:max_comments={limit}",
            normalized,
        ]
    )


def resolve_payload(
    user_input: str,
    payload: dict[str, Any],
    *,
    source_query: str | None = None,
) -> ResolutionResult:
    targets: list[DownloadTarget] = []
    skipped_messages: list[str] = []
    entries = payload.get("entries")
    if isinstance(entries, list):
        for index, entry in enumerate(entries, start=1):
            if not entry:
                skipped_messages.append(
                    f"Skipped unavailable playlist entry #{index} from {user_input}."
                )
                continue
            try:
                info = VideoInfo.from_yt_dlp(entry, original_url=user_input)
            except InvalidInputError:
                skipped_messages.append(
                    f"Skipped playlist entry #{index} from {user_input}: missing id."
                )
                continue
            targets.append(
                DownloadTarget(original_input=user_input, info=info, source_query=source_query)
            )
        return ResolutionResult(targets=targets, skipped_messages=skipped_messages)

    info = VideoInfo.from_yt_dlp(payload, original_url=user_input)
    targets.append(DownloadTarget(original_input=user_input, info=info, source_query=source_query))
    return ResolutionResult(targets=targets, skipped_messages=skipped_messages)


def resolve_targets(inputs: list[str], *, source_query: str | None = None) -> ResolutionResult:
    all_targets: list[DownloadTarget] = []
    all_skipped_messages: list[str] = []
    for user_input in inputs:
        payload = fetch_info(user_input)
        resolution = resolve_payload(user_input, payload, source_query=source_query)
        all_targets.extend(resolution.targets)
        all_skipped_messages.extend(resolution.skipped_messages)
    return ResolutionResult(targets=all_targets, skipped_messages=all_skipped_messages)


def download_target(
    target: DownloadTarget,
    settings: Settings,
    *,
    mode: str = "video",
    fetch_subs: bool = False,
    auto_subs: bool = False,
    sponsorblock: bool = False,
    sponsorblock_remove: bool = False,
) -> DownloadExecution | None:
    yt_dlp = command_path()

    # Validate config-sourced values before passing to yt-dlp CLI.
    format_value = settings.audio_format if mode == "audio" else settings.video_format
    if not _FORMAT_ALLOWLIST_RE.fullmatch(format_value):
        label = "audio_format" if mode == "audio" else "video_format"
        raise InvalidInputError(f"Invalid characters in {label}.")
    if fetch_subs and not _valid_subtitle_languages(settings.subtitle_languages):
        raise InvalidInputError("Invalid characters in subtitle_languages.")

    output_template = build_output_template(settings.download_root, target.info)
    args = [
        yt_dlp,
        "--quiet",
        "--no-warnings",
        "--print",
        "after_move:filepath",
        "--output",
        str(output_template),
        "--download-archive",
        str(settings.archive_file),
        "--format",
        format_value,
    ]

    if mode == "audio":
        args.extend(["--extract-audio", "--audio-format", "mp3"])

    if settings.write_thumbnail:
        args.append("--write-thumbnail")
    if settings.write_description:
        args.append("--write-description")
    if settings.write_info_json:
        args.append("--write-info-json")
    if settings.embed_metadata:
        args.append("--embed-metadata")
    if settings.embed_thumbnail:
        args.append("--embed-thumbnail")

    if fetch_subs:
        args.append("--write-subs")
        if auto_subs:
            args.append("--write-auto-subs")
        args.extend(["--sub-langs", settings.subtitle_languages])

    if sponsorblock_remove:
        args.extend(["--sponsorblock-remove", "all"])
    elif sponsorblock:
        # Mark-only is deliberately the default; destructive removal requires
        # the separate explicit sponsorblock_remove flag.
        args.extend(["--sponsorblock-mark", "all"])

    args.append(normalize_target(target.info.webpage_url))
    execution = _run_download(args)
    if execution is None:
        return None

    # Verify the output path is within the expected download root.
    try:
        execution.output_path.resolve().relative_to(settings.download_root.resolve())
    except ValueError as exc:
        raise InvalidInputError(
            "Download output path is outside the download root directory."
        ) from exc
    protect_private_tree(execution.output_path.parent)

    return DownloadExecution(
        output_path=execution.output_path,
        stdout=execution.stdout,
        info_json_path=discover_info_json(execution.output_path),
    )


def record_live(
    target: DownloadTarget,
    settings: Settings,
    *,
    live_from_start: bool = True,
    wait_seconds: int = 0,
) -> DownloadExecution:
    """Record one live stream with bounded, typed live options."""
    if not 0 <= wait_seconds <= 86_400:
        raise InvalidInputError("Live wait seconds must be between 0 and 86400.")
    if not _FORMAT_ALLOWLIST_RE.fullmatch(settings.video_format):
        raise InvalidInputError("Invalid characters in video_format.")
    output_template = build_output_template(settings.download_root, target.info)
    args = [
        command_path(),
        "--quiet",
        "--no-warnings",
        "--print",
        "after_move:filepath",
        "--output",
        str(output_template),
        "--download-archive",
        str(settings.archive_file),
        "--format",
        settings.video_format,
        "--write-info-json",
    ]
    if live_from_start:
        args.append("--live-from-start")
    if wait_seconds:
        args.extend(["--wait-for-video", str(wait_seconds)])
    args.append(normalize_target(target.info.webpage_url))
    execution = _run_download(args)
    if execution is None:
        raise ExternalCommandError("yt-dlp did not produce a live recording.")
    try:
        execution.output_path.resolve().relative_to(settings.download_root.resolve())
    except ValueError as exc:
        raise InvalidInputError("Live output path is outside the download root directory.") from exc
    protect_private_tree(execution.output_path.parent)
    return DownloadExecution(
        output_path=execution.output_path,
        stdout=execution.stdout,
        info_json_path=discover_info_json(execution.output_path),
    )
