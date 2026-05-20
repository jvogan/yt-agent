# Codex Prompt Starter

Use this when you want Codex to operate `yt-agent` with approval-safe mutations.

```text
Use yt-agent for this task.

Rules:
- Prefer --output json when parsing results
- Use --select on commands that would otherwise prompt
- Use --dry-run before any mutation that writes files or changes the catalog
- Use --quiet after I approve the exact action
- Treat clip result IDs as short-lived handles
- If a mutation exits 7, another yt-agent operation is already running

Task:
Search YouTube for "QUERY", summarize the top results, then preview the specific downloads or clips I choose. Do not write files until I explicitly approve.
```

If Codex is reading repo-local instructions, also point it at [skills/yt-agent/SKILL.md](../../skills/yt-agent/SKILL.md).
