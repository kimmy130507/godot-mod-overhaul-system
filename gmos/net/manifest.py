# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025-2026 Kim
#
# This file is part of GMOS.
#
# GMOS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GMOS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GMOS.  If not, see <https://www.gnu.org/licenses/>.
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple, cast

from gmos.core.session import GmosSession


@dataclass
class ManifestEntry:
    mod_id: str
    name: str
    version: str
    provider: str  # "Nexus", or "Local"
    archive_hash: str  # SHA256 of the original zip/folder
    download_url: Optional[str] = None
    file_size: int = 0


@dataclass
class LobbyManifest:
    host_name: str
    game_version: str
    timestamp: float
    mods: List[ManifestEntry]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, data: str) -> "LobbyManifest":
        d = json.loads(data)
        mods_data = cast(List[Dict[str, Any]], d.pop("mods"))
        mods = [ManifestEntry(**m) for m in mods_data]
        return cls(mods=mods, **d)


# Cache: {dir_path: (max_mtime, hash_string)}
_hash_cache: Dict[str, Tuple[float, str]] = {}


def compute_dir_hash(directory: str) -> str:
    """Computes a stable hash of a directory's contents."""
    # Quick check for modifications to avoid reading GBs of data
    current_mtime = 0.0
    try:
        for root, _, files in os.walk(directory):
            for f in files:
                st = os.stat(os.path.join(root, f))
                if st.st_mtime > current_mtime:
                    current_mtime = st.st_mtime
    except OSError:
        pass  # Fallback to full re-hash

    if directory in _hash_cache:
        last_time, last_hash = _hash_cache[directory]
        if last_time == current_mtime:
            return last_hash
    sha = hashlib.sha256()
    for root, _, files in os.walk(directory):
        for names in sorted(files):
            filepath = os.path.join(root, names)
            try:
                # Hash filenames for structure
                sha.update(os.path.relpath(filepath, directory).encode())
                # Hash content
                with open(filepath, "rb") as stream:
                    while True:
                        block = stream.read(65536)
                        if not block:
                            break
                        sha.update(block)
            except OSError:
                pass
    result = sha.hexdigest()
    _hash_cache[directory] = (current_mtime, result)
    return result


def generate_manifest(session: GmosSession, host_name: str = "Host") -> LobbyManifest:

    entries: List[ManifestEntry] = []

    for _ in session.refresh_mods():
        pass

    for mod in session.mods:
        if not mod.is_enabled:
            continue

        cfg = cast(Dict[str, Any], mod.config or {})

        sections = cast(Dict[str, Any], cfg.get("Sections", {}))
        mod_info = cast(Dict[str, Any], sections.get("ModInfo", {}))

        provider = "Local"
        dl_url = None

        if os.path.isdir(mod.path):
            file_hash = compute_dir_hash(mod.path)
            size = sum(
                os.path.getsize(os.path.join(dirpath, filename))
                for dirpath, _, filenames in os.walk(mod.path)
                for filename in filenames
            )
        else:
            file_hash = "unknown"
            size = 0

        entries.append(
            ManifestEntry(
                mod_id=str(mod_info.get("Name", os.path.basename(mod.path))),
                name=str(mod_info.get("Name", "Unknown")),
                version=str(mod_info.get("Version", "0.0.0")),
                provider=provider,
                download_url=dl_url,
                archive_hash=file_hash,
                file_size=size,
            )
        )

    return LobbyManifest(
        host_name=host_name,
        game_version="1.0.0",  # Todo: Fetch from project.godot
        timestamp=time.time(),
        mods=entries,
    )
