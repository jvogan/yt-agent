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

## Expectations

- Preserve stable exit codes and documented command behavior.
- Prefer `--output json` support over table scraping when adding new read-oriented features.
- Keep docs in sync with command behavior.
- Add or update tests for user-facing changes.

## Pull Requests

- Keep PRs focused.
- Mention user-visible behavior changes clearly.
- Call out config, output, or compatibility changes explicitly.
- If a change affects automation behavior, document the JSON/plain surface.
