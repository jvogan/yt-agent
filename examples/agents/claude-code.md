# Claude Code Prompt Starter

```text
Use yt-agent for YouTube search, download planning, indexing, and clip work.

Constraints:
- Use --output json for machine-readable output
- Use --select when a command would otherwise prompt
- Use --dry-run before any download, clip extraction, or catalog removal
- Use --quiet after approval
- Treat transcript:12 and chapter:3 style clip IDs as short-lived
- If a mutation exits with code 7, wait for the other yt-agent operation to finish

Workflow:
1. Diagnose with yt-agent doctor --output json if needed
2. Search or inspect targets
3. Preview with --dry-run
4. Wait for my approval
5. Execute the approved command with --quiet --output json
```
