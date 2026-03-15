# Release Checklist

Use this checklist before the first public release and before each tagged release after that.

## Public-readiness gate

- Confirm the repo stays private until the `P0` public-readiness work is complete.
- Confirm `config/config.sample.toml` is the only canonical sample config in the repo.
- Confirm the repo contains no cookies, exported browser sessions, local media, subtitle caches, or personal state files.
- Run `gitleaks git . --no-banner --redact` to scan for secrets and personal data.
- Confirm screenshots and brand assets are intentional public artifacts.

## Release validation

- Run `uv sync --dev`.
- Run `uv run ruff check .`.
- Run `uv run pytest`.
- Run `uv build`.
- Run `uv run --with twine twine check dist/*`.
- Run `uv tool run --from . yt-agent --help`.
- Confirm the GitHub Actions `Secret Scan` job passed on the release candidate branch or `main`.

## Documentation check

- README quickstart works from a clean machine.
- Install guidance covers `uv`, `pipx`, `yt-dlp`, `ffmpeg`, and `fzf`.
- Confirm `youtube-cli` is no longer installed (entry point was removed in 0.3.x).
- Platform support is explicit: macOS and Linux first-class, Windows experimental.
- The public-use note is explicit about non-affiliation, rights, platform terms, and local law.

## GitHub release prep

- Draft release notes from `CHANGELOG.md`.
- Review merged `codex/*` branches and delete stale remote branches after confirming they are no longer needed.
- Verify repo description, topics, and social preview image are public-ready.
- If the repo is being made public, enable branch protection on `main` with passing CI required.

## Post-tag follow-up

- Push the version tag.
- Publish the GitHub release.
- Record any known limitations or platform-specific caveats in the release notes.
