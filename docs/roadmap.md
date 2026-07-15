# Roadmap

## Current scope

- Terminal search and download built on `yt-dlp`
- Organized local media library
- Playlist entry selection
- SQLite + FTS5 catalog
- Transcript/chapter clip search
- Textual TUI for catalog browsing
- Deep verification and safe repair previews
- Saved-source sync and a persistent synchronous retry queue
- Transcript export/local generation, comments FTS, smart clips, and media previews
- Live recording plus safe local/timestamp playback
- User notes, ratings, tags, collections, and bookmarks
- Versioned backup/restore of core indexed content, comments, and curation data

## Next likely steps

- Better agent-facing recipes and scriptability polish on top of the CLI backend
- Better clip result ranking and transcript context windows
- Chapter-aware clip labels and export presets
- Optional semantic search on top of the deterministic catalog
- Optional daemon mode for the existing synchronous queue
- More sync scheduling and notification integrations

## Explicitly out of scope for this release

- Browser automation against YouTube
- Skill-first packaging as the primary product surface
- Multi-user sync or remote shared catalogs
- AI ranking as the default clip-search path
- Silent external embedding or upload of library text
