# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025 Kim
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
Handles detection of Godot version and safe purging of stale import artifacts.
"""

import os

from gmos.io import safe_rmtree
from gmos.utils import logger


def detect_godot_version(game_dir: str) -> int:
    """
    Heuristic to detect Godot major version from project.godot.
    Returns 3 or 4. Defaults to 3 if uncertain.
    """
    proj_path = os.path.join(game_dir, "project.godot")
    if not os.path.exists(proj_path):
        # Fallback: check for .godot folder (G4 feature)
        if os.path.isdir(os.path.join(game_dir, ".godot")):
            return 4
        return 3

    try:
        with open(proj_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                # Godot 4 usually has config_version=5
                if line.startswith("config_version="):
                    try:
                        ver = int(line.split("=")[1].strip())
                        if ver >= 5:
                            return 4
                    except ValueError:
                        pass
    except Exception:
        pass

    return 3


def get_cache_path(game_dir: str) -> str:
    """Returns the absolute path to the import cache directory."""
    ver = detect_godot_version(game_dir)
    if ver >= 4:
        return os.path.join(game_dir, ".godot", "imported")
    return os.path.join(game_dir, ".import")


def purge_cache(game_dir: str) -> int:
    """
    Completely removes the asset cache directory.
    Forces Godot to re-import all assets on next launch.
    Returns number of files removed (rough estimate).
    """
    cache_dir = get_cache_path(game_dir)
    if not os.path.exists(cache_dir):
        logger.info("Cache purge: Directory not found: %s", cache_dir)
        return 0

    count = 0
    try:
        # Count files for reporting
        for _, _, files in os.walk(cache_dir):
            count += len(files)

        logger.info("Purging cache dir: %s (%d files)", cache_dir, count)
        safe_rmtree(cache_dir)

        # Re-create empty dir structure to be polite (though Godot will do it)
        if detect_godot_version(game_dir) >= 4:
            os.makedirs(cache_dir, exist_ok=True)

    except Exception as e:
        logger.error("Failed to purge cache %s: %s", cache_dir, e)
        raise e

    return count


def clean_stale_imports(game_dir: str) -> int:
    """
    Surgical cleanup: Removes .import files for assets that no longer exist.
    Useful for Godot 3 where .import files sit alongside assets,
    or the global .import folder retains orphans.
    """
    # This implementation focuses on the global .import folder cleanup
    # which is the most common cause of "ghost asset" issues.

    cache_dir = get_cache_path(game_dir)
    if not os.path.isdir(cache_dir):
        return 0

    removed_count = 0
    # This is a complex operation because mapping hash->file is hard without reading
    # every .import file. For v1, we stick to the safer "Purge" which fixes all issues.
    # A true stale cleaner requires parsing binary MD5 maps.

    # Placeholder for future expansion:
    # 1. Read all *.import files in game_dir (source side)
    # 2. Cross reference with cache_dir
    # 3. Delete orphans

    return removed_count
