#!/usr/bin/env bash
set -euo pipefail

target="${1:-https://www.youtube.com/watch?v=dQw4w9WgXcQ}"
query="${2:-keyboard shortcut}"

yt-agent index add "$target" --fetch-subs --output json
yt-agent clips search "$query" --output json

# If you already know the exact range, use durable coordinates:
# yt-agent clips grab --video-id VIDEO_ID --start-seconds 12.5 --end-seconds 18.0 --dry-run --output json
