# youtube_cli

Private terminal-first YouTube search and download workflow built on `yt-dlp`.

## Features

- search YouTube from the terminal via `yt-dlp`
- interactively pick results with prompts and optional `fzf`
- download whole videos or playlist entries into an organized local library
- skip duplicates via a persistent `yt-dlp` download archive
- append a JSONL manifest for every successful download

## Runtime Requirements

- Python 3.14+
- `uv`
- `yt-dlp`

Optional tools:

- `ffmpeg` for muxing and post-processing
- `fzf` for interactive multi-select
- `mpv` reserved for future preview support

## Installation

```bash
uv sync --dev
uv run youtube-cli doctor
```

## Commands

```bash
uv run youtube-cli doctor
uv run youtube-cli search "lofi hip hop"
uv run youtube-cli pick "documentary clips" --fzf
uv run youtube-cli info https://www.youtube.com/watch?v=dQw4w9WgXcQ
uv run youtube-cli info https://www.youtube.com/playlist?list=PL123 --entries
uv run youtube-cli download https://www.youtube.com/watch?v=dQw4w9WgXcQ
uv run youtube-cli download https://www.youtube.com/playlist?list=PL123 --select-playlist
uv run youtube-cli grab "synthwave mix"
```

## Default Paths

- Config: `~/.config/youtube-cli/config.toml`
- Archive: `~/.local/share/youtube-cli/archive.txt`
- Manifest: `~/.local/share/youtube-cli/downloads.jsonl`
- Download root: `~/Media/YouTube`

Downloads are organized as:

```text
<download_root>/<channel>/<upload_date> - <title> [<video_id>].<ext>
```

## Example Config

```toml
download_root = "~/Media/YouTube"
archive_file = "~/.local/share/youtube-cli/archive.txt"
manifest_file = "~/.local/share/youtube-cli/downloads.jsonl"
search_limit = 10
video_format = "bv*+ba/b"
audio_format = "bestaudio/best"
default_mode = "video"
selector = "prompt"
write_thumbnail = true
write_description = true
write_info_json = true
embed_metadata = true
embed_thumbnail = false
```

## Development

```bash
uv sync --dev
uv run ruff check .
uv run pytest
```
