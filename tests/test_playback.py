from pathlib import Path

import pytest

from yt_agent.errors import InvalidInputError
from yt_agent.playback import launch_media


def test_launch_media_prefers_mpv_with_safe_argument_boundary(tmp_path: Path, monkeypatch) -> None:
    media = tmp_path / "video;not-a-command.mp4"
    media.write_bytes(b"video")
    calls: list[tuple[list[str], bool]] = []
    monkeypatch.setattr("yt_agent.playback.shutil.which", lambda name: "/usr/bin/mpv")
    monkeypatch.setattr(
        "yt_agent.playback.subprocess.Popen",
        lambda args, start_new_session: calls.append((args, start_new_session)),
    )

    args = launch_media(media, start_seconds=12.5)

    assert args == ["/usr/bin/mpv", "--start=12.500", "--", str(media.resolve())]
    assert calls == [(args, True)]


def test_launch_media_uses_system_opener_fallback_without_shell(monkeypatch) -> None:
    monkeypatch.setattr(
        "yt_agent.playback.shutil.which",
        lambda name: None if name == "mpv" else "/usr/bin/xdg-open",
    )
    monkeypatch.setattr("yt_agent.playback.sys.platform", "linux")

    args = launch_media("https://www.youtube.com/watch?v=abc123def45&t=42", dry_run=True)

    assert args == [
        "/usr/bin/xdg-open",
        "https://www.youtube.com/watch?v=abc123def45&t=42",
    ]


def test_launch_media_uses_windows_startfile(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr("yt_agent.playback.shutil.which", lambda name: None)
    monkeypatch.setattr("yt_agent.playback.sys.platform", "win32")
    monkeypatch.setattr(
        "yt_agent.playback.os.startfile", lambda target: opened.append(target), raising=False
    )
    target = "https://www.youtube.com/watch?v=abc123def45"

    args = launch_media(target)

    assert args == ["startfile", target]
    assert opened == [target]


def test_launch_media_rejects_non_youtube_urls(monkeypatch) -> None:
    monkeypatch.setattr("yt_agent.playback.shutil.which", lambda name: "/usr/bin/mpv")
    with pytest.raises(InvalidInputError, match="Only YouTube URLs"):
        launch_media("https://example.com/video")
