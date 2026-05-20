from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from yt_agent.errors import InvalidInputError
from yt_agent.models import VideoInfo

TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40)
NON_BLANK_TEXT = TEXT.filter(lambda value: bool(value.strip()))
JSON_SCALAR = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False, allow_infinity=False)
    | TEXT
)
JSON_VALUE: st.SearchStrategy[Any] = st.recursive(
    JSON_SCALAR,
    lambda children: st.lists(children, max_size=4) | st.dictionaries(TEXT, children, max_size=4),
    max_leaves=12,
)
PAYLOADS = st.dictionaries(TEXT, JSON_VALUE, max_size=12)


@given(payload=PAYLOADS, original_url=st.one_of(st.none(), TEXT))
def test_from_yt_dlp_never_raises_unexpected_exceptions(
    payload: dict[str, Any], original_url: str | None
) -> None:
    try:
        info = VideoInfo.from_yt_dlp(payload, original_url=original_url)
    except InvalidInputError:
        return

    assert isinstance(info, VideoInfo)


@given(payload=PAYLOADS, video_id=NON_BLANK_TEXT, original_url=st.one_of(st.none(), TEXT))
def test_from_yt_dlp_with_id_returns_normalized_video_info(
    payload: dict[str, Any], video_id: str, original_url: str | None
) -> None:
    payload = {**payload, "id": video_id}

    info = VideoInfo.from_yt_dlp(payload, original_url=original_url)

    assert info.video_id == video_id.strip()
    assert info.title
    assert info.channel
    assert info.extractor_key
    assert info.webpage_url
    assert info.duration_seconds is None or isinstance(info.duration_seconds, int)
    assert info.original_url == original_url


@given(
    video_id=NON_BLANK_TEXT,
    extractor_key=st.one_of(st.just("youtube"), NON_BLANK_TEXT),
    candidate=JSON_VALUE,
)
def test_from_yt_dlp_only_accepts_http_webpage_urls(
    video_id: str, extractor_key: str, candidate: Any
) -> None:
    payload = {
        "id": video_id,
        "extractor_key": extractor_key,
        "webpage_url": candidate,
        "original_url": candidate,
    }

    info = VideoInfo.from_yt_dlp(payload)

    if isinstance(candidate, str) and candidate.startswith(("http://", "https://")):
        assert info.webpage_url == candidate
    elif extractor_key == "youtube":
        assert info.webpage_url == f"https://www.youtube.com/watch?v={video_id.strip()}"
    else:
        assert info.webpage_url == video_id.strip()
