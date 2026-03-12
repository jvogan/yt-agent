# Library Curator

Use this when the operator wants to understand, audit, or prune the local catalog without touching media files.

## Good prompt

```text
Use yt-agent to summarize my catalog by channel and playlist, identify likely duplicates or low-value items, and preview any removals before changing the catalog.
```

## Good command sequence

```bash
yt-agent library stats --output json
yt-agent library channels --output json
yt-agent library playlists --output json
yt-agent library search "ambient mix" --output json
yt-agent library remove abc123def45 def123abc45 --dry-run --output json
# wait for approval
yt-agent library remove abc123def45 def123abc45 --output json
```

## Notes

- `library remove` updates the catalog only. It does not delete media files.
- For human review, pair `library search` with `library show VIDEO_ID --output json`.
- If the catalog is empty, run `yt-agent index refresh --output json` first.
