# Clip Hunter

Use this when the operator wants exact transcript or chapter moments turned into clips.

## Good prompt

```text
Refresh the local yt-agent catalog, search for transcript hits for "keyboard shortcut", show me the best spans, and preview the clip extraction before writing anything.
```

## Good command sequence

```bash
yt-agent index refresh --fetch-subs --output json
yt-agent clips search "keyboard shortcut" --source transcript --output json
yt-agent clips show transcript:12 --output json
yt-agent clips grab transcript:12 --padding-before 2 --padding-after 3 --dry-run --output json
# optional approved write
yt-agent clips grab transcript:12 --padding-before 2 --padding-after 3 --quiet --output json
```

If transcript coverage is missing, rerun indexing with subtitle fetch enabled:

```bash
yt-agent index refresh --fetch-subs --output json
```

## Durable alternative

If you already know the exact span, skip short-lived clip IDs and use explicit coordinates:

```bash
yt-agent clips grab --video-id VIDEO_ID --start-seconds 12.5 --end-seconds 18.0 --dry-run --output json
```
