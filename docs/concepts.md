# Concepts

This document explains what `yt-agent` stores locally and how the major workflows fit together.

## Local State

- Config: `~/.config/yt-agent/config.toml`
- Archive: `~/.local/share/yt-agent/archive.txt`
- Manifest: `~/.local/share/yt-agent/downloads.jsonl`
- Catalog: `~/.local/share/yt-agent/catalog.sqlite`
- Download root: `~/Media/YouTube`
- Clips root: `~/Media/YouTube/_clips`

The manifest is an append-only audit trail. The SQLite catalog is the query layer for `library`, `clips search`, and the TUI.

## What `download` Does

- Resolves video or playlist targets through `yt-dlp`
- Downloads media into organized channel/date paths
- Uses the archive file to avoid re-downloading known items
- Appends a manifest row only after a successful download
- Triggers best-effort indexing for the downloaded item

## What `index refresh` Does

- Replays the manifest into the local SQLite catalog
- Reads `.info.json` sidecars for metadata and chapters
- Detects existing local subtitle sidecars next to media files
- Fetches missing subtitles only when `--fetch-subs` is enabled

`index add` is the ad hoc path for indexing a remote video or playlist without downloading it first.

## Transcript and Chapter Search

`clips search` is deterministic in `0.2.x`.

- Chapter matches come from native chapter data in `.info.json`
- Transcript matches come from indexed subtitle segments
- Manual subtitles are preferred over auto-generated subtitles
- Search quality depends on what metadata and subtitle sidecars exist locally or can be fetched

## Clip Extraction

`clips grab` prefers local media and uses `ffmpeg` when possible.

- `fast` mode tries to avoid re-encoding when it can
- `accurate` mode re-encodes the selected interval
- `--remote-fallback` uses `yt-dlp --download-sections` if local media is missing

## Output Modes

Read-oriented commands support:

- `--output table` for default human-friendly terminal output
- `--output json` for structured automation
- `--output plain` for lightweight text/TSV-style output

For agent automation, prefer `json`.

## Non-Interactive Selection

Prompt-based selection is still the default for humans, but these commands also accept `--select`:

- `yt-agent pick ... --select 1,3`
- `yt-agent grab ... --select 1,3`
- `yt-agent download PLAYLIST_URL --select 1,3`

This is the recommended path for coding agents and scripts.

## TUI Expectations

The TUI is a catalog browser, not a full media workstation.

- Good at browsing indexed videos, playlists, transcript previews, and chapters
- Good at opening local media
- Not the primary surface for downloads, clip extraction, or queue management

## Clip Result IDs

Clip result IDs such as `transcript:12` or `chapter:3` are intended for immediate use after search. They should not be treated as durable identifiers across catalog rebuilds or reindexing.
