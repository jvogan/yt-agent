"""Clip search and extraction helpers."""

from __future__ import annotations

import math
import subprocess
import uuid
from dataclasses import dataclass
from glob import escape as glob_escape
from pathlib import Path

from yt_agent.catalog import CatalogStore
from yt_agent.config import Settings
from yt_agent.errors import DependencyError, ExternalCommandError, InvalidInputError
from yt_agent.library import build_clip_output_path
from yt_agent.models import ClipSearchHit, VideoInfo
from yt_agent.security import ensure_private_directory, protect_private_tree
from yt_agent.yt_dlp import command_path, normalize_target, optional_tool_path

__all__ = [
    "ClipExtraction",
    "PlannedClipExtraction",
    "plan_clip",
    "plan_clip_for_range",
    "extract_clip",
    "extract_clip_for_range",
]

_SUBPROCESS_TIMEOUT_SECONDS = 300
_MAX_CAPTURED_STDERR_CHARS = 4000


@dataclass(frozen=True)
class ClipExtraction:
    output_path: Path
    source: str
    start_seconds: float
    end_seconds: float
    used_remote_fallback: bool


@dataclass(frozen=True)
class PlannedClipExtraction:
    output_path: Path
    source: str
    start_seconds: float
    end_seconds: float
    used_remote_fallback: bool
    output_template: Path | None = None


def _ffmpeg_path() -> str:
    path = optional_tool_path("ffmpeg")
    if path is None:
        raise DependencyError("ffmpeg is required for clip extraction.")
    return path


