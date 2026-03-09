# Getting Started

`yt-agent` is a terminal-first CLI for searching, downloading, cataloging, and clipping YouTube media with `yt-dlp`.

## Support Matrix

- macOS: first-class
- Linux: first-class
- Windows: experimental
- `yt-dlp`: required
- `ffmpeg`: required for local clip extraction and some post-processing
- `fzf`: optional for multi-select
- `mpv`: optional and reserved for future preview support

## Install

Repo-first install is the recommended public path for `0.2.x`.

### `uv`

```bash
uv tool install git+https://github.com/jvogan/yt-agent
yt-agent doctor
```

### `pipx`

```bash
pipx install git+https://github.com/jvogan/yt-agent
yt-agent doctor
```

### From source

```bash
git clone https://github.com/jvogan/yt-agent
cd yt-agent
uv sync --dev
uv run yt-agent doctor
```

## Install Runtime Tools

### macOS

```bash
brew install yt-dlp ffmpeg fzf
```

### Linux

```bash
python3 -m pip install -U yt-dlp
sudo apt-get install -y ffmpeg fzf
```

`doctor` reports what is installed and prints platform-specific install hints for missing tools.

## First Run

```bash
yt-agent config init
yt-agent config path
yt-agent doctor
```

The generated config uses the same defaults as [`config/config.sample.toml`](../config/config.sample.toml).

## Shell Completion

### `zsh`

```bash
yt-agent --install-completion
```

### `bash`

```bash
eval "$(_YT_AGENT_COMPLETE=bash_source yt-agent)"
```

### `fish`

```bash
_YT_AGENT_COMPLETE=fish_source yt-agent | source
```

## Quick Workflows

### Search and download

```bash
yt-agent search "lofi hip hop" --limit 5
yt-agent grab "lofi hip hop" --select 1
```

### Curate a playlist

```bash
yt-agent info "https://www.youtube.com/playlist?list=PL123" --entries
yt-agent download "https://www.youtube.com/playlist?list=PL123" --select 1,3
```

### Index and clip

```bash
yt-agent index refresh
yt-agent clips search "chorus drop" --source all
yt-agent clips grab transcript:12 --padding-before 2 --padding-after 2
```

## Troubleshooting

- If `doctor` says `yt-dlp` is missing, install it first; most other commands depend on it.
- If clip extraction fails locally, confirm `ffmpeg` is on `PATH`.
- If `fzf` is missing, selection falls back to prompt mode.
- If a search or download suddenly stops working, try updating `yt-dlp` first. Upstream site changes can break extractors.
