# Gemini CLI Prompt Starter

```text
Use yt-agent for this YouTube workflow.

Operational rules:
- Prefer structured output: --output json
- Avoid prompts in JSON mode: provide --select when needed
- Preview mutations with --dry-run
- After approval, rerun the approved mutation with --quiet --output json
- Use explicit clip coordinates with --video-id, --start-seconds, and --end-seconds when I give exact times
- If a command exits 7, the local yt-agent mutation lock is busy

Goal:
Help me inspect results first, then execute only the downloads or clip extractions I approve.
```
