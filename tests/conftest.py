from pathlib import Path

import pytest

from yt_agent.config import Settings

MARKER_DESCRIPTIONS = (
    ("slow", "tests that take longer than five seconds to finish"),
    ("integration", "tests that require a real yt-dlp binary"),
)


def pytest_configure(config: pytest.Config) -> None:
    existing_markers = set(config.getini("markers"))
    for marker, description in MARKER_DESCRIPTIONS:
        marker_line = f"{marker}: {description}"
        if marker_line not in existing_markers:
            config.addinivalue_line("markers", marker_line)


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        config_path=tmp_path / "config.toml",
        download_root=tmp_path / "downloads",
        archive_file=tmp_path / "state" / "archive.txt",
        manifest_file=tmp_path / "state" / "downloads.jsonl",
        catalog_file=tmp_path / "state" / "catalog.sqlite",
        clips_root=tmp_path / "clips",
    )
