"""Bounded, opt-in YouTube comment indexing."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from yt_agent import yt_dlp
from yt_agent.catalog import CatalogStore, CommentUpsert, VideoUpsert
from yt_agent.config import Settings
from yt_agent.models import VideoInfo
from yt_agent.security import sanitize_terminal_text

__all__ = ["CommentIndexReport", "index_comments", "search_comments"]


@dataclass(frozen=True)
class CommentIndexReport:
    video_id: str
    fetched: int
    indexed: int
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "command": "comments index",
            "status": "noop" if self.dry_run else "ok",
            "summary": {
                "fetched": self.fetched,
                "indexed": self.indexed,
                "dry_run": self.dry_run,
            },
            "video_id": self.video_id,
            "network_fetch_attempted": True,
            "dry_run": self.dry_run,
        }


def _published_at(raw: Any) -> str | None:
    if isinstance(raw, (int, float)):
        if not math.isfinite(raw):
            return None
        try:
            return datetime.fromtimestamp(raw, tz=UTC).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    return sanitize_terminal_text(raw) if raw else None


def _normalize_comments(payload: dict[str, Any], limit: int) -> list[CommentUpsert]:
    raw_comments = payload.get("comments")
    if not isinstance(raw_comments, list):
        return []
    comments: list[CommentUpsert] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_comments[:limit]):
        if not isinstance(raw, dict):
            continue
        text = sanitize_terminal_text(raw.get("text") or "")[:10_000]
        if not text:
            continue
        author = sanitize_terminal_text(raw.get("author") or "Unknown")[:500]
        raw_id = sanitize_terminal_text(raw.get("id") or "")[:512]
        if not raw_id:
            digest = hashlib.sha256(
                f"{author}\0{text}\0{position}".encode()
            ).hexdigest()[:24]
            raw_id = f"generated:{digest}"
        if raw_id in seen:
            continue
        seen.add(raw_id)
        like_count = raw.get("like_count")
        normalized_likes = (
            max(0, int(like_count))
            if isinstance(like_count, (int, float))
            and not isinstance(like_count, bool)
            and math.isfinite(like_count)
            else 0
        )
        comments.append(
            CommentUpsert(
                comment_id=raw_id,
                author=author,
                text=text,
                published_at=_published_at(raw.get("timestamp") or raw.get("time_text")),
                like_count=normalized_likes,
                parent_id=sanitize_terminal_text(raw.get("parent") or "")[:512] or None,
            )
        )
    return comments


def index_comments(
    settings: Settings,
    target: str,
    *,
    limit: int = 100,
    dry_run: bool = False,
    fetch_fn: Callable[..., dict[str, Any]] = yt_dlp.fetch_comments,
) -> CommentIndexReport:
    """Fetch and atomically replace one video's bounded local comment set."""
    payload = fetch_fn(target, limit=limit)
    info = VideoInfo.from_yt_dlp(payload, original_url=target)
    comments = _normalize_comments(payload, limit)
    if not dry_run:
        store = CatalogStore(settings.catalog_file)
        store.ensure_schema()
        store.upsert_video(
            VideoUpsert(
                video_id=info.video_id,
                title=info.title,
                channel=info.channel,
                upload_date=info.upload_date,
                duration_seconds=info.duration_seconds,
                extractor_key=info.extractor_key,
                webpage_url=info.webpage_url,
                requested_input=target,
                source_query=None,
                output_path=None,
                info_json_path=None,
                downloaded_at=None,
                indexed_at=datetime.now(UTC).isoformat(),
            )
        )
        store.replace_comments(info.video_id, comments)
    return CommentIndexReport(
        info.video_id,
        len(payload.get("comments") or []),
        len(comments),
        dry_run,
    )


def search_comments(settings: Settings, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    return CatalogStore(settings.catalog_file, readonly=True).search_comments(query, limit=limit)
