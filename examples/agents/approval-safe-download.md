# Approval-Safe Download

Use this when the operator wants search help first and file writes only after explicit approval.

## Good prompt

```text
Search YouTube for "lofi hip hop" with yt-agent, summarize the top 6 results, then preview the downloads I pick with a dry run. Do not download anything until I explicitly approve.
```

## Good command sequence

```bash
yt-agent search "lofi hip hop" --limit 6 --output json
yt-agent grab "lofi hip hop" --select 2,4 --dry-run --output json
# wait for approval
yt-agent grab "lofi hip hop" --select 2,4 --quiet --output json
```

## Why this is safe

- `search --output json` gives a structured result set to summarize.
- `grab --dry-run --output json` previews the exact targets without writing files.
- The final `grab` uses `--quiet` so the operator sees a clean structured result instead of chatter.

## Notes

- If the operator already gave exact URLs, prefer `download ... --dry-run --output json` instead of `grab`.
- If exit code `7` occurs, another mutation is running. Wait and retry.
