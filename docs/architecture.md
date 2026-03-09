# Architecture

`yt-agent` has three layers:

1. `yt-dlp` remains the external retrieval engine for search, metadata extraction, downloads, playlist expansion, and remote section downloads.
2. `yt_agent` is the local orchestration layer. It normalizes metadata, manages output paths, writes the download manifest, and exposes the CLI and TUI surfaces.
3. SQLite powers the local catalog. FTS5 indexes chapter titles and transcript text so clip search, library browsing, and the TUI all query the same source of truth.

## Storage Model

- Config: `~/.config/yt-agent/config.toml`
- Archive: `~/.local/share/yt-agent/archive.txt`
- Manifest: `~/.local/share/yt-agent/downloads.jsonl`
- Catalog: `~/.local/share/yt-agent/catalog.sqlite`
- Default media root: `~/Media/YouTube`
- Default clip root: `~/Media/YouTube/_clips`

The manifest is append-only audit data. The catalog is the query layer.

## Catalog Tables

- `videos`
- `chapters`
- `subtitle_tracks`
- `transcript_segments`
- `playlists`
- `playlist_entries`
- FTS5 virtual tables:
  - `chapter_fts`
  - `transcript_fts`

## Indexing Strategy

- Downloads write a manifest row after successful completion.
- `yt-agent index refresh` backfills from the manifest and sidecars.
- Chapter indexing prefers native chapter metadata from `.info.json`.
- Transcript indexing prefers local subtitle sidecars first and only fetches missing subtitles during explicit indexing runs.
- Manual subtitles win over auto-subs when both exist.

## Clip Search Strategy

- Phase 1 is deterministic.
- `yt-agent clips search` queries chapter titles and transcript text through FTS5.
- `yt-agent clips grab` extracts from local media with `ffmpeg` when available.
- Remote fallback uses `yt-dlp --download-sections`.

## UI Strategy

- CLI commands are the stable backend contract.
- Read-oriented CLI commands support `--output table|json|plain` for human and agent workflows.
- The Textual TUI is read-mostly in v1 and rides on the same catalog/query layer.
