# Saved-source sync

Save a YouTube channel or playlist once, then incrementally process newly observed video IDs:

```console
yt-agent sync add research https://www.youtube.com/@example/videos --kind channel
yt-agent sync list
yt-agent sync run research --latest 5 --index --output json
yt-agent sync run --since 2026-01-01 --download --dry-run
yt-agent sync remove research
```

Definitions and seen-video IDs are stored in a private `sources.json` beside the catalog. Runs
are deterministic: candidates are ordered by upload date and video ID, already-seen IDs are
ignored, and `--latest` is applied per source. `--since` requires `YYYY-MM-DD`; entries without
an upload date are excluded when it is used.

Indexing is enabled by default. Downloading is opt-in and respects the yt-dlp download archive.
Dry runs fetch remote metadata but do not download, index, or update `sources.json`. There is no
scheduler or background daemon; invoke `sync run` from your scheduler of choice if desired.
