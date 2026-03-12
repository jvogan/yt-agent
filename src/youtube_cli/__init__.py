"""Compatibility shim for the old ``youtube_cli`` package name."""

from __future__ import annotations

import importlib
import sys

from yt_agent import __version__

for module_name in (
    "archive",
    "catalog",
    "chapters",
    "cli",
    "clips",
    "config",
    "errors",
    "indexer",
    "library",
    "manifest",
    "models",
    "security",
    "selector",
    "transcripts",
    "tui",
    "yt_dlp",
):
    sys.modules.setdefault(f"{__name__}.{module_name}", importlib.import_module(f"yt_agent.{module_name}"))

__all__ = ["__version__"]
