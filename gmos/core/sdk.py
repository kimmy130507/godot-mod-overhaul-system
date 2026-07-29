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
Core: Modder SDK / Godot Bridge
Facilitates the "Decompile -> Edit -> Diff -> Patch" workflow.
Optimized to use persistent PCKReader contexts.
"""

import contextlib
import difflib
import filecmp
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, cast

try:
    import bsdiff4  # type: ignore[reportMissingTypeStubs, unused-ignore]

    _bsdiff_available = True
except ImportError:
    bsdiff4 = cast(Any, None)
    _bsdiff_available = False

from gmos.core.patcher import get_function_block, resolve_res_path
from gmos.core.tools import ToolManager
from gmos.io import atomic_write_bytes, atomic_write_with_backup, pck
from gmos.io.pck import PCKReader
from gmos.utils import logger, safe_spawn


@dataclass
class PatchDraft:
    """In-memory representation of a mod patch instructions and VFS snippets."""

    file_replaces: List[str] = field(default_factory=lambda: [])
    variable_patches: List[str] = field(default_factory=lambda: [])
    function_patches: List[str] = field(default_factory=lambda: [])
    smart_patches: List[str] = field(default_factory=lambda: [])
    binary_patches: List[str] = field(default_factory=lambda: [])
    vfs: Dict[str, bytes] = field(default_factory=lambda: {})
    changed_res: Dict[str, str] = field(default_factory=lambda: {})


class GodotBridge:
    """
    Orchestrates the modding workflow:
    1. Initialize Workspace (Extract PCK)
    2. Launch Editor (User makes changes)
    3. Compile Mod (Diff Workspace vs PCK -> mod.mos)
    """

    def __init__(
        self,
        game_dir: str,
        workspace_dir: str,
        tool_manager: Optional[ToolManager] = None,
        vanilla_cache_dir: Optional[str] = None,
    ):
        self.game_dir = os.path.abspath(game_dir)
        self.workspace_dir = os.path.abspath(workspace_dir)
        self.vanilla_cache_dir = (
            os.path.abspath(vanilla_cache_dir) if vanilla_cache_dir else None
        )
        self.pck_path = pck.get_main_pck_path(self.game_dir)
        self.gdre_path: Optional[str] = None
        if tool_manager and tool_manager.is_installed("gdre_tools"):
            self.gdre_path = tool_manager.get_tool_path("gdre_tools")

    def init_workspace(self, source_path: Optional[str] = None) -> int:
        """
        Extracts the game's main PCK into the workspace directory.
        Returns the number of files extracted.
        """
        path_to_extract = source_path or self.pck_path
        if not path_to_extract:
            raise FileNotFoundError("No PCK or EXE file found/provided to extract.")

        logger.info("Initializing workspace at %s", self.workspace_dir)
        logs = self.recover_project(path_to_extract)
        return len(logs)

    def set_gdre_tools_path(self, path: str) -> None:
        """Configures the path to the GDRE Tools executable."""
        if not os.path.exists(path):
            raise FileNotFoundError("GDRE Tools executable not found")
        self.gdre_path = path

    def recover_project(
        self, source_path: Optional[str] = None, target_dir: Optional[str] = None
    ) -> List[str]:
        """
        Executes GDRE Tools to recover the project from the PCK.
        Returns a list of log lines from the process.
        """
        if not self.gdre_path:
            raise RuntimeError("GDRE Tools path not configured")
        path_to_recover = source_path or self.pck_path
        if not path_to_recover:
            raise FileNotFoundError("No PCK or EXE file found/provided to recover.")
        output_dir = target_dir or self.workspace_dir
        cmd = [
            self.gdre_path,
            "--headless",
            "--recover",
            path_to_recover,
            "--output-dir",
            output_dir,
        ]

        result = cast(Dict[str, Any], safe_spawn(cmd, capture_output=True))
        if result["returncode"] != 0:
            raise RuntimeError(f"Recovery failed: {result['stderr']}")
        return str(result["stdout"]).splitlines()

    def launch_editor(self, editor_exe: str) -> None:
        """Launches the Godot Editor pointing to the workspace project."""
        if not os.path.exists(editor_exe):
            raise FileNotFoundError(f"Godot Editor not found: {editor_exe}")

        project_file = os.path.join(self.workspace_dir, "project.godot")
        if not os.path.exists(project_file):
            logger.warning(
                "No project.godot found in workspace. Editor may prompt to create one."
            )

        logger.info("Launching Godot Editor...")
        cmd = [editor_exe, "--editor", "--path", self.workspace_dir]
        safe_spawn(cmd, cwd=self.workspace_dir)

    def scan_for_changes(
        self,
        reader: Optional[PCKReader] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, str]:
        """
        Compare workspace files against the original PCK to find modified files.
        Uses an existing PCKReader if provided, otherwise opens a temporary one.
        Returns a dictionary mapping res paths to state (added, patched, replaced).
        """
        if not self.vanilla_cache_dir or not os.path.exists(self.vanilla_cache_dir):
            logger.warning(
                "No vanilla_cache_dir found! Diffing against raw game files will produce false positives."
            )
            if self.pck_path:
                if reader:
                    return self._scan_internal_pck(reader, progress_callback)
                with PCKReader(self.pck_path) as local_reader:
                    return self._scan_internal_pck(local_reader, progress_callback)
            else:
                return self._scan_internal_loose(progress_callback)

        file_list: List[Tuple[str, str]] = []
        for root, dirs, files in os.walk(self.workspace_dir):
            for ignored in [".godot", ".import", "gmos_data", ".gmos_import_staging"]:
                if ignored in dirs:
                    dirs.remove(ignored)
            for file in files:
                if (
                    file == "project.godot"
                    or file.startswith(".")
                    or file.endswith(".bak")
                    or file.endswith(".import")
                    or file.endswith(".uid")
                ):
                    continue
                file_list.append((root, file))

        total_files = len(file_list)
        changes: Dict[str, str] = {}

        for i, (root, file) in enumerate(file_list):
            if progress_callback:
                progress_callback(i + 1, total_files, file)

            abs_path = os.path.join(root, file)
            rel_os_path = os.path.relpath(abs_path, self.workspace_dir)
            res_path = "res://" + rel_os_path.replace(os.sep, "/")
            baseline_path = os.path.join(self.vanilla_cache_dir, rel_os_path)

            if not os.path.exists(baseline_path):
                changes[res_path] = "added"
                continue

            # Fast size check, fallback to deep byte comparison
            if os.path.getsize(abs_path) != os.path.getsize(baseline_path):
                changes[res_path] = (
                    "patched"
                    if file.endswith(
                        (".gd", ".tscn", ".tres", ".cfg", ".txt", ".json", ".csv")
                    )
                    else "replaced"
                )
                continue

            if not filecmp.cmp(abs_path, baseline_path, shallow=False):
                changes[res_path] = (
                    "patched"
                    if file.endswith(
                        (".gd", ".tscn", ".tres", ".cfg", ".txt", ".json", ".csv")
                    )
                    else "replaced"
                )

        return changes

    def _scan_internal_loose(
        self, progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Dict[str, str]:
        """Scans workspace against the raw game directory for loose file setups."""
        file_list: List[Tuple[str, str]] = []
        for root, dirs, files in os.walk(self.workspace_dir):
            for ignored in [".godot", ".import", "gmos_data", ".gmos_import_staging"]:
                if ignored in dirs:
                    dirs.remove(ignored)
            for file in files:
                if (
                    file == "project.godot"
                    or file.startswith(".")
                    or file.endswith(".bak")
                    or file.endswith(".import")
                    or file.endswith(".uid")
                ):
                    continue
                file_list.append((root, file))

        total_files = len(file_list)
        changes: Dict[str, str] = {}

        for i, (root, file) in enumerate(file_list):
            if progress_callback:
                progress_callback(i + 1, total_files, file)

            abs_path = os.path.join(root, file)
            rel_os_path = os.path.relpath(abs_path, self.workspace_dir)
            res_path = "res://" + rel_os_path.replace(os.sep, "/")
            vanilla_path = os.path.join(self.game_dir, rel_os_path)

            if not os.path.exists(vanilla_path):
                changes[res_path] = "added"
                continue

            # Compare size; fallback byte comparison can be added if needed
            if os.path.getsize(abs_path) != os.path.getsize(vanilla_path):
                changes[res_path] = (
                    "patched"
                    if file.endswith(
                        (".gd", ".tscn", ".tres", ".cfg", ".txt", ".json", ".csv")
                    )
                    else "replaced"
                )
                continue

        return changes

    def _scan_internal_pck(
        self,
        reader: PCKReader,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, str]:
        """Internal scan logic utilizing an open PCK reader."""
        if not reader.header:
            return {}
        vanilla_map = {entry.path: entry for entry in reader.header.files}

        file_list: List[Tuple[str, str]] = []
        for root, dirs, files in os.walk(self.workspace_dir):
            for ignored in [".godot", ".import", "gmos_data", ".gmos_import_staging"]:
                if ignored in dirs:
                    dirs.remove(ignored)
            for file in files:
                if (
                    file == "project.godot"
                    or file.startswith(".")
                    or file.endswith(".bak")
                    or file.endswith(".import")
                    or file.endswith(".uid")
                ):
                    continue
                file_list.append((root, file))

        total_files = len(file_list)
        changes: Dict[str, str] = {}

        for i, (root, file) in enumerate(file_list):
            if progress_callback:
                progress_callback(i + 1, total_files, file)

            abs_path = os.path.join(root, file)
            rel_os_path = os.path.relpath(abs_path, self.workspace_dir)
            res_path = "res://" + rel_os_path.replace(os.sep, "/")

            if res_path not in vanilla_map:
                changes[res_path] = "added"
                continue

            entry = vanilla_map[res_path]
            stat = os.stat(abs_path)
            if stat.st_size != entry.size:
                changes[res_path] = (
                    "patched"
                    if file.endswith(
                        (".gd", ".tscn", ".tres", ".cfg", ".txt", ".json", ".csv")
                    )
                    else "replaced"
                )
                continue

        return changes

    def _get_vanilla_bytes(
        self, reader: Optional[PCKReader], res_path: str
    ) -> Optional[bytes]:
        if self.vanilla_cache_dir:
            rel_os = resolve_res_path(res_path)
            baseline_path = os.path.join(self.vanilla_cache_dir, rel_os)
            if os.path.exists(baseline_path):
                with open(baseline_path, "rb") as f:
                    return f.read()
        if reader:
            return reader.read_file(res_path)
        rel_os = resolve_res_path(res_path)
        vanilla_path = os.path.join(self.game_dir, rel_os)
        if os.path.exists(vanilla_path):
            with open(vanilla_path, "rb") as f:
                return f.read()
        return None

    def try_detect_variable_change(
        self, reader: Optional[PCKReader], res_path: str, abs_path: str
    ) -> Optional[str]:
        """
        Analyzes a modified .gd file to see if it's a simple variable value change.
        """
        try:
            # Read vanilla content using the open reader
            vanilla_bytes = self._get_vanilla_bytes(reader, res_path)
            if vanilla_bytes is None:
                return None
            vanilla_lines = vanilla_bytes.decode("utf-8", errors="ignore").splitlines()

            # Read workspace content
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                workspace_lines = f.read().splitlines()

            # Generate diff
            diff = list(difflib.unified_diff(vanilla_lines, workspace_lines, n=0))

            if not diff:
                return None

            # Extract change lines
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

            # Heuristic: Single line replacement of a var/const assignment
            if len(minus) == 1 and len(plus) == 1:
                var_pat = re.compile(r"^\s*(?:var|const)\s+(\w+)\s*[:=]")

                m_old = var_pat.search(minus[0][1:])
                m_new = var_pat.search(plus[0][1:])

                if m_old and m_new and m_old.group(1) == m_new.group(1):
                    var_name = m_new.group(1)
                    rel_os = resolve_res_path(res_path)
                    # Generate manifest entry
                    return f"{res_path}::{var_name} = {rel_os.replace(os.sep, '/')}::{var_name} ; mode=replace"

            return None
        except Exception:
            return None

    def try_detect_code_patch(
        self, reader: Optional[PCKReader], res_path: str, abs_path: str
    ) -> Tuple[Dict[str, List[str]], Dict[str, bytes]]:
        """
        Detects structural code changes and routes to optimal patch operations.
        SmartPatch is used for pure injections, FunctionPatch for destructions/replacements.
        Returns (patch_instructions_dict, virtual_files_dict).
        """
        patches: Dict[str, List[str]] = {"FunctionPatch": [], "SmartPatch": []}
        vfs_files: Dict[str, bytes] = {}
        try:
            # Read contents
            vanilla_bytes = self._get_vanilla_bytes(reader, res_path)
            if vanilla_bytes is None:
                return patches, vfs_files
            vanilla_text = vanilla_bytes.decode("utf-8", errors="ignore")
            vanilla_lines = vanilla_text.splitlines(keepends=True)

            with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                workspace_text = f.read()
                workspace_lines = workspace_text.splitlines(keepends=True)

            # Regex to find 'func function_name(...):'
            func_pat = re.compile(r"^\s*func\s+(\w+)\s*\(")

            # Map of func_name -> (start_idx, end_idx)
            ws_funcs: Dict[str, Tuple[int, int]] = {}

            def _normalize_lines(lines: List[str]) -> str:
                return "\n".join(ln.rstrip() for ln in lines).strip()

            def _extract_block(
                marker_start: str,
                marker_end: str,
                out_prefix: str,
                f_name: str,
                w_start: int,
                w_body: List[str],
                r_os: str,
            ) -> None:
                lines_to_extract: List[str] = []
                collecting = False
                for ln in w_body:
                    if marker_start in ln:
                        collecting = True
                        continue
                    if marker_end in ln:
                        collecting = False
                        continue
                    if collecting:
                        lines_to_extract.append(ln)

                if not lines_to_extract:
                    return

                src_func_name = f"{out_prefix}_{f_name}"
                sig_line = ""
                for k in range(w_start - 1, -1, -1):
                    if re.match(
                        rf"^\s*func\s+{re.escape(f_name)}\s*\(", workspace_lines[k]
                    ):
                        sig_line = workspace_lines[k].replace(
                            f"func {f_name}", f"func {src_func_name}"
                        )
                        break
                if not sig_line:
                    sig_line = f"func {src_func_name}():\n"

                injected_lines_to_save = [sig_line] + lines_to_extract
                mode_str = f"mode={out_prefix}"
                base_name = os.path.basename(r_os)
                partial_rel = f"patches/{base_name}/{src_func_name}.gd"

                vfs_files[partial_rel.replace(os.sep, "/")] = "".join(
                    injected_lines_to_save
                ).encode("utf-8")
                patches["FunctionPatch"].append(
                    f"{res_path}::{f_name} = {partial_rel.replace(os.sep, '/')}::{src_func_name} ; {mode_str}"
                )

            for _, line in enumerate(workspace_lines):
                m = func_pat.match(line)
                if m:
                    func_name = m.group(1)
                    # Use core.patcher logic to find bounds
                    bounds = get_function_block(workspace_lines, func_name)
                    if bounds:
                        ws_funcs[func_name] = bounds

            for func_name, (ws_start, ws_end) in ws_funcs.items():
                # Locate in vanilla
                v_bounds = get_function_block(vanilla_lines, func_name)

                # Extract bodies
                if v_bounds:
                    v_body_lines = vanilla_lines[v_bounds[0] : v_bounds[1] + 1]
                    ws_body_lines = workspace_lines[ws_start : ws_end + 1]

                    if _normalize_lines(ws_body_lines) == _normalize_lines(
                        v_body_lines
                    ):
                        continue  # No actual change

                    # Check for Prefix/Postfix markers
                    ws_text_block = "".join(ws_body_lines)
                    has_prefix = "#--- START PREFIX PATCH" in ws_text_block
                    has_postfix = "#--- START POSTFIX PATCH" in ws_text_block

                    rel_os = resolve_res_path(res_path)

                    if has_prefix or has_postfix:

                        if has_prefix:
                            _extract_block(
                                "#--- START PREFIX PATCH",
                                "#--- END PREFIX PATCH",
                                "prefix",
                                func_name,
                                ws_start,
                                ws_body_lines,
                                rel_os,
                            )
                        if has_postfix:
                            _extract_block(
                                "#--- START POSTFIX PATCH",
                                "#--- END POSTFIX PATCH",
                                "postfix",
                                func_name,
                                ws_start,
                                ws_body_lines,
                                rel_os,
                            )

                        orig_body_lines: List[str] = []
                        collecting_orig = False
                        for ln in ws_body_lines:
                            if "#--- ORIGINAL FUNCTION BODY ---" in ln:
                                collecting_orig = True
                                continue
                            if "#--- START" in ln or "#--- END" in ln:
                                if collecting_orig:
                                    collecting_orig = False
                                    break
                            if collecting_orig:
                                orig_body_lines.append(ln)

                        if orig_body_lines and _normalize_lines(
                            orig_body_lines
                        ) != _normalize_lines(v_body_lines):
                            pass  # Handled by standard diff fallback if original body was also modified
                        continue

                    sm = difflib.SequenceMatcher(None, v_body_lines, ws_body_lines)
                    ops = sm.get_opcodes()

                    is_smart_eligible = True
                    inserts: List[Tuple[int, int, int]] = []
                    for tag, i1, _i2, j1, j2 in ops:
                        if tag in ("replace", "delete"):
                            is_smart_eligible = False
                            break
                        if tag == "insert":
                            inserts.append((i1, j1, j2))

                    mode_str = "mode=replace"
                    op_type = "FunctionPatch"
                    injected_lines_to_save = ws_body_lines

                    if is_smart_eligible and len(inserts) == 1:
                        i1, j1, j2 = inserts[0]
                        injected_lines_to_save = ws_body_lines[j1:j2]
                        op_type = "SmartPatch"
                        if i1 == 0:
                            mode_str = "at=start"
                        elif i1 == len(v_body_lines):
                            mode_str = "at=end"
                        else:
                            anchor_line = v_body_lines[i1 - 1].strip()
                            anchor_safe = anchor_line.replace('"', "'")
                            mode_str = f'anchor="{anchor_safe}"'
                else:
                    op_type = "FunctionPatch"
                    mode_str = "mode=create"
                    injected_lines_to_save = workspace_lines[ws_start : ws_end + 1]
                rel_os = resolve_res_path(res_path)
                # Create a subfolder for partials to keep things clean
                base_name = os.path.basename(resolve_res_path(res_path))
                partial_rel = f"patches/{base_name}/{func_name}.gd"

                if op_type == "SmartPatch":
                    vfs_files[partial_rel.replace(os.sep, "/")] = "".join(
                        injected_lines_to_save
                    ).encode("utf-8")
                    patches["SmartPatch"].append(
                        f"{res_path}::{func_name} = {partial_rel.replace(os.sep, '/')} ; {mode_str}"
                    )
                else:
                    sig_idx = -1
                    for k in range(ws_start - 1, -1, -1):
                        if f"func {func_name}" in workspace_lines[k]:
                            sig_idx = k
                            break
                    if sig_idx != -1:
                        full_func_code = "".join(workspace_lines[sig_idx : ws_end + 1])
                        vfs_files[partial_rel.replace(os.sep, "/")] = (
                            full_func_code.encode("utf-8")
                        )
                        patches["FunctionPatch"].append(
                            f"{res_path}::{func_name} = {partial_rel.replace(os.sep, '/')}::{func_name} ; {mode_str}"
                        )

        except Exception as e:
            logger.warning(f"Smart function detection failed for {res_path}: {e}")

        return patches, vfs_files

    def _generate_binary_patch(
        self, reader: Optional[PCKReader], res_path: str, abs_path: str
    ) -> Optional[Tuple[str, str, bytes]]:
        """
        Generates a BSDIFF binary patch if the module is available and efficient.
        Returns (patch_instruction, patch_rel_path, patch_bytes).
        """
        if not _bsdiff_available:
            return None

        try:
            # Check size threshold - don't bsdiff tiny files, just copy them
            if os.path.getsize(abs_path) < 1024:
                return None

            vanilla_bytes = self._get_vanilla_bytes(reader, res_path)
            if not vanilla_bytes:
                return None

            with open(abs_path, "rb") as f:
                workspace_bytes = f.read()

            # Create Patch
            patch_data = cast(Any, bsdiff4).diff(vanilla_bytes, workspace_bytes)

            # If patch is > 70% of original, just replace file (faster install)
            if len(patch_data) > (len(workspace_bytes) * 0.7):
                return None

            # Write Patch
            rel_os = resolve_res_path(res_path)
            base_name = os.path.basename(rel_os)
            patch_rel = f"patches/{base_name}/patch.bin"
            patch_inst = f"{res_path} = {patch_rel.replace(os.sep, '/')}"

            return (patch_inst, patch_rel.replace(os.sep, "/"), patch_data)

        except Exception as e:
            logger.warning(f"Binary diff failed for {res_path}: {e}")
            return None

    def build_patch_draft(
        self, mod_name: str, reader: Optional[PCKReader] = None
    ) -> PatchDraft:
        """Scans for changes and constructs an in-memory draft of the mod patch."""
        draft = PatchDraft()
        mod_prefix = f"mods/{mod_name}/"

        @contextlib.contextmanager
        def get_reader() -> Generator[Optional[PCKReader], None, None]:
            if reader is not None:
                yield reader
                return
            if self.pck_path:
                with PCKReader(self.pck_path) as r:
                    yield r
            else:
                yield None

        with get_reader() as r:
            changed_res_paths = self.scan_for_changes(r)
            draft.changed_res = changed_res_paths

            if not changed_res_paths:
                return draft

            for res, state in changed_res_paths.items():
                rel_os = resolve_res_path(res).replace(os.sep, "/")
                src_abs = os.path.join(self.workspace_dir, rel_os)
                export_rel = rel_os
                is_native_asset = False
                if export_rel.startswith(mod_prefix):
                    export_rel = export_rel[len(mod_prefix) :]
                    is_native_asset = True
                is_handled = False
                if state == "added":
                    # Brand new file created by the mod. Bundle it, no patch instructions needed.
                    with open(src_abs, "rb") as f:
                        draft.vfs[export_rel] = f.read()
                    continue
                if res.endswith(".gd"):
                    var_patch = self.try_detect_variable_change(r, res, src_abs)
                    if var_patch:
                        draft.variable_patches.append(var_patch)
                        with open(src_abs, "rb") as f:
                            draft.vfs[export_rel] = f.read()
                        is_handled = True
                    else:
                        code_patches, vfs_files = self.try_detect_code_patch(
                            r, res, src_abs
                        )
                        if code_patches["FunctionPatch"] or code_patches["SmartPatch"]:
                            draft.function_patches.extend(code_patches["FunctionPatch"])
                            draft.smart_patches.extend(code_patches["SmartPatch"])
                            draft.vfs.update(vfs_files)
                            is_handled = True

                if not is_handled and _bsdiff_available:
                    bin_patch_data = self._generate_binary_patch(r, res, src_abs)
                    if bin_patch_data:
                        instruction, rel_path, bdata = bin_patch_data
                        draft.binary_patches.append(instruction)
                        draft.vfs[rel_path] = bdata
                        is_handled = True

                if not is_handled:
                    # Unmodified code / static assets fall into FileReplace
                    with open(src_abs, "rb") as f:
                        draft.vfs[export_rel] = f.read()
                    if not is_native_asset:
                        draft.file_replaces.append(f"{res} = {export_rel}")

        return draft

    def commit_mod_patch(
        self,
        output_dir: str,
        mod_name: str,
        author: str,
        version: str,
        description: str,
        draft: PatchDraft,
    ) -> str:
        """Writes a PatchDraft out to the filesystem."""
        os.makedirs(output_dir, exist_ok=True)

        # 1. Write the Virtual File System blocks
        for rel_path, data in draft.vfs.items():
            dst_abs = os.path.join(output_dir, rel_path)
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            atomic_write_bytes(dst_abs, data)

        # 2. Build mod.mos Manifest
        lines: List[str] = [
            f"# Generated by GMOS SDK for {mod_name}",
            "[ModInfo]",
            f"Name = {mod_name}",
            f"Version = {version}",
            f"Author = {author}",
        ]
        if description:
            lines.append(f"Description = {description}")
        lines.append("")

        if draft.file_replaces:
            lines.append("[FileReplace]")
            lines.extend(draft.file_replaces)
            lines.append("")

        if draft.variable_patches:
            lines.append("[VariablePatch]")
            lines.extend(draft.variable_patches)
            lines.append("")

        if draft.function_patches:
            lines.append("[FunctionPatch]")
            lines.extend(draft.function_patches)
            lines.append("")

        if draft.smart_patches:
            lines.append("[SmartPatch]")
            lines.extend(draft.smart_patches)
            lines.append("")

        if draft.binary_patches:
            lines.append("[BinaryPatch]")
            lines.extend(draft.binary_patches)
            lines.append("")

        manifest_path = os.path.join(output_dir, "mod.mos")

        atomic_write_with_backup(manifest_path, "\n".join(lines))

        logger.info(
            "Generated mod package at %s with %d files.",
            output_dir,
            len(draft.changed_res),
        )
        return manifest_path
