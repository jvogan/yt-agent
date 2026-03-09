# yt-agent

![yt-agent hero](assets/brand/yt-agent-hero.png)

`yt-agent` is a terminal-first YouTube workflow for search, organized downloads, local cataloging, transcript and chapter search, and deterministic clip extraction.

It is built on `yt-dlp`, local sidecar files, SQLite FTS, and a small Textual TUI.

`youtube-cli` is still shipped as a temporary alias for one transition release.

## Public-use note

Use this tool responsibly. You are responsible for complying with platform terms, copyright, licenses, and any local laws that apply to the media you search, download, index, or clip.

## Features

- Search YouTube from the terminal via `yt-dlp`
- Interactively pick results with prompts and optional `fzf`
- Download whole videos or playlist selections into an organized local library
- Skip duplicates via a persistent `yt-dlp` archive file
- Append a JSONL manifest for every successful download
- Index downloads and remote targets into a local SQLite catalog
- Search native chapters and subtitle transcripts with FTS5
- Extract clips from local media with `ffmpeg`, with remote fallback when needed
- Browse the local catalog in a read-mostly Textual TUI

## Screens

![Search screenshot](assets/screenshots/search.png)

![Clip search screenshot](assets/screenshots/clips-search.png)

![TUI screenshot](assets/screenshots/tui.png)

Additional docs assets are in [`assets/screenshots/`](assets/screenshots/) and [`assets/brand/`](assets/brand/).

## Runtime requirements

- Python 3.14+
- `uv`
- `yt-dlp`

Optional tools:

- `ffmpeg` for muxing, post-processing, and local clip extraction
- `fzf` for interactive multi-select
- `mpv` reserved for future preview support

## Install

```bash
uv sync --dev
uv run yt-agent doctor
```

## Commands

```bash
uv run yt-agent doctor
uv run yt-agent search "lofi hip hop"
uv run yt-agent pick "documentary clips" --fzf
uv run yt-agent grab "synthwave mix"
uv run yt-agent info https://www.youtube.com/watch?v=dQw4w9WgXcQ
uv run yt-agent info https://www.youtube.com/playlist?list=PL123 --entries
uv run yt-agent download https://www.youtube.com/watch?v=dQw4w9WgXcQ
uv run yt-agent download https://www.youtube.com/playlist?list=PL123 --select-playlist
uv run yt-agent index refresh
uv run yt-agent index add https://www.youtube.com/watch?v=dQw4w9WgXcQ
uv run yt-agent clips search "chorus drop" --source all
uv run yt-agent clips show transcript:12
uv run yt-agent clips grab transcript:12 --padding-before 2 --padding-after 2
uv run yt-agent library list
uv run yt-agent library search "ambient mix"
uv run yt-agent library show dQw4w9WgXcQ
uv run yt-agent tui
```

## Default paths

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

## Sample config

See [`config/config.sample.toml`](config/config.sample.toml).

## Docs

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/workflow.md`](docs/workflow.md)
- [`docs/roadmap.md`](docs/roadmap.md)

## Development

```bash
uv sync --dev
uv run ruff check .
uv run pytest
```
