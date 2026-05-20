# Contributing

Thanks for contributing to `yt-agent`.

## Before You Start

- For larger changes, open an issue or start a discussion in the issue tracker first.
- Keep the project CLI-first. Agent integrations and docs are welcome, but they should not replace a solid terminal workflow.
- Do not commit cookies, exported browser sessions, downloaded media, subtitle caches, or personal local state.

## Development Setup

```bash
uv sync --dev
uv run ruff check .
uv run pytest
uv build
```

## Pre-commit Hooks

Install `pre-commit` locally, then enable the repository hooks:

```bash
uv tool install pre-commit
pre-commit install
```

To run the full hook suite on demand:

```bash
pre-commit run --all-files
```

## Local Security Checks

Run a full-history secret scan before opening a PR or cutting a release:

```bash
gitleaks git . --no-banner --redact
```

If `gitleaks` is not installed locally yet, install it first. On macOS:

```bash
brew install gitleaks
```

If you want to inspect the current working tree, including untracked files, run:

```bash
gitleaks dir . --no-banner --redact
```

## Expectations

- Preserve stable exit codes and documented command behavior.
- Prefer `--output json` support over table scraping when adding new read-oriented features.
- Keep docs in sync with command behavior.
- Add or update tests for user-facing changes.
- Keep the repo free of cookies, local state files, and secret-like artifacts.

## Pull Requests

- Keep PRs focused.
- Mention user-visible behavior changes clearly.
- Call out config, output, or compatibility changes explicitly.
- If a change affects automation behavior, document the JSON/plain surface.
