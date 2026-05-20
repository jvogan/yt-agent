#!/usr/bin/env bash
set -euo pipefail

# Daily channel/topic sweep using saved yt-agent search queries.
# Usage:
#   DRY_RUN=1 ./examples/scripts/daily-channel-check.sh checks.tsv reports/daily-channel-check.json
# Input format:
#   One check per line. Use either:
#     search query
#     label<TAB>search query
#   Blank lines and lines starting with # are ignored.
# Example:
#   Fireship	fireship latest
#   ThePrimeTime	the prime time youtube

checks_file="${1:-channel-checks.tsv}"
report_file="${2:-daily-channel-check-report.json}"
search_limit="${SEARCH_LIMIT:-5}"
history_limit="${HISTORY_LIMIT:-25}"
dry_run="${DRY_RUN:-1}"
fetch_subs="${FETCH_SUBS:-0}"

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

if [ ! -f "$checks_file" ]; then
  echo "Checks file not found: $checks_file" >&2
  exit 1
fi

mkdir -p "$(dirname "$report_file")"

tmp_dir="$(make_temp_dir)"
trap 'rm -rf "$tmp_dir"' EXIT

jsonl_file="$tmp_dir/check-results.jsonl"
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

  label="$trimmed_line"
  query="$trimmed_line"
  if printf '%s' "$trimmed_line" | grep -q "$(printf '\t')"; then
    label="${trimmed_line%%	*}"
    query="${trimmed_line#*	}"
  fi

  if [ -z "$query" ]; then
    had_error=1
    append_json_line "$(jq -cn \
      --arg label "$label" \
      --arg line_number "$line_number" \
      '{
        label: $label,
        line_number: ($line_number | tonumber),
        status: "error",
        error: "Missing search query."
      }')"
    continue
  fi

  if ! run_capture "$stdout_file" "$stderr_file" \
    yt-agent search "$query" --limit "$search_limit" --output json; then
    had_error=1
    append_json_line "$(jq -cn \
      --arg label "$label" \
      --arg query "$query" \
      --arg line_number "$line_number" \
      --arg stderr "$(cat "$stderr_file")" \
      '{
        label: $label,
        query: $query,
        line_number: ($line_number | tonumber),
        status: "error",
        step: "search",
        stderr: $stderr
      }')"
    continue
  fi

  search_json="$(cat "$stdout_file")"
  if ! printf '%s' "$search_json" | jq -e 'length > 0' >/dev/null; then
    append_json_line "$(jq -cn \
      --arg label "$label" \
      --arg query "$query" \
      --arg line_number "$line_number" \
      '{
        label: $label,
        query: $query,
        line_number: ($line_number | tonumber),
        status: "no_results",
        search_results: 0
      }')"
    continue
  fi

  top_result="$(printf '%s' "$search_json" | jq '.[0]')"
  video_id="$(printf '%s' "$top_result" | jq -r '.video_id')"
  channel_name="$(printf '%s' "$top_result" | jq -r '.channel')"
  webpage_url="$(printf '%s' "$top_result" | jq -r '.webpage_url')"

  if ! run_capture "$stdout_file" "$stderr_file" \
    yt-agent history --channel "$channel_name" --limit "$history_limit" --output json; then
    had_error=1
    append_json_line "$(jq -cn \
      --arg label "$label" \
      --arg query "$query" \
      --arg line_number "$line_number" \
      --arg video_id "$video_id" \
      --arg channel_name "$channel_name" \
      --arg stderr "$(cat "$stderr_file")" \
      '{
        label: $label,
        query: $query,
        line_number: ($line_number | tonumber),
        status: "error",
        step: "history",
        video_id: $video_id,
        channel: $channel_name,
        stderr: $stderr
      }')"
    continue
  fi

  history_json="$(cat "$stdout_file")"
  if printf '%s' "$history_json" | jq -e --arg video_id "$video_id" \
    'map(.video_id) | index($video_id) != null' >/dev/null; then
    append_json_line "$(jq -cn \
      --arg label "$label" \
      --arg query "$query" \
      --arg line_number "$line_number" \
      --argjson top_result "$top_result" \
      --argjson history "$history_json" \
      '{
        label: $label,
        query: $query,
        line_number: ($line_number | tonumber),
        status: "already_downloaded",
        top_result: $top_result,
        recent_history: $history
      }')"
    continue
  fi

  if [ "$dry_run" != "0" ]; then
    append_json_line "$(jq -cn \
      --arg label "$label" \
      --arg query "$query" \
      --arg line_number "$line_number" \
      --argjson top_result "$top_result" \
      --argjson history "$history_json" \
      '{
        label: $label,
        query: $query,
        line_number: ($line_number | tonumber),
        status: "would_download",
        top_result: $top_result,
        recent_history: $history
      }')"
    continue
  fi

  download_args=(yt-agent download --quiet --output json)
  if [ "$fetch_subs" != "0" ]; then
    download_args+=(--fetch-subs)
  fi
  download_args+=("$webpage_url")

  if ! run_capture "$stdout_file" "$stderr_file" "${download_args[@]}"; then
    had_error=1
    append_json_line "$(jq -cn \
      --arg label "$label" \
      --arg query "$query" \
      --arg line_number "$line_number" \
      --argjson top_result "$top_result" \
      --arg stderr "$(cat "$stderr_file")" \
      '{
        label: $label,
        query: $query,
        line_number: ($line_number | tonumber),
        status: "error",
        step: "download",
        top_result: $top_result,
        stderr: $stderr
      }')"
    continue
  fi

  download_json="$(cat "$stdout_file")"
  append_json_line "$(jq -cn \
    --arg label "$label" \
    --arg query "$query" \
    --arg line_number "$line_number" \
    --argjson top_result "$top_result" \
    --argjson history "$history_json" \
    --argjson download "$download_json" \
    '{
      label: $label,
      query: $query,
      line_number: ($line_number | tonumber),
      status: ($download.status // "ok"),
      top_result: $top_result,
      recent_history: $history,
      download: $download
    }')"
done <"$checks_file"

report_json="$(jq -s --arg generated_at "$timestamp" \
  --arg checks_file "$checks_file" \
  --argjson dry_run_value "$(jq -n --arg dry_run "$dry_run" '$dry_run != "0"')" \
  '{
    generated_at: $generated_at,
    checks_file: $checks_file,
    dry_run: $dry_run_value,
    summary: {
      total_checks: length,
      no_results: map(select(.status == "no_results")) | length,
      already_downloaded: map(select(.status == "already_downloaded")) | length,
      would_download: map(select(.status == "would_download")) | length,
      downloaded: map(select(.status == "ok")) | length,
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
