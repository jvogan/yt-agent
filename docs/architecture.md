# Architecture

`yt-agent` is a terminal-first orchestration layer around `yt-dlp`, a local media library, and a SQLite catalog. The codebase keeps retrieval, local state, indexing, and presentation separate enough that the CLI, TUI, and agent-style JSON workflows all ride on the same storage and query model.

At a high level:

1. `yt-dlp` remains the external engine for YouTube metadata, search, downloads, subtitle fetches, and remote section downloads.
2. `yt_agent` normalizes metadata, resolves output paths, protects local state, writes the append-only manifest, and indexes queryable state into SQLite.
3. SQLite plus FTS5 power library browsing, transcript/chapter clip search, and the Textual TUI.

## Runtime Layers

- Entry surface: Typer CLI commands in `cli.py`, plus the package entrypoint in `__main__.py`.
- Retrieval layer: `yt_dlp.py`, `transcripts.py`, and the external `yt-dlp` binary.
- Local state layer: `config.py`, `archive.py`, `manifest.py`, `library.py`, and `security.py`.
- Query/index layer: `catalog.py`, `indexer.py`, `chapters.py`, and the data models in `models.py`.
- User interfaces: Rich-rendered CLI output, JSON mutation payloads, and the read-mostly Textual TUI in `tui.py`.

## Module Dependency Diagram

The main package contains 18 Python modules. The diagram below shows direct package-level dependencies inside `src/yt_agent`.

```text
                               +------------------+
                               |  yt_agent.__init__ |
                               |   version only    |
                               +---------+--------+
                                         |
                                         v
+-------------------+           +------------------+
| yt_agent.__main__ | --------> |   yt_agent.cli   |
+-------------------+           +------------------+
                                     |  |  |  |  |  \____________________________
                                     |  |  |  |  |                               \
                                     |  |  |  |  v                                v
                                     |  |  |  | +------------------+     +------------------+
                                     |  |  |  +>| yt_agent.selector|     |   yt_agent.tui   |
                                     |  |  |    +------------------+     +------------------+
                                     |  |  |             |                         |
                                     |  |  |             v                         v
                                     |  |  |    +------------------+     +------------------+
                                     |  |  |    | yt_agent.security|     | yt_agent.catalog |
                                     |  |  |    +------------------+     +------------------+
                                     |  |  |                                     |      |
                                     |  |  |                                     |      v
                                     |  |  |                                     | +------------------+
                                     |  |  |                                     | | yt_agent.models  |
                                     |  |  |                                     | +------------------+
                                     |  |  |                                     v
                                     |  |  |                             +------------------+
                                     |  |  +---------------------------> | yt_agent.clips   |
                                     |  |                                +------------------+
                                     |  |                                   |    |      |
                                     |  |                                   |    |      v
                                     |  |                                   |    | +------------------+
                                     |  |                                   |    +>| yt_agent.library |
                                     |  |                                   |      +------------------+
                                     |  |                                   v
                                     |  |                          +------------------+
                                     |  +------------------------> | yt_agent.indexer |
                                     |                             +------------------+
                                     |                               |   |   |   |   \
                                     |                               |   |   |   |    v
                                     |                               |   |   |   | +------------------+
                                     |                               |   |   |   +>|yt_agent.manifest |
                                     |                               |   |   v      +------------------+
                                     |                               |   | +------------------+
                                     |                               |   +>| yt_agent.chapters|
                                     |                               |     +------------------+
                                     |                               v
                                     |                      +------------------+
                                     |                      |yt_agent.transcripts|
                                     |                      +------------------+
                                     |                               |
                                     |                               v
                                     |                      +------------------+
                                     |                      | yt_agent.yt_dlp  |
                                     |                      +------------------+
                                     |                               |    |    |
                                     |                               |    |    v
                                     |                               |    | +------------------+
                                     |                               |    +>| yt_agent.library |
                                     |                               |      +------------------+
                                     |                               v
                                     |                      +------------------+
                                     +--------------------> | yt_agent.config  |
                                                             +------------------+
                                                                       |
                                                                       v
                                                             +------------------+
                                                             | yt_agent.errors  |
                                                             +------------------+

Additional state helpers used directly by cli/index flows:
  yt_agent.archive  -> models, security
  yt_agent.manifest -> models, security
  yt_agent.catalog  -> models, security
  yt_agent.models   -> errors
```

