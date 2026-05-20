# opencode Prompt Starter

```text
When the task involves YouTube search, playlist curation, local catalog indexing, transcript search, or clip extraction, use yt-agent.

Contract:
- Read and parse JSON output with --output json
- Use --select for non-interactive selection
- Use --dry-run before any mutation
- Use --quiet after approval
- Treat clip search result IDs as short-lived
- Handle exit code 7 as a busy lock, not as a fatal parser error

Please summarize options first, then wait for approval before running the final mutation command.
```
