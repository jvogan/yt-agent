# Recipes

These are the fastest paths through `yt-agent` for both direct terminal use and approval-safe agent use.

## Four core workflows

| Workflow | Best for | Human-first path | Agent-safe path |
|---|---|---|---|
| Direct download | You already know the URL or video ID | `download URL` | `download URL --dry-run --output json`, then approved `download URL --quiet --output json` |
| Search and curate | One-off videos, music, talks, tutorials | `search`, then `grab` | `search --output json`, then `grab --select ... --dry-run --output json` |
| Playlist curation | Picking a subset from a playlist | `info --entries`, then `download --select-playlist` | `info --entries --output json`, then `download --select ... --dry-run --output json` |
| Library and clips | Indexing, transcript search, clip extraction | `index refresh`, `clips search`, `clips grab`, `tui` | `index ... --output json`, `clips ... --output json`, `library ... --output json` |

## Direct URL download

Human:

```bash
yt-agent download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

Approval-safe agent:

```bash
yt-agent download "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --dry-run --output json
# wait for approval
yt-agent download "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --quiet --output json
```

## Search then download

Human:

```bash
yt-agent search "lofi hip hop" --limit 8
yt-agent grab "lofi hip hop"
```

Agent:

```bash
yt-agent search "lofi hip hop" --limit 8 --output json
yt-agent grab "lofi hip hop" --select 2,4 --dry-run --output json
# wait for approval
yt-agent grab "lofi hip hop" --select 2,4 --quiet --output json
```

## Curate a playlist

Human:

```bash
yt-agent info "PLAYLIST_URL" --entries
yt-agent download "PLAYLIST_URL" --select-playlist
```

Agent:

```bash
yt-agent info "PLAYLIST_URL" --entries --output json
yt-agent download "PLAYLIST_URL" --select 1,3,5 --dry-run --output json
# wait for approval
yt-agent download "PLAYLIST_URL" --select 1,3,5 --quiet --output json
```

## Audio-only download and subtitle sidecars

Human:

```bash
yt-agent download dQw4w9WgXcQ --audio
yt-agent download dQw4w9WgXcQ --fetch-subs
```

Agent:

```bash
yt-agent download dQw4w9WgXcQ --audio --dry-run --output json
yt-agent download dQw4w9WgXcQ --fetch-subs --dry-run --output json
```

Notes:

- `--auto-subs` requires `--fetch-subs`.
- `--fetch-subs` is explicit. Subtitle fetching is not automatic during `index refresh` or `index add`.

## Batch download from a file

Create a text file with one URL or video ID per line. Blank lines and `# comments` are ignored.

```bash
yt-agent download --from-file targets.txt
yt-agent download --from-file targets.txt --dry-run --output json
```

## Remote-only indexing

Index a target into the catalog without downloading media first:

```bash
yt-agent index add "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
yt-agent index add "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --dry-run --output json
yt-agent index add "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --fetch-subs --output json
```

Use `index refresh` when you already have manifest-backed local downloads:

```bash
yt-agent index refresh
yt-agent index refresh --fetch-subs --output json
```

## Index and extract clips

From search hits:

```bash
yt-agent index refresh --fetch-subs
yt-agent clips search "keyboard shortcut"
yt-agent clips show transcript:12
yt-agent clips grab transcript:12 --padding-before 2 --padding-after 3
```

Explicit clip coordinates:

```bash
yt-agent clips grab --video-id VIDEO_ID --start-seconds 12.5 --end-seconds 18.0
yt-agent clips grab --video-id VIDEO_ID --start-seconds 12.5 --end-seconds 18.0 --dry-run --output json
```

## Library cleanup

Inspect first:

```bash
yt-agent library stats
yt-agent library channels
yt-agent library playlists
yt-agent library search "ambient mix"
yt-agent library show VIDEO_ID
```

Preview catalog removals:

```bash
yt-agent library remove VIDEO_ID --dry-run
yt-agent library remove VIDEO_ID --dry-run --output json
```

Apply catalog removals:

```bash
yt-agent library remove VIDEO_ID
```

`library remove` updates the catalog only. It does not delete media files.

## Operator safety defaults

- Use `--dry-run` before approval-gated mutations.
- Use `--quiet` after approval to reduce terminal chatter.
- Use `--select` on commands that would otherwise prompt when you are in JSON mode.
- If a mutation exits with code `7`, another `yt-agent` mutation is already running.

## Tool-specific prompts

- Codex: [examples/agents/codex.md](../examples/agents/codex.md)
- Claude Code: [examples/agents/claude-code.md](../examples/agents/claude-code.md)
- Gemini CLI: [examples/agents/gemini-cli.md](../examples/agents/gemini-cli.md)
- opencode: [examples/agents/opencode.md](../examples/agents/opencode.md)
- antigraviti: [examples/agents/antigraviti.md](../examples/agents/antigraviti.md)
- Generic approval-safe flows: [examples/agents/approval-safe-download.md](../examples/agents/approval-safe-download.md)
