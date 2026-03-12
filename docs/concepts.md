# Concepts

This document explains what `yt-agent` stores locally and how the major workflows fit together.

## Local state

- Config: `~/.config/yt-agent/config.toml`
- Archive: `~/.local/share/yt-agent/archive.txt`
- Manifest: `~/.local/share/yt-agent/downloads.jsonl`
- Catalog: `~/.local/share/yt-agent/catalog.sqlite`
- Operation lock: `~/.local/share/yt-agent/operation.lock`
- Download root: `~/Media/YouTube`
- Clips root: `~/Media/YouTube/_clips`

The manifest is an append-only audit trail. The SQLite catalog is the query layer for `library`, `clips search`, and the TUI.

## What `download` and `grab` do

- Resolve video or playlist targets through `yt-dlp`
- Download media into organized channel/date paths
- Use the archive file to avoid re-downloading known items
- Append a manifest row only after a successful download
- Trigger best-effort indexing for downloaded items

Use `--audio` for audio-only downloads and `--fetch-subs` when subtitle sidecars are important for later transcript search.

## What `index refresh` and `index add` do

- Rebuild or extend the local SQLite catalog
- Read `.info.json` sidecars for metadata and chapters
- Detect existing local subtitle sidecars next to media files
- Fetch missing subtitles only when `--fetch-subs` is enabled

`index refresh` replays the manifest. `index add` is the ad hoc path for indexing a remote video or playlist without downloading it first.

## Transcript and chapter search

`clips search` is deterministic in `0.2.x`.

- Chapter matches come from native chapter data in `.info.json`
- Transcript matches come from indexed subtitle segments
- Manual subtitles are preferred over auto-generated subtitles
- Search quality depends on what metadata and subtitle sidecars exist locally or can be fetched

## Clip extraction

`clips grab` prefers local media and uses `ffmpeg` when possible.

- `fast` mode tries to avoid re-encoding when it can
- `accurate` mode re-encodes the selected interval
- `--remote-fallback` uses `yt-dlp --download-sections` if local media is missing
- If you already know the exact span, you can skip result IDs and use `--video-id`, `--start-seconds`, and `--end-seconds`

## Output modes

Commands support:

- `--output table` for default human-friendly terminal output
- `--output json` for structured automation
- `--output plain` for lightweight text/TSV-style output

For agents and scripts, prefer `json`.

## Mutation safety

Mutating commands support two operator-safe patterns:

- `--dry-run` previews what would happen without writing files or changing the catalog
- `--quiet` reduces non-essential output once the action is approved

Mutating commands also serialize through a local operation lock. If another mutation is already running, the next mutation exits with code `7`.

## Non-interactive selection

Prompt-based selection is still the default for humans, but these commands also accept `--select`:

- `yt-agent pick ... --select 1,3`
- `yt-agent grab ... --select 1,3`
- `yt-agent download PLAYLIST_URL --select 1,3`

This is the recommended path for coding agents and scripts.

## Download flags

- `--audio` downloads audio only
- `--fetch-subs` saves subtitle files alongside media during download or indexing
- `--auto-subs` includes auto-generated subtitles (requires `--fetch-subs`)
- `--from-file FILE` reads URLs or IDs from a text file

## TUI expectations

The TUI is a catalog browser, not a full media workstation.

- Good at browsing indexed videos, playlists, transcript previews, and chapters
- Good at opening local media
- Not the primary surface for downloads, clip extraction, or queue management

## Clip result IDs

Clip result IDs such as `transcript:12` or `chapter:3` are intended for immediate use after search. They should not be treated as durable identifiers across catalog rebuilds or reindexing.
