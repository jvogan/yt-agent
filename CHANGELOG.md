# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog, and this project follows pre-1.0 semantic versioning.

## [Unreleased]

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
