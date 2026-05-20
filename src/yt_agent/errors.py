"""Application-specific errors and exit codes."""

from __future__ import annotations

import re
import sys
from enum import IntEnum
from pathlib import Path


class ExitCode(IntEnum):
    """Process exit codes for stable CLI behavior."""

    OK = 0
    DEPENDENCY = 3
    INPUT = 4
    CONFIG = 5
    EXTERNAL = 6
    BUSY = 7
    STORAGE = 8
    INTERRUPTED = 130


class YtAgentError(Exception):
    """Base application error."""

    exit_code = ExitCode.EXTERNAL


def dependency_install_hint(tool_name: str) -> str:
    """Return a platform-appropriate install command for a required tool."""

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


def _append_sentence(message: str, sentence: str) -> str:
    base = message.strip()
    if not sentence or sentence in base:
        return base
    if base and base[-1] not in ".!?":
        base = f"{base}."
    return f"{base} {sentence}".strip()


def _path_sentence(label: str, path: str | Path | None) -> str:
    if path is None:
        return ""
    return f"{label}: {Path(path)}."


_TOOL_NAME_RE = re.compile(r"\b(yt-dlp|ffmpeg|fzf|mpv)\b")


def _infer_tool_name(message: str) -> str | None:
    match = _TOOL_NAME_RE.search(message)
    return match.group(1) if match else None


class DependencyError(YtAgentError):
    """Raised when a required system tool is unavailable."""

    exit_code = ExitCode.DEPENDENCY

    def __init__(
        self,
        message: str,
        *,
        tool_name: str | None = None,
        install_hint: str | None = None,
    ) -> None:
        resolved_tool_name = tool_name or _infer_tool_name(message)
        resolved_install_hint = install_hint
        if resolved_install_hint is None and resolved_tool_name is not None:
            resolved_install_hint = dependency_install_hint(resolved_tool_name)
        if resolved_install_hint:
            message = _append_sentence(
                message,
                f"Install it with `{resolved_install_hint}` and retry.",
            )
        super().__init__(message)


class InvalidInputError(YtAgentError):
    """Raised for invalid command input or unsupported metadata."""

    exit_code = ExitCode.INPUT


class ConfigError(YtAgentError):
    """Raised for invalid configuration."""

    exit_code = ExitCode.CONFIG

    def __init__(self, message: str, *, config_path: str | Path | None = None) -> None:
        super().__init__(_append_sentence(message, _path_sentence("Config file", config_path)))


class SelectionError(YtAgentError):
    """Raised for invalid interactive selections."""

    exit_code = ExitCode.INPUT


class ExternalServiceError(YtAgentError):
    """Raised when an external tool or service fails."""

    exit_code = ExitCode.EXTERNAL

    def __init__(self, message: str, *, retry_hint: str | None = None) -> None:
        super().__init__(
            _append_sentence(
                message,
                retry_hint or "Retry the command. If it keeps failing, try again later.",
            )
        )


class ExternalCommandError(ExternalServiceError):
    """Raised when an external command fails."""

    def __init__(self, message: str, *, stderr: str | None = None) -> None:
        super().__init__(message)
        self.stderr = stderr


class StorageError(YtAgentError):
    """Raised for catalog or state storage failures."""

    exit_code = ExitCode.STORAGE

    def __init__(self, message: str, *, database_path: str | Path | None = None) -> None:
        final_message = _append_sentence(message, _path_sentence("Database", database_path))
        super().__init__(_append_sentence(final_message, "Check the database file and retry."))


class StateLockError(YtAgentError):
    """Raised when another yt-agent mutation is already running."""

    exit_code = ExitCode.BUSY
