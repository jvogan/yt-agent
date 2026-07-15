# Catalog TUI

Launch the local catalog browser with:

```bash
yt-agent tui
```

The video table loads 50 records at a time directly from SQLite. Press `n` for the
next page and `p` for the previous page. Changing the source or search text resets to
the first page. Search is database-backed across video title, channel, and video ID,
so results are not limited to records already visible in the table.

Available actions:

- `o` opens an existing local media file through the platform launcher.
- `c` copies the selected video ID for use in a clip search workflow.
- `d` adds a persistent download job for the selected video to `jobs.sqlite`.
- `r` refreshes sources and the current result page.

Actions pass structured values to Python services. The TUI does not construct or run
shell command strings. Queued downloads are persistent and can be inspected or run
with the `yt-agent queue` commands.
