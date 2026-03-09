from pathlib import Path

import pytest

from yt_agent.config import Settings


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
