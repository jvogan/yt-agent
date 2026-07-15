# Library curation

User-owned curation data lives in the versioned catalog and is included in catalog backups.

```bash
yt-agent curate set VIDEO_ID --note "Revisit this" --rating 5
yt-agent curate clear VIDEO_ID
yt-agent curate tag VIDEO_ID research
yt-agent curate tag VIDEO_ID research --remove

yt-agent curate collection-create "Course material" --description "ML lectures"
yt-agent curate collection-video 1 VIDEO_ID
yt-agent curate collection-video 1 VIDEO_ID --remove
yt-agent curate collection-delete 1

yt-agent curate bookmark VIDEO_ID 92.5 --label "Core argument" --note "Quote this"
yt-agent curate bookmark-remove 1
yt-agent curate show VIDEO_ID --output json
yt-agent curate search research --output json
```

Ratings are integers from one to five and bookmark timestamps must be non-negative. Every
mutation supports `--dry-run`; every command supports JSON output. Search remains deterministic
and local, matching video titles, notes, tag and collection names, and bookmark labels/notes.

Semantic search is intentionally not enabled by default: the deterministic FTS/catalog search
path has no model dependency and sends no library text to an external service. A future embedder
must be explicitly configured before any embeddings are generated.
