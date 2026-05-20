# Agent Workflows

`yt-agent` is a human-friendly CLI first, but it is designed to work cleanly with coding agents and shell-driven automation.

Use this doc when you are writing repo instructions, agent prompts, or shell-driven workflows that need a clear contract.

## Agent contract

- Prefer `--output json` whenever output will be parsed.
- Use `--select` on commands that would otherwise prompt.
- Use `--dry-run` before any approval-gated mutation.
- Use `--quiet` on approved mutations to reduce chatter.
- Treat clip result IDs such as `transcript:12` as short-lived handles.
- Mutating commands are serialized through a local operation lock. Exit code `7` means another mutation is already running.
- `index refresh` and `index add` are local-first by default. Add `--fetch-subs` when transcript coverage matters.

## Install and verify

```bash
# 1. Install yt-dlp (required)
brew install yt-dlp              # macOS
# python3 -m pip install -U yt-dlp  # Linux

# 2. Install yt-agent
uv tool install git+https://github.com/jvogan/yt-agent

# 3. Verify
yt-agent doctor --output json
```

`ffmpeg` is required for local clip extraction. `fzf` is optional.

## Four core workflows

### 1. Approval-safe search and download

Use this when the operator wants results first and writes only after explicit approval.

```bash
yt-agent search "lofi hip hop" --limit 8 --output json
yt-agent grab "lofi hip hop" --select 2,4 --dry-run --output json
# wait for approval
yt-agent grab "lofi hip hop" --select 2,4 --quiet --output json
```

Recipe: [examples/agents/approval-safe-download.md](../examples/agents/approval-safe-download.md)

### 2. Playlist curator

Use this when the operator wants to inspect a playlist and pick a subset.

```bash
yt-agent info "PLAYLIST_URL" --entries --output json
yt-agent download "PLAYLIST_URL" --select 1,3,5 --dry-run --output json
# wait for approval
yt-agent download "PLAYLIST_URL" --select 1,3,5 --quiet --output json
```

Recipe: [examples/agents/playlist-curator.md](../examples/agents/playlist-curator.md)

### 3. Clip hunter

Use this when the operator wants exact transcript or chapter moments turned into clips.

```bash
yt-agent index refresh --fetch-subs --output json
yt-agent clips search "keyboard shortcut" --source transcript --output json
yt-agent clips show transcript:12 --output json
yt-agent clips grab transcript:12 --padding-before 2 --padding-after 3 --dry-run --output json
```

If transcript hits are sparse, rerun the index step with `--fetch-subs`, then search again:

```bash
yt-agent index refresh --fetch-subs --output json
```

If you already know the exact span, use explicit coordinates:

```bash
yt-agent clips grab --video-id VIDEO_ID --start-seconds 12.5 --end-seconds 18.0 --dry-run --output json
```

Recipe: [examples/agents/clip-hunter.md](../examples/agents/clip-hunter.md)

### 4. Library curator

Use this when the operator wants to audit or prune the local catalog without touching media files.

```bash
yt-agent library stats --output json
yt-agent library channels --output json
yt-agent library playlists --output json
yt-agent library search "ambient mix" --output json
yt-agent library remove abc123def45 --dry-run --output json
```

Recipe: [examples/agents/library-curator.md](../examples/agents/library-curator.md)

### 5. Data management

Use these commands when you need to back up the catalog, migrate it to another machine, review download history, or reclaim disk space.

**Export and import**

`export` writes the full catalog to a portable JSON Lines file. `import` merges a previously exported file back into a catalog. Both support `--output json` for machine-readable feedback.

```bash
yt-agent export ~/backups/catalog-$(date +%Y%m%d).jsonl --output json
yt-agent import ~/backups/catalog-20260314.jsonl --dry-run --output json
# wait for approval
yt-agent import ~/backups/catalog-20260314.jsonl --output json
```

Export JSON envelope fields: `schema_version`, `command`, `status`, `summary` (with `exported` count), `warnings`, `errors`.

Import JSON envelope fields: `schema_version`, `command`, `status`, `summary` (with `imported`, `skipped`, `failed` counts), `warnings`, `errors`.

**History**

`history` is read-only and reports manifest-backed downloads. Use it to audit what was downloaded and when without touching the catalog.

```bash
yt-agent history --limit 20 --output json
yt-agent history --channel "Channel Name" --output json
```

History rows include `video_id`, `title`, `channel`, `upload_date`, `download_timestamp`, and `output_path`.

**Cleanup**

