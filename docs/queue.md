# Persistent job queue

The queue records work for later synchronous execution. It does not start a daemon or run work
in the background.

```bash
yt-agent queue add download VIDEO_ID --max-retries 2
yt-agent queue add download VIDEO_ID --audio --fetch-subs
yt-agent queue add index VIDEO_ID
yt-agent queue add sync PLAYLIST_URL
yt-agent queue list
yt-agent queue show 1
yt-agent queue run-next
```

`sync` refreshes the target's current remote metadata, playlist membership, chapters, and any
requested subtitles through the same indexing flow as `index add`. A queue worker processes one
job at a time under a local lock. Each attempt is recorded; transient failures use bounded
exponential backoff and stop after the configured retry count. If a synchronous worker is
interrupted, the next worker safely recovers its abandoned running job.

Pending or failed jobs can be cancelled, and failed or cancelled jobs can be manually retried:

```bash
yt-agent queue cancel 1
yt-agent queue retry 1
```

Mutating queue commands support `--dry-run`, and every command supports `--output json`. Dry runs
do not create the queue database. Job payloads contain only allowlisted options; arbitrary shell
commands and raw yt-dlp arguments are not accepted.
