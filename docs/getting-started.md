# Getting Started

`yt-agent` is a terminal-first CLI for searching, downloading, cataloging, and clipping YouTube media with `yt-dlp`.

## Requirements

| Tool | Status | Purpose |
|---|---|---|
| `yt-dlp` | **Required** | Search, metadata, downloads |
| `ffmpeg` | Optional for basic use | Required for local clip extraction and some post-processing |
| `fzf` | Optional | Terminal multi-select (falls back to prompt mode) |
| `mpv` | Optional | Reserved for future preview support |

| Platform | Status |
|---|---|
| macOS | First-class |
| Linux | First-class |
| Windows | Experimental |

## Install Runtime Tools

### macOS

Basic search and download:

```bash
brew install yt-dlp
```

Optional follow-on tools:

```bash
brew install ffmpeg fzf
```

### Linux

Basic search and download:

```bash
python3 -m pip install -U yt-dlp
```

Optional follow-on tools:

```bash
sudo apt-get install -y ffmpeg fzf
```

## Install yt-agent

### With [`uv`](https://docs.astral.sh/uv/) (recommended)

```bash
uv tool install git+https://github.com/jvogan/yt-agent
yt-agent doctor
```

### With `pipx`

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

## First Run

```bash
yt-agent doctor
yt-agent download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
yt-agent config init     # optional: write a starter config
yt-agent config path     # show where config and data live
```

The generated config uses the same defaults as [`config/config.sample.toml`](../config/config.sample.toml). All settings are optional - `yt-agent` works out of the box with sensible defaults.

## Shell Completion

```bash
yt-agent completions install
```

Run that command from the shell you want to configure. The detailed guide covers the generated file locations, verification commands, and troubleshooting for `bash`, `zsh`, and `fish`.

For shell-by-shell notes and team-friendly setup guidance, see [shell-completion.md](shell-completion.md).

## Quick workflows

### Direct download

If you already have a URL or video ID, this is the fastest path:

```bash
yt-agent download "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
yt-agent download dQw4w9WgXcQ --dry-run
```

### Search and download

`grab` searches, shows results, prompts you to pick, and downloads:

```bash
yt-agent grab "lofi hip hop"
# shows numbered results, you type 1,3 to pick, downloads start
```

To browse results first without downloading anything:

```bash
yt-agent search "lofi hip hop" --limit 5
# note the video ID from the results table, then:
yt-agent download abc123def45
```

### Curate a playlist

See what's in a playlist, then pick which entries to download:

```bash
yt-agent download "https://www.youtube.com/playlist?list=PL123" --select-playlist
# shows all entries, you pick interactively, only selected items download
```

Or preview the playlist first without downloading:

```bash
yt-agent info "https://www.youtube.com/playlist?list=PL123" --entries
```

### Index and clip

After downloading videos, index them into the searchable catalog, then find specific moments and cut clips:

```bash
yt-agent index refresh
# add --fetch-subs when transcript coverage matters
yt-agent index refresh --fetch-subs
yt-agent clips search "chorus drop"
# shows matches with result IDs like transcript:12, chapter:3
yt-agent clips show transcript:12
# inspect the match in context before extracting
yt-agent clips grab transcript:12 --padding-before 2 --padding-after 2
```

`index refresh` is local-first by default. It reuses any sidecars that already exist. Add `--fetch-subs` when transcript coverage matters. You can also use `--fetch-subs` during download:

```bash
yt-agent download abc123def45 --fetch-subs
```

### Curate your library

```bash
yt-agent library stats
yt-agent library channels
yt-agent library playlists
yt-agent library search "ambient mix"
```

## Uninstall

Remove the tool:

```bash
uv tool uninstall yt-agent     # if installed with uv
pipx uninstall yt-agent        # if installed with pipx
```

This removes the CLI but leaves your downloaded media and local data intact. To also remove local data:

```bash
rm -rf ~/.config/yt-agent
rm -rf ~/.local/share/yt-agent
```

Downloaded media in `~/Media/YouTube` is yours to keep or remove separately.

For troubleshooting, workflow recipes, operator-safe agent prompts, and the support matrix:

- [docs/workflow.md](workflow.md)
- [docs/recipes.md](recipes.md)
- [docs/agent-workflows.md](agent-workflows.md)
- [docs/support-matrix.md](support-matrix.md)
- [docs/troubleshooting.md](troubleshooting.md)
- [docs/shell-completion.md](shell-completion.md)
- [docs/faq.md](faq.md)
