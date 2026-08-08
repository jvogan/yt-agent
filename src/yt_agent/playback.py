"""Safe media and YouTube URL launcher."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from yt_agent.errors import DependencyError, InvalidInputError
from yt_agent.yt_dlp import normalize_target


def launch_media(
    reference: str | Path,
    *,
    start_seconds: float | None = None,
    dry_run: bool = False,
) -> list[str]:
    raw = str(reference)
    candidate = Path(raw).expanduser()
    if candidate.exists():
        resolved = candidate.resolve()
        if not resolved.is_file():
            raise InvalidInputError("Playback target must be a file.")
        target = str(resolved)
    else:
        target = normalize_target(raw)

    mpv = shutil.which("mpv")
    if mpv:
        args = [mpv]
        if start_seconds is not None:
            if start_seconds < 0:
                raise InvalidInputError("Playback start must not be negative.")
            args.append(f"--start={start_seconds:.3f}")
        args.extend(["--", target])
    elif sys.platform == "win32":
        args = ["startfile", target]
        if not dry_run:
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
        return args
    else:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        opener_path = shutil.which(opener)
        if opener_path is None:
            raise DependencyError("Install mpv or a supported system opener to play media.")
        args = [opener_path, target]
    if not dry_run:
        subprocess.Popen(args, start_new_session=True)  # noqa: S603
    return args