### Module Roles

- `__init__.py`: publishes `__version__`.
- `__main__.py`: `python -m yt_agent` entrypoint; delegates straight to `cli.main()`.
- `archive.py`: manages the yt-dlp-compatible archive file used to suppress duplicate downloads.
- `catalog.py`: owns the SQLite schema, FTS5 tables, and high-level read/write/query APIs.
- `chapters.py`: extracts normalized chapter rows from yt-dlp metadata payloads.
- `cli.py`: the main application surface; command parsing, guarded execution, Rich output, JSON payloads.
- `clips.py`: plans and executes clip extraction from catalog hits or explicit time ranges.
- `config.py`: resolves default paths and loads validated TOML configuration.
- `errors.py`: stable application error classes and exit codes.
- `indexer.py`: turns manifest rows or ad hoc targets into catalog records, chapters, transcripts, and playlist entries.
- `library.py`: deterministic local path generation and sidecar discovery.
- `manifest.py`: append-only persistence for successful downloads.
- `models.py`: immutable dataclasses and normalization helpers shared across modules.
- `security.py`: terminal sanitization, private file permissions, and mutation locking.
- `selector.py`: interactive and non-interactive result selection.
- `transcripts.py`: subtitle fetch, parse, and track inference helpers.
- `tui.py`: Textual UI backed by the catalog read APIs.
- `yt_dlp.py`: subprocess wrapper around `yt-dlp`, plus YouTube URL normalization and allowlisting.

## Storage Model

Default paths come from `config.py` and are intentionally split by responsibility:

- Config: `~/.config/yt-agent/config.toml`
- Archive: `~/.local/share/yt-agent/archive.txt`
- Manifest: `~/.local/share/yt-agent/downloads.jsonl`
- Catalog: `~/.local/share/yt-agent/catalog.sqlite`
- Download root: `~/Media/YouTube`
- Clip root: `~/Media/YouTube/_clips`
- Subtitle cache: `<catalog parent>/subtitle-cache/<video-id>/`
- Lock file: `<catalog parent>/operation.lock`

This split matters because each file answers a different operational question:

- Archive answers "has yt-dlp already downloaded this extractor/id pair?"
- Manifest answers "what exactly succeeded, when, and where was it written?"
- Catalog answers "what can the user or TUI query right now?"

## Manifest and Catalog Separation

The manifest and catalog are deliberately not the same store.

The manifest is append-only audit data:

- Each successful download appends one `ManifestRecord`.
- The write happens immediately after `yt_dlp.download_target()` succeeds.
- A broken later indexing pass does not erase that audit trail.
- `index refresh` can rebuild query state from the manifest plus local sidecars.

The catalog is a derived query layer:

- Rows are upserted and replaced, not appended forever.
- Chapters, subtitle tracks, transcript segments, and playlist entries can be refreshed.
- FTS5 indexes are optimized for query speed, not immutable history.
- The TUI, library commands, and clip search all depend on this low-latency query store.

The practical consequence is that download success and catalog freshness are related but not transactional. A download can succeed, be recorded in the manifest, and still carry an `index_warning` if catalog enrichment fails. That tradeoff favors durable acquisition first and repairable indexing second.

## Download Data Flow

The exact non-dry-run download call chain is:

```text
python -m yt_agent
  -> yt_agent.__main__.main()
  -> yt_agent.cli.main()
  -> Typer dispatch -> download()
  -> _run_guarded(_command)
  -> _load_settings()
  -> _validate_subtitle_flags()
  -> operation_lock(_operation_lock_path(settings))
  -> _prepare_storage(settings)
  -> _resolve_download_inputs(...)
  -> _download_targets(...)
  -> _download_operation_payload(...)
  -> _render_download_payload(...)
```

