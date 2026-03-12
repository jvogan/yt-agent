---
name: yt-agent
description: Use when a task needs terminal-first YouTube search, playlist curation, download planning, catalog indexing, transcript or chapter search, or clip extraction through yt-agent. Especially useful for Codex, Claude Code, Gemini CLI, opencode, and similar agents that need approval-safe mutations, JSON output, dry runs, and prompt-free selection.
---

# yt-agent

Use this skill when the repo or machine has `yt-agent` available and the task involves YouTube search, download, indexing, catalog browsing, or clip extraction from the terminal.

## Core contract

- Use `--output json` whenever output will be parsed.
- Use `--select` on commands that would otherwise prompt.
- Use `--dry-run` before any approval-gated mutation.
- Use `--quiet` on the approved mutation to reduce terminal chatter.
- Treat clip result IDs like `transcript:12` as short-lived handles.
- Mutating commands are serialized by a local operation lock. Exit code `7` means another mutation is already running.
- `index refresh` and `index add` are local-first by default. Add `--fetch-subs` when transcript coverage matters.

## Install and verify

```bash
yt-agent doctor --output json
```

If `yt-dlp` is missing, install it first:

- macOS: `brew install yt-dlp`
- Linux: `python3 -m pip install -U yt-dlp`

`ffmpeg` is required for local clip extraction. `fzf` is optional.

## Preferred workflows

### Approval-safe search and download

```bash
yt-agent search "query" --limit 8 --output json
yt-agent grab "query" --select 1,3 --dry-run --output json
# wait for approval
yt-agent grab "query" --select 1,3 --quiet --output json
```

### Playlist curation

```bash
yt-agent info "PLAYLIST_URL" --entries --output json
yt-agent download "PLAYLIST_URL" --select 1,3 --dry-run --output json
```

### Clip hunting

```bash
yt-agent index refresh --fetch-subs --output json
yt-agent clips search "query" --output json
yt-agent clips grab transcript:12 --dry-run --output json
```

### Library curation

```bash
yt-agent library stats --output json
yt-agent library search "query" --output json
yt-agent library remove VIDEO_ID --dry-run --output json
```

## Safety notes

- Do not mutate until the user has approved the specific download, clip, or removal step.
- In JSON mode, do not rely on prompts. Supply `--select`.
- Prefer explicit clip extraction via `--video-id`, `--start-seconds`, and `--end-seconds` once the operator has approved an exact span.
- If a mutation returns exit code `7`, wait and retry rather than starting a second concurrent mutation.

## Read next when needed

- Operator guide: `/Users/jacobvogan/github_2/youtube_cli/docs/agent-workflows.md`
- Concepts and local state: `/Users/jacobvogan/github_2/youtube_cli/docs/concepts.md`
- Recipes: `/Users/jacobvogan/github_2/youtube_cli/docs/recipes.md`
- Prompt examples: `/Users/jacobvogan/github_2/youtube_cli/examples/agents/`
