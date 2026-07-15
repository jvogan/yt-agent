# Timeline enrichment and comments

SponsorBlock integration is available on `download` and `grab` without arbitrary yt-dlp
argument passthrough:

```console
yt-agent download VIDEO --sponsorblock
yt-agent download VIDEO --sponsorblock-remove
```

`--sponsorblock` uses yt-dlp's built-in `--sponsorblock-mark all`, preserving the media and
adding timeline chapters. Segment removal is never implied: it requires the explicit
`--sponsorblock-remove` flag and uses yt-dlp's built-in removal postprocessor.

Comment indexing is separate from transcripts and is always opt-in and bounded:

```console
yt-agent comments index VIDEO --limit 100 --dry-run --output json
yt-agent comments index VIDEO --limit 100
yt-agent comments search "useful phrase" --output json
```

The limit is constrained to 1–1000. Comment text and author names are terminal-sanitized and
length-bounded before storage, then indexed in dedicated `comments` and `comment_fts` tables.
Existing comments for the video are atomically replaced, so refreshes do not leave stale search
rows.
