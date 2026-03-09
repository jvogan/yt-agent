from pathlib import Path

import pytest

from yt_agent.config import DEFAULT_ARCHIVE_FILE, DEFAULT_CATALOG_FILE, DEFAULT_DOWNLOAD_ROOT, load_settings
from yt_agent.errors import ConfigError


def test_load_settings_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"
    settings = load_settings(config_path)
    assert settings.config_path == config_path
    assert settings.download_root == DEFAULT_DOWNLOAD_ROOT
    assert settings.archive_file == DEFAULT_ARCHIVE_FILE
    assert settings.catalog_file == DEFAULT_CATALOG_FILE
    assert settings.search_limit == 10
    assert settings.selector == "prompt"


def test_load_settings_rejects_invalid_bool(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('write_thumbnail = "yes"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="write_thumbnail"):
        load_settings(config_path)


def test_load_settings_rejects_unknown_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("unknown = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown config key"):
        load_settings(config_path)
