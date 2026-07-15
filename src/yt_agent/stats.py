"""Optional yt-dlp-backed time-series statistics for catalog videos."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from yt_agent import yt_dlp
from yt_agent.catalog import CatalogStore
from yt_agent.errors import InvalidInputError

MAX_STATS_BATCH = 100


@dataclass(frozen=True)
class StatsSnapshot:
    snapshot_id: int | None
    video_id: str
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    fetched_at: str
    provider: str = "yt-dlp"


@dataclass(frozen=True)
class StatsTrend:
    video_id: str
    current: StatsSnapshot
    previous: StatsSnapshot | None

    @staticmethod
    def _delta(current: int | None, previous: int | None) -> int | None:
        if current is None or previous is None:
            return None
        return current - previous

    @property
    def view_delta(self) -> int | None:
        return self._delta(
            self.current.view_count,
            self.previous.view_count if self.previous else None,
        )

    @property
    def like_delta(self) -> int | None:
        return self._delta(
            self.current.like_count,
            self.previous.like_count if self.previous else None,
        )

    @property
    def comment_delta(self) -> int | None:
        return self._delta(
            self.current.comment_count,
            self.previous.comment_count if self.previous else None,
        )


def _count(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(parsed, 0)


def _snapshot_from_row(row: Any) -> StatsSnapshot:
    return StatsSnapshot(
        snapshot_id=int(row["snapshot_id"]),
        video_id=str(row["video_id"]),
        view_count=int(row["view_count"]) if row["view_count"] is not None else None,
        like_count=int(row["like_count"]) if row["like_count"] is not None else None,
        comment_count=int(row["comment_count"])
        if row["comment_count"] is not None
        else None,
        fetched_at=str(row["fetched_at"]),
        provider=str(row["provider"]),
    )


def resolve_stats_videos(
    store: CatalogStore, video_ids: list[str] | None, *, limit: int
) -> list[Any]:
    if not 1 <= limit <= MAX_STATS_BATCH:
        raise InvalidInputError(f"Stats batch limit must be between 1 and {MAX_STATS_BATCH}.")
    requested = list(dict.fromkeys(video_ids or []))
    if len(requested) > limit:
        raise InvalidInputError(f"Requested video count exceeds the batch limit of {limit}.")
    if not requested:
        return store.list_videos(limit=limit)
    videos = []
    for video_id in requested:
        video = store.get_video(video_id, readonly=True)
        if video is None:
            raise InvalidInputError(f"Video id '{video_id}' is not in the catalog.")
        videos.append(video)
    return videos


def refresh_stats(
    store: CatalogStore,
    video_ids: list[str] | None = None,
    *,
    limit: int = 25,
    dry_run: bool = False,
) -> list[StatsSnapshot]:
    videos = resolve_stats_videos(store, video_ids, limit=limit)
    fetched_at = datetime.now(UTC).isoformat()
    if dry_run:
        return [
            StatsSnapshot(None, video.video_id, None, None, None, fetched_at)
            for video in videos
        ]
    snapshots = []
    for video in videos:
        payload = yt_dlp.fetch_info(video.webpage_url)
        snapshots.append(
            StatsSnapshot(
                None,
                video.video_id,
                _count(payload, "view_count"),
                _count(payload, "like_count"),
                _count(payload, "comment_count"),
                fetched_at,
            )
        )
    stored: list[StatsSnapshot] = []
    with store.connect() as conn:
        for snapshot in snapshots:
            cursor = conn.execute(
                """
                INSERT INTO video_stats (
                    video_id, view_count, like_count, comment_count, fetched_at, provider
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.video_id,
                    snapshot.view_count,
                    snapshot.like_count,
                    snapshot.comment_count,
                    snapshot.fetched_at,
                    snapshot.provider,
                ),
            )
            stored.append(
                StatsSnapshot(
                    int(cursor.lastrowid or 0),
                    snapshot.video_id,
                    snapshot.view_count,
                    snapshot.like_count,
                    snapshot.comment_count,
                    snapshot.fetched_at,
                    snapshot.provider,
                )
            )
    return stored


def stats_history(store: CatalogStore, video_id: str, *, limit: int = 20) -> list[StatsSnapshot]:
    if not 1 <= limit <= 1000:
        raise InvalidInputError("Stats history limit must be between 1 and 1000.")
    if store.get_video(video_id, readonly=True) is None:
        raise InvalidInputError(f"Video id '{video_id}' is not in the catalog.")
    try:
        with store.connect(readonly=True) as conn:
            rows = conn.execute(
                """
                SELECT snapshot_id, video_id, view_count, like_count, comment_count,
                       fetched_at, provider
                FROM video_stats WHERE video_id = ?
                ORDER BY fetched_at DESC, snapshot_id DESC LIMIT ?
                """,
                (video_id, limit),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table: video_stats" not in str(exc):
            raise
        return []
    return [_snapshot_from_row(row) for row in rows]


def stats_trends(
    store: CatalogStore, video_ids: list[str] | None = None, *, limit: int = 25
) -> list[StatsTrend]:
    videos = resolve_stats_videos(store, video_ids, limit=limit)
    trends = []
    for video in videos:
        history = stats_history(store, video.video_id, limit=2)
        if history:
            trends.append(
                StatsTrend(
                    video_id=video.video_id,
                    current=history[0],
                    previous=history[1] if len(history) > 1 else None,
                )
            )
    return trends


def snapshot_payload(snapshot: StatsSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "video_id": snapshot.video_id,
        "view_count": snapshot.view_count,
        "like_count": snapshot.like_count,
        "comment_count": snapshot.comment_count,
        "fetched_at": snapshot.fetched_at,
        "provider": snapshot.provider,
    }


def trend_payload(trend: StatsTrend) -> dict[str, Any]:
    return {
        "video_id": trend.video_id,
        "current": snapshot_payload(trend.current),
        "previous": snapshot_payload(trend.previous) if trend.previous else None,
        "view_delta": trend.view_delta,
        "like_delta": trend.like_delta,
        "comment_delta": trend.comment_delta,
    }


__all__ = [
    "MAX_STATS_BATCH",
    "StatsSnapshot",
    "StatsTrend",
    "refresh_stats",
    "resolve_stats_videos",
    "snapshot_payload",
    "stats_history",
    "stats_trends",
    "trend_payload",
]
