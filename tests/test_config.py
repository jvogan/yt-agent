from pathlib import Path

import pytest

from yt_agent.config import (
    DEFAULT_ARCHIVE_FILE,
    DEFAULT_CATALOG_FILE,
    DEFAULT_DOWNLOAD_ROOT,
    _default_paths,
    load_settings,
    render_default_config,
)
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


def test_load_settings_accepts_audio_default_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_mode = "audio"\n', encoding="utf-8")
    settings = load_settings(config_path)
    assert settings.default_mode == "audio"


def test_load_settings_rejects_invalid_default_mode(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_mode = "podcast"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="default_mode"):
        load_settings(config_path)


def test_load_settings_rejects_invalid_selector(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('selector = "dmenu"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="selector"):
        load_settings(config_path)


def test_load_settings_rejects_search_limit_below_one(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("search_limit = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="search_limit"):
        load_settings(config_path)


def test_load_settings_rejects_empty_subtitle_languages(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('subtitle_languages = "   "\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="subtitle_languages"):
        load_settings(config_path)


def test_load_settings_env_download_root_overrides_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'download_root = "{tmp_path / "from-toml"}"\n', encoding="utf-8")
    override_root = tmp_path / "from-env"
    monkeypatch.setenv("YT_AGENT_DOWNLOAD_ROOT", str(override_root))

    settings = load_settings(config_path)

    assert settings.download_root == override_root


def test_load_settings_env_audio_format_overrides_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('audio_format = "worst"\n', encoding="utf-8")
    monkeypatch.setenv("YT_AGENT_AUDIO_FORMAT", "bestaudio[ext=m4a]/best")

    settings = load_settings(config_path)

    assert settings.audio_format == "bestaudio[ext=m4a]/best"


def test_load_settings_env_default_mode_overrides_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('default_mode = "video"\n', encoding="utf-8")
    monkeypatch.setenv("YT_AGENT_DEFAULT_MODE", "audio")

    settings = load_settings(config_path)

    assert settings.default_mode == "audio"


def test_load_settings_env_languages_overrides_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('subtitle_languages = "en"\n', encoding="utf-8")
    monkeypatch.setenv("YT_AGENT_LANGUAGES", "ja.*,ja")

    settings = load_settings(config_path)

    assert settings.subtitle_languages == "ja.*,ja"


def test_load_settings_ignores_unset_env_vars(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '\n'.join(
            [
                f'download_root = "{tmp_path / "from-toml"}"',
                'audio_format = "best"',
                'default_mode = "audio"',
                'subtitle_languages = "fr"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path, env={})

    assert settings.download_root == tmp_path / "from-toml"
    assert settings.audio_format == "best"
    assert settings.default_mode == "audio"
    assert settings.subtitle_languages == "fr"


def test_load_settings_rejects_invalid_default_mode_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("YT_AGENT_DEFAULT_MODE", "podcast")

    with pytest.raises(ConfigError, match="default_mode"):
        load_settings(config_path)


def test_load_settings_rejects_empty_download_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setenv("YT_AGENT_DOWNLOAD_ROOT", "   ")

    with pytest.raises(ConfigError, match="YT_AGENT_DOWNLOAD_ROOT"):
        load_settings(config_path)


def test_default_paths_use_appdata_on_windows() -> None:
    appdata = Path(r"C:\Users\demo\AppData\Roaming")
    local_appdata = Path(r"C:\Users\demo\AppData\Local")
    home = Path(r"C:\Users\demo")
    defaults = _default_paths(
        platform="nt",
        env={
            "APPDATA": r"C:\Users\demo\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\demo\AppData\Local",
        },
        home=home,
    )

    assert defaults.config_path == appdata / "yt-agent" / "config.toml"
    assert defaults.archive_file == local_appdata / "yt-agent" / "archive.txt"
    assert defaults.manifest_file == local_appdata / "yt-agent" / "downloads.jsonl"
    assert defaults.catalog_file == local_appdata / "yt-agent" / "catalog.sqlite"
    assert defaults.download_root == home / "Media" / "YouTube"
    assert defaults.clips_root == home / "Media" / "YouTube" / "_clips"


def test_render_default_config_uses_windows_env_paths() -> None:
    rendered = render_default_config(
        platform="nt",
        env={
            "APPDATA": r"C:\Users\demo\AppData\Roaming",
            "LOCALAPPDATA": r"C:\Users\demo\AppData\Local",
        },
        home=Path(r"C:\Users\demo"),
    )

    assert 'archive_file = "%LOCALAPPDATA%/yt-agent/archive.txt"' in rendered
    assert 'manifest_file = "%LOCALAPPDATA%/yt-agent/downloads.jsonl"' in rendered
    assert 'catalog_file = "%LOCALAPPDATA%/yt-agent/catalog.sqlite"' in rendered
    assert 'download_root = "~/Media/YouTube"' in rendered
