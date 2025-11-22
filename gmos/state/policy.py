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
Policy Module: Handles persistent user decisions that override default behaviors.
Primarily manages the 'Load Order' and 'Enabled State' of mods.
"""

import json
import os
from typing import Any, Dict, List, Optional, TypedDict, cast

try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired

from gmos.io import atomic_replace
from gmos.state.config import get_config_path
from gmos.utils import logger

# We store policies next to config.json
POLICY_FILENAME = "user_load_order.json"


class ModPolicyEntry(TypedDict):
    name: str
    enabled: bool


class PolicyManifest(TypedDict):
    version: int
    load_order: List[ModPolicyEntry]
    file_rules: NotRequired[Dict[str, str]]  # target_res -> winner_mod_name


def _get_policy_path() -> str:
    """Returns path to user_load_order.json in the same dir as config.json"""
    cfg_path = get_config_path()
    return os.path.join(os.path.dirname(cfg_path), POLICY_FILENAME)


def save_policy(
    mod_configs: List[Dict[str, Any]], file_rules: Optional[Dict[str, str]] = None
) -> None:
    """
    Persist the current list of mods and file rules.
    If file_rules is None, preserves existing rules from disk.
    """
    path = _get_policy_path()
    # Load existing to preserve file_rules if not updating them
    existing_rules: Dict[str, str] = {}
    if file_rules is None and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing_rules = data.get("file_rules", {})
        except Exception:
            pass
    else:
        existing_rules = file_rules or {}
    entries: List[ModPolicyEntry] = []

    for cfg in mod_configs:
        # Extract name safely
        name = str(cfg.get("Name", "Unknown"))
        enabled = bool(cfg.get("Enabled", True))
        entries.append({"name": name, "enabled": enabled})

    manifest: PolicyManifest = {
        "version": 1,
        "load_order": entries,
        "file_rules": existing_rules,
    }

    try:
        atomic_replace(path, json.dumps(manifest, indent=2))
        logger.info("Saved policy to %s", path)
    except Exception as e:
        logger.error("Failed to save policy: %s", e)


save_load_order = save_policy


def load_and_apply_policy(mod_configs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Reorders and updates 'Enabled' status of the provided mod_configs.
    """
    path = _get_policy_path()
    if not os.path.exists(path):
        return mod_configs

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Handle both list (legacy/simple) and dict (manifest) formats if needed
            # But strictly we defined PolicyManifest as dict
            items = data.get("load_order", [])
    except Exception as e:
        logger.warning("Failed to load policy file %s: %s", path, e)
        return mod_configs

    # Index current configs by name for O(1) lookup
    config_map: Dict[str, Dict[str, Any]] = {}
    for cfg in mod_configs:
        name = str(cfg.get("Name", "Unknown"))
        config_map[name] = cfg

    ordered_configs: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    # 1. Reconstruct order from policy
    for entry in items:
        name = entry.get("name")
        if name in config_map:
            cfg = config_map[name]
            # Apply persistent enabled state
            cfg["Enabled"] = entry.get("enabled", True)
            ordered_configs.append(cfg)
            seen_names.add(name)

    # 2. Append any new/unknown mods that weren't in the policy
    # (e.g. user just downloaded a new mod)
    for cfg in mod_configs:
        name = str(cfg.get("Name", "Unknown"))
        if name not in seen_names:
            # Default to enabled for new mods
            cfg["Enabled"] = cfg.get("Enabled", True)
            ordered_configs.append(cfg)
            logger.info("Found new mod not in policy: %s", name)

    return ordered_configs


def load_file_rules() -> Dict[str, str]:
    """Returns the saved file conflict rules (target -> winner)."""
    path = _get_policy_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return cast(Dict[str, str], data.get("file_rules", {}))
    except Exception:
        return {}
