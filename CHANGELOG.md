# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog, and this project follows pre-1.0 semantic versioning.

## [Unreleased]

## [0.3.0] - 2026-03-14

### Added

- History-level secret scanning in CI with a pinned `gitleaks` CLI install and checksum verification, plus contributor and release-checklist guidance for local secret scans.
- Audio-only download mode via `--audio` flag on `download` and `grab`, backed by the `audio_format` and `default_mode` config keys.
- `--fetch-subs` and `--auto-subs` flags on `download` and `grab` to save subtitle files during download.
- `--from-file FILE` option on `download` for batch URL input from a text file.
- `yt-agent export` and `yt-agent import` for catalog portability.
- `yt-agent history` to inspect recent manifest-backed downloads.
- `yt-agent cleanup` to remove orphaned subtitle cache directories, empty channel directories, and leftover `.part` files.
- `yt-agent library channels` to list distinct channels in the catalog.
- `yt-agent library playlists` to list indexed playlists with video counts.
- `yt-agent library remove` to delete videos from the catalog by ID.
- `yt-agent config validate` to check a config file for errors.
- Shell completion setup support, `--verbose` / `-v` CLI logging, a TUI search/filter bar, and config environment variable overrides.
- Timestamped YouTube URLs (`?t=NNN`) in clip search and show output.

### Changed

- Enabled SQLite WAL mode for the catalog to reduce contention during local mutations.
- Upgraded library search from `LIKE` matching to FTS5-backed queries for faster catalog browsing.
- Expanded architecture documentation with module roles and end-to-end data-flow diagrams.
- Polished package metadata and PyPI release readiness for the `0.3.x` line.

### Fixed

- Replaced fragile `assert`-based validation paths in clip and CLI flows with explicit user-facing errors.
- Narrowed `grab` lock scope and removed duplicate catalog lookups in clip workflows.
- Added subprocess timeouts around subtitle sidecar fetches.
- Pruned stale subtitle cache directories when removing catalog entries.

### CI

- Added a tag-triggered PyPI release workflow.
- Reworked CI with separate lint and security stages, Windows coverage in the test matrix, Codecov uploads, and stricter build ordering.
- Raised the coverage floor to 85%, expanded property-based and module coverage, and enabled broader Ruff rule sets.

### Documentation

- Expanded command reference, architecture, support-matrix, troubleshooting, workflow, recipe, and release-checklist docs.
- Improved getting-started guidance for shell completions and environment-driven configuration.
- Added broader module docstrings across the public API surface.

## [0.2.0] - 2026-03-09

### Added

- Public-readiness docs for getting started, concepts, and agent workflows.
- Public community files including contributing, security, support, code of conduct, issue templates, and a PR template.
- `yt-agent --version`, `yt-agent config init`, and `yt-agent config path`.
- `--output table|json|plain` for read-oriented commands.
- `--select 1,3` for non-interactive search and playlist selection.
- `yt-agent library stats`.
- Cross-platform local-media opening in the TUI.

### Changed

- Lowered the supported Python floor to 3.11.
- Expanded CI to a multi-platform, multi-Python matrix and added distribution build checks.
- Reworked the README around quickstart, golden paths, support matrix, and responsible-use guidance.
- Clarified that the TUI is a read-mostly catalog browser.
- Kept `youtube-cli` as a transitional alias for the `0.2.x` line.

### Removed

- The duplicate sample config file. `config/config.sample.toml` is now the single canonical example.

## [0.1.0] - 2026-03-08

### Added

- Initial private release of `yt-agent` with terminal search, download, catalog, clip, and TUI workflows.
