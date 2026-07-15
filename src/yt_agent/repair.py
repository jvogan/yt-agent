"""Conservative repairs for derived catalog and cache state."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from yt_agent.catalog import CatalogStore
from yt_agent.config import Settings
from yt_agent.indexer import index_refresh
from yt_agent.verify import verify_library

__all__ = ["RepairAction", "RepairReport", "repair_library"]


@dataclass(frozen=True)
class RepairAction:
    action: str
    status: str
    path: str | None = None
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class RepairReport:
    applied: bool
    actions: tuple[RepairAction, ...]

    def as_dict(self) -> dict[str, Any]:
        applied_actions = sum(item.status == "applied" for item in self.actions)
        return {
            "schema_version": 1,
            "command": "repair",
            "status": "ok" if self.applied else "noop",
            "applied": self.applied,
            "summary": {
                "actions": len(self.actions),
                "planned": len(self.actions) - applied_actions,
                "applied": applied_actions,
            },
            "actions": [item.as_dict() for item in self.actions],
            "media_deleted": 0,
        }


def _rebuild_fts(settings: Settings) -> None:
    store = CatalogStore(settings.catalog_file)
    store.ensure_schema()
    with store.connect() as conn:
        conn.execute("DELETE FROM chapter_fts")
        conn.execute(
            "INSERT INTO chapter_fts (video_id, chapter_id, title) "
            "SELECT video_id, chapter_id, title FROM chapters"
        )
        conn.execute("DELETE FROM transcript_fts")
        conn.execute(
            "INSERT INTO transcript_fts (video_id, segment_id, text) "
            "SELECT video_id, segment_id, text FROM transcript_segments"
        )
        conn.execute("DELETE FROM comment_fts")
        conn.execute(
            "INSERT INTO comment_fts (video_id, comment_id, text) "
            "SELECT video_id, comment_id, text FROM comments"
        )


def _remove_stale_cache(settings: Settings, path: Path) -> None:
    configured_root = settings.catalog_file.parent / "subtitle-cache"
    if configured_root.is_symlink():
        raise OSError(f"Refusing symlinked subtitle-cache root: {configured_root}")
    root = configured_root.resolve()
    if path.parent.resolve() != root:
        raise OSError(f"Refusing cache path outside subtitle-cache: {path}")
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def repair_library(settings: Settings, *, apply: bool = False) -> RepairReport:
    """Plan or apply safe repairs; this function never deletes media files."""
    verification = verify_library(settings)
    codes = {item.code for item in verification.findings}
    planned: list[tuple[str, Path | None, str]] = []
    if codes & {
        "chapter_fts_missing",
        "chapter_fts_orphan",
        "transcript_fts_missing",
        "transcript_fts_orphan",
        "comment_fts_missing",
        "comment_fts_orphan",
    }:
        planned.append(("rebuild_fts", None, "Rebuild derived full-text indexes."))
    if verification.manifest_records and codes & {
        "catalog_missing",
        "catalog_schema_missing",
        "manifest_not_cataloged",
    }:
        planned.append(("reindex_manifest", None, "Reindex valid manifest records."))
    stale_cache_paths = sorted(
        {
            Path(item.path)
            for item in verification.findings
            if item.code == "stale_subtitle_cache" and item.path
        }
    )
    for cache_path in stale_cache_paths:
        planned.append(
            ("remove_stale_subtitle_cache", cache_path, "Remove orphaned subtitle cache.")
        )

    if not apply:
        return RepairReport(
            False,
            tuple(
                RepairAction(action, "planned", str(path) if path else None, detail)
                for action, path, detail in planned
            ),
        )

    actions: list[RepairAction] = []
    for action, action_path, detail in planned:
        if action == "reindex_manifest":
            index_refresh(settings)
        elif action == "rebuild_fts":
            _rebuild_fts(settings)
        elif action_path is not None:
            _remove_stale_cache(settings, action_path)
        actions.append(
            RepairAction(
                action,
                "applied",
                str(action_path) if action_path else None,
                detail,
            )
        )
    return RepairReport(True, tuple(actions))
