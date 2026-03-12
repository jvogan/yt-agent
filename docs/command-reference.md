# Command Reference

This is the compact command map for `yt-agent`. Use it when you already understand the workflows and just need the surface area.

## Core commands

| Command | Purpose | Common flags |
|---|---|---|
| `doctor` | Check dependencies and data paths | `--output json` |
| `search QUERY` | Search YouTube | `--limit`, `--output json` |
| `pick QUERY` | Search and select without downloading | `--select`, `--fzf`, `--output json` |
| `grab QUERY` | Search, select, and download | `--select`, `--audio`, `--dry-run`, `--quiet`, `--output json` |
| `info TARGET` | Show normalized metadata for a video or playlist | `--entries`, `--output json` |
| `download TARGET...` | Download videos or playlists | `--select-playlist`, `--select`, `--audio`, `--from-file`, `--fetch-subs`, `--dry-run`, `--quiet`, `--output json` |

## Index and clip commands

| Command | Purpose | Common flags |
|---|---|---|
| `index refresh` | Backfill or rebuild the catalog from the manifest | `--fetch-subs`, `--auto-subs`, `--dry-run`, `--quiet`, `--output json` |
| `index add TARGET` | Index one remote video or playlist without downloading | `--fetch-subs`, `--auto-subs`, `--dry-run`, `--quiet`, `--output json` |
| `clips search QUERY` | Search transcript and chapter hits | `--source`, `--channel`, `--lang`, `--limit`, `--output json` |
| `clips show RESULT_ID` | Show one clip hit with context | `--output json` |
| `clips grab RESULT_ID` | Extract a clip from a search hit | `--padding-before`, `--padding-after`, `--mode`, `--remote-fallback`, `--dry-run`, `--quiet`, `--output json` |
| `clips grab --video-id ID --start-seconds S --end-seconds E` | Extract a clip from explicit coordinates | `--mode`, `--remote-fallback`, `--dry-run`, `--quiet`, `--output json` |

## Library commands

| Command | Purpose | Common flags |
|---|---|---|
| `library stats` | Show high-level catalog counts | `--output json` |
| `library list` | List catalog entries | `--channel`, `--playlist`, `--has-transcript`, `--has-chapters`, `--output json` |
| `library search QUERY` | Search the local catalog | `--channel`, `--playlist`, `--has-transcript`, `--has-chapters`, `--output json` |
| `library show VIDEO_ID` | Show chapters, subtitle tracks, and transcript preview | `--output json` |
| `library channels` | List distinct channels | `--output json` |
| `library playlists` | List indexed playlists | `--output json` |
| `library remove VIDEO_ID...` | Remove catalog rows without deleting media files | `--dry-run`, `--output json` |

## Config and UI commands

| Command | Purpose | Common flags |
|---|---|---|
| `config init` | Write a starter config file | `--force`, `--config` |
| `config path` | Show config and data paths | `--output json` |
| `config validate` | Validate the active config | `--config` |
| `tui` | Launch the Textual catalog browser | `--config` |

## Output and automation contract

- Read commands support `--output table|json|plain`.
- Mutating commands also support `--output json`.
- Collection-style read commands return a top-level JSON array; detail-style read commands return a top-level JSON object.
- Video-like JSON rows use `video_id` and `webpage_url` consistently.
- Catalog/library JSON rows use `output_path` for the local media path.
- `library channels --output json` returns row objects with a single `channel` key.
- Use `--select` in JSON mode for commands that would otherwise prompt.
- Use `--dry-run` before approval-gated mutations.
- Use `--quiet` on approved mutations.
- Clip result IDs such as `transcript:12` are short-lived handles, not durable identifiers.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `3` | Missing dependency |
| `4` | Invalid input |
| `5` | Invalid config |
| `6` | External tool failure |
| `7` | Busy mutation lock |
| `8` | Storage/database issue |
| `130` | Interrupted |