### Target Resolution Chain

For each user-supplied input, `download()` resolves targets like this:

```text
_resolve_download_inputs()
  -> yt_dlp.fetch_info(user_input)
    -> normalize_target(user_input)
    -> _run_json([yt-dlp, --dump-single-json, --no-warnings, normalized])
  -> yt_dlp.resolve_payload(user_input, payload, source_query=...)
    -> VideoInfo.from_yt_dlp(...) for a single video
    -> or DownloadTarget(...) per valid playlist entry
```

Important branches:

- If the payload is not a playlist, all resolved targets are kept.
- If the payload is a playlist and selection is enabled, `cli._choose_results()` narrows the list through:
  `parse_selection()` for `--select`, or `select_results()` -> `prompt_for_selection()` / `select_with_fzf()`.
- Invalid or unavailable playlist entries become `skipped_messages`, not hard failures.

### Per-Target Download, Manifest, and Index Chain

Once targets are resolved, each item passes through `_download_targets()`:

```text
_download_targets()
  -> load_archive_entries(settings.archive_file)
  -> is_archived(archive_entries, target.info)?
     yes -> mark skipped
     no  -> yt_dlp.download_target(target, settings, ...)
             -> command_path()
             -> build_output_template(settings.download_root, target.info)
             -> _run_download(args)
                -> subprocess.run([...])
             -> discover_info_json(output_path)
  -> ManifestRecord.from_download(target, output_path, info_json_path)
  -> append_manifest_record(settings.manifest_file, record)
  -> archive_entries.add(target.info.archive_key)
  -> index_manifest_record(settings, record, ...)
```

Important branches inside that chain:

- `is_archived(...)` causes an immediate skip before invoking `yt-dlp`.
- `_run_download(...)` returning `None` means yt-dlp exited `0` but produced no final filepath, which the CLI interprets as "already archived (reported by yt-dlp)".
- `ExternalCommandError` marks the item as failed and keeps the rest of the batch moving.
- `index_manifest_record(...)` is wrapped in a broad best-effort `try/except`; indexing warnings do not retroactively invalidate a successful download.

### Exact Post-Download Index Chain

The follow-up index path is:

```text
index_manifest_record()
  -> catalog_for_settings()
  -> discover_info_json(output_path) if manifest sidecar path is missing
  -> _load_info_json(info_json_path)
  -> VideoInfo.from_yt_dlp(payload) or fallback VideoInfo(...)
  -> _index_video_payload(...)
     -> catalog.upsert_video(...)
     -> extract_chapters(payload)
     -> catalog.replace_chapters(...)
     -> _index_transcripts(...)
        -> discover_subtitle_files(media_path)
        -> optional fetch_subtitle_sidecars(...)
        -> parse_subtitle_file(...)
        -> infer_subtitle_track(...)
        -> catalog.replace_transcripts(...)
```

This is why downloaded media can exist before all chapter or transcript query features are available: index enrichment is a separate step that runs immediately, but not atomically.

## Clip Search and Extract Data Flow

Clip workflows are intentionally split into two separate concerns:

1. Search the catalog for timed chapter or transcript hits.
2. Extract media for a chosen hit or explicit range.

### Search Flow

The `clips search` chain is read-only:

```text
clips_search_command()
  -> _run_guarded(_command)
  -> _load_settings()
  -> _catalog(settings, readonly=True)
  -> CatalogStore.search_clips(query, source, channel, language, limit)
     -> _fts_query(query)
     -> SQL against chapter_fts and/or transcript_fts
```

`CatalogStore.search_clips()` does the following:

