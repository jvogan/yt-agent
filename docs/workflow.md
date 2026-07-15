# Workflow

This page maps the product into four repeatable workflows. Each workflow can be run directly by a human or driven by an agent with `--output json`, `--select`, `--dry-run`, and `--quiet`.

## 1. Direct download

Human-first:

1. `yt-agent download URL`
2. Optional preview: `yt-agent download URL --dry-run`

Agent-safe:

1. `yt-agent download URL --dry-run --output json`
2. Wait for approval
3. `yt-agent download URL --quiet --output json`

## 2. Search and curation

Human-first:

1. `yt-agent search "query"`
2. `yt-agent grab "query"`
3. Pick results interactively and let the download finish

Agent-safe:

1. `yt-agent search "query" --limit 8 --output json`
2. `yt-agent grab "query" --select 1,3 --dry-run --output json`
3. Wait for approval
4. `yt-agent grab "query" --select 1,3 --quiet --output json`

Playlist variant:

Human-first:

1. `yt-agent info PLAYLIST_URL --entries`
2. `yt-agent download PLAYLIST_URL --select-playlist`

Agent-safe:

1. `yt-agent info PLAYLIST_URL --entries --output json`
2. `yt-agent download PLAYLIST_URL --select 1,3,5 --dry-run --output json`
3. Wait for approval
4. `yt-agent download PLAYLIST_URL --select 1,3,5 --quiet --output json`

## 3. Library, index, and clip work

Human-first:

1. `yt-agent index refresh`
2. If transcript coverage matters: `yt-agent index refresh --fetch-subs`
3. `yt-agent clips search "query"`
4. `yt-agent clips show RESULT_ID`
5. `yt-agent clips grab RESULT_ID --padding-before 2 --padding-after 4`
6. `yt-agent tui`

Agent-safe:

1. `yt-agent index refresh --fetch-subs --output json`
2. `yt-agent clips search "query" --output json`
3. `yt-agent clips show RESULT_ID --output json`
4. `yt-agent clips grab RESULT_ID --dry-run --output json`
5. After approval, rerun with `--quiet --output json`

If transcript coverage is missing, rerun step 1 with `--fetch-subs`.

## 4. Curate, sync, and queue

1. Add notes/tags/bookmarks with `yt-agent curate ...`; search them with `curate search`.
2. Save recurring work with `yt-agent sync add NAME CHANNEL_OR_PLAYLIST_URL --kind KIND`, where
   `KIND` is `channel` or `playlist`.
3. Preview an incremental pass with `yt-agent sync run --dry-run --output json`.
4. Queue typed work with `yt-agent queue add download TARGET` and run it synchronously with
   `yt-agent queue run-next`.

For live material, use `live record`; inspect available encodings first with `formats TARGET`.
Use `open` or `play` for local files, catalog IDs, and clip result timestamps.

## 5. Verify, back up, and repair

1. `yt-agent verify --deep --output json`
2. `yt-agent backup create backup.json --output json`
3. Preview derived-state fixes with `yt-agent repair --output json`.
4. Apply only reviewed repairs with `yt-agent repair --apply --output json`. Backup restore also
   supports a validation-only dry run.

## 6. Agent-operator automation

Use the CLI as a stable execution layer:

1. Inspect with `doctor --deep`, `search`, `info`, `formats`, `library`, or `clips ... --output json`
2. Preview mutations with `--dry-run --output json`
3. Wait for approval
4. Execute the approved mutation with `--quiet --output json`
5. Treat exit code `7` as a busy lock and retry later

## Recipe links

- [Approval-safe download](../examples/agents/approval-safe-download.md)
- [Playlist curator](../examples/agents/playlist-curator.md)
- [Clip hunter](../examples/agents/clip-hunter.md)
- [Library curator](../examples/agents/library-curator.md)
- [Codex prompt starter](../examples/agents/codex.md)
- [Claude Code prompt starter](../examples/agents/claude-code.md)
- [Gemini CLI prompt starter](../examples/agents/gemini-cli.md)
- [opencode prompt starter](../examples/agents/opencode.md)
- [antigraviti prompt starter](../examples/agents/antigraviti.md)
