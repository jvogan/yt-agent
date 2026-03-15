# Migration Guide: `youtube-cli` to `yt-agent`

This project was renamed from `youtube-cli` to `yt-agent`.

The rename affects the CLI command, Python package imports, and the `python -m ...` entrypoint. To keep upgrades low-friction, the old command and package name still work as compatibility aliases in the current release line.

## Timeline

- `0.2.x`: the `yt-agent` name is the primary public name, but `youtube-cli` still works as a compatibility alias.
- `0.3.x`: the `youtube-cli` command and `src/youtube_cli/` shim still work. Migrate now rather than waiting for the next minor release.
- `0.4.0` or the next minor release after `0.3.x`: plan for the `youtube-cli` command alias and the `youtube_cli` import shim to be removed.

If you maintain scripts, docs, shell aliases, or Python integrations, treat `0.3.x` as the last safe window to move everything to `yt-agent` / `yt_agent`.

## What Still Works Today

The following compatibility paths are intentionally still available in `0.2.x` and `0.3.x`:

- CLI command: `youtube-cli`
- Module execution: `python -m youtube_cli`
- Python package shim: `src/youtube_cli/`

Today the `youtube-cli` console script still points at `yt_agent.cli:main`, and `python -m youtube_cli` still forwards to the same `main()` function used by `python -m yt_agent`.

The `src/youtube_cli/` package is only a compatibility shim. It re-exports the old package name and maps a fixed set of submodules to `yt_agent.*` at import time.

## Breaking Import Changes

The rule is simple:

- Old: `youtube_cli`
- New: `yt_agent`

Update all imports to the new package name before the shim is removed.

| Old import | New import |
|---|---|
| `import youtube_cli` | `import yt_agent` |
| `from youtube_cli import __version__` | `from yt_agent import __version__` |
| `import youtube_cli.archive` | `import yt_agent.archive` |
| `import youtube_cli.catalog` | `import yt_agent.catalog` |
| `import youtube_cli.chapters` | `import yt_agent.chapters` |
| `import youtube_cli.cli` | `import yt_agent.cli` |
| `import youtube_cli.clips` | `import yt_agent.clips` |
| `import youtube_cli.config` | `import yt_agent.config` |
| `import youtube_cli.errors` | `import yt_agent.errors` |
| `import youtube_cli.indexer` | `import yt_agent.indexer` |
| `import youtube_cli.library` | `import yt_agent.library` |
| `import youtube_cli.manifest` | `import yt_agent.manifest` |
| `import youtube_cli.models` | `import yt_agent.models` |
| `import youtube_cli.security` | `import yt_agent.security` |
| `import youtube_cli.selector` | `import yt_agent.selector` |
| `import youtube_cli.transcripts` | `import yt_agent.transcripts` |
| `import youtube_cli.tui` | `import yt_agent.tui` |
| `import youtube_cli.yt_dlp` | `import yt_agent.yt_dlp` |

Equivalent `from ... import ...` statements should be updated the same way.

### Shim Scope

The compatibility shim only covers the modules listed above, plus:

- `youtube_cli.cli.app`
- `youtube_cli.cli.main`
- `python -m youtube_cli`

Do not build new integrations against `youtube_cli`. New code should import only from `yt_agent`.

## CLI Migration

Update scripts, shell aliases, cron jobs, CI steps, and documentation:

```bash
# old
youtube-cli doctor
youtube-cli download URL
python -m youtube_cli search "query"

# new
yt-agent doctor
yt-agent download URL
python -m yt_agent search "query"
```

During `0.2.x` and `0.3.x`, the old command still works. The point of that alias is to give you time to change automation before the compatibility layer disappears.

## Config and Path Migration

No config key rename is required for this transition.

For scripts or docs that mention old app-specific paths, update them to the current `yt-agent` paths:

- Config file: `~/.config/yt-agent/config.toml`
- Archive: `~/.local/share/yt-agent/archive.txt`
- Manifest: `~/.local/share/yt-agent/downloads.jsonl`
- Catalog: `~/.local/share/yt-agent/catalog.sqlite`

If you already use those `yt-agent` paths, no config migration is needed.

## Recommended Update Steps

1. Replace every `youtube-cli` command invocation with `yt-agent`.
2. Replace every `youtube_cli` Python import with `yt_agent`.
3. Replace `python -m youtube_cli` with `python -m yt_agent`.
4. Check shell aliases, CI jobs, cron jobs, docs, and copy-paste snippets for the old name.
5. If you reference app data paths directly, confirm they point at `~/.config/yt-agent` and `~/.local/share/yt-agent`.
6. Release or deploy those updates while `0.3.x` still ships the compatibility layer.

## Quick Checks

Use these searches to find old references in your own repo or automation:

```bash
rg -n "youtube-cli|youtube_cli"
```

If that search returns nothing in your code, scripts, and docs, your migration is likely complete.
