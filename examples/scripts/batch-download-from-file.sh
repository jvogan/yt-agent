#!/usr/bin/env bash
set -euo pipefail

targets_file="${1:-targets.txt}"

yt-agent download --from-file "$targets_file" --dry-run --output json

# After approval, rerun without --dry-run:
# yt-agent download --from-file "$targets_file" --quiet --output json
