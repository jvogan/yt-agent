"""Bounded local ffmpeg utilities for smart clips and visual previews."""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from yt_agent.catalog import CatalogStore
from yt_agent.config import Settings
from yt_agent.errors import DependencyError, ExternalCommandError, InvalidInputError
from yt_agent.models import CatalogVideo
from yt_agent.security import protect_artifact_file
from yt_agent.yt_dlp import optional_tool_path

_SILENCE_RE = re.compile(r"silence_(start|end):\s*([0-9]+(?:\.[0-9]+)?)")


@dataclass(frozen=True)
class SmartClipBounds:
    video_id: str
    original_start: float
    original_end: float
    start_seconds: float
    end_seconds: float
    media_path: Path


@dataclass(frozen=True)
class PreviewPlan:
    video_id: str
    media_path: Path
    contact_sheet_path: Path
    gif_path: Path | None
    contact_sheet_args: list[str]
    gif_args: list[str] | None


def _ffmpeg() -> str:
    path = optional_tool_path("ffmpeg")
    if path is None:
        raise DependencyError("ffmpeg is required for local media utilities.")
    return path


def _local_video(settings: Settings, video_id: str) -> CatalogVideo:
    video = CatalogStore(settings.catalog_file, readonly=True).get_video(video_id, readonly=True)
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
    return video


