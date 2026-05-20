# Support Matrix

This matrix describes what `yt-agent` is intended to support today, not every configuration that might happen to work.

## Platforms

| Platform | Status | Notes |
|---|---|---|
| macOS | First-class | Best-tested path for `brew install yt-dlp ffmpeg fzf` |
| Linux | First-class | Best-tested with `yt-dlp` via `pip` and `ffmpeg`/`fzf` via package manager |
| Windows | Experimental | Core flows may work, but docs and testing are not yet as strong |

## Tools

| Tool | Status | Why it matters |
|---|---|---|
| `yt-dlp` | Required | Search, metadata, downloads, section downloads |
| `ffmpeg` | Required for clips | Local clip extraction and some media post-processing |
| `fzf` | Optional | Multi-select convenience in interactive terminals |
| `mpv` | Optional | Reserved for future preview support |

## Workflow surface

| Surface | Status | Notes |
|---|---|---|
| Human CLI | First-class | Table output and prompts are the default experience |
| JSON output | First-class | Use `--output json` for automation and scripts |
| Dry-run mutations | First-class | `download`, `grab`, `index`, `clips grab`, and `library remove` support `--dry-run` |
| Quiet mutations | First-class | Use `--quiet` after approval to reduce chatter |
| Operation lock | First-class | Mutating commands serialize through a local lock; busy state exits with code `7` |
| Textual TUI | Read-mostly | Best for browsing, not for end-to-end download or clip automation |

## Windows gaps

- Default config/state paths now resolve to `%APPDATA%` and `%LOCALAPPDATA%` on Windows instead of XDG-style `~/.config` and `~/.local/share`.
- Path handling is built on `pathlib` and sanitized filename components, so path separators are reviewed and expected to be safe for normal local workflows.
- Subprocess calls use explicit argv lists with `shell=False`; no Windows-specific shell quoting workaround is currently required.
- `operation_lock` uses `msvcrt.locking` on Windows instead of POSIX `fcntl.flock`. It still protects concurrent local mutations, but it has less real-world coverage than the macOS/Linux path.
- `ensure_private_file` and `ensure_private_directory` do not apply Windows ACLs. On Windows they create the path but skip POSIX mode enforcement, so privacy hardening remains best-effort.
- CI and local validation are still centered on macOS and Linux. Windows remains experimental until it has broader automated coverage.

## Agent notes

- The `youtube-cli` transitional alias has been removed. Use `yt-agent` exclusively.
- In JSON mode, commands that would otherwise prompt should be given `--select`.
- Clip search results are useful handles, but explicit clip extraction via `--video-id`, `--start-seconds`, and `--end-seconds` is the most durable automation path.

For install detail, see [docs/getting-started.md](getting-started.md). For recipes, see [docs/recipes.md](recipes.md).
