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
Handles reading and writing of project.godot (ConfigParser-like syntax).
Essential for Runtime Sandboxing and SDK features.
"""
import os
from typing import Dict, List, Optional

from gmos.io import atomic_write_with_backup
from gmos.utils import logger


class GodotProjectFile:
    def __init__(self, path: str):
        self.path = path
        self.lines: List[str] = []
        self._sections: Dict[str, int] = {}  # Section Name -> Line Index
        self.loaded = False

    def load(self) -> None:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Project file not found: {self.path}")

        with open(self.path, "r", encoding="utf-8") as f:
            self.lines = [line.rstrip("\n") for line in f.readlines()]

        self._rebuild_index()
        self.loaded = True

    def _rebuild_index(self) -> None:
        """Map [headers] to their line numbers."""
        self._sections.clear()
        for i, line in enumerate(self.lines):
            sline = line.strip()
            if sline.startswith("[") and sline.endswith("]"):
                # [application]
                # [autoload]
                sec = sline[1:-1]
                self._sections[sec] = i

    def get_value(self, section: str, key: str) -> Optional[str]:
        """
        Retrieve a raw value string for a key in a section.
        Returns None if not found.
        """
        start_idx = self._sections.get(section)
        if start_idx is None:
            return None

        # Scan lines after section header until next section or EOF
        for i in range(start_idx + 1, len(self.lines)):
            line = self.lines[i].strip()
            if line.startswith("["):  # Next section start
                break
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"')  # Strip quotes for convenience
        return None

    def set_value(self, section: str, key: str, value: str) -> None:
        """
        Set a value. Creates the section if missing.
        Preserves surrounding formatting/comments.
        """
        if not self.loaded:
            self.load()

        target_val = f'"{value}"' if " " in value or "/" in value else value

        # 1. Try to find and replace existing key
        start_idx = self._sections.get(section)
        if start_idx is not None:
            for i in range(start_idx + 1, len(self.lines)):
                line = self.lines[i].strip()
                if line.startswith("["):
                    # Key missing in section, insert before next section
                    self.lines.insert(i, f"{key}={target_val}")
                    self._rebuild_index()
                    return
                if "=" in line:
                    k, _ = line.split("=", 1)
                    if k.strip() == key:
                        # Found it, replace line
                        self.lines[i] = f"{key}={target_val}"
                        return

            # End of file reached, append to section
            self.lines.append(f"{key}={target_val}")
            return

        # 2. Section missing, append new section
        if self.lines and self.lines[-1] != "":
            self.lines.append("")
        self.lines.append(f"[{section}]")
        self.lines.append(f"{key}={target_val}")
        self._rebuild_index()

    def remove_key(self, section: str, key: str) -> bool:
        """
        Remove a key from a section. Returns True if removed.
        """
        if not self.loaded:
            self.load()

        start_idx = self._sections.get(section)
        if start_idx is None:
            return False

        for i in range(start_idx + 1, len(self.lines)):
            line = self.lines[i].strip()
            if line.startswith("["):
                return False
            if "=" in line and line.split("=", 1)[0].strip() == key:
                del self.lines[i]
                return True
        return False

    def save(self) -> None:
        """Atomically save changes back to disk."""
        content = "\n".join(self.lines) + "\n"
        atomic_write_with_backup(self.path, content)
        # Mark as re-loaded to force fresh read next time
        self.loaded = False
        logger.info("Saved Godot project config: %s", self.path)

    def get_entry_point(self) -> str:
        """Helper to get the main scene (res://...)."""
        # Godot 3/4 use application/run/main_scene usually,
        # but it appears as run/main_scene under [application] in file
        return self.get_value("application", "run/main_scene") or ""

    def set_entry_point(self, res_path: str) -> None:
        """Helper to set the main scene."""
        self.set_value("application", "run/main_scene", res_path)
