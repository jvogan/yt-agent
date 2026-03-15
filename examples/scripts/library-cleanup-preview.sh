#!/usr/bin/env bash
set -euo pipefail

video_id="${1:-VIDEO_ID}"

yt-agent library show "$video_id" --output json
yt-agent library remove "$video_id" --dry-run --output json

# After approval, rerun without --dry-run:
# yt-agent library remove "$video_id"
