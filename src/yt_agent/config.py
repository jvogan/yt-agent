"""Configuration loading and path resolution."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yt_agent.errors import ConfigError
from yt_agent.security import ensure_private_directory

__all__ = [
    "DefaultPaths",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DOWNLOAD_ROOT",
    "DEFAULT_ARCHIVE_FILE",
    "DEFAULT_MANIFEST_FILE",
    "DEFAULT_CATALOG_FILE",
    "DEFAULT_CLIPS_ROOT",
    "ALLOWED_SELECTOR_VALUES",
    "ALLOWED_DEFAULT_MODES",
    "ENV_VAR_CONFIG_KEYS",
    "Settings",
    "load_settings",
    "render_default_config",
]



@dataclass(frozen=True)
class DefaultPaths:
    config_path: Path
    download_root: Path
    archive_file: Path
    manifest_file: Path
    catalog_file: Path
    clips_root: Path


def _relative_to(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def _default_paths(
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> DefaultPaths:
    current_platform = platform or os.name
    current_env = env or os.environ
    current_home = home or Path.home()
    download_root = current_home / "Media" / "YouTube"

    if current_platform == "nt":
        appdata_root = Path(current_env.get("APPDATA", current_home / "AppData" / "Roaming"))
        local_appdata_root = Path(current_env.get("LOCALAPPDATA", appdata_root))
        state_root = local_appdata_root / "yt-agent"
        config_path = appdata_root / "yt-agent" / "config.toml"
    else:
        state_root = current_home / ".local" / "share" / "yt-agent"
        config_path = current_home / ".config" / "yt-agent" / "config.toml"

    return DefaultPaths(
        config_path=config_path,
        download_root=download_root,
        archive_file=state_root / "archive.txt",
        manifest_file=state_root / "downloads.jsonl",
        catalog_file=state_root / "catalog.sqlite",
        clips_root=download_root / "_clips",
    )


def _render_path_for_config(
    path: Path,
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> str:
    current_platform = platform or os.name
    current_env = env or os.environ
    current_home = home or Path.home()

    if current_platform == "nt":
        for env_key in ("LOCALAPPDATA", "APPDATA"):
            base_raw = current_env.get(env_key)
            if not base_raw:
                continue
            relative = _relative_to(path, Path(base_raw))
            if relative is not None:
                suffix = relative.as_posix()
                return f"%{env_key}%" if not suffix else f"%{env_key}%/{suffix}"

    relative_home = _relative_to(path, current_home)
    if relative_home is not None:
        suffix = relative_home.as_posix()
        return "~" if not suffix else f"~/{suffix}"
    return str(path)


_DEFAULT_PATHS = _default_paths()

DEFAULT_CONFIG_PATH = _DEFAULT_PATHS.config_path
DEFAULT_DOWNLOAD_ROOT = _DEFAULT_PATHS.download_root
DEFAULT_ARCHIVE_FILE = _DEFAULT_PATHS.archive_file
DEFAULT_MANIFEST_FILE = _DEFAULT_PATHS.manifest_file
DEFAULT_CATALOG_FILE = _DEFAULT_PATHS.catalog_file
DEFAULT_CLIPS_ROOT = _DEFAULT_PATHS.clips_root

ALLOWED_SELECTOR_VALUES = {"prompt", "fzf"}
ALLOWED_DEFAULT_MODES = {"video", "audio"}
ENV_VAR_CONFIG_KEYS = {
    "YT_AGENT_DOWNLOAD_ROOT": "download_root",
    "YT_AGENT_AUDIO_FORMAT": "audio_format",
    "YT_AGENT_DEFAULT_MODE": "default_mode",
    "YT_AGENT_LANGUAGES": "subtitle_languages",
}


def _expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(str(value))).expanduser()


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


def _apply_env_overrides(
    values: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    overridden = dict(values)
    for env_key, config_key in ENV_VAR_CONFIG_KEYS.items():
        value = env.get(env_key)
        if value is None:
            continue
        if not value.strip():
            raise ConfigError(f"Environment variable '{env_key}' must not be empty.")
        overridden[config_key] = value
    return overridden


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
        ensure_private_directory(self.archive_file.parent)
        ensure_private_directory(self.manifest_file.parent)
        ensure_private_directory(self.catalog_file.parent)
        self.clips_root.mkdir(parents=True, exist_ok=True)
        ensure_private_directory(self.config_path.parent)


def load_settings(
    config_path: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings from disk, falling back to defaults."""

    current_env = env or os.environ
    defaults = _default_paths(env=current_env)
    resolved_config_path = _expand_path(config_path or defaults.config_path)
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
    values = _apply_env_overrides(values, current_env)

    settings = Settings(
        config_path=resolved_config_path,
        download_root=_expand_path(values.get("download_root", defaults.download_root)),
        archive_file=_expand_path(values.get("archive_file", defaults.archive_file)),
        manifest_file=_expand_path(values.get("manifest_file", defaults.manifest_file)),
        catalog_file=_expand_path(values.get("catalog_file", defaults.catalog_file)),
        clips_root=_expand_path(values.get("clips_root", defaults.clips_root)),
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


def render_default_config(
    *,
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> str:
    """Return the canonical starter config content."""

    defaults = _default_paths(platform=platform, env=env, home=home)
    rendered_paths = {
        "download_root": _render_path_for_config(
            defaults.download_root, platform=platform, env=env, home=home
        ),
        "archive_file": _render_path_for_config(
            defaults.archive_file, platform=platform, env=env, home=home
        ),
        "manifest_file": _render_path_for_config(
            defaults.manifest_file, platform=platform, env=env, home=home
        ),
        "catalog_file": _render_path_for_config(
            defaults.catalog_file, platform=platform, env=env, home=home
        ),
        "clips_root": _render_path_for_config(
            defaults.clips_root, platform=platform, env=env, home=home
        ),
    }
    return "\n".join(
        [
            f'download_root = "{rendered_paths["download_root"]}"',
            f'archive_file = "{rendered_paths["archive_file"]}"',
            f'manifest_file = "{rendered_paths["manifest_file"]}"',
            f'catalog_file = "{rendered_paths["catalog_file"]}"',
            f'clips_root = "{rendered_paths["clips_root"]}"',
            "search_limit = 10",
            'video_format = "bv*+ba/b"',
            'audio_format = "bestaudio/best"',
            'default_mode = "video"',
            'selector = "prompt"',
            'subtitle_languages = "en.*,en"',
            "write_thumbnail = true",
            "write_description = true",
            "write_info_json = true",
            "embed_metadata = true",
            "embed_thumbnail = false",
            "",
        ]
    )
