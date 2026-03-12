"""Subtitle acquisition and transcript parsing helpers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Iterable

from yt_agent.errors import ExternalCommandError
from yt_agent.models import SubtitleTrack, TranscriptSegment
from yt_agent.security import ensure_private_directory, protect_private_tree
from yt_agent.yt_dlp import command_path, normalize_target

TIMECODE_RE = re.compile(
    r"(?:(?P<hours>\d+):)?(?P<minutes>\d{2}):(?P<seconds>\d{2})[.,](?P<millis>\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def _parse_timestamp(value: str) -> float:
    match = TIMECODE_RE.search(value.strip())
    if match is None:
        raise ValueError(f"Unsupported subtitle timestamp: {value}")
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = int(match.group("seconds"))
    millis = int(match.group("millis"))
    return (hours * 3600) + (minutes * 60) + seconds + (millis / 1000)


def _normalize_text(lines: Iterable[str]) -> str:
    cleaned = " ".join(line.strip() for line in lines if line.strip())
    cleaned = TAG_RE.sub("", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _parse_vtt(path: Path) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore"))
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if not lines or lines[0].startswith("WEBVTT"):
            continue
        if "-->" not in lines[0]:
            if len(lines) < 2 or "-->" not in lines[1]:
                continue
            lines = lines[1:]
        start_raw, end_raw = [part.strip() for part in lines[0].split("-->", 1)]
        text = _normalize_text(lines[1:])
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                segment_index=len(segments),
                start_seconds=_parse_timestamp(start_raw),
                end_seconds=_parse_timestamp(end_raw),
                text=text,
            )
        )
    return segments


def _parse_srt(path: Path) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8", errors="ignore"))
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        if "-->" in lines[0]:
            timing = lines[0]
            body = lines[1:]
        elif len(lines) >= 3 and "-->" in lines[1]:
            timing = lines[1]
            body = lines[2:]
        else:
            continue
        start_raw, end_raw = [part.strip() for part in timing.split("-->", 1)]
        text = _normalize_text(body)
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                segment_index=len(segments),
                start_seconds=_parse_timestamp(start_raw),
                end_seconds=_parse_timestamp(end_raw),
                text=text,
            )
        )
    return segments


def parse_subtitle_file(path: Path) -> list[TranscriptSegment]:
    suffix = path.suffix.casefold()
    if suffix == ".vtt":
        return _parse_vtt(path)
    if suffix == ".srt":
        return _parse_srt(path)
    raise ValueError(f"Unsupported subtitle format: {path.suffix}")


def infer_subtitle_track(
    path: Path,
    *,
    manual_languages: set[str] | None = None,
    auto_languages: set[str] | None = None,
) -> SubtitleTrack:
    manual_languages = manual_languages or set()
    auto_languages = auto_languages or set()
    stem_parts = path.stem.split(".")
    lang = stem_parts[-1] if len(stem_parts) > 1 else "unknown"
    is_auto = lang in auto_languages and lang not in manual_languages
    source = "indexed-sidecar"
    return SubtitleTrack(
        lang=lang,
        source=source,
        is_auto=is_auto,
        format=path.suffix.lstrip("."),
        file_path=path,
    )


def fetch_subtitle_sidecars(
    target: str,
    destination: Path,
    *,
    languages: list[str],
    allow_auto_subs: bool,
) -> tuple[Path | None, list[Path]]:
    ensure_private_directory(destination)
    output_template = destination / "%(id)s.%(ext)s"

    def _run(write_auto_subs: bool) -> None:
        args = [
            command_path(),
            "--skip-download",
            "--no-warnings",
            "--write-info-json",
            "--sub-langs",
            ",".join(languages),
            "--sub-format",
            "vtt",
            "--convert-subs",
            "vtt",
            "--output",
            str(output_template),
        ]
        args.append("--write-auto-subs" if write_auto_subs else "--write-subs")
        args.append(normalize_target(target))
        completed = subprocess.run(args, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            raise ExternalCommandError("yt-dlp failed while fetching subtitles.", stderr=stderr)
        protect_private_tree(destination)

    before = {path.name for path in destination.iterdir()} if destination.exists() else set()
    _run(write_auto_subs=False)
    after_manual = {path.name for path in destination.iterdir()}
    info_json = next(iter(sorted(destination.glob("*.info.json"))), None)
    subtitle_paths = sorted(destination.glob("*.vtt"))
    if subtitle_paths or not allow_auto_subs:
        return info_json, subtitle_paths

    manual_only = after_manual - before
    for path_name in manual_only:
        candidate = destination / path_name
        if candidate.exists() and not candidate.name.endswith(".info.json"):
            candidate.unlink()

    _run(write_auto_subs=True)
    info_json = next(iter(sorted(destination.glob("*.info.json"))), None)
    return info_json, sorted(destination.glob("*.vtt"))
