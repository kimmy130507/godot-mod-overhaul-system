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
Core: Modder SDK / Godot Bridge
Facilitates "Decompile -> Edit -> Diff -> Patch" workflow.
"""
import difflib
import os
import re
from typing import Any, Dict, List, Optional, cast

from gmos.io import atomic_write_with_backup, pck
from gmos.io.pck import read_pck_header
from gmos.utils import _safe_spawn  # type: ignore [reportPrivateUsage]
from gmos.utils import logger


class GodotBridge:
    """
    Orchestrates the modding workflow:
    1. Initialize Workspace (Extract PCK)
    2. Launch Editor (User makes changes)
    3. Compile Mod (Diff Workspace vs PCK -> mod.mos)
    """

    def __init__(self, game_dir: str, workspace_dir: str):
        self.game_dir = os.path.abspath(game_dir)
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.pck_path = pck.get_main_pck_path(self.game_dir)
        self.gdre_path: Optional[str] = None

    def init_workspace(self) -> int:
        """
        Extracts the game's main PCK into the workspace directory.
        Returns the number of files extracted.
        """
        if not self.pck_path:
            raise FileNotFoundError("No PCK file found in game directory.")

        logger.info("Initializing workspace at %s", self.workspace_dir)
        # Use the bulk extractor we added to gmos.io.pck
        return pck.extract_pck(self.pck_path, self.workspace_dir)

    def set_gdre_tools_path(self, path: str) -> None:
        """Configures the path to the GDRE Tools executable."""
        if not os.path.exists(path):
            raise FileNotFoundError("GDRE Tools executable not found")
        self.gdre_path = path

    def recover_project(self) -> List[str]:
        """
        Executes GDRE Tools to recover the project from the PCK (decompiling assets).
        Returns a list of log lines from the process.
        """
        if not self.gdre_path:
            raise RuntimeError("GDRE Tools path not configured")
        if not self.pck_path:
            raise FileNotFoundError("No PCK file found to recover.")

        # Construct command for headless recovery
        cmd = [
            self.gdre_path,
            "--headless",
            "--recover",
            self.pck_path,
            "--output-dir",
            self.workspace_dir,
        ]

        # Cast result to Dict because capture_output=True guarantees a dict return in _safe_spawn
        result = cast(Dict[str, Any], _safe_spawn(cmd, capture_output=True))
        if result["returncode"] != 0:
            raise RuntimeError(f"Recovery failed: {result['stderr']}")

        return str(result["stdout"]).splitlines()

    def launch_editor(self, editor_exe: str) -> None:
        """
        Launches the Godot Editor pointing to the workspace project.
        This is a blocking call (waits for editor to close) if we want to
        detect 'session end', but usually non-blocking is better for UI.
        """
        if not os.path.exists(editor_exe):
            raise FileNotFoundError(f"Godot Editor not found: {editor_exe}")

        project_file = os.path.join(self.workspace_dir, "project.godot")
        if not os.path.exists(project_file):
            logger.warning(
                "No project.godot found in workspace. Editor may prompt to create one."
            )

        logger.info("Launching Godot Editor...")
        # -e/--editor opens the editor. --path sets the project path.
        cmd = [editor_exe, "--editor", "--path", self.workspace_dir]
        _safe_spawn(cmd, cwd=self.workspace_dir)

    def scan_for_changes(self) -> List[str]:
        """
        Compare workspace files against the original PCK to find modified files.
        Returns a list of relative paths (e.g. 'res://scenes/player.tscn').
        """
        if not self.pck_path:
            return []

        # Load vanilla index
        header = read_pck_header(self.pck_path)
        vanilla_map = {entry.path: entry for entry in header.files}

        changes: List[str] = []

        # Walk workspace
        for root, _, files in os.walk(self.workspace_dir):
            for file in files:
                if file.startswith("."):
                    continue  # skip hidden/tmp

                abs_path = os.path.join(root, file)
                rel_os_path = os.path.relpath(abs_path, self.workspace_dir)
                # Convert OS path to Godot res:// path
                res_path = "res://" + rel_os_path.replace(os.sep, "/")

                # 1. Check if new file
                if res_path not in vanilla_map:
                    changes.append(res_path)
                    continue

                # 2. Check if modified (Size/MD5 comparison)
                # Note: PCK stores MD5. We can compute local MD5 to be sure.
                # For speed, we'll check size first.
                entry = vanilla_map[res_path]
                stat = os.stat(abs_path)

                if stat.st_size != entry.size:
                    changes.append(res_path)
                    continue

                # Deep check: MD5
                # (Optimization: Only do this if we really need to confirm)
                import hashlib

                with open(abs_path, "rb") as f:
                    local_md5 = hashlib.md5(f.read(), usedforsecurity=False).digest()

                if local_md5 != entry.md5:
                    changes.append(res_path)

        return changes

    def _try_detect_variable_change(
        self, res_path: str, abs_path: str
    ) -> Optional[str]:
        """
        Analyzes a modified .gd file to see if it's a simple variable value change.
        Returns a [VariablePatch] line if successful, or None.
        """
        if not self.pck_path:
            return None

        try:
            # Read vanilla content
            vanilla_bytes = pck.get_file_content(self.pck_path, res_path)
            if vanilla_bytes is None:
                return None
            vanilla_lines = vanilla_bytes.decode("utf-8", errors="ignore").splitlines()

            # Read workspace content
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                workspace_lines = f.read().splitlines()

            # Generate diff
            diff = list(difflib.unified_diff(vanilla_lines, workspace_lines, n=0))

            # Heuristic: A clean variable patch usually looks like:
            # - var x = old
            # + var x = new
            if not diff:
                return None

            # Extract change lines (excluding diff headers)
            minus = [
                line
                for line in diff
                if line.startswith("-") and not line.startswith("---")
            ]
            plus = [
                line
                for line in diff
                if line.startswith("+") and not line.startswith("+++")
            ]

            # Only proceed if it looks like a single line replacement
            if len(minus) == 1 and len(plus) == 1:
                # Regex to capture variable name: var/const NAME = value
                var_pat = re.compile(r"^\s*(?:var|const)\s+(\w+)\s*[:=]")

                m_old = var_pat.search(minus[0][1:])  # Skip '-' prefix
                m_new = var_pat.search(plus[0][1:])  # Skip '+' prefix

                # If both are variable assignments to the SAME variable
                if m_old and m_new and m_old.group(1) == m_new.group(1):
                    var_name = m_new.group(1)
                    rel_os = res_path.replace("res://", "").replace("/", os.sep)
                    # Generate manifest entry: Target = Source ; options
                    return f"{res_path}::{var_name} = {rel_os.replace(os.sep, '/')}::{var_name} ; mode=replace"

            return None
        except Exception:
            return None

    def generate_mod_patch(self, output_dir: str, mod_name: str, author: str) -> str:
        """
        Scans for changes and writes a 'mod.mos' manifest and copies changed files
        to the output directory, creating a ready-to-zip mod package.
        Returns path to the created mod.mos.
        """
        changed_res_paths = self.scan_for_changes()
        if not changed_res_paths:
            logger.info("No changes detected in workspace.")
            return ""

        os.makedirs(output_dir, exist_ok=True)

        # Prepare Manifest Content
        lines: List[str] = []
        lines.append(f"# Generated by GMOS SDK for {mod_name}")
        lines.append("[Metadata]")
        lines.append(f"Name = {mod_name}")
        lines.append(f"Author = {author}")
        lines.append("Version = 1.0")
        lines.append("")

        file_replaces: List[str] = []
        variable_patches: List[str] = []

        for res in changed_res_paths:
            # Determine source path in workspace
            rel_os = res.replace("res://", "").replace("/", os.sep)
            src_abs = os.path.join(self.workspace_dir, rel_os)

            # Logic: If it's a script, try to make it a variable patch
            is_handled = False
            if res.endswith(".gd"):
                # Attempt smart diff
                # Implementation note: Currently basic, can be expanded
                var_patch = self._try_detect_variable_change(res, src_abs)
                if var_patch:
                    variable_patches.append(var_patch)
                    is_handled = True

            if is_handled:
                continue

            # Default: File Replace
            # Determine dest path in output mod folder
            # We'll flatten structure or keep it? Keeping it is safer.
            dst_abs = os.path.join(output_dir, rel_os)

            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)

            # Copy file
            from gmos.io import atomic_write_copy

            atomic_write_copy(src_abs, dst_abs)

            # Add to manifest
            # Syntax: res://target = local/path
            # We use the relative path inside the mod folder
            file_replaces.append(f"{res} = {rel_os.replace(os.sep, '/')}")

        if file_replaces:
            lines.append("[FileReplace]")
            lines.extend(file_replaces)
            lines.append("")

        if variable_patches:
            lines.append("[VariablePatch]")
            lines.extend(variable_patches)
            lines.append("")

        manifest_path = os.path.join(output_dir, "mod.mos")
        atomic_write_with_backup(manifest_path, "\n".join(lines))

        logger.info(
            "Generated mod package at %s with %d files.",
            output_dir,
            len(changed_res_paths),
        )
        return manifest_path
