# Repair and lifecycle events

`repair` previews conservative maintenance by default:

```console
yt-agent repair
yt-agent repair --output json
yt-agent repair --apply
```

Plans are derived from the same checks as `verify`. Repairs may rebuild derived FTS rows, reindex
valid manifest records, and remove orphaned subtitle-cache entries. `--apply` uses the operation
lock. Repair never deletes media files; the JSON contract includes `media_deleted: 0`.

Downloads and grabs can append stable lifecycle events to a separate private JSONL file:

```console
yt-agent download VIDEO --events-jsonl ./events.jsonl
yt-agent grab "query" --events-jsonl ./events.jsonl
```

Events use schema version 1, an event name, per-invocation sequence number, UTC timestamp, and
sanitized fields. Current names are `download.started`, `download.completed`, `download.failed`,
`download.skipped`, `index.completed`, and `index.failed`. This sink is separate from stdout, so
existing `--output json` responses remain one valid JSON document.
