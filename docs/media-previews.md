# Smart clips and media previews

## Silence-aware clips

`clips smart` analyzes a local video's audio around an indexed transcript or chapter
hit and snaps the start/end to nearby silence before extracting the clip:

```bash
yt-agent clips smart transcript:42 --window 2 --mode accurate
```

The analysis uses ffmpeg `silencedetect`; there is no ML dependency. `--window` is
bounded to 0.1-10 seconds, `--noise-db` to -80 through -10 dB, and `--min-silence`
to 0.05-5 seconds. Use `--dry-run --output json` to inspect the original and snapped
bounds. Smart clipping requires cataloged media under the configured download root.

## Contact sheets and GIF previews

Create an evenly sampled image grid:

```bash
yt-agent preview contact-sheet VIDEO_ID --frames 12 --columns 4 --width 320
```

Add `--gif` for a short adjacent GIF, with bounded `--gif-start`, `--gif-duration`,
and `--gif-fps` controls. Contact sheets accept 4-25 frames, 1-5 columns, and frame
widths from 160-1920 pixels. Existing outputs require explicit `--force`.

Both workflows are local-only, pass structured argument lists to ffmpeg, validate
catalog media paths, and support `--dry-run --output json` without creating output.
