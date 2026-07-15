# Format inspection

Use `yt-agent formats TARGET` to inspect the formats yt-dlp reports for one video without
downloading it:

```bash
yt-agent formats abc123def45
yt-agent formats abc123def45 --output json
```

The normalized output includes codecs, dimensions, frame rate, approximate size, bitrate,
language, and yt-dlp's format note. It also shows a small set of reviewed selector presets for
1080p, smaller 720p, M4A audio, Opus audio, and source-quality archival downloads.

This is a read-only command. It intentionally does not accept arbitrary yt-dlp arguments or a
raw `--format` option. Playlist targets are rejected because formats belong to individual videos.
