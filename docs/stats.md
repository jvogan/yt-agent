# Public statistics history

`yt-agent stats` stores optional time-series snapshots of public counts reported by
yt-dlp. It does not require a Google API key or OAuth credentials.

Refresh specific catalog videos:

```bash
yt-agent stats refresh VIDEO_ID [VIDEO_ID ...]
```

With no IDs, the newest catalog batch is refreshed. `--limit` is bounded to 1-100
videos and also caps an explicit list. A dry run validates the catalog selection but
does not fetch metadata or write snapshots:

```bash
yt-agent stats refresh VIDEO_ID --dry-run --output json
```

View newest-first history and latest deltas:

```bash
yt-agent stats show VIDEO_ID --output json
yt-agent stats trends VIDEO_ID [VIDEO_ID ...]
```

Each snapshot records nullable view, like, and comment counts, its fetch time, and the
provider name. A missing count remains unknown rather than becoming zero. Deltas compare
the newest two snapshots and remain unknown when either value is unavailable.

## Future official provider

YouTube Data API `videos.list` can batch statistics, but it requires API-key/project
configuration, quota handling, and provider-specific availability semantics. That is
intentionally left as a future opt-in provider; the current command never asks for or
stores Google credentials.
