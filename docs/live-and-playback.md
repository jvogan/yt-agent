# Live recording and playback

Record a current or scheduled YouTube live stream synchronously:

```bash
yt-agent live record VIDEO_ID
yt-agent live record VIDEO_ID --wait-seconds 300 --live-from-start
yt-agent live record VIDEO_ID --from-now
yt-agent live record VIDEO_ID --dry-run --output json
```

The wait is bounded to 24 hours. The command accepts no raw yt-dlp arguments. Completed recordings
use the configured private download tree, are appended to the normal manifest, and are indexed in
the catalog. A dry run validates and normalizes the target without contacting YouTube.

Use `play` or `open` with a local file, catalog video ID, clip-search result ID, or YouTube URL:

```bash
yt-agent play ~/Media/YouTube/video.mp4
yt-agent play abc123def45
yt-agent open transcript:42
yt-agent open 'https://www.youtube.com/watch?v=abc123def45&t=90'
```

mpv is preferred and receives local clip timestamps directly. If mpv is unavailable, yt-agent
uses the platform's system opener. Launches always use argument arrays with an explicit option
boundary for mpv; no shell or arbitrary player arguments are accepted. Use `--dry-run --output
json` to inspect resolution and launcher selection without opening anything.
