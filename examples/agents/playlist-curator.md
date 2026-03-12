# Playlist Curator

Use this when the operator wants to inspect a playlist and choose a subset.

## Good prompt

```text
Inspect this playlist with yt-agent, show me the entries in a compact summary, then preview the items I choose before downloading anything.
```

## Good command sequence

```bash
yt-agent info "PLAYLIST_URL" --entries --output json
yt-agent download "PLAYLIST_URL" --select 1,3,5 --dry-run --output json
# wait for approval
yt-agent download "PLAYLIST_URL" --select 1,3,5 --quiet --output json
```

## Notes

- `info --entries --output json` is the right inspection step.
- `download --select ... --dry-run --output json` gives the exact resolved targets without writing files.
- Use `--quiet` after approval.
