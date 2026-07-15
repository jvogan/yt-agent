# Verify local state

`yt-agent verify` performs a read-only consistency audit of the manifest, download archive,
SQLite catalog, referenced media and sidecar files, and full-text-search linkage.

```console
yt-agent verify
yt-agent verify --output json
yt-agent verify --deep
```

The normal audit does not invoke network services or modify local state. `--deep` additionally
runs `ffprobe` against each existing catalog media path. Missing `ffprobe` is reported as a
warning; unreadable media is reported as an error.

Findings include stable `code` and `severity` fields for scripting. The command currently
reports problems only; it does not repair, delete, re-index, or rewrite any files.
