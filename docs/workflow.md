# Workflow

## Download-first flow

1. `yt-agent search "query"`
2. `yt-agent grab "query"` or `yt-agent download URL`
3. Successful downloads land in the organized media library.
4. Each successful download appends a manifest row and is indexed into the local catalog.

## Playlist curation flow

1. `yt-agent info PLAYLIST_URL --entries`
2. `yt-agent download PLAYLIST_URL --select-playlist`
3. Re-run the same playlist later without duplicate redownloads because the archive file stays in place.

## Catalog refresh flow

1. `yt-agent index refresh`
2. The catalog backfills videos from the manifest.
3. Existing `.info.json` sidecars provide chapters and local subtitle metadata.
4. Optional subtitle fetching fills transcript gaps when requested.

## Clip flow

1. `yt-agent clips search "query"`
2. `yt-agent clips show RESULT_ID`
3. `yt-agent clips grab RESULT_ID --padding-before 2 --padding-after 4`

## Library browse flow

- `yt-agent library list`
- `yt-agent library search "query"`
- `yt-agent library show VIDEO_ID`
- `yt-agent tui`
