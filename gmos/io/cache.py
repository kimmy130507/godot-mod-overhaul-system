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
"""
Cache Module: Manages Godot's internal asset cache (.import folder).
"""

import os

from gmos.io import safe_rmtree
from gmos.io.pck import get_main_pck_path, read_pck_header
from gmos.utils import logger


def detect_godot_version(game_dir: str) -> int:
    """
    Detects Godot major version by checking the main PCK header or project.godot.
    Returns 3 or 4. Defaults to 0 if uncertain.
    """
    # 1. Inspect PCK header directly for compiled games
    try:
        pck_path = get_main_pck_path(game_dir)
        if pck_path:
            header = read_pck_header(pck_path)
            if header.major in (3, 4):
                return header.major
    except Exception:
        pass

    # 2. Check loose project.godot (Decompiled / Dev setups)
    proj_path = os.path.join(game_dir, "project.godot")
    if not os.path.exists(proj_path):
        # Fallback: check for .godot folder (G4 feature)
        if os.path.isdir(os.path.join(game_dir, ".godot")):
            return 4
        return 0

    try:
        with open(proj_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                # Godot 4 usually has config_version=5
                if line.startswith("config_version") and "=" in line:
                    try:
                        ver = int(line.split("=", 1)[1].strip())
                        if ver >= 5:
                            return 4
                        if ver in (3, 4):
                            return 3
                    except ValueError:
                        pass
    except Exception:
        pass

    return 0


def get_cache_path(game_dir: str) -> str:
    """Returns the path to the import cache directory."""
    ver = detect_godot_version(game_dir)
    if ver >= 4:
        return os.path.join(game_dir, ".godot", "imported")
    return os.path.join(game_dir, ".import")


def purge_cache(game_dir: str) -> int:
    """
    Completely removes the asset cache directory, forcing a re-import.
    Only proceeds if 'project.godot' exists; otherwise raises PermissionError
    to prevent corruption of runtime-only game builds.
    Returns number of files removed.
    """
    if not os.path.exists(os.path.join(game_dir, "project.godot")):
        raise PermissionError(
            "'project.godot' not found.\n"
            "This appears to be a compiled runtime game, which cannot "
            "rebuild asset caches. Deleting these files will break the game."
        )
    cache_dir = get_cache_path(game_dir)
    if not os.path.exists(cache_dir):
        logger.info("Cache purge: Directory not found: %s", cache_dir)
        return 0

    count = 0
    try:
        # Count files
        for _, _, files in os.walk(cache_dir):
            count += len(files)

        logger.info("Purging cache dir: %s (%d files)", cache_dir, count)
        safe_rmtree(cache_dir)

        # Re-create empty dir structure
        if detect_godot_version(game_dir) >= 4:
            os.makedirs(cache_dir, exist_ok=True)

    except Exception as e:
        logger.error("Failed to purge cache %s: %s", cache_dir, e)
        raise e

    return count


def clean_stale_imports(game_dir: str) -> int:
    """
    Removes .import files for assets that no longer exist.
    """
    cache_dir = get_cache_path(game_dir)
    if not os.path.isdir(cache_dir):
        return 0

    # TODO: Cross-reference .import hashes with active VFS to remove stale assets
    return 0
