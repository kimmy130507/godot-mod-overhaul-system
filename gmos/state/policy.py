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
Policy Module: Handles persistent user decisions (Load Order, Enabled State).
"""

import json
import os
from typing import Any, Dict, List, Optional, Set, TypedDict, cast

try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired

from gmos.io import atomic_replace
from gmos.utils import logger

POLICY_FILENAME = "user_load_order.json"


class ModPolicyEntry(TypedDict):
    name: str
    enabled: bool


class PolicyManifest(TypedDict):
    version: int
    load_order: List[ModPolicyEntry]
    file_rules: NotRequired[Dict[str, str]]  # target_res -> winner_mod_name


def _get_policy_path(game_dir: str) -> str:
    """Returns path to user_load_order.json in the instance data directory."""
    return os.path.join(game_dir, "gmos_data", POLICY_FILENAME)


def save_policy(
    mod_configs: List[Dict[str, Any]],
    file_rules: Optional[Dict[str, str]] = None,
    game_dir: Optional[str] = None,
) -> None:
    """Persist the current list of mods and file rules to the game instance."""
    if not game_dir:
        logger.error("save_policy called without game_dir")
        return

    path = _get_policy_path(game_dir)

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
        name = str(cfg.get("Name", "Unknown"))
        enabled = bool(cfg.get("Enabled", True))
        entries.append({"name": name, "enabled": enabled})

    manifest: PolicyManifest = {
        "version": 1,
        "load_order": entries,
        "file_rules": existing_rules,
    }

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_replace(path, json.dumps(manifest, indent=2))
        logger.info("Saved policy to %s", path)
    except Exception as e:
        logger.error("Failed to save policy: %s", e)


save_load_order = save_policy


def load_and_apply_policy(
    mod_configs: List[Dict[str, Any]], game_dir: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Reorders and updates 'Enabled' status based on the instance policy."""
    if not game_dir:
        return mod_configs

    path = _get_policy_path(game_dir)
    if not os.path.exists(path):
        return mod_configs

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            items = data.get("load_order", [])
    except Exception as e:
        logger.debug("Failed to load policy file %s: %s", path, e)
        return mod_configs

    config_map: Dict[str, Dict[str, Any]] = {
        str(cfg.get("Name", "Unknown")): cfg for cfg in mod_configs
    }

    ordered_configs: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()

    for entry in items:
        name = entry.get("name")
        if name in config_map:
            cfg = config_map[name]
            cfg["Enabled"] = entry.get("enabled", True)
            ordered_configs.append(cfg)
            seen_names.add(name)

    for cfg in mod_configs:
        name = str(cfg.get("Name", "Unknown"))
        if name not in seen_names:
            cfg["Enabled"] = cfg.get("Enabled", True)
            ordered_configs.append(cfg)

    return ordered_configs


def load_file_rules(game_dir: Optional[str] = None) -> Dict[str, str]:
    """Returns the saved file conflict rules (target -> winner)."""
    if not game_dir:
        return {}

    path = _get_policy_path(game_dir)
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return cast(Dict[str, str], data.get("file_rules", {}))
    except Exception:
        return {}


def load_load_order(game_dir: str) -> List[ModPolicyEntry]:
    """Raw loader for the policy file."""
    path = _get_policy_path(game_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            raw = data.get("load_order", [])
            return [cast(ModPolicyEntry, x) for x in raw]
    except Exception:
        return []
