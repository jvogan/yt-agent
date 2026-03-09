"""Selection helpers for prompt and fzf workflows."""

from __future__ import annotations

import shutil
import subprocess

from rich.prompt import Prompt

from yt_agent.errors import SelectionError
from yt_agent.models import VideoInfo


def _format_line(index: int, result: VideoInfo) -> str:
    return "\t".join(
        [
            str(index),
            result.title,
            result.channel,
            result.display_duration,
            result.upload_date or "undated",
            result.video_id,
            result.webpage_url,
        ]
    )


def parse_selection(selection: str, max_index: int) -> list[int]:
    """Parse a comma-separated list of result indexes."""

    raw = selection.strip()
    if not raw or raw.lower() in {"q", "quit", "exit"}:
        return []

    indexes: list[int] = []
    seen: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token.isdigit():
            raise SelectionError("Selections must be comma-separated result numbers.")
        index = int(token)
        if index < 1 or index > max_index:
            raise SelectionError(f"Selection {index} is out of range.")
        if index not in seen:
            indexes.append(index)
            seen.add(index)
    return indexes


def prompt_for_selection(results: list[VideoInfo], *, raw_selection: str | None = None) -> list[VideoInfo]:
    if not results:
        return []
    raw = raw_selection or Prompt.ask("Select result numbers (e.g. 1,3) or q to cancel")
    indexes = parse_selection(raw, len(results))
    return [results[index - 1] for index in indexes]


def select_with_fzf(results: list[VideoInfo]) -> list[VideoInfo]:
    if shutil.which("fzf") is None:
        raise SelectionError("fzf is not installed.")

    payload = "\n".join(_format_line(index, result) for index, result in enumerate(results, start=1))
    completed = subprocess.run(
        ["fzf", "--multi", "--with-nth=1,2,3,4,5"],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode == 130:
        return []
    if completed.returncode != 0:
        message = completed.stderr.strip() or "fzf selection failed."
        raise SelectionError(message)

    selected_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    indexes: list[int] = []
    for line in selected_lines:
        first_field = line.split("\t", 1)[0].strip()
        if first_field.isdigit():
            indexes.append(int(first_field))
    if not indexes:
        return []
    return [results[index - 1] for index in indexes]


def select_results(
    results: list[VideoInfo],
    *,
    prefer_fzf: bool = False,
    configured_selector: str = "prompt",
    raw_selection: str | None = None,
) -> list[VideoInfo]:
    if not results:
        return []
    if prefer_fzf or configured_selector == "fzf":
        try:
            return select_with_fzf(results)
        except SelectionError:
            if raw_selection is None:
                return prompt_for_selection(results)
    return prompt_for_selection(results, raw_selection=raw_selection)
