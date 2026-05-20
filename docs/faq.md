# FAQ

## Why not just use `yt-dlp`?

If plain `yt-dlp` already covers your workflow, use it directly. `yt-agent` adds higher-level ergonomics around it: safer defaults, a local catalog, transcript and chapter search, clip workflows, and agent-friendly JSON or dry-run modes. See [concepts.md](concepts.md), [architecture.md](architecture.md), and [agent-workflows.md](agent-workflows.md).

## Does `yt-agent` support Windows?

Windows support is currently experimental. Core CLI flows may work, but the project is better tested on macOS and Linux, and some filesystem hardening remains best-effort on Windows. See [support-matrix.md](support-matrix.md) and [getting-started.md](getting-started.md).

## How do I update `yt-dlp`?

Update `yt-dlp` with the same package manager you used to install it. For the documented paths, that usually means `brew upgrade yt-dlp` on macOS or `python3 -m pip install -U yt-dlp` on Linux. Afterward, rerun `yt-agent doctor` to confirm the binary is still on `PATH`; see [getting-started.md](getting-started.md) and [troubleshooting.md](troubleshooting.md).

## Why does clip extraction use `ffmpeg`?

`ffmpeg` is the local clip engine because it can cut already-downloaded media precisely without re-downloading the whole source. That keeps clip workflows fast, local-first, and predictable when the media file already exists on disk. If local media is missing, `clips grab` can fall back to a remote `yt-dlp` section download; see [concepts.md](concepts.md) and [command-reference.md](command-reference.md).

## Do I need a YouTube API key?

No. `yt-agent` is built around the `yt-dlp` runtime rather than the YouTube Data API, so there is no API key step in normal setup. See [getting-started.md](getting-started.md) and [architecture.md](architecture.md).

## How do I reset the catalog?

Start by running `yt-agent config path --output json` so you can see the active `catalog_file` location. Deleting that SQLite file resets the searchable catalog, and `yt-agent index refresh` rebuilds it from the download manifest and local sidecars. For the storage model, see [concepts.md](concepts.md) and [command-reference.md](command-reference.md).

## Why are downloads or searches slow?

Most slowdowns come from the external `yt-dlp` layer, network conditions, or YouTube-side throttling rather than the local wrapper. Start with `yt-agent doctor --output json`, then update `yt-dlp`, retry, and prefer direct URLs when you already know the target. See [troubleshooting.md](troubleshooting.md) and [workflow.md](workflow.md).

## Can I use this with Codex or Claude Code?

Yes. The CLI is designed to work well with coding agents through `--output json`, `--dry-run`, `--select`, and `--quiet`. Start with [agent-workflows.md](agent-workflows.md), then use the prompt starters in [examples/agents/codex.md](../examples/agents/codex.md) and [examples/agents/claude-code.md](../examples/agents/claude-code.md).

## Is `yt-agent` affiliated with YouTube?

No. `yt-agent` is an independent tool and is not affiliated with YouTube or Google. See the responsible-use note in [README.md](../README.md).

## What about age-restricted videos?

`yt-agent` does not add a special bypass for age-restricted content. If access requires authentication or cookies, that has to be solved at the `yt-dlp` layer, and those credentials should be treated as local secrets rather than repo data. See [README.md](../README.md) for the responsible-use warning and [troubleshooting.md](troubleshooting.md) for general recovery steps.
