# Troubleshooting

Start with:

```bash
yt-agent doctor --output json
```

That confirms tools, paths, and platform status in one place.

## Exit codes

| Code | Meaning | Typical fix |
|---|---|---|
| `0` | Success | No action needed |
| `3` | Missing dependency | Install `yt-dlp` or `ffmpeg` |
| `4` | Invalid input | Fix URL, selection, or command arguments |
| `5` | Invalid config | Run `yt-agent config validate` or regenerate config |
| `6` | External command failure | Read stderr, then retry/update `yt-dlp` if appropriate |
| `7` | Busy state | Another mutating `yt-agent` command is running |
| `8` | Storage problem | Check catalog/state path permissions or disk state |
| `130` | Interrupted | Re-run the command if needed |

## Common situations

### `yt-dlp` is missing

- Symptom: `doctor` reports `yt-dlp` as missing, or most commands fail with exit code `3`.
- Fix:
  - macOS: `brew install yt-dlp`
  - Linux: `python3 -m pip install -U yt-dlp`

### `ffmpeg` is missing

- Symptom: clip extraction fails with exit code `3`.
- Fix:
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt-get install -y ffmpeg`

### Another operation is already running

- Symptom: a mutating command exits with code `7`.
- Meaning: another `download`, `grab`, `index`, `clips grab`, or `library remove` command is holding the local operation lock.
- Fix: wait for the other mutation to finish, then retry.
- Agent note: do not blindly retry in a tight loop. Back off and try again after the active operation completes.

### Search returns no results

- Symptom: `search` exits `0` but returns no matches.
- Fix:
  - broaden the query
  - update `yt-dlp`
  - retry later if YouTube search is temporarily degraded

### The catalog is empty

- Symptom: `library stats` shows zero videos, or `clips search` returns nothing.
- Fix:
  - `yt-agent index refresh --output json`
  - or `yt-agent index add "URL" --output json`
  - add `--fetch-subs` if transcript coverage matters

### Transcript search misses expected lines

- Symptom: the video exists locally, but transcript hits are missing.
- Fix:
  - inspect the item with `yt-agent library show VIDEO_ID --output json`
  - if `transcript_preview` is empty, re-index with subtitles:

```bash
yt-agent index add "URL" --fetch-subs --output json
yt-agent index add "URL" --fetch-subs --auto-subs --output json
```

### Clip result IDs look stale

- Symptom: a previous `transcript:12` or `chapter:3` no longer resolves.
- Meaning: clip result IDs are short-lived handles for the current catalog state.
- Fix:
  - rerun `clips search`
  - or use explicit clip extraction:

```bash
yt-agent clips grab --video-id VIDEO_ID --start-seconds 12.5 --end-seconds 18.0 --output json
```

## Human-safe and agent-safe defaults

- Human operators:
  - use prompt mode
  - use `table` output
  - inspect with `info`, `library show`, and `clips show`
- Agents:
  - use `--output json`
  - add `--select` on commands that would otherwise prompt
  - use `--dry-run` before approval-gated mutations
  - use `--quiet` once the action is approved

For richer examples, see [docs/recipes.md](recipes.md) and the copy-paste prompts in [examples/agents/](../examples/agents/).
