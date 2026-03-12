# antigraviti Prompt Starter

```text
Use yt-agent as the execution layer for this YouTube task.

Behavior rules:
- Prefer --output json for all parsed command results
- Use --select whenever a command would prompt
- Use --dry-run before any download, clip extraction, or catalog removal
- Use --quiet on the approved mutation
- Treat clip result IDs as ephemeral search handles
- If a mutating command returns exit code 7, another yt-agent operation is already in progress

Desired flow:
inspect -> summarize -> preview -> wait for approval -> execute
```