def _bounded_stderr(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= _MAX_CAPTURED_STDERR_CHARS:
        return stripped
    return f"{stripped[:_MAX_CAPTURED_STDERR_CHARS]}..."


def _require_resolved_under(path: Path, root: Path, message: str) -> None:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise InvalidInputError(message) from exc


def _validate_local_media_path(media_path: Path, download_root: Path) -> None:
    _require_resolved_under(
        media_path,
        download_root,
        "Local media path is outside the download root.",
    )
    if media_path.is_symlink() or not media_path.is_file():
        raise InvalidInputError("Local media path must be a regular file.")


def _validate_clip_output_path(output_path: Path, clips_root: Path) -> None:
    ensure_private_directory(output_path.parent)
    if output_path.is_symlink():
        raise InvalidInputError("Clip output path must not be a symlink.")
    _require_resolved_under(
        output_path,
        clips_root,
        "Clip output path is outside the clips root.",
    )


def _replace_clip_output(temp_path: Path, output_path: Path, clips_root: Path) -> None:
    _validate_clip_output_path(output_path, clips_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.replace(output_path)


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
    # Uses resolved tool paths and argument vectors without invoking a shell.
    try:
        completed = subprocess.run(  # noqa: S603
            args,
            text=True,
            capture_output=True,
            check=False,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExternalCommandError(f"{message} Command timed out.") from exc
    if completed.returncode != 0:
        raise ExternalCommandError(message, stderr=_bounded_stderr(completed.stderr))


def _clip_bounds(
    hit: ClipSearchHit, padding_before: float, padding_after: float
) -> tuple[float, float]:
    values = (hit.start_seconds, hit.end_seconds, padding_before, padding_after)
    if not all(math.isfinite(value) for value in values):
        raise InvalidInputError("Clip times and padding must be finite numbers.")
    if padding_before < 0 or padding_after < 0:
        raise InvalidInputError("Clip padding must not be negative.")
    start = max(0.0, hit.start_seconds - padding_before)
    end = max(start + 0.1, hit.end_seconds + padding_after)
    return start, end


def _plan_resolved_clip(
    settings: Settings,
    info: VideoInfo,
    *,
    media_path: Path | None,
    label: str,
    start_seconds: float,
    end_seconds: float,
    mode: str,
    prefer_remote: bool,
) -> PlannedClipExtraction:
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds):
        raise InvalidInputError("Clip start and end times must be finite numbers.")
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise InvalidInputError("Clip end time must be greater than a non-negative start time.")
    local_media = media_path if media_path and media_path.exists() else None
    if local_media is not None and not prefer_remote:
        output_path = build_clip_output_path(
            settings.clips_root,
            info,
            label=label,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            extension="mp4" if mode == "accurate" else local_media.suffix.lstrip("."),
        )
        return PlannedClipExtraction(
            output_path=output_path,
            source="local",
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            used_remote_fallback=False,
        )
    if not prefer_remote:
        raise InvalidInputError(
            "Local media is unavailable for this clip. Re-run with --remote-fallback."
        )
    output_path = build_clip_output_path(
        settings.clips_root,
        info,
        label=label,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        extension="mp4",
    )
    return PlannedClipExtraction(
        output_path=output_path,
        output_template=output_path.with_suffix(".%(ext)s"),
        source="remote",
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        used_remote_fallback=True,
    )


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
    plan = _plan_resolved_clip(
        settings,
        info,
        media_path=media_path,
        label=label,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        mode=mode,
        prefer_remote=prefer_remote,
    )
    output_path = plan.output_path
    _validate_clip_output_path(output_path, settings.clips_root)

    if plan.source == "local":
        ffmpeg = _ffmpeg_path()
        if media_path is None:
            raise InvalidInputError(
                "Local media is unavailable for this clip. Re-run with --remote-fallback."
            )
        _validate_local_media_path(media_path, settings.download_root)
        temp_path = output_path.with_name(
            f".{output_path.stem}.{uuid.uuid4().hex}{output_path.suffix}"
        )
        args = [
            ffmpeg,
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-to",
            f"{end_seconds:.3f}",
            "-i",
            str(media_path),
        ]
        if mode == "fast":
            args.extend(["-c", "copy"])
        else:
            args.extend(["-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart"])
        args.append(str(temp_path))
        try:
            _run(args, "ffmpeg clip extraction failed.")
            _replace_clip_output(temp_path, output_path, settings.clips_root)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        protect_private_tree(output_path.parent)
        return ClipExtraction(
            output_path=output_path,
            source="local",
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            used_remote_fallback=False,
        )

    if plan.output_template is None:
        raise RuntimeError("Remote clip extraction requires an output template.")
    temp_stem = f".{output_path.stem}.{uuid.uuid4().hex}"
    temp_template = output_path.parent / f"{temp_stem}.%(ext)s"
    args = [
        command_path(),
        "--quiet",
        "--no-warnings",
        "--force-overwrites",
        "--download-sections",
        f"*{start_seconds:.3f}-{end_seconds:.3f}",
        "--output",
        str(temp_template),
        normalize_target(info.webpage_url),
    ]
    try:
        _run(args, "yt-dlp remote clip extraction failed.")
        temp_pattern = f"{glob_escape(temp_stem)}.*"
        remote_temp_output = next(iter(sorted(output_path.parent.glob(temp_pattern))), None)
        if remote_temp_output is None:
            raise ExternalCommandError("yt-dlp remote clip extraction produced no output.")
        remote_output = output_path.with_suffix(remote_temp_output.suffix)
        _replace_clip_output(remote_temp_output, remote_output, settings.clips_root)
    finally:
        for leftover in output_path.parent.glob(f"{glob_escape(temp_stem)}.*"):
            leftover.unlink()
    protect_private_tree(output_path.parent)
    return ClipExtraction(
        output_path=remote_output,
        source="remote",
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        used_remote_fallback=True,
    )


def plan_clip(
    settings: Settings,
    result_id: str,
    *,
    padding_before: float = 0.0,
    padding_after: float = 0.0,
    mode: str = "fast",
    prefer_remote: bool = False,
) -> PlannedClipExtraction:
    catalog = CatalogStore(settings.catalog_file, readonly=True)
    hit = catalog.get_clip_hit(result_id, readonly=True)
    if hit is None:
        raise InvalidInputError(f"Unknown clip result: {result_id}")
    if mode not in {"fast", "accurate"}:
        raise InvalidInputError("Clip mode must be 'fast' or 'accurate'.")

    start_seconds, end_seconds = _clip_bounds(hit, padding_before, padding_after)
    return _plan_resolved_clip(
        settings,
        _video_info_from_hit(hit),
        label=hit.source,
        media_path=hit.output_path,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        mode=mode,
        prefer_remote=prefer_remote,
    )


def plan_clip_for_range(
    settings: Settings,
    *,
    video_id: str,
    start_seconds: float,
    end_seconds: float,
    mode: str = "fast",
    prefer_remote: bool = False,
) -> PlannedClipExtraction:
    if mode not in {"fast", "accurate"}:
        raise InvalidInputError("Clip mode must be 'fast' or 'accurate'.")
    if not math.isfinite(start_seconds) or not math.isfinite(end_seconds):
        raise InvalidInputError("--start-seconds and --end-seconds must be finite numbers.")
    normalized_start = max(0.0, start_seconds)
    if end_seconds <= normalized_start:
        raise InvalidInputError("--end-seconds must be greater than --start-seconds.")

    catalog = CatalogStore(settings.catalog_file, readonly=True)
    video = catalog.get_video(video_id, readonly=True)
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
    return _plan_resolved_clip(
        settings,
        info,
        media_path=video.output_path,
        label="range",
        start_seconds=normalized_start,
        end_seconds=end_seconds,
        mode=mode,
        prefer_remote=prefer_remote,
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
    plan = plan_clip(
        settings,
        result_id,
        padding_before=padding_before,
        padding_after=padding_after,
        mode=mode,
        prefer_remote=prefer_remote,
    )
    catalog = CatalogStore(settings.catalog_file, readonly=True)
    hit = catalog.get_clip_hit(result_id, readonly=True)
    if hit is None:
        raise InvalidInputError(f"Unknown clip result: {result_id}")
    return _extract_resolved_clip(
        settings,
        _video_info_from_hit(hit),
        media_path=hit.output_path,
        label=hit.source,
        start_seconds=plan.start_seconds,
        end_seconds=plan.end_seconds,
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
    plan = plan_clip_for_range(
        settings,
        video_id=video_id,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        mode=mode,
        prefer_remote=prefer_remote,
    )
    catalog = CatalogStore(settings.catalog_file, readonly=True)
    video = catalog.get_video(video_id, readonly=True)
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
        start_seconds=plan.start_seconds,
        end_seconds=plan.end_seconds,
        mode=mode,
        prefer_remote=prefer_remote,
    )
