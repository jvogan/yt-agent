# yt-agent

![yt-agent hero](assets/brand/yt-agent-hero.png)

`yt-agent` is a terminal-first CLI for YouTube search, organized downloads, local cataloging, transcript and chapter search, and deterministic clip extraction.

It is built on `yt-dlp`, local sidecar files, SQLite FTS, and a small Textual catalog browser. It works well for direct human use in the terminal and for coding agents that need structured, scriptable output.

`youtube-cli` is still shipped as a transitional alias during the `0.2.x` release line. It is planned for removal in the next minor release after deprecation is documented.

## Responsible Use

`yt-agent` is not affiliated with YouTube or Google.

You are responsible for complying with platform terms, copyright, licenses, permissions, and local law when you search, download, index, or clip media. Do not commit cookies, exported browser sessions, downloaded media, or private subtitle/data caches to this repo.

## Three Promises

- Search and download media from the terminal with organized output and duplicate avoidance.
- Build a local catalog that supports transcript and chapter search.
- Extract deterministic clips from local media, with remote fallback when needed.

## Quickstart

Install the runtime tools you want, then install `yt-agent` from the repo:

```bash
brew install yt-dlp ffmpeg fzf
uv tool install git+https://github.com/jvogan/yt-agent
yt-agent doctor
```

Linux users can use `python3 -m pip install -U yt-dlp` and `sudo apt-get install -y ffmpeg fzf`.

If you prefer `pipx`:

```bash
pipx install git+https://github.com/jvogan/yt-agent
yt-agent doctor
```

First-run setup:

```bash
yt-agent config init
yt-agent config path
```

More install detail is in [docs/getting-started.md](docs/getting-started.md).

## Golden Paths

### 1. Search and download one video

```bash
yt-agent search "lofi hip hop" --limit 5
yt-agent grab "lofi hip hop" --select 1
```

### 2. Curate a playlist before download

```bash
yt-agent info "https://www.youtube.com/playlist?list=PL123" --entries
yt-agent download "https://www.youtube.com/playlist?list=PL123" --select 1,3
```

### 3. Index local media and cut a clip

```bash
yt-agent index refresh
yt-agent clips search "chorus drop" --source all
yt-agent clips grab transcript:12 --padding-before 2 --padding-after 2
```

## Agent-Friendly Surface

`yt-agent` is a CLI first, not a skill package, but it is designed to work cleanly with coding agents.

- Use `--output table|json|plain` on read-oriented commands.
- Use `--select 1,3` to bypass interactive prompts for search and playlist selection.
- Use `yt-agent library stats --output json` for a quick local catalog summary.
- Stable exit codes are documented and preserved for scripting.
- `youtube-cli` remains available as a transitional alias in `0.2.x`.

Agent recipes and copy-paste prompts live in [docs/agent-workflows.md](docs/agent-workflows.md).

## Screens

![Search screenshot](assets/screenshots/search.png)

![Clip search screenshot](assets/screenshots/clips-search.png)

![TUI screenshot](assets/screenshots/tui.png)

The TUI is a read-mostly catalog browser today. It is useful for browsing, inspecting metadata, and opening local media, but it is not positioned as a full interactive media workstation yet.

## Support Matrix

- macOS: first-class
- Linux: first-class
- Windows: experimental
- `yt-dlp`: required
- `ffmpeg`: required for local clip extraction and some post-processing
- `fzf`: optional for terminal multi-select
- `mpv`: optional and reserved for future preview features

## Command Examples

```bash
yt-agent doctor
yt-agent search "documentary clips" --output json
yt-agent pick "documentary clips" --select 2
yt-agent info "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
yt-agent info "https://www.youtube.com/playlist?list=PL123" --entries --output plain
yt-agent download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
yt-agent download "https://www.youtube.com/playlist?list=PL123" --select 1,3
yt-agent index refresh
yt-agent index add "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
yt-agent clips search "chorus drop" --source all --output json
yt-agent clips show transcript:12 --output plain
yt-agent library list --output plain
yt-agent library search "ambient mix" --output json
yt-agent library show dQw4w9WgXcQ
yt-agent library stats
yt-agent tui
```

## What Gets Stored Locally

- Config: `~/.config/yt-agent/config.toml`
- Archive: `~/.local/share/yt-agent/archive.txt`
- Manifest: `~/.local/share/yt-agent/downloads.jsonl`
- Catalog: `~/.local/share/yt-agent/catalog.sqlite`
- Download root: `~/Media/YouTube`
- Clips root: `~/Media/YouTube/_clips`

Downloads are organized as:

```text
<download_root>/<channel>/<upload_date> - <title> [<video_id>].<ext>
```

Clips are organized as:

```text
<clips_root>/<channel>/<title> [<video_id>] <start-end> <label>.<ext>
```

More detail is in [docs/concepts.md](docs/concepts.md).

## Docs

- [Getting Started](docs/getting-started.md)
- [Concepts](docs/concepts.md)
- [Agent Workflows](docs/agent-workflows.md)
- [Architecture](docs/architecture.md)
- [Workflow](docs/workflow.md)
- [Roadmap](docs/roadmap.md)
- [Release Checklist](docs/release-checklist.md)

## Development

```bash
uv sync --dev
uv run ruff check .
uv run pytest
uv build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution expectations and [THIRD_PARTY.md](THIRD_PARTY.md) for upstream acknowledgements.
