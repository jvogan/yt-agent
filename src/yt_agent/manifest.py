"""Manifest persistence for successful downloads."""

from __future__ import annotations

import json
from pathlib import Path

from yt_agent.models import ManifestRecord


def ensure_manifest_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def append_manifest_record(path: Path, record: ManifestRecord) -> None:
    ensure_manifest_file(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.as_dict(), sort_keys=True))
        handle.write("\n")


def iter_manifest_records(path: Path) -> list[ManifestRecord]:
    if not path.exists():
        return []
    rows: list[ManifestRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        rows.append(ManifestRecord.from_dict(payload))
    return rows
