"""Transcript export and opt-in local whisper.cpp transcription workflows."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yt_agent import __version__
from yt_agent.catalog import CatalogStore
from yt_agent.config import Settings
from yt_agent.errors import DependencyError, ExternalCommandError, InvalidInputError
from yt_agent.models import ChapterEntry, SubtitleTrack, TranscriptSegment
from yt_agent.security import atomic_write_artifact_text, protect_artifact_file
from yt_agent.transcripts import parse_subtitle_file
from yt_agent.yt_dlp import optional_tool_path

TRANSCRIPT_FORMATS = {"txt", "md", "json", "vtt", "srt"}
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class TranscriptDocument:
    video_id: str
    title: str
    track: SubtitleTrack
    segments: list[TranscriptSegment]
    chapters: list[ChapterEntry]


@dataclass(frozen=True)
class TranscriptionPlan:
    video_id: str
    media_path: Path
    model_path: Path
    language: str
    output_path: Path
    provenance_path: Path
    whisper_path: str
    ffmpeg_path: str


@dataclass(frozen=True)
class TranscriptionResult:
    plan: TranscriptionPlan
    segment_count: int


def load_transcript_document(
    store: CatalogStore, video_id: str, *, language: str | None = None
) -> TranscriptDocument:
    try:
        with store.connect(readonly=True) as conn:
            video = conn.execute(
                "SELECT video_id, title FROM videos WHERE video_id = ?", (video_id,)
            ).fetchone()
            if video is None:
                raise InvalidInputError(f"Video id '{video_id}' is not in the catalog.")
            params: list[object] = [video_id]
            language_clause = ""
            if language:
                language_clause = " AND lang = ?"
                params.append(language)
            track = conn.execute(
                f"""
                SELECT track_id, lang, source, is_auto, format, file_path
                FROM subtitle_tracks
                WHERE video_id = ?{language_clause}
                ORDER BY is_auto, lang, track_id
                LIMIT 1
                """,  # noqa: S608 -- clause is a fixed internal fragment
                params,
            ).fetchone()
            if track is None:
                suffix = f" for language '{language}'" if language else ""
                raise InvalidInputError(f"No indexed transcript exists for '{video_id}'{suffix}.")
            segment_rows = conn.execute(
                """
                SELECT segment_index, start_seconds, end_seconds, text
                FROM transcript_segments
                WHERE track_id = ?
                ORDER BY segment_index
                """,
                (track["track_id"],),
            ).fetchall()
            chapter_rows = conn.execute(
                """
                SELECT position, title, start_seconds, end_seconds
                FROM chapters WHERE video_id = ? ORDER BY position
                """,
                (video_id,),
            ).fetchall()
    except FileNotFoundError as exc:
        raise InvalidInputError("The catalog does not exist.") from exc
    return TranscriptDocument(
        video_id=str(video["video_id"]),
        title=str(video["title"]),
        track=SubtitleTrack(
            lang=str(track["lang"]),
            source=str(track["source"]),
            is_auto=bool(track["is_auto"]),
            format=str(track["format"]),
            file_path=Path(str(track["file_path"])),
        ),
        segments=[
            TranscriptSegment(
                segment_index=int(row["segment_index"]),
                start_seconds=float(row["start_seconds"]),
                end_seconds=float(row["end_seconds"]),
                text=str(row["text"]),
            )
            for row in segment_rows
        ],
        chapters=[
            ChapterEntry(
                position=int(row["position"]),
                title=str(row["title"]),
                start_seconds=float(row["start_seconds"]),
                end_seconds=float(row["end_seconds"]) if row["end_seconds"] is not None else None,
            )
            for row in chapter_rows
        ],
    )


def _timestamp(seconds: float, *, separator: str = ".") -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def _chapter_for(segment: TranscriptSegment, chapters: list[ChapterEntry]) -> str | None:
    for chapter in chapters:
        if segment.start_seconds < chapter.start_seconds:
            continue
        if chapter.end_seconds is None or segment.start_seconds < chapter.end_seconds:
            return chapter.title
    return None


def render_transcript(
    document: TranscriptDocument,
    format: str,  # noqa: A002
    *,
    timestamps: bool = True,
    group_chapters: bool = False,
) -> str:
    normalized = format.casefold()
    if normalized not in TRANSCRIPT_FORMATS:
        raise InvalidInputError(
            f"Transcript format must be one of: {', '.join(sorted(TRANSCRIPT_FORMATS))}."
        )
    if normalized == "vtt":
        cues = [
            f"{_timestamp(item.start_seconds)} --> {_timestamp(item.end_seconds)}\n{item.text}"
            for item in document.segments
        ]
        return "WEBVTT\n\n" + "\n\n".join(cues) + ("\n" if cues else "")
    if normalized == "srt":
        cues = [
            f"{index}\n{_timestamp(item.start_seconds, separator=',')} --> "
            f"{_timestamp(item.end_seconds, separator=',')}\n{item.text}"
            for index, item in enumerate(document.segments, start=1)
        ]
        return "\n\n".join(cues) + ("\n" if cues else "")
    if normalized == "json":
        payload: dict[str, Any] = {
            "schema_version": 1,
            "video_id": document.video_id,
            "title": document.title,
            "track": {
                "language": document.track.lang,
                "source": document.track.source,
                "is_auto": document.track.is_auto,
                "file_path": str(document.track.file_path),
            },
            "segments": [
                {
                    "index": item.segment_index,
                    "start_seconds": item.start_seconds,
                    "end_seconds": item.end_seconds,
                    "timestamp": _timestamp(item.start_seconds) if timestamps else None,
                    "chapter": _chapter_for(item, document.chapters) if group_chapters else None,
                    "text": item.text,
                }
                for item in document.segments
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    lines: list[str] = []
    if normalized == "md":
        lines.extend(
            [
                f"# {document.title}",
                "",
                f"Language: `{document.track.lang}` · Source: `{document.track.source}`",
                "",
            ]
        )
    previous_chapter: str | None = None
    for item in document.segments:
        chapter = _chapter_for(item, document.chapters) if group_chapters else None
        if chapter != previous_chapter and chapter is not None:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"## {chapter}" if normalized == "md" else f"# {chapter}")
            lines.append("")
        prefix = f"[{_timestamp(item.start_seconds)}] " if timestamps else ""
        lines.append(f"{prefix}{item.text}")
        previous_chapter = chapter
    return "\n".join(lines).rstrip() + "\n"


def plan_local_transcription(
    settings: Settings,
    video_id: str,
    *,
    model_path: Path,
    language: str = "auto",
    output_path: Path | None = None,
    force: bool = False,
) -> TranscriptionPlan:
    store = CatalogStore(settings.catalog_file, readonly=True)
    video = store.get_video(video_id, readonly=True)
    if video is None:
        raise InvalidInputError(f"Video id '{video_id}' is not in the catalog.")
    if video.output_path is None or not video.output_path.is_file():
        raise InvalidInputError(f"Video '{video_id}' has no available local media file.")
    try:
        video.output_path.resolve().relative_to(settings.download_root.resolve())
    except ValueError as exc:
        raise InvalidInputError(
            "Local media path is outside the configured download root."
        ) from exc
    if not model_path.is_file() or model_path.is_symlink():
        raise InvalidInputError("--model must reference an existing regular model file.")
    if not _LANGUAGE_RE.fullmatch(language):
        raise InvalidInputError("--language must be a non-empty language code or 'auto'.")
    whisper_path = optional_tool_path("whisper-cli")
    if whisper_path is None:
        raise DependencyError("whisper-cli is required for local transcription.")
    ffmpeg_path = optional_tool_path("ffmpeg")
    if ffmpeg_path is None:
        raise DependencyError("ffmpeg is required to prepare audio for local transcription.")
    resolved_output = output_path or (
        settings.catalog_file.parent / "generated-transcripts" / f"{video_id}.{language}.local.vtt"
    )
    if resolved_output.suffix.casefold() != ".vtt":
        raise InvalidInputError("Transcript output must use the .vtt extension.")
    if resolved_output.resolve() in {video.output_path.resolve(), model_path.resolve()}:
        raise InvalidInputError("Transcript output must not replace the media or model file.")
    provenance_exists = resolved_output.with_suffix(".provenance.json").exists()
    if (resolved_output.exists() or provenance_exists) and not force:
        raise InvalidInputError(
            "Generated transcript output already exists. Use --force to replace it."
        )
    if resolved_output.is_symlink():
        raise InvalidInputError("Transcript output must not be a symlink.")
    with store.connect(readonly=True) as conn:
        indexed_path = conn.execute(
            "SELECT 1 FROM subtitle_tracks WHERE video_id = ? AND file_path = ? LIMIT 1",
            (video_id, str(resolved_output)),
        ).fetchone()
    if indexed_path is not None and not force:
        raise InvalidInputError(
            "Transcript output belongs to an indexed track. Use --force to replace it."
        )
    return TranscriptionPlan(
        video_id=video_id,
        media_path=video.output_path,
        model_path=model_path,
        language=language,
        output_path=resolved_output,
        provenance_path=resolved_output.with_suffix(".provenance.json"),
        whisper_path=whisper_path,
        ffmpeg_path=ffmpeg_path,
    )


def _run(args: list[str], message: str) -> None:
    completed = subprocess.run(args, text=True, capture_output=True, check=False)  # noqa: S603
    if completed.returncode != 0:
        raise ExternalCommandError(message, stderr=completed.stderr[-4000:].strip())


def _index_generated_track(
    store: CatalogStore,
    plan: TranscriptionPlan,
    segments: list[TranscriptSegment],
    *,
    force: bool,
) -> None:
    with store.connect() as conn:
        existing = conn.execute(
            "SELECT track_id, source FROM subtitle_tracks WHERE video_id = ? AND file_path = ?",
            (plan.video_id, str(plan.output_path)),
        ).fetchall()
        if existing and not force:
            raise InvalidInputError(
                "Transcript path is already indexed. Use --force to replace it."
            )
        for row in existing:
            conn.execute(
                "DELETE FROM transcript_fts WHERE segment_id IN "
                "(SELECT segment_id FROM transcript_segments WHERE track_id = ?)",
                (row["track_id"],),
            )
            conn.execute("DELETE FROM subtitle_tracks WHERE track_id = ?", (row["track_id"],))
        cursor = conn.execute(
            """
            INSERT INTO subtitle_tracks (video_id, lang, source, is_auto, format, file_path)
            VALUES (?, ?, 'local-whisper', 0, 'vtt', ?)
            """,
            (plan.video_id, plan.language, str(plan.output_path)),
        )
        track_id = int(cursor.lastrowid or 0)
        for segment in segments:
            segment_cursor = conn.execute(
                """
                INSERT INTO transcript_segments
                    (track_id, video_id, segment_index, start_seconds, end_seconds, text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    track_id,
                    plan.video_id,
                    segment.segment_index,
                    segment.start_seconds,
                    segment.end_seconds,
                    segment.text,
                ),
            )
            conn.execute(
                "INSERT INTO transcript_fts (video_id, segment_id, text) VALUES (?, ?, ?)",
                (plan.video_id, int(segment_cursor.lastrowid or 0), segment.text),
            )


