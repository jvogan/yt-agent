"""Configuration loading and path resolution."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_agent.errors import ConfigError


DEFAULT_CONFIG_PATH = Path("~/.config/yt-agent/config.toml").expanduser()
DEFAULT_DOWNLOAD_ROOT = Path("~/Media/YouTube").expanduser()
DEFAULT_ARCHIVE_FILE = Path("~/.local/share/yt-agent/archive.txt").expanduser()
DEFAULT_MANIFEST_FILE = Path("~/.local/share/yt-agent/downloads.jsonl").expanduser()
DEFAULT_CATALOG_FILE = Path("~/.local/share/yt-agent/catalog.sqlite").expanduser()
DEFAULT_CLIPS_ROOT = (DEFAULT_DOWNLOAD_ROOT / "_clips").expanduser()

DEFAULT_CONFIG_TEXT = """download_root = "~/Media/YouTube"
archive_file = "~/.local/share/yt-agent/archive.txt"
manifest_file = "~/.local/share/yt-agent/downloads.jsonl"
catalog_file = "~/.local/share/yt-agent/catalog.sqlite"
clips_root = "~/Media/YouTube/_clips"
search_limit = 10
video_format = "bv*+ba/b"
audio_format = "bestaudio/best"
default_mode = "video"
selector = "prompt"
subtitle_languages = "en.*,en"
write_thumbnail = true
write_description = true
write_info_json = true
embed_metadata = true
embed_thumbnail = false
"""

ALLOWED_SELECTOR_VALUES = {"prompt", "fzf"}
ALLOWED_DEFAULT_MODES = {"video"}


def _expand_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def _require_type(key: str, value: Any, expected: type[Any]) -> Any:
    if not isinstance(value, expected):
        raise ConfigError(f"Config key '{key}' must be a {expected.__name__}.")
    return value


def _bool_value(values: dict[str, Any], key: str, default: bool) -> bool:
    value = values.get(key, default)
    return bool(_require_type(key, value, bool))


def _int_value(values: dict[str, Any], key: str, default: int) -> int:
    value = values.get(key, default)
    return int(_require_type(key, value, int))


def _str_value(values: dict[str, Any], key: str, default: str) -> str:
    value = values.get(key, default)
    return str(_require_type(key, value, str))


@dataclass(frozen=True)
class Settings:
    """Runtime settings loaded from TOML and defaults."""

    config_path: Path = DEFAULT_CONFIG_PATH
    download_root: Path = DEFAULT_DOWNLOAD_ROOT
    archive_file: Path = DEFAULT_ARCHIVE_FILE
    manifest_file: Path = DEFAULT_MANIFEST_FILE
    catalog_file: Path = DEFAULT_CATALOG_FILE
    clips_root: Path = DEFAULT_CLIPS_ROOT
    search_limit: int = 10
    video_format: str = "bv*+ba/b"
    audio_format: str = "bestaudio/best"
    default_mode: str = "video"
    selector: str = "prompt"
    subtitle_languages: str = "en.*,en"
    write_thumbnail: bool = True
    write_description: bool = True
    write_info_json: bool = True
    embed_metadata: bool = True
    embed_thumbnail: bool = False

    def ensure_storage_paths(self) -> None:
        self.download_root.mkdir(parents=True, exist_ok=True)
        self.archive_file.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        self.catalog_file.parent.mkdir(parents=True, exist_ok=True)
        self.clips_root.mkdir(parents=True, exist_ok=True)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)


def load_settings(config_path: Path | None = None) -> Settings:
    """Load settings from disk, falling back to defaults."""

    resolved_config_path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    values: dict[str, Any] = {}
    if resolved_config_path.exists():
        with resolved_config_path.open("rb") as handle:
            parsed = tomllib.load(handle)
        if not isinstance(parsed, dict):
            raise ConfigError("Config file must contain a TOML table.")
        values = parsed

    unexpected_keys = set(values) - {
        "download_root",
        "archive_file",
        "manifest_file",
        "catalog_file",
        "clips_root",
        "search_limit",
        "video_format",
        "audio_format",
        "default_mode",
        "selector",
        "subtitle_languages",
        "write_thumbnail",
        "write_description",
        "write_info_json",
        "embed_metadata",
        "embed_thumbnail",
    }
    if unexpected_keys:
        joined = ", ".join(sorted(unexpected_keys))
        raise ConfigError(f"Unknown config key(s): {joined}")

    settings = Settings(
        config_path=resolved_config_path,
        download_root=_expand_path(values.get("download_root", DEFAULT_DOWNLOAD_ROOT)),
        archive_file=_expand_path(values.get("archive_file", DEFAULT_ARCHIVE_FILE)),
        manifest_file=_expand_path(values.get("manifest_file", DEFAULT_MANIFEST_FILE)),
        catalog_file=_expand_path(values.get("catalog_file", DEFAULT_CATALOG_FILE)),
        clips_root=_expand_path(values.get("clips_root", DEFAULT_CLIPS_ROOT)),
        search_limit=_int_value(values, "search_limit", 10),
        video_format=_str_value(values, "video_format", "bv*+ba/b"),
        audio_format=_str_value(values, "audio_format", "bestaudio/best"),
        default_mode=_str_value(values, "default_mode", "video"),
        selector=_str_value(values, "selector", "prompt"),
        subtitle_languages=_str_value(values, "subtitle_languages", "en.*,en"),
        write_thumbnail=_bool_value(values, "write_thumbnail", True),
        write_description=_bool_value(values, "write_description", True),
        write_info_json=_bool_value(values, "write_info_json", True),
        embed_metadata=_bool_value(values, "embed_metadata", True),
        embed_thumbnail=_bool_value(values, "embed_thumbnail", False),
    )

    if settings.search_limit < 1:
        raise ConfigError("Config key 'search_limit' must be at least 1.")
    if settings.selector not in ALLOWED_SELECTOR_VALUES:
        allowed = ", ".join(sorted(ALLOWED_SELECTOR_VALUES))
        raise ConfigError(f"Config key 'selector' must be one of: {allowed}")
    if settings.default_mode not in ALLOWED_DEFAULT_MODES:
        allowed = ", ".join(sorted(ALLOWED_DEFAULT_MODES))
        raise ConfigError(f"Config key 'default_mode' must be one of: {allowed}")
    if not settings.subtitle_languages.strip():
        raise ConfigError("Config key 'subtitle_languages' must not be empty.")

    return settings


def render_default_config() -> str:
    """Return the canonical starter config content."""

    return DEFAULT_CONFIG_TEXT
