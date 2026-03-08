"""Subprocess wrapper around yt-dlp."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_cli.config import Settings
from youtube_cli.errors import DependencyError, ExternalCommandError, InvalidInputError
from youtube_cli.library import build_output_template
from youtube_cli.models import DownloadTarget, VideoInfo

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class DownloadExecution:
    """Successful yt-dlp invocation details."""

    output_path: Path
    stdout: str


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
        return stripped
    if YOUTUBE_ID_RE.fullmatch(stripped):
        return f"https://www.youtube.com/watch?v={stripped}"
    raise InvalidInputError("Target must be a full URL or an 11-character YouTube video id.")


def _run_json(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise ExternalCommandError("yt-dlp failed while extracting metadata.", stderr=stderr)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ExternalCommandError("yt-dlp returned invalid JSON metadata.") from exc


def _run_download(args: list[str]) -> DownloadExecution:
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise ExternalCommandError("yt-dlp download failed.", stderr=stderr)

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    output_path = next((Path(line) for line in reversed(lines) if not line.startswith("[debug]")), None)
    if output_path is None:
        raise ExternalCommandError("yt-dlp completed without printing a final output path.")
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


def resolve_targets(inputs: list[str], *, source_query: str | None = None) -> ResolutionResult:
    targets: list[DownloadTarget] = []
    skipped_messages: list[str] = []
    for user_input in inputs:
        payload = fetch_info(user_input)
        entries = payload.get("entries")
        if isinstance(entries, list):
            for index, entry in enumerate(entries, start=1):
                if not entry:
                    skipped_messages.append(f"Skipped unavailable playlist entry #{index} from {user_input}.")
                    continue
                try:
                    info = VideoInfo.from_yt_dlp(entry, original_url=user_input)
                except InvalidInputError:
                    skipped_messages.append(f"Skipped playlist entry #{index} from {user_input}: missing id.")
                    continue
                targets.append(DownloadTarget(original_input=user_input, info=info, source_query=source_query))
            continue

        info = VideoInfo.from_yt_dlp(payload, original_url=user_input)
        targets.append(DownloadTarget(original_input=user_input, info=info, source_query=source_query))
    return ResolutionResult(targets=targets, skipped_messages=skipped_messages)


def download_target(target: DownloadTarget, settings: Settings) -> DownloadExecution:
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
        settings.video_format,
    ]

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

    args.append(target.info.webpage_url)
    return _run_download(args)
