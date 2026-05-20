#!/usr/bin/env bash
set -euo pipefail

# Back up yt-agent state files discovered through `yt-agent config path --output json`.
# Usage:
#   ./examples/scripts/catalog-backup.sh backups
# The script creates a timestamped backup directory plus a tar.gz archive.
# It prefers `sqlite3 .backup` for the catalog when available and falls back to `cp -p`.

backup_root="${1:-backups}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

copy_if_exists() {
  local source_path="$1"
  local destination_name="$2"
  local kind="$3"

  if [ ! -f "$source_path" ]; then
    return 0
  fi

  cp -p "$source_path" "$backup_dir/$destination_name"
  printf '%s\n' "$(jq -cn \
    --arg kind "$kind" \
    --arg source "$source_path" \
    --arg destination "$backup_dir/$destination_name" \
    '{
      kind: $kind,
      source: $source,
      destination: $destination
    }')" >>"$copied_jsonl"
}

require_command yt-agent
require_command jq
require_command tar

mkdir -p "$backup_root"

timestamp="$(date -u +"%Y%m%dT%H%M%SZ")"
backup_dir="$backup_root/yt-agent-state-$timestamp"
archive_path="$backup_root/yt-agent-state-$timestamp.tar.gz"
copied_jsonl="$(mktemp "${TMPDIR:-/tmp}/yt-agent-backup.XXXXXX")"
trap 'rm -f "$copied_jsonl"' EXIT

mkdir -p "$backup_dir"

config_json="$(yt-agent config path --output json)"
printf '%s\n' "$config_json" >"$backup_dir/config-paths.json"

config_path="$(printf '%s' "$config_json" | jq -r '.config')"
archive_file="$(printf '%s' "$config_json" | jq -r '.archive_file')"
manifest_file="$(printf '%s' "$config_json" | jq -r '.manifest_file')"
catalog_file="$(printf '%s' "$config_json" | jq -r '.catalog_file')"

copy_if_exists "$config_path" "config.toml" "config"
copy_if_exists "$archive_file" "archive.txt" "archive"
copy_if_exists "$manifest_file" "downloads.jsonl" "manifest"

catalog_strategy="missing"
if [ -f "$catalog_file" ]; then
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$catalog_file" ".backup '$backup_dir/catalog.sqlite'"
    catalog_strategy="sqlite_backup"
  else
    cp -p "$catalog_file" "$backup_dir/catalog.sqlite"
    catalog_strategy="file_copy"
  fi

  printf '%s\n' "$(jq -cn \
    --arg kind "catalog" \
    --arg source "$catalog_file" \
    --arg destination "$backup_dir/catalog.sqlite" \
    --arg strategy "$catalog_strategy" \
    '{
      kind: $kind,
      source: $source,
      destination: $destination,
      strategy: $strategy
    }')" >>"$copied_jsonl"
fi

copied_count="$(wc -l <"$copied_jsonl" | tr -d ' ')"
if [ "$copied_count" -eq 0 ]; then
  echo "No yt-agent state files were found to back up." >&2
  exit 1
fi

summary_json="$(jq -n \
  --arg generated_at "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
  --arg backup_dir "$backup_dir" \
  --arg archive_path "$archive_path" \
  --arg catalog_strategy "$catalog_strategy" \
  --argjson paths "$config_json" \
  --slurpfile copied "$copied_jsonl" \
  '{
    generated_at: $generated_at,
    backup_dir: $backup_dir,
    archive_path: $archive_path,
    catalog_strategy: $catalog_strategy,
    discovered_paths: $paths,
    copied_files: $copied
  }')"

printf '%s\n' "$summary_json" >"$backup_dir/backup-summary.json"
tar -czf "$archive_path" -C "$backup_root" "$(basename "$backup_dir")"

printf 'Wrote %s and %s\n' "$backup_dir" "$archive_path" >&2
printf '%s\n' "$summary_json"
