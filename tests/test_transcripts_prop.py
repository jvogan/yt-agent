from __future__ import annotations

import re
from pathlib import Path
from tempfile import TemporaryDirectory

from hypothesis import given, settings
from hypothesis import strategies as st

from yt_agent.transcripts import parse_subtitle_file

TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=200)
LINE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\n\r<>"),
    max_size=40,
)
NON_BLANK_LINE = LINE_TEXT.filter(lambda value: bool(value.strip()))


def _write_vtt(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _expected_text(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(line.strip() for line in lines if line.strip())).strip()


@given(content=TEXT)
@settings(deadline=None)
def test_parse_subtitle_file_handles_arbitrary_vtt_content(content: str) -> None:
    with TemporaryDirectory() as tmp_dir:
        subtitle_path = _write_vtt(Path(tmp_dir) / "fuzz.vtt", content)
        segments = parse_subtitle_file(subtitle_path)

    assert [segment.segment_index for segment in segments] == list(range(len(segments)))
    assert all(segment.text for segment in segments)


@given(cue_bodies=st.lists(st.lists(NON_BLANK_LINE, min_size=1, max_size=3), min_size=1, max_size=8))
@settings(deadline=None)
def test_parse_subtitle_file_skips_bad_vtt_blocks_and_keeps_valid_cues(cue_bodies: list[list[str]]) -> None:
    blocks: list[str] = []
    expected_texts: list[str] = []

    for index, body in enumerate(cue_bodies):
        blocks.append(f"bad-{index}\nnot-a-time --> still-not-a-time\nnoise")
        blocks.append(
            "\n".join(
                [
                    f"cue-{index}",
                    f"00:00:{index:02d}.000 --> 00:00:{index + 1:02d}.000",
                    *body,
                ]
            )
        )
        expected_texts.append(_expected_text(body))

    with TemporaryDirectory() as tmp_dir:
        subtitle_path = _write_vtt(Path(tmp_dir) / "mixed.vtt", "WEBVTT\n\n" + "\n\n".join(blocks))
        segments = parse_subtitle_file(subtitle_path)

    assert [segment.text for segment in segments] == expected_texts
    assert [segment.segment_index for segment in segments] == list(range(len(expected_texts)))
