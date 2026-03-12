import os
import stat
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from yt_agent.archive import ensure_archive_file
from yt_agent.catalog import CatalogStore
from yt_agent.errors import StateLockError
from yt_agent.manifest import ensure_manifest_file
from yt_agent.security import (
    _chmod,
    ensure_private_directory,
    ensure_private_file,
    operation_lock,
    protect_private_tree,
    sanitize_json_payload,
    sanitize_terminal_text,
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission modes only")
def test_private_helpers_apply_expected_modes(tmp_path: Path) -> None:
    private_dir = tmp_path / "state"
    private_file = private_dir / "demo.txt"

    ensure_private_directory(private_dir)
    ensure_private_file(private_file)

    assert stat.S_IMODE(private_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(private_file.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission modes only")
def test_catalog_and_state_files_are_private(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    archive_path = state_root / "archive.txt"
    manifest_path = state_root / "downloads.jsonl"
    catalog_path = state_root / "catalog.sqlite"

    ensure_archive_file(archive_path)
    ensure_manifest_file(manifest_path)
    CatalogStore(catalog_path).ensure_schema()

    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(archive_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(catalog_path.stat().st_mode) == 0o600


def test_sanitize_terminal_text_strips_control_sequences() -> None:
    value = "hello\x1b[31m\r\nworld\t\x07"
    assert sanitize_terminal_text(value) == "hello world"


def test_sanitize_json_payload_recursively_cleans_nested_strings() -> None:
    payload = {
        "ti\tle": "bad\nline\x1b[31m",
        "items": ["ok", {"nested": "more\r\nnoise"}],
        "tuple": ("alpha\tbeta", 2),
    }

    assert sanitize_json_payload(payload) == {
        "ti le": "bad line",
        "items": ["ok", {"nested": "more noise"}],
        "tuple": ["alpha beta", 2],
    }


def test_chmod_ignores_missing_files() -> None:
    _chmod(Path("/definitely/missing/file"), 0o600)


def test_chmod_is_noop_on_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[Path, int]] = []
    monkeypatch.setattr("yt_agent.security.os.name", "nt", raising=False)
    monkeypatch.setattr("yt_agent.security.os.chmod", lambda path, mode: calls.append((Path(path), mode)))

    _chmod(tmp_path / "demo.txt", 0o600)

    assert calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission modes only")
def test_protect_private_tree_reapplies_private_modes(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    nested_dir = state_root / "nested"
    nested_file = nested_dir / "demo.txt"
    nested_dir.mkdir(parents=True, mode=0o755)
    nested_file.write_text("demo", encoding="utf-8")
    state_root.chmod(0o755)
    nested_dir.chmod(0o755)
    nested_file.chmod(0o644)

    protect_private_tree(state_root)

    assert stat.S_IMODE(state_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(nested_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(nested_file.stat().st_mode) == 0o600


def test_protect_private_tree_ignores_missing_paths(tmp_path: Path) -> None:
    protect_private_tree(tmp_path / "missing")


def test_operation_lock_raises_when_already_held(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / "operation.lock"

    with operation_lock(lock_path):
        with pytest.raises(StateLockError, match="already running"):
            with operation_lock(lock_path):
                pass


def test_operation_lock_overwrites_stale_lock_contents(tmp_path: Path) -> None:
    lock_path = tmp_path / "state" / "operation.lock"
    ensure_private_file(lock_path)
    lock_path.write_text("999999", encoding="utf-8")

    with operation_lock(lock_path):
        assert lock_path.read_text(encoding="utf-8") == str(os.getpid())


def test_operation_lock_windows_branch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_NBLCK=1,
        LK_UNLCK=2,
        locking=lambda fd, mode, size: calls.append((mode, size)),
    )
    monkeypatch.setattr("yt_agent.security.os.name", "nt", raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    lock_path = tmp_path / "state" / "operation.lock"
    with operation_lock(lock_path):
        assert lock_path.read_text(encoding="utf-8") == str(os.getpid())

    assert calls == [(1, 1), (2, 1)]


def test_operation_lock_windows_branch_raises_state_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_lock(fd: int, mode: int, size: int) -> None:
        raise OSError("busy")

    fake_msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=fail_lock)
    monkeypatch.setattr("yt_agent.security.os.name", "nt", raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    with pytest.raises(StateLockError, match="already running"):
        with operation_lock(tmp_path / "state" / "operation.lock"):
            pass


def test_operation_lock_ignores_windows_unlock_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []

    def fake_lock(fd: int, mode: int, size: int) -> None:
        calls.append((mode, size))
        if mode == 2:
            raise OSError("unlock failed")

    fake_msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=fake_lock)
    monkeypatch.setattr("yt_agent.security.os.name", "nt", raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)

    with operation_lock(tmp_path / "state" / "operation.lock"):
        pass

    assert calls == [(1, 1), (2, 1)]
