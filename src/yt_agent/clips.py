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
    start_seconds: float
    end_seconds: float
    used_remote_fallback: bool


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


def _extract_resolved_clip(
    settings: Settings,
    info: VideoInfo,
    *,
    media_path: Path | None,
    label: str,
    start_seconds: float,
    end_seconds: float,
    mode: str,
    prefer_remote: bool,
) -> ClipExtraction:
    output_path = build_clip_output_path(
        settings.clips_root,
        info,
        label=label,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        extension="mp4" if mode == "accurate" else (media_path.suffix.lstrip(".") if media_path else "mp4"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if media_path and media_path.exists() and not prefer_remote:
        ffmpeg = _ffmpeg_path()
        args = [ffmpeg, "-y", "-ss", f"{start_seconds:.3f}", "-to", f"{end_seconds:.3f}", "-i", str(media_path)]
        if mode == "fast":
            args.extend(["-c", "copy"])
        else:
            args.extend(["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"])
        args.append(str(output_path))
        _run(args, "ffmpeg clip extraction failed.")
        return ClipExtraction(
            output_path=output_path,
            source="local",
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            used_remote_fallback=False,
        )

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
        info.webpage_url,
    ]
    _run(args, "yt-dlp remote clip extraction failed.")
    remote_output = next(iter(sorted(output_path.parent.glob(f"{output_path.stem}.*"))), output_path)
    return ClipExtraction(
        output_path=remote_output,
        source="remote",
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        used_remote_fallback=True,
    )


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
    return _extract_resolved_clip(
        settings,
        _video_info_from_hit(hit),
        media_path=hit.output_path,
        label=hit.source,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        mode=mode,
        prefer_remote=prefer_remote,
    )


def extract_clip_for_range(
    settings: Settings,
    *,
    video_id: str,
    start_seconds: float,
    end_seconds: float,
    mode: str = "fast",
    prefer_remote: bool = False,
) -> ClipExtraction:
    if mode not in {"fast", "accurate"}:
        raise InvalidInputError("Clip mode must be 'fast' or 'accurate'.")
    if end_seconds <= start_seconds:
        raise InvalidInputError("--end-seconds must be greater than --start-seconds.")

    catalog = CatalogStore(settings.catalog_file)
    catalog.ensure_schema()
    video = catalog.get_video(video_id)
    if video is None:
        raise InvalidInputError(f"Video id '{video_id}' is not in the catalog.")

    info = VideoInfo(
        video_id=video.video_id,
        title=video.title,
        channel=video.channel,
        upload_date=video.upload_date,
        duration_seconds=video.duration_seconds,
        extractor_key=video.extractor_key,
        webpage_url=video.webpage_url,
        original_url=video.requested_input,
    )
    return _extract_resolved_clip(
        settings,
        info,
        media_path=video.output_path,
        label="range",
        start_seconds=max(0.0, start_seconds),
        end_seconds=end_seconds,
        mode=mode,
        prefer_remote=prefer_remote,
    )