`cleanup` removes orphaned subtitle cache directories, empty channel directories, and leftover `.part` files. Always preview first.

```bash
yt-agent cleanup --dry-run --output json
# wait for approval
yt-agent cleanup --quiet --output json
```

Cleanup JSON envelope includes a `removed` list with `path` and `reason` for each item that would be or was removed.

## Output notes

- Read commands return structured JSON when `--output json` is used.
- Collection-style read commands return a top-level array.
- Detail-style read commands return a top-level object.
- Video-like JSON rows use `video_id` and `webpage_url` consistently.
- Catalog rows use `output_path` for the local media path when one exists.
- `library channels --output json` returns rows shaped as `{"channel": "..."}`.
- Mutating commands also support `--output json`; pair that with `--dry-run` for previews.
- In JSON mode, do not depend on prompts. Supply `--select` where needed.
- `plain` output is useful for shell pipelines; `table` output is best for direct human use.

## Mutation JSON envelopes

Mutating commands return a shared top-level shape in JSON mode:

- `schema_version`
- `command`
- `status`
- `summary`
- `warnings`
- `errors`

Status values:

- `ok`: the mutation completed and changed state
- `partial`: some requested work completed and some failed
- `noop`: nothing changed, or the command was a dry run
- `error`: the command failed before producing a success payload

Command-specific payload sections:

- `download` / `grab`: requested inputs, resolved targets, downloaded, skipped, failed, mode, subtitle flags, download root
- `index refresh` / `index add`: requested inputs, summary counts, subtitle flags, whether network subtitle fetching was attempted
- `clips grab`: locator, start/end offsets, padding, mode, output path, source, remote-fallback indicator
- `library remove`: requested ids, removed ids, not-found ids
- `pick`: query, results, selected items, selected URLs

Read-command row conventions:

- Search results, `info --entries`, `pick.results`, and mutation target lists use `video_id`, `title`, `channel`, `duration`, `duration_seconds`, `upload_date`, `webpage_url`, and `extractor_key`.
- Library row collections use `video_id`, `title`, `channel`, `upload_date`, `duration`, `duration_seconds`, `webpage_url`, `output_path`, `has_local_media`, `transcript_segments`, `chapters`, and `playlists`.
- Clip-hit rows use `result_id`, `source`, `range`, `start_seconds`, `end_seconds`, `title`, `channel`, `match`, `context`, `video_id`, `webpage_url`, `timestamp_url`, and `output_path`.

When a command fails before producing a success payload in JSON mode, `yt-agent` writes an error envelope to `stderr` with:

- `schema_version`
- `status`: always `error`
- `exit_code`
- `error_type`
- `message`
- `stderr`: present only when the underlying external tool returned stderr

This keeps machine-readable success payloads on `stdout` while preserving a structured error contract on `stderr`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `3` | Missing dependency |
| `4` | Invalid input |
| `5` | Invalid configuration |
| `6` | External tool failure (`yt-dlp`, `ffmpeg`) |
| `7` | Busy mutation lock |
| `8` | Storage/database issue |
| `130` | Interrupted |

## Suggested repo-level instructions

Drop this into a repo instruction file such as `CLAUDE.md` or `.codex/instructions.md`:

```text
If you need to search, download, index, or clip YouTube media, use yt-agent.

Rules:
- Prefer --output json when parsing results
- Use --select on commands that would otherwise prompt
- Use --dry-run before approval-gated mutations
- Use --quiet after approval
- Treat clip result IDs as short-lived handles
- If a command exits 7, another yt-agent mutation is already running

Typical flow:
1. yt-agent doctor --output json
2. yt-agent search "query" --output json
3. yt-agent grab "query" --select 1 --dry-run --output json
4. Wait for approval
5. yt-agent grab "query" --select 1 --quiet --output json
```

## Tool-specific prompt starters

- Codex: [examples/agents/codex.md](../examples/agents/codex.md)
- Claude Code: [examples/agents/claude-code.md](../examples/agents/claude-code.md)
- Gemini CLI: [examples/agents/gemini-cli.md](../examples/agents/gemini-cli.md)
- opencode: [examples/agents/opencode.md](../examples/agents/opencode.md)
- antigraviti: [examples/agents/antigraviti.md](../examples/agents/antigraviti.md)

## Troubleshooting

- Missing tools, lock conflicts, empty catalogs, and stale clip IDs: [docs/troubleshooting.md](troubleshooting.md)
- Storage model and safety notes: [docs/concepts.md](concepts.md)
- Quick recipes: [docs/recipes.md](recipes.md)