- Sanitizes the free-text query token-by-token through `_fts_query()`.
- Returns `[]` immediately if sanitization removes every token.
- Queries `chapter_fts` joined to `chapters` and `videos` when `source` includes chapters.
- Queries `transcript_fts` joined to `transcript_segments`, `videos`, and `subtitle_tracks` when `source` includes transcript text.
- Applies optional `channel` and transcript-language filters.
- Ranks results with `bm25(...)`.
- Emits synthetic result ids like `chapter:12` and `transcript:34`.

### Extract Flow

The `clips grab` command supports either a clip-search result id or an explicit `video_id` plus time range.

The non-dry-run call chain is:

```text
clips_grab_command()
  -> _run_guarded(_command)
  -> _load_settings()
  -> _validate_clip_mode(mode)
  -> operation_lock(_operation_lock_path(settings))
  -> _prepare_storage(settings)
  -> extract_clip(...) or extract_clip_for_range(...)
  -> _clip_grab_payload(...)
  -> _render_clip_grab_payload(...)
```

`extract_clip(...)` resolves a catalog result like this:

```text
extract_clip(result_id, ...)
  -> plan_clip(result_id, ...)
     -> CatalogStore(..., readonly=True)
     -> get_clip_hit(result_id)
     -> _clip_bounds(hit, padding_before, padding_after)
     -> _plan_resolved_clip(...)
  -> CatalogStore(..., readonly=True).get_clip_hit(result_id)
  -> _extract_resolved_clip(...)
```

`extract_clip_for_range(...)` is the explicit-range sibling:

```text
extract_clip_for_range(video_id, start_seconds, end_seconds, ...)
  -> plan_clip_for_range(...)
     -> CatalogStore(..., readonly=True).get_video(video_id)
     -> _plan_resolved_clip(...)
  -> CatalogStore(..., readonly=True).get_video(video_id)
  -> _extract_resolved_clip(...)
```

### Local vs Remote Clip Extraction

`_plan_resolved_clip(...)` decides whether extraction is local or remote:

- Local path:
  - Chosen when `media_path` exists and `prefer_remote` is false.
  - Output path comes from `build_clip_output_path(...)`.
  - `_extract_resolved_clip(...)` resolves `ffmpeg` via `_ffmpeg_path()`.
  - `fast` mode uses `-c copy`.
  - `accurate` mode re-encodes with `libx264` and `aac`.

- Remote fallback path:
  - Triggered only when local media is missing and `prefer_remote` is true.
  - Planning returns an output template rather than a concrete output filename.
  - `_extract_resolved_clip(...)` calls:
    `yt-dlp --download-sections "*start-end" --output TEMPLATE URL`
  - The created remote file is discovered by globbing the output directory.

The result is that clip search depends entirely on the catalog, but clip extraction can still succeed even when the local library lacks the original media, as long as remote fallback is allowed.

## SQLite Schema

`catalog.py` defines the complete schema in a single `SCHEMA` string. The design keeps canonical entities in normal tables and search text in FTS5 virtual tables.

### Core Tables

`videos`

- Primary key: `video_id`
- Core metadata: `title`, `channel`, `upload_date`, `duration_seconds`, `extractor_key`, `webpage_url`
- Provenance: `requested_input`, `source_query`
- Local state: `output_path`, `info_json_path`
- Timestamps: `downloaded_at`, `indexed_at`

Role:
- One row per normalized video.
- The anchor table for chapters, subtitles, transcripts, and playlist membership.

`chapters`

- Primary key: `chapter_id`
- Foreign key: `video_id -> videos(video_id)` with `ON DELETE CASCADE`
- Ordering: `position`
- Timing: `start_seconds`, `end_seconds`
- Text: `title`
- Constraint: `UNIQUE(video_id, position)`

Role:
- Stores normalized chapter boundaries from yt-dlp metadata.

`subtitle_tracks`

- Primary key: `track_id`
- Foreign key: `video_id -> videos(video_id)` with `ON DELETE CASCADE`
- Fields: `lang`, `source`, `is_auto`, `format`, `file_path`
- Constraint: `UNIQUE(video_id, lang, source, file_path)`

Role:
- Distinguishes manual vs auto subtitle sources and records the local file used for indexing.

