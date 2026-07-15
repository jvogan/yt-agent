# Lossless catalog backup

```console
yt-agent backup create catalog-backup.json --output json
yt-agent backup restore catalog-backup.json --dry-run --output json
yt-agent backup restore catalog-backup.json --output json
```

Bundle schema version 4 includes videos, chapters, subtitle tracks, transcript segments,
playlists, playlist entries, comments, notes/ratings, tags, collections, collection membership,
bookmarks, and video-stat snapshots. Chapter, transcript, and comment FTS rows are derived from
those authoritative records during restore instead of being copied as opaque index internals.

Restore remains compatible with bundle versions 1, 2, and 3; collections introduced by later
versions are initialized empty. Validation checks field types, identifiers, bounds, uniqueness,
and parent relationships before the destination transaction deletes existing rows.

The bundle is catalog-scoped. `sources.json`, `jobs.sqlite`, the download archive, manifest,
media files, subtitle caches, and lifecycle JSONL streams are separate operational or media
state and intentionally excluded. No semantic-embedding table exists in the current schema.
