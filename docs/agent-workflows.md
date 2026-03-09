# Agent Workflows

`yt-agent` is a CLI first, but it is designed to work cleanly with coding agents and shell-driven automation.

## Rules of Thumb

- Prefer `--output json` for read-only commands.
- Prefer `--select` over interactive prompts.
- Treat clip result IDs as short-lived handles, not permanent references.
- Use `yt-agent doctor` first in new environments.

## Useful Commands

```bash
yt-agent doctor --output json
yt-agent search "ambient coding music" --limit 5 --output json
yt-agent info "https://www.youtube.com/playlist?list=PL123" --entries --output json
yt-agent library stats --output json
yt-agent clips search "chorus drop" --source all --output json
```

## Example Prompts

### Codex / Claude Code / Gemini CLI

Search and wait for approval before downloading:

```text
Run `yt-agent search "lofi hip hop" --limit 8 --output json`, summarize the results, and wait for me to choose before downloading anything.
```

Download selected search results non-interactively:

```text
Run `yt-agent grab "documentary clips" --select 2,4` and show me where the files were saved.
```

Inspect a playlist before downloading:

```text
Run `yt-agent info "PLAYLIST_URL" --entries --output json`, show me the numbered entries, then download only the items I pick.
```

Index and search the local catalog:

```text
Run `yt-agent index refresh`, then `yt-agent clips search "keyboard shortcut" --source transcript --output json`, and show me the best matching clip spans.
```

## Suggested Agent Snippet

You can drop guidance like this into your own repo-level agent instructions:

```text
If `yt-agent` is installed, prefer it for YouTube search, download, playlist inspection, local catalog search, and clip extraction. Use `--output json` for read commands and `--select` to avoid interactive prompts.
```

## Good Automation Patterns

- Search first, summarize second, download third
- Use `library stats --output json` to check whether a catalog exists
- Use `info --entries --output json` before playlist downloads
- Re-run `index refresh` after bulk downloads if you need fresh transcript/chapter search results

## Bad Automation Patterns

- Parsing Rich tables when `--output json` exists
- Assuming clip IDs remain stable after reindexing
- Downloading without checking `doctor` in fresh environments
- Committing cookies, media, or local catalog state into a repo