def execute_local_transcription(
    settings: Settings, plan: TranscriptionPlan, *, force: bool = False
) -> TranscriptionResult:
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yt-agent-asr-", dir=plan.output_path.parent) as raw:
        temp_root = Path(raw)
        audio_path = temp_root / "audio.wav"
        output_prefix = temp_root / "transcript"
        _run(
            [
                plan.ffmpeg_path,
                "-y",
                "-i",
                str(plan.media_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(audio_path),
            ],
            "ffmpeg failed while preparing transcription audio.",
        )
        _run(
            [
                plan.whisper_path,
                "-m",
                str(plan.model_path),
                "-f",
                str(audio_path),
                "-l",
                plan.language,
                "-ovtt",
                "-of",
                str(output_prefix),
                "-np",
            ],
            "whisper-cli failed while transcribing local media.",
        )
        generated = output_prefix.with_suffix(".vtt")
        if not generated.is_file():
            raise ExternalCommandError("whisper-cli produced no VTT transcript.")
        segments = parse_subtitle_file(generated)
        if not segments:
            raise ExternalCommandError("whisper-cli produced an empty VTT transcript.")
        generated.replace(plan.output_path)
        protect_artifact_file(plan.output_path)
    provenance = {
        "schema_version": 1,
        "generator": "whisper-cli",
        "yt_agent_version": __version__,
        "generated_at": datetime.now(UTC).isoformat(),
        "video_id": plan.video_id,
        "language": plan.language,
        "model_path": str(plan.model_path.resolve()),
        "media_path": str(plan.media_path.resolve()),
        "transcript_path": str(plan.output_path.resolve()),
        "segment_count": len(segments),
    }
    atomic_write_artifact_text(
        plan.provenance_path,
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    store = CatalogStore(settings.catalog_file)
    _index_generated_track(store, plan, segments, force=force)
    return TranscriptionResult(plan=plan, segment_count=len(segments))


__all__ = [
    "TRANSCRIPT_FORMATS",
    "TranscriptDocument",
    "TranscriptionPlan",
    "TranscriptionResult",
    "execute_local_transcription",
    "load_transcript_document",
    "plan_local_transcription",
    "render_transcript",
]
