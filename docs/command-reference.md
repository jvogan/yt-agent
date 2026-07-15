# Command Reference

This is the compact command map for `yt-agent`. Use it when you already understand the workflows and just need the surface area.

## Core commands

| Command | Purpose | Common flags |
|---|---|---|
| `doctor` | Check dependencies and data paths | `--deep`, `--output json` |
| `search QUERY` | Search YouTube | `--limit`, `--output json` |
| `pick QUERY` | Search and select without downloading | `--select`, `--fzf`, `--output json` |
| `grab QUERY` | Search, select, and download | `--select`, `--audio`, `--sponsorblock`, `--events-jsonl`, `--dry-run`, `--quiet`, `--output json` |
| `info TARGET` | Show normalized metadata for a video or playlist | `--entries`, `--output json` |
| `formats TARGET` | Inspect normalized formats and reviewed presets | `--output json` |
| `open REFERENCE` / `play REFERENCE` | Launch local media, catalog/clip references, or YouTube URLs | `--dry-run`, `--output json` |
| `live record TARGET` | Record, manifest, and index a live stream | `--live-from-start`, `--from-now`, `--wait-seconds`, `--dry-run`, `--output json` |
| `download TARGET...` | Download videos or playlists | `--select-playlist`, `--select`, `--audio`, `--from-file`, `--fetch-subs`, `--sponsorblock`, `--sponsorblock-remove`, `--events-jsonl`, `--dry-run`, `--quiet`, `--output json` |

## Index and clip commands

| Command | Purpose | Common flags |
|---|---|---|
| `index refresh` | Backfill or rebuild the catalog from the manifest | `--fetch-subs`, `--auto-subs`, `--dry-run`, `--quiet`, `--output json` |
| `index add TARGET` | Index one remote video or playlist without downloading | `--fetch-subs`, `--auto-subs`, `--dry-run`, `--quiet`, `--output json` |
| `clips search QUERY` | Search transcript and chapter hits | `--source`, `--channel`, `--lang`, `--limit`, `--output json` |
| `clips show RESULT_ID` | Show one clip hit with context | `--output json` |
| `clips grab RESULT_ID` | Extract a clip from a search hit | `--padding-before`, `--padding-after`, `--mode`, `--remote-fallback`, `--dry-run`, `--quiet`, `--output json` |
| `clips grab --video-id ID --start-seconds S --end-seconds E` | Extract a clip from explicit coordinates | `--mode`, `--remote-fallback`, `--dry-run`, `--quiet`, `--output json` |
| `clips smart RESULT_ID` | Snap clip boundaries to nearby silence | `--dry-run`, `--output json` |
| `transcripts export VIDEO_ID` | Export an indexed track as text, Markdown, JSON, VTT, or SRT | `--format`, `--lang`, `--dest` |
| `transcripts generate VIDEO_ID` | Generate and index a local whisper transcript | `--model`, `--lang`, `--dry-run`, `--output json` |
| `comments index VIDEO_ID` | Fetch a bounded local comment set | `--limit`, `--dry-run`, `--output json` |
| `comments search QUERY` | Search locally indexed comments | `--video-id`, `--limit`, `--output json` |
| `preview contact-sheet VIDEO_ID` | Create a sampled contact sheet and optional GIF | `--frames`, `--gif`, `--dry-run`, `--output json` |

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

## Data management commands

| Command | Purpose | Common flags |
|---|---|---|
| `export --dest DEST` | Export the local catalog to a JSON or CSV file | `--format`, `--limit`, `--output json` |
| `import SRC` | Import catalog entries from a JSON file (created by `export`) | `--dry-run`, `--output json` |
| `history` | Show recent downloads from the manifest | `--limit`, `--channel`, `--output json` |
| `cleanup` | Remove orphaned subtitle caches, empty channel dirs, and `.part` files | `--dry-run`, `--quiet`, `--output json` |
| `verify` | Audit archive, manifest, catalog, sidecars, and media | `--deep`, `--output json` |
| `repair` | Preview/apply safe derived-state repairs; never deletes media | `--apply`, `--output json` |
| `backup create DEST` | Back up core indexed content, comments, and curation | `--output json` |
| `backup restore SRC` | Validate and atomically restore a catalog backup | `--dry-run`, `--output json` |

Backups include cataloged video-stat snapshots. They do not include downloaded media, the
append-only manifest/archive, queue jobs, or saved sync source state. Back up those state files
separately when you need a complete operational clone.

## Sync, queue, and curation

| Command | Purpose | Common flags |
|---|---|---|
| `sync add NAME URL` / `sync remove NAME` | Manage saved channel/playlist sources | `--kind`, `--output json` |
| `sync list` / `sync run [NAME...]` | Inspect or incrementally process saved sources | `--since`, `--latest`, `--index`, `--download`, `--dry-run`, `--output json` |
| `queue add OP TARGET` | Queue a typed `download`, `index`, or `sync` job | `--max-retries`, `--dry-run`, `--output json` |
| `queue list` / `queue show ID` | Inspect persistent jobs | `--status`, `--output json` |
| `queue cancel ID` / `queue retry ID` | Change a queued job state | `--dry-run`, `--output json` |
| `queue run-next` | Run one available job synchronously | `--dry-run`, `--output json` |
| `curate set VIDEO_ID` / `curate clear VIDEO_ID` | Manage notes and ratings | `--note`, `--rating`, `--dry-run`, `--output json` |
| `curate tag VIDEO_ID TAG` | Add/remove a tag | `--remove`, `--dry-run`, `--output json` |
| `curate collection-*` | Create/delete collections and manage membership | `--dry-run`, `--output json` |
| `curate bookmark VIDEO_ID SECONDS` | Add a timestamp bookmark | `--label`, `--note`, `--dry-run`, `--output json` |
| `curate show` / `curate search QUERY` | Inspect/search user-owned metadata | `--output json` |

## Config and UI commands

| Command | Purpose | Common flags |
|---|---|---|
| `config init` | Write a starter config file | `--force`, `--config` |
| `config path` | Show config and data paths | `--output json` |
| `config validate` | Validate the active config | `--config` |
| `tui` | Launch the Textual catalog browser | `--config` |

## Shell completions

| Command | Purpose | Common flags |
|---|---|---|
| `completions install` | Install shell completion for yt-agent | `--shell`, `--output json` |
| `completions show` | Print the shell completion script to stdout | `--shell`, `--output json` |

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
