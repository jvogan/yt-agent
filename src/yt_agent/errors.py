"""Application-specific errors and exit codes."""

from __future__ import annotations

from enum import IntEnum


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


class DependencyError(YtAgentError):
    """Raised when a required system tool is unavailable."""

    exit_code = ExitCode.DEPENDENCY


class InvalidInputError(YtAgentError):
    """Raised for invalid command input or unsupported metadata."""

    exit_code = ExitCode.INPUT


class ConfigError(YtAgentError):
    """Raised for invalid configuration."""

    exit_code = ExitCode.CONFIG


class SelectionError(YtAgentError):
    """Raised for invalid interactive selections."""

    exit_code = ExitCode.INPUT


class ExternalCommandError(YtAgentError):
    """Raised when an external command fails."""

    exit_code = ExitCode.EXTERNAL

    def __init__(self, message: str, *, stderr: str | None = None) -> None:
        super().__init__(message)
        self.stderr = stderr


class StateLockError(YtAgentError):
    """Raised when another yt-agent mutation is already running."""

    exit_code = ExitCode.BUSY
