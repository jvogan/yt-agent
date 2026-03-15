#!/usr/bin/env bash
set -euo pipefail

# Extract a batch of clips from TSV time ranges in a repeatable way.
# Usage:
#   APPLY=1 ./examples/scripts/batch-clip-extract.sh clips.tsv reports/batch-clip-extract.json
# Input format:
#   video_id<TAB>start_seconds<TAB>end_seconds<TAB>optional_note
# Example:
#   abc123def45	12.5	18.0	intro-hook
#   xyz987uvw65	44	59	b-roll
# By default the script only previews the clips. Set APPLY=1 to write files.

clips_file="${1:-clips.tsv}"
report_file="${2:-batch-clip-extract-report.json}"
apply_changes="${APPLY:-0}"
clip_mode="${CLIP_MODE:-fast}"
remote_fallback="${REMOTE_FALLBACK:-0}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

make_temp_dir() {
  local temp_dir

  if temp_dir="$(mktemp -d "${TMPDIR:-/tmp}/yt-agent.XXXXXX" 2>/dev/null)"; then
    printf '%s\n' "$temp_dir"
  else
    mktemp -d -t yt-agent
  fi
}

trim_line() {
  printf '%s' "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

is_number() {
  [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]
}

append_json_line() {
  printf '%s\n' "$1" >>"$jsonl_file"
}

run_capture() {
  local stdout_file="$1"
  local stderr_file="$2"
  shift 2

  if "$@" >"$stdout_file" 2>"$stderr_file"; then
    return 0
  fi

  return $?
}

require_command yt-agent
require_command jq

if [ ! -f "$clips_file" ]; then
  echo "Clip batch file not found: $clips_file" >&2
  exit 1
fi

mkdir -p "$(dirname "$report_file")"

tmp_dir="$(make_temp_dir)"
trap 'rm -rf "$tmp_dir"' EXIT

jsonl_file="$tmp_dir/clip-results.jsonl"
stdout_file="$tmp_dir/command.stdout"
stderr_file="$tmp_dir/command.stderr"
timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

line_number=0
had_error=0

while IFS= read -r raw_line || [ -n "$raw_line" ]; do
  line_number=$((line_number + 1))
  trimmed_line="$(trim_line "$raw_line")"

  case "$trimmed_line" in
    "" | \#*)
      continue
      ;;
  esac

  IFS='	' read -r video_id start_seconds end_seconds note <<EOF
$trimmed_line
EOF

  note="${note:-}"

  if [ -z "${video_id:-}" ] || [ -z "${start_seconds:-}" ] || [ -z "${end_seconds:-}" ]; then
    had_error=1
    append_json_line "$(jq -cn \
      --arg line_number "$line_number" \
      --arg raw_line "$trimmed_line" \
      '{
        line_number: ($line_number | tonumber),
        status: "error",
        error: "Expected video_id, start_seconds, and end_seconds.",
        raw_line: $raw_line
      }')"
    continue
  fi

  if ! is_number "$start_seconds" || ! is_number "$end_seconds"; then
    had_error=1
    append_json_line "$(jq -cn \
      --arg line_number "$line_number" \
      --arg video_id "$video_id" \
      --arg start_seconds "$start_seconds" \
      --arg end_seconds "$end_seconds" \
      '{
        line_number: ($line_number | tonumber),
        video_id: $video_id,
        status: "error",
        error: "start_seconds and end_seconds must be numeric.",
        start_seconds: $start_seconds,
        end_seconds: $end_seconds
      }')"
    continue
  fi

  preview_args=(
    yt-agent clips grab
    --dry-run
    --output json
    --mode "$clip_mode"
    --video-id "$video_id"
    --start-seconds "$start_seconds"
    --end-seconds "$end_seconds"
  )
  if [ "$remote_fallback" != "0" ]; then
    preview_args+=(--remote-fallback)
  fi

  if ! run_capture "$stdout_file" "$stderr_file" "${preview_args[@]}"; then
    had_error=1
    append_json_line "$(jq -cn \
      --arg line_number "$line_number" \
      --arg video_id "$video_id" \
      --arg start_seconds "$start_seconds" \
      --arg end_seconds "$end_seconds" \
      --arg note "$note" \
      --arg stderr "$(cat "$stderr_file")" \
      '{
        line_number: ($line_number | tonumber),
        video_id: $video_id,
        start_seconds: ($start_seconds | tonumber),
        end_seconds: ($end_seconds | tonumber),
        note: $note,
        status: "error",
        step: "preview",
        stderr: $stderr
      }')"
    continue
  fi

  preview_json="$(cat "$stdout_file")"
  if [ "$apply_changes" = "1" ]; then
    run_args=(
      yt-agent clips grab
      --quiet
      --output json
      --mode "$clip_mode"
      --video-id "$video_id"
      --start-seconds "$start_seconds"
      --end-seconds "$end_seconds"
    )
    if [ "$remote_fallback" != "0" ]; then
      run_args+=(--remote-fallback)
    fi

    if ! run_capture "$stdout_file" "$stderr_file" "${run_args[@]}"; then
      had_error=1
      append_json_line "$(jq -cn \
        --arg line_number "$line_number" \
        --arg video_id "$video_id" \
        --arg note "$note" \
        --argjson preview "$preview_json" \
        --arg stderr "$(cat "$stderr_file")" \
        '{
          line_number: ($line_number | tonumber),
          video_id: $video_id,
          note: $note,
          status: "error",
          step: "extract",
          preview: $preview,
          stderr: $stderr
        }')"
      continue
    fi

    result_json="$(cat "$stdout_file")"
    append_json_line "$(jq -cn \
      --arg line_number "$line_number" \
      --arg video_id "$video_id" \
      --arg note "$note" \
      --argjson preview "$preview_json" \
      --argjson result "$result_json" \
      '{
        line_number: ($line_number | tonumber),
        video_id: $video_id,
        note: $note,
        status: ($result.status // "ok"),
        preview: $preview,
        result: $result
      }')"
  else
    append_json_line "$(jq -cn \
      --arg line_number "$line_number" \
      --arg video_id "$video_id" \
      --arg note "$note" \
      --argjson preview "$preview_json" \
      '{
        line_number: ($line_number | tonumber),
        video_id: $video_id,
        note: $note,
        status: "planned",
        preview: $preview
      }')"
  fi
done <"$clips_file"

report_json="$(jq -s --arg generated_at "$timestamp" \
  --arg clips_file "$clips_file" \
  --arg mode "$clip_mode" \
  --argjson apply_value "$(jq -n --arg apply_changes "$apply_changes" '$apply_changes == "1"')" \
  --argjson remote_fallback_value "$(jq -n --arg remote_fallback "$remote_fallback" '$remote_fallback != "0"')" \
  '{
    generated_at: $generated_at,
    clips_file: $clips_file,
    apply: $apply_value,
    mode: $mode,
    remote_fallback: $remote_fallback_value,
    summary: {
      total_rows: length,
      planned: map(select(.status == "planned")) | length,
      saved: map(select(.status == "ok")) | length,
      failed: map(select(.status == "error")) | length
    },
    results: .
  }' "$jsonl_file")"

printf '%s\n' "$report_json" >"$report_file"
printf 'Wrote %s\n' "$report_file" >&2
printf '%s\n' "$report_json"

if [ "$had_error" -ne 0 ]; then
  exit 1
fi
