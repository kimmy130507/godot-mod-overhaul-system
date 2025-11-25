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
Profiles Module: Handles the import/export of GMOS profiles (gmos_profile.json).
Standardizes the sharing of mod load orders and configurations.
"""

import json
import time
from typing import Any, Dict, List, Optional, Tuple, TypedDict, cast

from gmos.io import atomic_replace
from gmos.state.config import DEFAULTS
from gmos.utils import (
    ModConfig,
    _get_mod_name_from_config,  # type: ignore[reportPrivateUsage]
    logger,
)

# Schema Version
PROFILE_FORMAT_VERSION = "1.0"


class ProfileModEntry(TypedDict):
    """Schema for a single mod entry in a profile."""

    name: str
    enabled: bool
    version: Optional[str]
    # Future: hash, download_url


class ProfileManifest(TypedDict):
    """Root schema for gmos_profile.json."""

    format_version: str
    gmos_version: str
    timestamp_utc: str
    game_executable: str
    mods: List[ProfileModEntry]
    description: str


def _extract_version(cfg: Dict[str, Any]) -> Optional[str]:
    """Helper to extract version string from mod config metadata."""
    sections = cast(Dict[str, Any], cfg.get("Sections", {}))
    if sections and "ModInfo" in sections:
        meta_lines = cast(List[str], sections["ModInfo"])
        for line in meta_lines:
            if line.lower().startswith("version"):
                try:
                    _, v = line.split("=", 1)
                    return v.strip().strip('"')
                except ValueError:
                    pass
    return None


def create_profile_data(
    mod_configs: List[Dict[str, Any]],
    game_config: Dict[str, Any],
    description: str = "",
) -> ProfileManifest:
    """
    Generates the profile dictionary from the current application state.
    """
    mod_entries: List[ProfileModEntry] = []

    for cfg in mod_configs:
        # Use cast(ModConfig, ...) to satisfy the type checker for the private helper
        name = cfg.get("Name") or _get_mod_name_from_config(cast(ModConfig, cfg))
        enabled = bool(cfg.get("Enabled", True))

        # Extract version from metadata if available
        version = _extract_version(cfg)

        entry: ProfileModEntry = {
            "name": str(name),
            "enabled": enabled,
            "version": version,
        }
        mod_entries.append(entry)

    exe_name = game_config.get("game_executable", DEFAULTS["game_executable"])

    profile: ProfileManifest = {
        "format_version": PROFILE_FORMAT_VERSION,
        "gmos_version": "1.0.0",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "game_executable": str(exe_name),
        "description": description,
        "mods": mod_entries,
    }
    return profile


def save_profile_to_disk(data: ProfileManifest, path: str) -> None:
    """Writes the profile data to disk atomically."""
    try:
        json_str = json.dumps(data, indent=2)
        atomic_replace(path, json_str)
        logger.info("Profile saved to %s", path)
    except Exception as e:
        logger.error("Failed to save profile to %s: %s", path, e)
        raise e


def load_profile_from_disk(path: str) -> ProfileManifest:
    """
    Reads and validates a profile from disk.
    Raises ValueError on validation failure.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ValueError(f"Failed to read file: {e}") from e

    # Basic Schema Validation
    if not isinstance(data, dict):
        raise ValueError("Invalid profile format: Root must be a dictionary.")

    if "mods" not in data or not isinstance(data["mods"], list):
        raise ValueError("Invalid profile format: Missing 'mods' list.")

    return cast(ProfileManifest, data)


def apply_profile_to_configs(
    profile: ProfileManifest, current_configs: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Reorders and updates the enabled state of current_configs based on the profile.

    Logic:
    1. Mods in the profile are placed first, in the profile's order.
    2. Mods in the profile have their 'Enabled' state updated.
    3. Mods NOT in the profile are appended at the end and DISABLED (to match the profile's intent).

    Returns: (new_mod_list, warnings_list)
    """

    # Map name -> current config
    config_map: Dict[str, Dict[str, Any]] = {}
    for cfg in current_configs:
        name = cfg.get("Name") or _get_mod_name_from_config(cast(ModConfig, cfg))
        config_map[str(name)] = cfg

    new_order: List[Dict[str, Any]] = []
    processed_names: set[str] = set()
    warnings: List[str] = []

    # 1. Process profile entries
    for entry in profile["mods"]:
        name = entry["name"]
        if name in config_map:
            cfg = config_map[name]
            cfg["Enabled"] = entry["enabled"]
            new_order.append(cfg)
            processed_names.add(name)
            # Version Pinning Check
            local_ver = _extract_version(cfg)
            if entry.get("version") and local_ver != entry["version"]:
                warnings.append(
                    f"Version Mismatch: '{name}' (Profile: {entry['version']}, Local: {local_ver})"
                )
        else:
            logger.warning("Profile references missing mod: %s", name)

    # 2. Process remaining mods (not in profile)
    for name, cfg in config_map.items():
        if name not in processed_names:
            # Disable mods that aren't part of the profile to ensure exact reproduction
            cfg["Enabled"] = False
            new_order.append(cfg)
            logger.info("Disabled mod not in profile: %s", name)

    return new_order, warnings
