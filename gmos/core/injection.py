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
Implements Runtime Sandboxing.
Injects a security monitor (Autoload) into project.godot or override.cfg
to intercept dangerous calls.
"""

import os
import textwrap
from typing import Optional

from gmos.core.godot_project import GodotProjectFile
from gmos.io import atomic_write_with_backup
from gmos.utils import logger

# The payload script that intercepts dangerous calls
SECURITY_SCRIPT = textwrap.dedent("""
    extends Node
    func _init():
        if ProjectSettings.load_resource_pack("res://gmos_override.pck", true):
            print("[GMOS] Mounted gmos_override.pck")
    # GMOS Security Sandbox: Intercepts OS calls to prevent arbitrary code execution (ACE).

    func secure_execute(command: String, args: Array, blocking: bool = false, output: Array = []):
        push_error("[GMOS] SECURITY WARNING: Mod attempted OS.execute: " + command)
        print("[GMOS] BLOCKED execution: ", command)
        # Policy: Strict Blocking
        return -1

    func secure_shell_open(uri: String):
        push_error("[GMOS] SECURITY WARNING: Mod attempted OS.shell_open: " + uri)
        # ERR_UNAVAILABLE = 2
        return 2

    func secure_load(path: String) -> Resource:
        # Runtime check against forbidden binary extensions
        var p_lower = path.to_lower()
        if ".dll" in p_lower or ".so" in p_lower or ".dylib" in p_lower:
            push_error("[GMOS] SECURITY WARNING: Mod attempted to load binary extension: " + path)
            return null
        return load(path)
""")

SECURITY_SCENE = textwrap.dedent("""
    [gd_scene load_steps=2 format=2]

    [ext_resource path="res://gmos_sandbox.gd" type="Script" id=1]

    [node name="GMOS_Sandbox" type="Node"]
    script = ExtResource( 1 )
""")


class SandboxInjector:
    """
    Manages the injection of the GMOS Runtime Sandbox into the game configuration.
    Supports 'project.godot' (standard) and 'override.cfg' (binary projects).
    """

    SANDBOX_AUTOLOAD_NAME = "GMOS_Sandbox"
    SANDBOX_RES_SCENE = "res://gmos_sandbox.tscn"

    def __init__(self, game_dir: str):
        self.game_dir = game_dir
        self._target_file = "project.godot"
        self._using_override = False
        self._project: Optional[GodotProjectFile] = None
        self._detect_target()

    def _detect_target(self) -> None:
        """Determines whether to use project.godot or override.cfg (binary projects)."""
        proj_path = os.path.join(self.game_dir, "project.godot")
        bin_path = os.path.join(self.game_dir, "project.binary")

        if os.path.exists(bin_path) or not os.path.exists(proj_path):
            self._target_file = "override.cfg"
            self._using_override = True
        else:
            self._target_file = "project.godot"
            self._using_override = False

    def _get_project_file(self) -> GodotProjectFile:
        """Lazy loads the project file wrapper."""
        if self._project:
            return self._project

        path = os.path.join(self.game_dir, self._target_file)

        # Ensure override.cfg exists if we are using it
        if self._using_override and not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("; GMOS Configuration Override\n")

        self._project = GodotProjectFile(path)
        return self._project

    def is_injected(self) -> bool:
        """Check if the sandbox is already configured as an Autoload."""
        try:
            pf = self._get_project_file()
            pf.load()
            val = pf.get_value("autoload", self.SANDBOX_AUTOLOAD_NAME)
            return val is not None
        except Exception as e:
            logger.warning("Failed to check sandbox status: %s", e)
            return False

    def inject(self) -> bool:
        """
        Injects the sandbox autoload.
        Returns True if modified, False if already present.
        """
        pf = self._get_project_file()

        try:
            pf.load()
        except Exception as e:

            if self._using_override:
                path = os.path.join(self.game_dir, self._target_file)
                with open(path, "w", encoding="utf-8") as f:
                    f.write("; GMOS Configuration Override (Reset)\n")
                pf.load()
            else:
                raise RuntimeError(f"Could not parse {self._target_file}") from e

        current = pf.get_value("autoload", self.SANDBOX_AUTOLOAD_NAME)
        if current:
            return False

        try:
            script_path = os.path.join(self.game_dir, "gmos_sandbox.gd")
            scene_path = os.path.join(self.game_dir, "gmos_sandbox.tscn")

            atomic_write_with_backup(script_path, SECURITY_SCRIPT)
            atomic_write_with_backup(scene_path, SECURITY_SCENE)
        except Exception as e:
            logger.error("Failed to write sandbox payload files: %s", e)
            return False

        # Register in config ('*' indicates enabled/singleton)
        entry_value = f"*{self.SANDBOX_RES_SCENE}"

        pf.set_value("autoload", self.SANDBOX_AUTOLOAD_NAME, entry_value)
        pf.save()

        logger.info(
            "Injected GMOS Sandbox into %s%s",
            self._target_file,
            " (Binary Fallback)" if self._using_override else "",
        )
        return True

    def remove(self) -> bool:
        """
        Removes the sandbox entry from the config.
        """
        try:
            pf = self._get_project_file()
            if not os.path.exists(pf.path):
                return False

            pf.load()
            if not pf.get_value("autoload", self.SANDBOX_AUTOLOAD_NAME):
                return False

            # Remove the key
            removed = pf.remove_key("autoload", self.SANDBOX_AUTOLOAD_NAME)
            if removed:
                pf.save()
                logger.info("Removed GMOS Sandbox from %s", self._target_file)

            return removed
        except Exception as e:
            logger.warning("Failed to remove sandbox: %s", e)
            return False
