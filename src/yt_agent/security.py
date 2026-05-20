"""Security and sanitization helpers."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = [
    "ANSI_ESCAPE_RE",
    "CONTROL_CHAR_RE",
    "WHITESPACE_RE",
    "POSIX_PRIVATE_DIR_MODE",
    "POSIX_PRIVATE_FILE_MODE",
    "sanitize_terminal_text",
    "sanitize_json_payload",
    "atomic_write_text",
    "ensure_private_directory",
    "ensure_private_file",
    "protect_private_tree",
    "operation_lock",
]


ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:"
    r"\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|[@-Z\\-_]"
    r")"
)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
WHITESPACE_RE = re.compile(r"\s+")

POSIX_PRIVATE_DIR_MODE = 0o700
POSIX_PRIVATE_FILE_MODE = 0o600


def _private_file_flags(flags: int) -> int:
    if os.name == "nt":
        return flags
    return flags | getattr(os, "O_NOFOLLOW", 0)


def sanitize_terminal_text(value: object) -> str:
    """Strip terminal control bytes and collapse line-breaking whitespace."""

    text = str(value)
    text = ANSI_ESCAPE_RE.sub("", text)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = CONTROL_CHAR_RE.sub("", text)
    return WHITESPACE_RE.sub(" ", text).strip()


def sanitize_json_payload(value: Any) -> Any:
    """Recursively sanitize strings before serializing JSON to the terminal."""

    if isinstance(value, str):
        return sanitize_terminal_text(value)
    if isinstance(value, Mapping):
        return {
            sanitize_terminal_text(key): sanitize_json_payload(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_json_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json_payload(item) for item in value]
    return value


def _chmod(path: Path, mode: int) -> None:
    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except FileNotFoundError:
        return


def _open_private_file_descriptor(path: Path, flags: int) -> int:
    ensure_private_directory(path.parent)
    if path.is_symlink():
        raise OSError(f"Refusing to operate on symlink: {path}")
    fd = os.open(
        path,
        _private_file_flags(flags | os.O_CREAT),
        POSIX_PRIVATE_FILE_MODE,
    )
    try:
        if os.name != "nt":
            stat_result = os.fstat(fd)
            if stat_result.st_nlink != 1:
                raise OSError(f"Refusing to operate on hardlinked file: {path}")
            os.fchmod(fd, POSIX_PRIVATE_FILE_MODE)
    except Exception:
        os.close(fd)
        raise
    return fd


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace a private text file without following existing symlinks."""

    ensure_private_directory(path.parent)
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
        _chmod(temp_path, POSIX_PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _chmod(path, POSIX_PRIVATE_FILE_MODE)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def ensure_private_directory(path: Path) -> None:
    """Create a private directory for local state on POSIX systems."""

    if path.is_symlink():
        raise OSError(f"Refusing to operate on symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=POSIX_PRIVATE_DIR_MODE)
    _chmod(path, POSIX_PRIVATE_DIR_MODE)


def ensure_private_file(path: Path) -> None:
    """Create a private file for local state on POSIX systems."""

    fd = _open_private_file_descriptor(path, os.O_RDWR)
    os.close(fd)


def protect_private_tree(path: Path) -> None:
    """Best-effort permission hardening for a local state directory tree."""

    if not path.exists() or path.is_symlink():
        return
    ensure_private_directory(path)
    for candidate in path.rglob("*"):
        if candidate.is_symlink():
            continue
        if candidate.is_dir():
            ensure_private_directory(candidate)
        elif candidate.is_file():
            _chmod(candidate, POSIX_PRIVATE_FILE_MODE)


@contextmanager
def operation_lock(path: Path) -> Iterator[None]:
    """Acquire an exclusive non-blocking operation lock for mutating workflows."""

    from yt_agent.errors import StateLockError

    fd = _open_private_file_descriptor(path, os.O_RDWR)
    handle = os.fdopen(fd, "a+", encoding="utf-8")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            except OSError as exc:
                raise StateLockError("Another yt-agent operation is already running.") from exc
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise StateLockError("Another yt-agent operation is already running.") from exc
        acquired = True
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()
