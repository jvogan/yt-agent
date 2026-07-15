"""Read-only consistency checks for local yt-agent state."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from yt_agent.config import Settings

__all__ = ["VerifyFinding", "VerifyReport", "verify_library"]


@dataclass(frozen=True)
class VerifyFinding:
    """One actionable inconsistency found by :func:`verify_library`."""

    severity: str
    code: str
    message: str
    video_id: str | None = None
    path: str | None = None
    line: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class VerifyReport:
    """Structured result of a read-only local-state audit."""

    deep: bool
    findings: tuple[VerifyFinding, ...]
    manifest_records: int
    catalog_videos: int
    media_checked: int

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    @property
    def healthy(self) -> bool:
        return self.error_count == 0 and self.warning_count == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "command": "verify",
            "status": "ok" if self.healthy else "issues",
            "healthy": self.healthy,
            "deep": self.deep,
            "summary": {
                "errors": self.error_count,
                "warnings": self.warning_count,
                "manifest_records": self.manifest_records,
                "catalog_videos": self.catalog_videos,
                "media_checked": self.media_checked,
            },
            "findings": [item.as_dict() for item in self.findings],
        }


def _finding(
    findings: list[VerifyFinding],
    severity: str,
    code: str,
    message: str,
    *,
    video_id: str | None = None,
    path: Path | str | None = None,
    line: int | None = None,
) -> None:
    findings.append(
        VerifyFinding(
            severity=severity,
            code=code,
            message=message,
            video_id=video_id,
            path=str(path) if path is not None else None,
            line=line,
        )
    )


def _audit_manifest(path: Path, findings: list[VerifyFinding]) -> tuple[int, set[str]]:
    if not path.exists():
        _finding(
            findings, "warning", "manifest_missing", "Manifest file does not exist.", path=path
        )
        return 0, set()

    records = 0
    video_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            _finding(
                findings,
                "error",
                "manifest_invalid_json",
                "Manifest line is not valid JSON.",
                path=path,
                line=line_number,
            )
            continue
        if not isinstance(payload, dict):
            _finding(
                findings,
                "error",
                "manifest_invalid_record",
                "Manifest line must contain a JSON object.",
                path=path,
                line=line_number,
            )
            continue
        video_id = str(payload.get("video_id") or "").strip()
        if not video_id:
            _finding(
                findings,
                "error",
                "manifest_missing_video_id",
                "Manifest record has no video_id.",
                path=path,
                line=line_number,
            )
            continue
        records += 1
        video_ids.add(video_id)
    return records, video_ids


def _archive_keys(path: Path, findings: list[VerifyFinding]) -> set[tuple[str, str]]:
    if not path.exists():
        _finding(
            findings,
            "warning",
            "archive_missing",
            "Download archive does not exist.",
            path=path,
        )
        return set()
    keys: set[tuple[str, str]] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        extractor, separator, video_id = line.partition(" ")
        if not separator or not extractor or not video_id:
            _finding(
                findings,
                "warning",
                "archive_invalid_entry",
                "Archive entry is not an extractor and video-id pair.",
                path=path,
                line=line_number,
            )
            continue
        keys.add((extractor.casefold(), video_id))
    return keys


def _existing_tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
    return {str(row[0]) for row in rows}


def _audit_path(
    findings: list[VerifyFinding],
    *,
    code: str,
    label: str,
    video_id: str,
    raw_path: Any,
    severity: str,
) -> Path | None:
    if not raw_path:
        return None
    path = Path(str(raw_path))
    if not path.is_file():
        _finding(
            findings,
            severity,
            code,
            f"Catalog references a missing {label} file.",
            video_id=video_id,
            path=path,
        )
        return None
    return path


def _audit_fts(conn: sqlite3.Connection, tables: set[str], findings: list[VerifyFinding]) -> None:
    checks = (
        (
            """
                SELECT s.video_id, s.chapter_id
                FROM chapters AS s
                WHERE NOT EXISTS (
                    SELECT 1 FROM chapter_fts AS f WHERE f.chapter_id = s.chapter_id
                )
            """,
            {"chapters", "chapter_fts"},
            "chapter_fts_missing",
            "Catalog chapter has no matching FTS row",
        ),
        (
            """
                SELECT f.video_id, f.chapter_id
                FROM chapter_fts AS f
                WHERE NOT EXISTS (
                    SELECT 1 FROM chapters AS s WHERE s.chapter_id = f.chapter_id
                )
            """,
            {"chapters", "chapter_fts"},
            "chapter_fts_orphan",
            "Chapter FTS row has no matching catalog chapter",
        ),
        (
            """
                SELECT s.video_id, s.segment_id
                FROM transcript_segments AS s
                WHERE NOT EXISTS (
                    SELECT 1 FROM transcript_fts AS f WHERE f.segment_id = s.segment_id
                )
            """,
            {"transcript_segments", "transcript_fts"},
            "transcript_fts_missing",
            "Catalog transcript segment has no matching FTS row",
        ),
        (
            """
                SELECT f.video_id, f.segment_id
                FROM transcript_fts AS f
                WHERE NOT EXISTS (
                    SELECT 1 FROM transcript_segments AS s
                    WHERE s.segment_id = f.segment_id
                )
            """,
            {"transcript_segments", "transcript_fts"},
            "transcript_fts_orphan",
            "Transcript FTS row has no matching catalog segment",
        ),
        (
            """
                SELECT s.video_id, s.comment_id
                FROM comments AS s
                WHERE NOT EXISTS (
                    SELECT 1 FROM comment_fts AS f WHERE f.comment_id = s.comment_id
                )
            """,
            {"comments", "comment_fts"},
            "comment_fts_missing",
            "Catalog comment has no matching FTS row",
        ),
        (
            """
                SELECT f.video_id, f.comment_id
                FROM comment_fts AS f
                WHERE NOT EXISTS (
                    SELECT 1 FROM comments AS s WHERE s.comment_id = f.comment_id
                )
            """,
            {"comments", "comment_fts"},
            "comment_fts_orphan",
            "Comment FTS row has no matching catalog comment",
        ),
    )
    for sql, required_tables, code, message in checks:
        if not required_tables <= tables:
            continue
        for row in conn.execute(sql):
            _finding(
                findings,
                "error",
                code,
                f"{message} (id {row[1]}).",
                video_id=str(row[0]),
            )


def _probe_media(ffprobe: str, path: Path) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "ffprobe timed out after 30 seconds."
    if completed.returncode != 0:
        detail = completed.stderr.strip()
        return detail[:500] if detail else "ffprobe could not read the media file."
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return "ffprobe returned invalid JSON."
    if not isinstance(payload, dict) or not isinstance(payload.get("format"), dict):
        return "ffprobe returned no media format information."
    return None


def _audit_catalog(
    settings: Settings,
    findings: list[VerifyFinding],
    *,
    manifest_ids: set[str],
    archive_keys: set[tuple[str, str]],
    deep: bool,
) -> tuple[int, int]:
    if not settings.catalog_file.exists():
        _finding(
            findings,
            "warning",
            "catalog_missing",
            "Catalog database does not exist.",
            path=settings.catalog_file,
        )
        return 0, 0

    uri = f"file:{quote(str(settings.catalog_file.resolve()))}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        tables = _existing_tables(conn)
        if "videos" not in tables:
            _finding(
                findings,
                "error",
                "catalog_schema_missing",
                "Catalog is missing the videos table.",
                path=settings.catalog_file,
            )
            return 0, 0
        for required_table in (
            "chapters",
            "chapter_fts",
            "subtitle_tracks",
            "transcript_segments",
            "transcript_fts",
            "comments",
            "comment_fts",
        ):
            if required_table not in tables:
                _finding(
                    findings,
                    "error",
                    "catalog_table_missing",
                    f"Catalog is missing the {required_table} table.",
                    path=settings.catalog_file,
                )

        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).casefold() != "ok":
            _finding(
                findings,
                "error",
                "catalog_integrity_error",
                f"SQLite integrity check failed: {integrity[0] if integrity else 'no result'}.",
                path=settings.catalog_file,
            )

        video_rows = conn.execute(
            "SELECT video_id, extractor_key, output_path, info_json_path, downloaded_at FROM videos"
        ).fetchall()
        catalog_keys: set[tuple[str, str]] = set()
        catalog_ids: set[str] = set()
        media_paths: list[tuple[str, Path]] = []
        for video_id_raw, extractor_raw, output_raw, info_raw, downloaded_at in video_rows:
            video_id = str(video_id_raw)
            extractor = str(extractor_raw).casefold()
            catalog_ids.add(video_id)
            catalog_keys.add((extractor, video_id))
            media_path = _audit_path(
                findings,
                code="media_missing",
                label="media",
                video_id=video_id,
                raw_path=output_raw,
                severity="error",
            )
            if media_path is not None:
                media_paths.append((video_id, media_path))
            if downloaded_at and not output_raw:
                _finding(
                    findings,
                    "error",
                    "media_path_missing",
                    "Downloaded catalog video has no media path.",
                    video_id=video_id,
                )
            _audit_path(
                findings,
                code="info_json_missing",
                label="info JSON",
                video_id=video_id,
                raw_path=info_raw,
                severity="warning",
            )
            if output_raw and (extractor, video_id) not in archive_keys:
                _finding(
                    findings,
                    "warning",
                    "catalog_not_archived",
                    "Downloaded catalog video is absent from the download archive.",
                    video_id=video_id,
                )

        if "subtitle_tracks" in tables:
            for video_id_raw, file_path in conn.execute(
                "SELECT video_id, file_path FROM subtitle_tracks"
            ):
                _audit_path(
                    findings,
                    code="subtitle_missing",
                    label="subtitle",
                    video_id=str(video_id_raw),
                    raw_path=file_path,
                    severity="error",
                )

        _audit_fts(conn, tables, findings)

    subtitle_cache_root = settings.catalog_file.parent / "subtitle-cache"
    if subtitle_cache_root.is_dir():
        for cache_path in sorted(subtitle_cache_root.iterdir(), key=lambda path: path.name):
            if cache_path.name not in catalog_ids:
                _finding(
                    findings,
                    "warning",
                    "stale_subtitle_cache",
                    "Subtitle cache has no matching catalog video.",
                    path=cache_path,
                )

    for extractor, video_id in sorted(archive_keys - catalog_keys):
        _finding(
            findings,
            "warning",
            "archive_not_cataloged",
            f"Archive entry ({extractor}) has no matching catalog video.",
            video_id=video_id,
        )
    for video_id in sorted(manifest_ids - catalog_ids):
        _finding(
            findings,
            "warning",
            "manifest_not_cataloged",
            "Manifest video has no matching catalog row.",
            video_id=video_id,
        )

    if not deep:
        return len(video_rows), 0
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        _finding(
            findings,
            "warning",
            "ffprobe_missing",
            "Deep media verification requires ffprobe.",
        )
        return len(video_rows), 0
    for video_id, media_path in media_paths:
        problem = _probe_media(ffprobe, media_path)
        if problem is not None:
            _finding(
                findings,
                "error",
                "media_corrupt",
                problem,
                video_id=video_id,
                path=media_path,
            )
    return len(video_rows), len(media_paths)


def verify_library(settings: Settings, *, deep: bool = False) -> VerifyReport:
    """Audit local state without creating, modifying, or repairing any files."""
    findings: list[VerifyFinding] = []
    manifest_records, manifest_ids = _audit_manifest(settings.manifest_file, findings)
    archive_keys = _archive_keys(settings.archive_file, findings)
    catalog_videos, media_checked = _audit_catalog(
        settings,
        findings,
        manifest_ids=manifest_ids,
        archive_keys=archive_keys,
        deep=deep,
    )
    findings.sort(
        key=lambda item: (
            0 if item.severity == "error" else 1,
            item.code,
            item.video_id or "",
            item.line or 0,
        )
    )
    return VerifyReport(
        deep=deep,
        findings=tuple(findings),
        manifest_records=manifest_records,
        catalog_videos=catalog_videos,
        media_checked=media_checked,
    )
