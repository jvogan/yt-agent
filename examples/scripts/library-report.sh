#!/usr/bin/env bash
set -euo pipefail

# Build a lightweight library health report from yt-agent's JSON outputs.
# Usage:
#   ./examples/scripts/library-report.sh reports/library-report.md reports/library-report.json
# The JSON sidecar is suitable for cron jobs and CI artifacts; the Markdown file
# is easy to read in GitHub, email, or chat handoffs.

report_markdown="${1:-library-report.md}"
report_json="${2:-${report_markdown%.md}.json}"
sample_limit="${LIBRARY_SAMPLE_LIMIT:-250}"
history_limit="${HISTORY_LIMIT:-10}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_command yt-agent
require_command jq

mkdir -p "$(dirname "$report_markdown")"
mkdir -p "$(dirname "$report_json")"

stats_json="$(yt-agent library stats --output json)"
channels_json="$(yt-agent library channels --output json)"
history_json="$(yt-agent history --limit "$history_limit" --output json)"
videos_json="$(yt-agent library list --limit "$sample_limit" --output json)"

summary_json="$(jq -n \
  --arg generated_at "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
  --argjson stats "$stats_json" \
  --argjson channels "$channels_json" \
  --argjson history "$history_json" \
  --argjson videos "$videos_json" \
  --argjson sample_limit "$sample_limit" \
  '{
    generated_at: $generated_at,
    sample_limit: $sample_limit,
    stats: $stats,
    channels: {
      total: ($channels | length),
      names: ($channels | map(.channel)),
      preview: ($channels | map(.channel) | .[:10])
    },
    sample: {
      rows: ($videos | length),
      truncated: ($stats.videos > ($videos | length))
    },
    top_channels_from_sample: (
      $videos
      | group_by(.channel)
      | map({
          channel: .[0].channel,
          videos: length,
          local_media: map(select(.has_local_media == true)) | length,
          transcript_hits: map(select((.transcript_segments // 0) > 0)) | length
        })
      | sort_by(-.videos, .channel)
      | .[:10]
    ),
    recent_downloads: $history
  }')"

printf '%s\n' "$summary_json" >"$report_json"

markdown_body="$(printf '%s' "$summary_json" | jq -r '
  [
    "# yt-agent Library Report",
    "",
    "Generated: \(.generated_at)",
    "",
    "## Totals",
    "- Videos: \(.stats.videos)",
    "- Local media files: \(.stats.local_media)",
    "- Channels: \(.stats.channels)",
    "- Playlists: \(.stats.playlists)",
    "- Chapters: \(.stats.chapters)",
    "- Subtitle tracks: \(.stats.subtitle_tracks)",
    "- Transcript segments: \(.stats.transcript_segments)",
    "",
    "## Channel Preview",
    "- Known channels from catalog: \(.channels.total)",
    (
      if (.channels.preview | length) == 0
      then "- No channels indexed yet."
      else (.channels.preview[] | "- " + .)
      end
    ),
    "",
    "## Top Channels In Sample",
    (
      if (.top_channels_from_sample | length) == 0
      then "- No catalog rows available in the sample."
      else (
        .top_channels_from_sample[]
        | "- \(.channel): \(.videos) videos, \(.local_media) local media, \(.transcript_hits) with transcripts"
      )
      end
    ),
    "",
    "## Recent Downloads",
    (
      if (.recent_downloads | length) == 0
      then "- No download history found."
      else (
        .recent_downloads[]
        | "- \(.downloaded_at): \(.channel) - \(.title) [\(.video_id)]"
      )
      end
    ),
    "",
    "## Notes",
    (
      if .sample.truncated
      then "- Top-channel counts are based on a sample of \(.sample.rows) rows because `library list` is limit-based."
      else "- Top-channel counts cover the full sampled library."
      end
    ),
    "- JSON sidecar: `'"$report_json"'`"
  ] | join("\n")
')"

printf '%s\n' "$markdown_body" >"$report_markdown"

printf 'Wrote %s and %s\n' "$report_markdown" "$report_json" >&2
printf '%s\n' "$summary_json"
