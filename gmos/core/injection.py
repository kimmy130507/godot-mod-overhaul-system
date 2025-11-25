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
Implements Runtime Sandboxing.
Injects a security monitor (Autoload) into project.godot to intercept dangerous calls.
"""
import os
import textwrap

from gmos.core.godot_project import GodotProjectFile
from gmos.io import atomic_write_with_backup
from gmos.utils import logger

# The payload script that intercepts dangerous calls
SECURITY_SCRIPT = textwrap.dedent(
    """
    extends Node

    # GMOS Security Sandbox
    # Intercepts OS calls to prevent arbitrary code execution (ACE).

    func secure_execute(command: String, args: Array, blocking: bool = false, output: Array = []):
        print("[GMOS] SECURITY WARNING: Mod attempted OS.execute: ", command)
        # DEFAULT POLICY: BLOCK EVERYTHING
        # A future version could check a whitelist.json here.
        print("[GMOS] BLOCKED execution of: ", command)
        return -1

    func secure_shell_open(uri: String):
        print("[GMOS] SECURITY WARNING: Mod attempted OS.shell_open: ", uri)
        print("[GMOS] BLOCKED shell open: ", uri)
        # Return error code (ERR_UNAVAILABLE = 2)
        return 2
"""
)

# The scene file (.tscn) that Godot loads as a singleton
SECURITY_SCENE = textwrap.dedent(
    """
    [gd_scene load_steps=2 format=2]

    [ext_resource path="res://gmos_sandbox.gd" type="Script" id=1]

    [node name="GMOS_Sandbox" type="Node"]
    script = ExtResource( 1 )
"""
)


class SandboxInjector:
    """
    Manages the injection of the GMOS Runtime Sandbox into the game configuration.
    """

    SANDBOX_AUTOLOAD_NAME = "GMOS_Sandbox"
    SANDBOX_RES_SCENE = "res://gmos_sandbox.tscn"
    SANDBOX_RES_SCRIPT = "res://gmos_sandbox.gd"

    def __init__(self, game_dir: str):
        self.game_dir = game_dir
        self.project_path = os.path.join(game_dir, "project.godot")
        self.project = GodotProjectFile(self.project_path)

    def is_injected(self) -> bool:
        """Check if the sandbox is already configured as an Autoload."""
        if not os.path.exists(self.project_path):
            # If project.godot doesn't exist but project.binary does, we assume NOT safe to inject
            if os.path.exists(os.path.join(self.game_dir, "project.binary")):
                return False
            return False

        try:
            self.project.load()
            # Autoloads are typically stored in the [autoload] section
            val = self.project.get_value("autoload", self.SANDBOX_AUTOLOAD_NAME)
            return val is not None
        except Exception as e:
            logger.warning("Failed to check sandbox status: %s", e)
            return False

    def inject(self) -> bool:
        """
        Injects the sandbox autoload into project.godot.
        Returns True if modified, False if already present.
        """
        # Safety Check: Binary Projects
        if os.path.exists(os.path.join(self.game_dir, "project.binary")):
            raise RuntimeError(
                "Cannot inject sandbox into a binary project (project.binary detected).\nPlease decompile the project first using GDRE Tools."
            )
        if not os.path.exists(self.project_path):
            raise RuntimeError(
                f"Failed to inject sandbox. The file 'project.godot' was not found in: {self.game_dir}\n"
                "Please verify that the path is correct and points to a valid Godot project root."
            )

        self.project.load()

        # Check if already present to avoid redundant writes
        current = self.project.get_value("autoload", self.SANDBOX_AUTOLOAD_NAME)
        if current:
            logger.info("Sandbox already injected.")
            return False
        # 1. Write the Payload Files
        try:
            script_path = os.path.join(self.game_dir, "gmos_sandbox.gd")
            scene_path = os.path.join(self.game_dir, "gmos_sandbox.tscn")

            atomic_write_with_backup(script_path, SECURITY_SCRIPT)
            atomic_write_with_backup(scene_path, SECURITY_SCENE)
        except Exception as e:
            logger.error("Failed to write sandbox payload files: %s", e)
            return False

        # 2. Register in project.godot
        # Format: "path/to/scene.tscn" (the * indicates enabled/singleton in older versions,
        # but in project.godot it's usually just "path" or "*path")
        entry_value = f"*{self.SANDBOX_RES_SCENE}"

        self.project.set_value("autoload", self.SANDBOX_AUTOLOAD_NAME, entry_value)
        self.project.save()

        logger.info("Successfully injected GMOS Sandbox into %s", self.project_path)
        return True

    def remove(self) -> bool:
        """
        Removes the sandbox entry from project.godot (Uninstall).
        Currently, GodotProjectFile doesn't support deletion, so we manually
        disable it or implement delete logic.
        """
        if not os.path.exists(self.project_path):
            return False

        self.project.load()
        if not self.project.get_value("autoload", self.SANDBOX_AUTOLOAD_NAME):
            return False

        # Remove the key
        removed = self.project.remove_key("autoload", self.SANDBOX_AUTOLOAD_NAME)
        if removed:
            self.project.save()
            logger.info("Removed GMOS Sandbox from project.godot")

        # Optional: We could delete the .gd/.tscn files here,
        # but leaving them is harmless and safer for data integrity.
        return removed
