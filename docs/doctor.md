# Deep runtime diagnostics

The default `yt-agent doctor` check stays quick. Add `--deep` to inspect optional capabilities:

```bash
yt-agent doctor --deep
yt-agent doctor --deep --output json
```

Deep diagnostics report the yt-dlp version and minimum-safe comparison, ffprobe availability,
Node and Deno readiness for external JavaScript challenges, local `whisper-cli` availability,
and whether a known external PO-token provider executable is present.

The cookie diagnostic is deliberately informational. yt-agent does not read, store, or print
browser cookies or tokens. If authenticated extraction is necessary, keep credentials in a
protected external yt-dlp configuration and never commit them to a repository.
