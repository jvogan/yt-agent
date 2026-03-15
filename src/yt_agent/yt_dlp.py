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
]


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SUBPROCESS_TIMEOUT_SECONDS = 300  # 5 minutes
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
        parsed = urlsplit(stripped)
        host = (parsed.hostname or "").rstrip(".").casefold()
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


def _run_json(args: list[str]) -> dict[str, Any]:
    command = shlex.join(args)
    start_time = time.perf_counter()
    logger.debug("Running subprocess: %s", command)
    try:
        # Uses a resolved yt-dlp path and normalized arguments without invoking a shell.
        completed = subprocess.run(  # noqa: S603
            args, text=True, capture_output=True, check=False, timeout=_SUBPROCESS_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug("Subprocess timed out after %.2fms: %s", elapsed_ms, command)
        raise ExternalCommandError("yt-dlp timed out.") from exc
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    logger.debug(
        "Subprocess completed returncode=%s elapsed_ms=%.2f command=%s",
        completed.returncode,
        elapsed_ms,
        command,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise ExternalCommandError("yt-dlp failed while extracting metadata.", stderr=stderr)
    try:
        result: dict[str, Any] = json.loads(completed.stdout)
        return result
    except json.JSONDecodeError as exc:
        raise ExternalCommandError("yt-dlp returned invalid JSON metadata.") from exc


def _run_download(args: list[str]) -> DownloadExecution | None:
    command = shlex.join(args)
    start_time = time.perf_counter()
    logger.debug("Running subprocess: %s", command)
    try:
        # Uses a resolved yt-dlp path and normalized arguments without invoking a shell.
        completed = subprocess.run(  # noqa: S603
            args, text=True, capture_output=True, check=False, timeout=_SUBPROCESS_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.debug("Subprocess timed out after %.2fms: %s", elapsed_ms, command)
        raise ExternalCommandError("yt-dlp timed out.") from exc
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    logger.debug(
        "Subprocess completed returncode=%s elapsed_ms=%.2f command=%s",
        completed.returncode,
        elapsed_ms,
        command,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise ExternalCommandError("yt-dlp download failed.", stderr=stderr)

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    output_path = next(
        (Path(line) for line in reversed(lines) if not line.startswith("[debug]")), None
    )
    if output_path is None:
        return None  # yt-dlp exited 0 with no output — archive skip
    return DownloadExecution(output_path=output_path, stdout=completed.stdout)


def search(query: str, *, limit: int) -> list[VideoInfo]:
    yt_dlp = command_path()
    payload = _run_json([yt_dlp, "--dump-single-json", "--no-warnings", f"ytsearch{limit}:{query}"])
    entries = payload.get("entries") or []
    return [VideoInfo.from_yt_dlp(entry) for entry in entries if entry]


def fetch_info(target: str) -> dict[str, Any]:
    yt_dlp = command_path()
    normalized = normalize_target(target)
    return _run_json([yt_dlp, "--dump-single-json", "--no-warnings", normalized])


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
) -> DownloadExecution | None:
    yt_dlp = command_path()
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
        settings.audio_format if mode == "audio" else settings.video_format,
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

    args.append(target.info.webpage_url)
    execution = _run_download(args)
    if execution is None:
        return None
    return DownloadExecution(
        output_path=execution.output_path,
        stdout=execution.stdout,
        info_json_path=discover_info_json(execution.output_path),
    )
