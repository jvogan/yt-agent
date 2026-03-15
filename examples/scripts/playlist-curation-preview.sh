#!/usr/bin/env bash
set -euo pipefail

playlist_url="${1:-https://www.youtube.com/playlist?list=PL123}"

yt-agent info "$playlist_url" --entries --output json
yt-agent download "$playlist_url" --select 1,3,5 --dry-run --output json

# After approval, rerun without --dry-run:
# yt-agent download "$playlist_url" --select 1,3,5 --quiet --output json