`transcript_segments`

- Primary key: `segment_id`
- Foreign keys:
  - `track_id -> subtitle_tracks(track_id)` with `ON DELETE CASCADE`
  - `video_id -> videos(video_id)` with `ON DELETE CASCADE`
- Fields: `segment_index`, `start_seconds`, `end_seconds`, `text`
- Constraint: `UNIQUE(track_id, segment_index)`

Role:
- Holds timed subtitle/transcript lines used for preview and FTS search.

`playlists`

- Primary key: `playlist_id`
- Fields: `title`, `channel`, `webpage_url`

Role:
- Stores playlist identity separate from video membership.

`playlist_entries`

- Composite primary key: `(playlist_id, video_id)`
- Foreign keys:
  - `playlist_id -> playlists(playlist_id)` with `ON DELETE CASCADE`
  - `video_id -> videos(video_id)` with `ON DELETE CASCADE`
- Field: `position`

Role:
- Captures playlist membership and stable ordering without duplicating video metadata.

### FTS5 Tables

`chapter_fts`

- Virtual table columns: `video_id UNINDEXED`, `chapter_id UNINDEXED`, `title`

Role:
- Search index for chapter titles.

`transcript_fts`

- Virtual table columns: `video_id UNINDEXED`, `segment_id UNINDEXED`, `text`

Role:
- Search index for transcript text, with snippets and BM25 ranking.

### Schema Write Strategy

The catalog uses upsert-and-replace behavior rather than incremental row mutation:

- `upsert_video(...)` refreshes canonical video metadata.
- `replace_chapters(...)` clears and rebuilds a video's chapters plus `chapter_fts`.
- `replace_transcripts(...)` clears and rebuilds subtitle tracks, transcript segments, and `transcript_fts`.
- `upsert_playlist_entry(...)` refreshes playlist identity and membership idempotently.

That design keeps refresh logic simple and avoids trying to diff subtitle segments or chapter changes across repeated indexing runs.

## Security Model

The security posture is narrow and practical: trust as little external text as possible, keep local state private, and never invoke shell commands through a shell interpreter.

### Input Validation

- `yt_dlp.normalize_target(...)` only accepts:
  - full `http` or `https` URLs whose hosts are in `ALLOWED_YOUTUBE_HOSTS`, or
  - 11-character YouTube ids
- Non-YouTube URLs are rejected before subprocess execution.
- Clip modes, selection strings, and config keys are validated before work starts.

### Terminal Output Hygiene

- `sanitize_terminal_text(...)` strips ANSI escapes, control bytes, and line-breaking whitespace.
- Rich/plain human output passes through this sanitizer before being printed.
- This prevents hostile titles, channels, or transcript text from writing escape sequences into the terminal.

### Local State Protection

- Private directories are created with POSIX mode `0o700`.
- Private files are created with POSIX mode `0o600`.
- Subtitle cache trees are re-hardened with `protect_private_tree(...)` after yt-dlp writes files.

### Concurrency Control

- Mutation commands take `operation_lock(...)` on `<catalog parent>/operation.lock`.
- The lock uses `fcntl.flock` on POSIX and `msvcrt.locking` on Windows.
- Read commands do not take the lock.

### Subprocess Discipline

- Every subprocess call is argument-vector based.
- No command in the codebase uses `shell=True`.
- `yt-dlp`, `ffmpeg`, and `fzf` are resolved from `PATH` and executed directly.

### SQL Safety

- Catalog queries use `?` placeholders.
- LIKE-based library search escapes `%`, `_`, and `\`, then uses `ESCAPE '\'`.
- FTS input is sanitized by `_fts_query(...)`, which strips non-word tokens before building quoted terms.

## Error Hierarchy and Exit Codes

The error model is centralized in `errors.py` and surfaced consistently by `cli._run_guarded(...)`.

### Application Error Classes

