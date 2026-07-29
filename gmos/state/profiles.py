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
Profiles Module: Handles the import/export of GMOS profiles.
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple, TypedDict, cast

try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired
from gmos.io import atomic_replace
from gmos.utils import ModConfig, get_mod_name_from_config, logger

# Schema Version
PROFILE_FORMAT_VERSION = "2.0"


class ProfileModEntry(TypedDict):
    """Single mod entry in a profile."""

    name: str
    enabled: bool
    version: Optional[str]
    author: Optional[str]
    description: Optional[str]


class IsolationSettings(TypedDict):
    """Sandbox isolation settings."""

    isolate_data: bool


class ProfileManifest(TypedDict):
    """Root schema for profiles."""

    format_version: str
    gmos_version: str
    timestamp_utc: str
    last_used_utc: NotRequired[str]
    game_executable: str
    description: str
    isolation: IsolationSettings
    mods: List[ProfileModEntry]


def _extract_metadata(cfg: Dict[str, Any], key: str) -> Optional[str]:
    """Extract metadata string from mod config."""
    sections = cast(Dict[str, Any], cfg.get("Sections", {}))
    if sections and "ModInfo" in sections:
        mi = sections["ModInfo"]
        if isinstance(mi, dict):
            return str(cast(Dict[str, Any], mi).get(key, ""))
        elif isinstance(mi, list):
            for line_obj in cast(List[str], mi):
                line = str(line_obj)
                if line.lower().startswith(key.lower()):
                    try:
                        _, v = line.split("=", 1)
                        return str(v).strip().strip('"')
                    except ValueError:
                        pass
    return None


def create_profile_data(
    mod_configs: List[Dict[str, Any]],
    game_config: Dict[str, Any],
    description: str = "",
    isolate_data: bool = False,
) -> ProfileManifest:
    """Generates the profile dictionary from the current application state."""
    mod_entries: List[ProfileModEntry] = []

    for cfg in mod_configs:
        name = cfg.get("Name") or get_mod_name_from_config(cast(ModConfig, cfg))
        enabled = bool(cfg.get("Enabled", True))

        entry: ProfileModEntry = {
            "name": str(name),
            "enabled": enabled,
            "version": _extract_metadata(cfg, "Version"),
            "author": _extract_metadata(cfg, "Author"),
            "description": _extract_metadata(cfg, "Description"),
        }
        mod_entries.append(entry)

    exe_name = game_config.get("game_executable", "game.exe")

    profile: ProfileManifest = {
        "format_version": PROFILE_FORMAT_VERSION,
        "gmos_version": "1.0.0",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "game_executable": str(exe_name),
        "description": description,
        "isolation": {
            "isolate_data": isolate_data,
        },
        "mods": mod_entries,
    }
    return profile


def save_profile_to_disk(data: ProfileManifest, path: str) -> None:
    try:
        json_str = json.dumps(data, indent=2)
        atomic_replace(path, json_str)
        logger.info("Profile saved to %s", path)
    except Exception as e:
        logger.error("Failed to save profile to %s: %s", path, e)
        raise e


def load_profile_from_disk(path: str) -> ProfileManifest:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ValueError(f"Failed to read file: {e}") from e

    if not isinstance(data, dict):
        raise ValueError("Invalid profile format: Root must be a dictionary.")

    if "mods" not in data or not isinstance(data["mods"], list):
        raise ValueError("Invalid profile format: Missing 'mods' list.")

    # Compat: Default isolation if missing
    if "isolation" not in data:
        data["isolation"] = {"isolate_data": False}

    return cast(ProfileManifest, data)


def apply_profile_to_configs(
    profile: ProfileManifest, current_configs: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Reorders and updates configs. Returns (new_order, warnings).
    """
    config_map: Dict[str, Dict[str, Any]] = {}
    for cfg in current_configs:
        name = cfg.get("Name") or get_mod_name_from_config(cast(ModConfig, cfg))
        config_map[str(name)] = cfg

    new_order: List[Dict[str, Any]] = []
    processed_names: set[str] = set()
    warnings: List[str] = []

    for entry in profile["mods"]:
        name = entry["name"]
        if name in config_map:
            cfg = config_map[name]
            cfg["Enabled"] = entry["enabled"]
            new_order.append(cfg)
            processed_names.add(name)

            local_ver = _extract_metadata(cfg, "Version")
            if entry.get("version") and local_ver != entry["version"]:
                warnings.append(
                    f"Version Mismatch: '{name}' (Profile: {entry['version']}, Local: {local_ver})"
                )
        else:
            logger.warning("Profile references missing mod: %s", name)

    for name, cfg in config_map.items():
        if name not in processed_names:
            cfg["Enabled"] = False
            new_order.append(cfg)

    return new_order, warnings