def smart_clip_bounds(
    settings: Settings,
    result_id: str,
    *,
    window_seconds: float = 2.0,
    noise_db: float = -35.0,
    min_silence: float = 0.25,
) -> SmartClipBounds:
    if not math.isfinite(window_seconds) or not 0.1 <= window_seconds <= 10.0:
        raise InvalidInputError("Silence snap window must be between 0.1 and 10 seconds.")
    if not math.isfinite(noise_db) or not -80.0 <= noise_db <= -10.0:
        raise InvalidInputError("Silence threshold must be between -80 and -10 dB.")
    if not math.isfinite(min_silence) or not 0.05 <= min_silence <= 5.0:
        raise InvalidInputError("Minimum silence must be between 0.05 and 5 seconds.")
    store = CatalogStore(settings.catalog_file, readonly=True)
    hit = store.get_clip_hit(result_id, readonly=True)
    if hit is None:
        raise InvalidInputError(f"Unknown clip result: {result_id}")
    video = _local_video(settings, hit.video_id)
    media_path = video.output_path
    if media_path is None:  # Narrow the catalog model after local-media validation.
        raise InvalidInputError("Local media path is unavailable.")
    scan_start = max(0.0, hit.start_seconds - window_seconds)
    scan_end = hit.end_seconds + window_seconds
    args = [
        _ffmpeg(),
        "-hide_banner",
        "-ss",
        f"{scan_start:.3f}",
        "-t",
        f"{scan_end - scan_start:.3f}",
        "-i",
        str(media_path),
        "-af",
        f"silencedetect=noise={noise_db:.1f}dB:d={min_silence:.3f}",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(args, text=True, capture_output=True, check=False)  # noqa: S603
    if completed.returncode != 0:
        raise ExternalCommandError(
            "ffmpeg silence detection failed.", stderr=completed.stderr[-4000:].strip()
        )
    starts: list[float] = []
    ends: list[float] = []
    for kind, raw_value in _SILENCE_RE.findall(completed.stderr):
        absolute = scan_start + float(raw_value)
        (starts if kind == "start" else ends).append(absolute)
    start_candidates = [value for value in ends if abs(value - hit.start_seconds) <= window_seconds]
    end_candidates = [value for value in starts if abs(value - hit.end_seconds) <= window_seconds]
    snapped_start = (
        min(start_candidates, key=lambda value: abs(value - hit.start_seconds))
        if start_candidates
        else hit.start_seconds
    )
    snapped_end = (
        min(end_candidates, key=lambda value: abs(value - hit.end_seconds))
        if end_candidates
        else hit.end_seconds
    )
    if snapped_end <= snapped_start:
        snapped_start, snapped_end = hit.start_seconds, hit.end_seconds
    return SmartClipBounds(
        video_id=hit.video_id,
        original_start=hit.start_seconds,
        original_end=hit.end_seconds,
        start_seconds=max(0.0, snapped_start),
        end_seconds=snapped_end,
        media_path=media_path,
    )


def plan_preview(
    settings: Settings,
    video_id: str,
    *,
    dest: Path | None = None,
    frames: int = 12,
    columns: int = 4,
    width: int = 320,
    gif: bool = False,
    gif_start: float = 0.0,
    gif_duration: float = 4.0,
    gif_fps: int = 8,
    force: bool = False,
) -> PreviewPlan:
    if not 4 <= frames <= 25 or not 1 <= columns <= 5:
        raise InvalidInputError("Contact sheet frames must be 4-25 and columns 1-5.")
    if not 160 <= width <= 1920:
        raise InvalidInputError("Preview width must be between 160 and 1920 pixels.")
    if not math.isfinite(gif_start) or gif_start < 0:
        raise InvalidInputError("GIF start must be a non-negative finite number.")
    if not math.isfinite(gif_duration) or not 0.5 <= gif_duration <= 15.0:
        raise InvalidInputError("GIF duration must be between 0.5 and 15 seconds.")
    if not 1 <= gif_fps <= 20:
        raise InvalidInputError("GIF frame rate must be between 1 and 20.")
    video = _local_video(settings, video_id)
    media_path = video.output_path
    if media_path is None:  # Narrow the catalog model after local-media validation.
        raise InvalidInputError("Local media path is unavailable.")
    duration = float(video.duration_seconds or 0)
    if duration <= 0:
        raise InvalidInputError("Video duration is required to build an evenly sampled sheet.")
    output = dest or settings.clips_root / "previews" / f"{video_id}-contact-sheet.jpg"
    if output.suffix.casefold() not in {".jpg", ".jpeg", ".png"}:
        raise InvalidInputError("Contact sheet output must be .jpg, .jpeg, or .png.")
    gif_path = output.with_name(f"{output.stem}.gif") if gif else None
    if not force and (output.exists() or (gif_path is not None and gif_path.exists())):
        raise InvalidInputError("Preview output already exists. Use --force to replace it.")
    if output.is_symlink() or (gif_path is not None and gif_path.is_symlink()):
        raise InvalidInputError("Preview output must not be a symlink.")
    rows = math.ceil(frames / columns)
    fps = frames / duration
    ffmpeg = _ffmpeg()
    sheet_filter = f"fps={fps:.8f},scale={width}:-1,tile={columns}x{rows}:nb_frames={frames}"
    sheet_args = [
        ffmpeg,
        "-y",
        "-i",
        str(media_path),
        "-vf",
        sheet_filter,
        "-frames:v",
        "1",
        str(output),
    ]
    gif_args = None
    if gif_path is not None:
        gif_filter = (
            f"fps={gif_fps},scale={width}:-1:flags=lanczos,split[a][b];"
            "[a]palettegen[p];[b][p]paletteuse"
        )
        gif_args = [
            ffmpeg,
            "-y",
            "-ss",
            f"{gif_start:.3f}",
            "-t",
            f"{gif_duration:.3f}",
            "-i",
            str(media_path),
            "-filter_complex",
            gif_filter,
            str(gif_path),
        ]
    return PreviewPlan(video_id, media_path, output, gif_path, sheet_args, gif_args)


def execute_preview(plan: PreviewPlan) -> None:
    plan.contact_sheet_path.parent.mkdir(parents=True, exist_ok=True)
    for args, label in (
        (plan.contact_sheet_args, "contact sheet"),
        (plan.gif_args, "GIF preview"),
    ):
        if args is None:
            continue
        completed = subprocess.run(args, text=True, capture_output=True, check=False)  # noqa: S603
        if completed.returncode != 0:
            raise ExternalCommandError(
                f"ffmpeg failed while creating {label}.",
                stderr=completed.stderr[-4000:].strip(),
            )
        expected_output = Path(args[-1])
        if not expected_output.is_file():
            raise ExternalCommandError(f"ffmpeg produced no {label} output.")
        protect_artifact_file(expected_output)


__all__ = [
    "PreviewPlan",
    "SmartClipBounds",
    "execute_preview",
    "plan_preview",
    "smart_clip_bounds",
]