```text
YtAgentError                        -> ExitCode.EXTERNAL (6) by default
├── DependencyError                 -> 3
├── InvalidInputError               -> 4
├── ConfigError                     -> 5
├── SelectionError                  -> 4
├── ExternalCommandError            -> 6
└── StateLockError                  -> 7
```

Additional guarded mappings in `_run_guarded(...)`:

- `sqlite3.Error` -> `ExitCode.STORAGE` (`8`)
- `KeyboardInterrupt` -> `ExitCode.INTERRUPTED` (`130`)

### Exit Code Table

- `0`: success
- `3`: missing dependency such as `yt-dlp` or `ffmpeg`
- `4`: invalid input, bad selection, unsupported mode, or malformed metadata
- `5`: config parse or validation failure
- `6`: external command failure or uncategorized application failure
- `7`: another mutating command already holds the lock
- `8`: SQLite or catalog storage failure
- `130`: interrupted by the user

This is a stable CLI contract, not just an implementation detail. The same exit codes are reflected in JSON error envelopes for agent or script consumers.

## Agent Output Contract

The CLI supports human output modes (`table`, `plain`) and agent-friendly JSON payloads. Mutation commands share a common schema version and structure.

### Error Envelope

`_json_error_payload(...)` produces:

```json
{
  "schema_version": 1,
  "status": "error",
  "exit_code": 6,
  "error_type": "ExternalCommandError",
  "message": "yt-dlp download failed.",
  "stderr": "optional stderr"
}
```

Fields:

- `schema_version`: currently `1`
- `status`: always `"error"`
- `exit_code`: stable numeric process contract
- `error_type`: Python exception class name
- `message`: user-facing summary
- `stderr`: included only when an external command exposed stderr

### Shared Mutation Envelope

`_mutation_payload(...)` is the common success/partial/noop wrapper:

```json
{
  "schema_version": 1,
  "command": "download",
  "status": "ok",
  "summary": {},
  "warnings": [],
  "errors": []
}
```

Shared fields:

- `schema_version`
- `command`
- `status`
- `summary`
- `warnings`
- `errors`

`status` conventions:

- `"ok"`: the command completed requested work
- `"partial"`: mixed success and failure
- `"noop"`: dry run, empty selection, or no-op outcome
- `"error"`: reserved for the separate JSON error envelope

### Download Payload

`_download_operation_payload(...)` extends the shared envelope with:

- `requested`
- `resolved_targets`
- `downloaded`
- `skipped`
- `failed`
- `mode`
- `fetch_subs`
- `auto_subs`
- `download_root`
- `dry_run`

Each download item includes normalized video metadata plus:

- `status`
- `requested_input`
- optional `reason`
- optional `output_path`
- optional `info_json_path`
- `indexed`
- optional `index_summary`
- optional `index_warning`
- optional `error_message`
- optional `stderr`

### Index Payload

`_index_payload(...)` extends the shared envelope with:

- `requested`
- `fetch_subs`
- `auto_subs`
- `network_fetch_attempted`
- `dry_run`

Its `summary` always reports:

- `videos`
- `playlists`
- `chapters`
- `transcript_segments`

### Clip Grab Payload

`_clip_grab_payload(...)` extends the shared envelope with:

- `locator`
- `start_seconds`
- `end_seconds`
- `padding_before`
- `padding_after`
- `mode`
- `output_path`
- `output_path_is_template`
- `source`
- `used_remote_fallback`
- `dry_run`

This makes clip extraction deterministic for automation: a caller can tell whether the path is concrete or templated, whether the result was local or remote, and which final time range was applied after padding.

## TUI and CLI Relationship

The CLI is the stable backend contract. The TUI does not own a second storage model.

- Downloading and indexing happen through CLI-oriented flows.
- `tui.py` reads through `CatalogStore` and related detail methods such as `get_video_details(...)`.
- Clip search uses the same `CatalogStore.search_clips(...)` API whether the caller is a user, a script, or the Textual UI.

That separation keeps write paths explicit and makes the TUI safe to treat as a read-mostly surface in v1.
