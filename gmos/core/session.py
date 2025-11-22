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
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, cast

from gmos.core import patcher, security
from gmos.core.injection import SandboxInjector
from gmos.core.patcher import (
    apply_dependency_resolution,
    parse_mod_config,
    validate_mod_config,
)
from gmos.state import policy
from gmos.utils import _get_mod_name_from_config  # type: ignore [reportPrivateUsage]
from gmos.utils import ModConfig, logger, safe_norm


@dataclass
class RuntimeMod:
    """
    Represents a loaded mod with its runtime state (validity, errors, security).
    Decouples UI representation from configuration data.
    """

    config: ModConfig
    is_enabled: bool = True
    is_valid: bool = True
    errors: List[str] = field(default_factory=lambda: [])
    security_risks: List[security.SecurityRisk] = field(default_factory=lambda: [])

    @property
    def name(self) -> str:
        return self.config.get("Name") or _get_mod_name_from_config(self.config)

    @property
    def path(self) -> str:
        return self.config.get("Path", "")


class GmosSession:
    """
    The 'Brain' of the application.
    Encapsulates state and business logic, independent of the GUI.
    """

    def __init__(self, game_dir: str, mods_dir: str):
        self.game_dir = safe_norm(game_dir)
        self.mods_dir = safe_norm(mods_dir)
        self.mods: List[RuntimeMod] = []

    def refresh_mods(self) -> Generator[str, None, None]:
        """
        Scans disk, parses configs, applies policy, and resolves dependencies.
        Yields status strings for UI feedback.
        """
        yield "Scanning mods directory..."

        new_mods: List[RuntimeMod] = []

        if not os.path.isdir(self.mods_dir):
            logger.warning("Mods directory not found: %s", self.mods_dir)
            self.mods = []
            return

        # 1. Discovery & Parsing
        with os.scandir(self.mods_dir) as it:
            for entry in it:
                if entry.is_dir():
                    mod_path = entry.path
                    try:
                        # parse_mod_config returns Optional[ModConfig]
                        raw_cfg = parse_mod_config(mod_path)
                        if raw_cfg:
                            # Initialize RuntimeMod
                            rmod = RuntimeMod(config=raw_cfg)
                            # Ensure Path is set in config (legacy compat)
                            rmod.config["Path"] = mod_path

                            # 2. Validation
                            valid, err_obj = validate_mod_config(
                                cast(Dict[str, Any], raw_cfg)
                            )
                            rmod.is_valid = bool(valid)

                            if not valid:
                                if isinstance(err_obj, list):
                                    err_list = cast(List[object], err_obj)
                                    rmod.errors.extend([str(e) for e in err_list])
                                else:
                                    rmod.errors.append(str(err_obj))
                            else:
                                # 3. Security Scan (only if valid structure)
                                rmod.security_risks = security.scan_mod(mod_path)

                            new_mods.append(rmod)
                    except Exception as e:
                        logger.error("Failed to load mod at %s: %s", mod_path, e)
                        # We could add a "broken" mod entry here if desired

        yield f"Found {len(new_mods)} mods. Applying policy..."

        # 4. Apply Load Order Policy (Sort & Enablement)
        # Convert to list of dicts for the existing policy API
        # TODO: Refactor policy.py to work with RuntimeMod objects directly in future
        temp_configs = [m.config for m in new_mods]

        # Load enabled state from policy (this modifies temp_configs in-place or returns new list)
        # We need to map the results back to our RuntimeMod objects

        sorted_configs = policy.load_and_apply_policy(
            cast(List[Dict[str, Any]], temp_configs)
        )

        # Re-order new_mods to match sorted_configs and update is_enabled
        mod_map = {m.config["Path"]: m for m in new_mods if "Path" in m.config}
        ordered_mods: List[RuntimeMod] = []

        for cfg in sorted_configs:
            path = cfg.get("Path")
            if path and path in mod_map:
                rmod = mod_map[path]
                # Sync enabled state
                rmod.is_enabled = bool(cfg.get("Enabled", True))
                # Sync config (in case policy modified it)
                rmod.config = cast(ModConfig, cfg)
                ordered_mods.append(rmod)

        yield "Resolving dependencies..."

        # 5. Dependency Resolution
        # The existing resolver expects Sequence[ModConfig]
        resolve_input = [m.config for m in ordered_mods]
        final_order_configs, dep_errors = apply_dependency_resolution(resolve_input)

        # Re-map again to final order
        final_mods: List[RuntimeMod] = []
        for f_cfg in final_order_configs:
            path = f_cfg.get("Path")
            if path and path in mod_map:
                rmod = mod_map[path]
                final_mods.append(rmod)

                # Attach dependency errors
                name = rmod.name
                if name in dep_errors:
                    rmod.is_valid = False
                    rmod.errors.extend(dep_errors[name])

        self.mods = final_mods
        yield f"Refresh complete. {len(self.mods)} mods loaded."

    def apply_changes(
        self, conflict_delegate: Any = None
    ) -> Generator[str, None, None]:
        """
        Calculates patch plan and executes the patcher.
        """
        yield "Calculating patch plan..."
        # Collect all enabled configs
        enabled_configs = [m.config for m in self.mods if m.is_enabled]

        # Generate plan
        _ = patcher.analyze_mods_for_conflicts(enabled_configs)
        # TODO: We might want to yield conflict warnings here before proceeding?

        # For now, regenerate plan strictly for execution (analyze_mods_for_conflicts is for UI info)
        final_plan: List[Any] = []
        for mod in self.mods:
            if mod.is_enabled:
                try:
                    plan = patcher.generate_patch_plan(mod.path, mod.config)
                    final_plan.extend(plan)
                except Exception as e:
                    logger.error(f"Plan gen failed for {mod.name}: {e}")

        yield f"Executing patch plan ({len(final_plan)} operations)..."

        # Define a progress callback that yields messages?
        # Since run_patcher returns a log list, we can't yield real-time lines easily unless we wrap the callback.
        # We will use a simple wrapper.

        log_lines = patcher.run_patcher(
            self.game_dir,
            final_plan,
            force_pck=False,  # TODO: Expose this setting
            conflict_delegate=conflict_delegate,
        )

        for line in log_lines:
            yield line

        yield "Patching complete."

    def check_sandbox_status(self) -> bool:
        """Returns True if the sandbox is currently active in project.godot."""
        injector = SandboxInjector(self.game_dir)
        return injector.is_injected()

    def toggle_sandbox(self) -> bool:
        """
        Toggles the sandbox state. Returns the new state (True=Injected).
        """
        injector = SandboxInjector(self.game_dir)
        if injector.is_injected():
            injector.remove()
            return False
        else:
            injector.inject()
            return True
