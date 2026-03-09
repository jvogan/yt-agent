"""Clip search and extraction helpers."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from yt_agent.catalog import CatalogStore
from yt_agent.config import Settings
from yt_agent.errors import DependencyError, ExternalCommandError, InvalidInputError
from yt_agent.library import build_clip_output_path
from yt_agent.models import ClipSearchHit, VideoInfo
from yt_agent.yt_dlp import command_path, optional_tool_path


@dataclass(frozen=True)
class ClipExtraction:
    output_path: Path
    source: str


def _ffmpeg_path() -> str:
    path = optional_tool_path("ffmpeg")
    if path is None:
        raise DependencyError("ffmpeg is required for clip extraction.")
    return path


def _video_info_from_hit(hit: ClipSearchHit) -> VideoInfo:
    return VideoInfo(
        video_id=hit.video_id,
        title=hit.title,
        channel=hit.channel,
        upload_date=None,
        duration_seconds=None,
        extractor_key="youtube",
        webpage_url=hit.webpage_url,
        original_url=hit.webpage_url,
    )


def _run(args: list[str], message: str) -> None:
    completed = subprocess.run(args, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise ExternalCommandError(message, stderr=completed.stderr.strip())


def _clip_bounds(hit: ClipSearchHit, padding_before: float, padding_after: float) -> tuple[float, float]:
    start = max(0.0, hit.start_seconds - padding_before)
    end = max(start + 0.1, hit.end_seconds + padding_after)
    return start, end


def extract_clip(
    settings: Settings,
    result_id: str,
    *,
    padding_before: float = 0.0,
    padding_after: float = 0.0,
    mode: str = "fast",
    prefer_remote: bool = False,
) -> ClipExtraction:
    catalog = CatalogStore(settings.catalog_file)
    catalog.ensure_schema()
    hit = catalog.get_clip_hit(result_id)
    if hit is None:
        raise InvalidInputError(f"Unknown clip result: {result_id}")
    if mode not in {"fast", "accurate"}:
        raise InvalidInputError("Clip mode must be 'fast' or 'accurate'.")

    start_seconds, end_seconds = _clip_bounds(hit, padding_before, padding_after)
    output_path = build_clip_output_path(
        settings.clips_root,
        _video_info_from_hit(hit),
        label=hit.source,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        extension="mp4" if mode == "accurate" else (hit.output_path.suffix.lstrip(".") if hit.output_path else "mp4"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if hit.output_path and hit.output_path.exists() and not prefer_remote:
        ffmpeg = _ffmpeg_path()
        args = [ffmpeg, "-y", "-ss", f"{start_seconds:.3f}", "-to", f"{end_seconds:.3f}", "-i", str(hit.output_path)]
        if mode == "fast":
            args.extend(["-c", "copy"])
        else:
            args.extend(["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"])
        args.append(str(output_path))
        _run(args, "ffmpeg clip extraction failed.")
        return ClipExtraction(output_path=output_path, source="local")

    output_template = output_path.with_suffix(".%(ext)s")
    args = [
        command_path(),
        "--quiet",
        "--no-warnings",
        "--force-overwrites",
        "--download-sections",
        f"*{start_seconds:.3f}-{end_seconds:.3f}",
        "--output",
        str(output_template),
        hit.webpage_url,
    ]
    _run(args, "yt-dlp remote clip extraction failed.")
    remote_output = next(iter(sorted(output_path.parent.glob(f"{output_path.stem}.*"))), output_path)
    return ClipExtraction(output_path=remote_output, source="remote")

