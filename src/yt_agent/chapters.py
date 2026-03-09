"""Chapter extraction helpers."""

from __future__ import annotations

from typing import Any

from yt_agent.models import ChapterEntry, chapter_from_payload


def extract_chapters(payload: dict[str, Any]) -> list[ChapterEntry]:
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        return []

    results: list[ChapterEntry] = []
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            continue
        parsed = chapter_from_payload(index, chapter)
        if parsed is not None:
            results.append(parsed)
    return results

