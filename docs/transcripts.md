# Transcript workflows

## Export an indexed transcript

Export the preferred manual track as timestamped text:

```bash
yt-agent transcripts export VIDEO_ID --dest transcript.txt
```

Supported formats are `txt`, `md`, `json`, `vtt`, and `srt`. The format is inferred
from `--dest`, or can be set explicitly with `--format`. Use `--language en` to select
an exact indexed language, `--no-timestamps` for plain text, and `--chapters` to add
chapter headings to text/Markdown or chapter labels to JSON.

Without `--dest`, the transcript is written to stdout:

```bash
yt-agent transcripts export VIDEO_ID --format srt > transcript.srt
```

## Generate a local transcript

Local generation is opt-in and uses the separately installed `whisper-cli` from
whisper.cpp. A model file is always required; yt-agent never downloads one:

```bash
yt-agent transcripts generate VIDEO_ID \
  --model /path/to/ggml-base.en.bin \
  --language en
```

The video must already have local media under the configured download root. yt-agent
uses `ffmpeg` to create temporary 16 kHz mono audio, asks `whisper-cli` for VTT, writes
a `.provenance.json` sidecar, and indexes the generated VTT as a `local-whisper` track.
Existing manual and YouTube caption tracks remain indexed.

Preview all resolved paths and dependency checks without writing or running ASR:

```bash
yt-agent transcripts generate VIDEO_ID \
  --model /path/to/model.bin \
  --dry-run --output json
```

The default output is inside the catalog state directory under
`generated-transcripts/`. Use `--dest PATH.vtt` to choose another location. Existing
files or indexed transcript paths are refused unless `--force` is explicit. Force is
intended only for deliberately regenerating a known track; keep original sidecars in
their own paths.
