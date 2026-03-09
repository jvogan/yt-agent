# Third-Party Software

`yt-agent` is a wrapper/orchestration layer around existing tools and libraries. This repository does not bundle `yt-dlp` or `ffmpeg` binaries.

## Runtime Tools

- `yt-dlp`
  - Purpose: search, metadata extraction, downloads, remote section downloads
  - Upstream: <https://github.com/yt-dlp/yt-dlp>
  - License: Unlicense

- `ffmpeg`
  - Purpose: local clip extraction and some post-processing
  - Upstream: <https://ffmpeg.org/>
  - License: varies by build; see upstream licensing guidance

## Python Libraries

- `Typer`
  - Upstream: <https://github.com/fastapi/typer>
  - License: MIT

- `Rich`
  - Upstream: <https://github.com/Textualize/rich>
  - License: MIT

- `Textual`
  - Upstream: <https://github.com/Textualize/textual>
  - License: MIT

## Notes

- Users remain responsible for complying with the licenses and terms of the software they install and the media they process.
- If `yt-agent` ever ships bundled binaries in the future, licensing and redistribution requirements will need a separate review.
