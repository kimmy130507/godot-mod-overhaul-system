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
import configparser
import datetime
import difflib
import json
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, Union, cast

import bsdiff4  # type: ignore[reportMissingTypeStubs, unused-ignore]

from gmos.core.patcher import (
    generate_patch_plan,
    parse_mod_config,
    patch_file_replace,
    patch_function,
    patch_smart_inject,
    patch_variable,
    resolve_res_path,
)
from gmos.core.sdk import GodotBridge
from gmos.core.tools import ToolManager
from gmos.io import safe_rmtree
from gmos.io.base import get_io_executor
from gmos.io.pck import pack_pck
from gmos.state.config import save_global_config
from gmos.ui.widgets import AutoScrollbar, NameInputDialog, ProgressDialog, ToolTip
from gmos.utils import (
    apply_window_theme,
    detect_icon_theme,
    get_adaptive_color_variant,
    get_dynamic_text_color,
    load_and_apply_app_icon_to_toplevel,
    load_icon,
    logger,
    safe_spawn,
    sanitize_filename,
    setup_child_window,
)

if TYPE_CHECKING:
    from gmos.ui.app import App


class DeveloperToolsDialog(tk.Toplevel):
    def __init__(self, parent: "App"):
        super().__init__(parent)
        self.app = parent
        self.title("Mod Developer SDK")
        load_and_apply_app_icon_to_toplevel(self)
        setup_child_window(self, parent, width=1200, height=800, modal=False)

        self.game_dir = self.app.vars["game_dir"].get()

        # Project isolation
        active_id = self.app.global_cfg.default_instance_id
        if not active_id:
            messagebox.showerror("Error", "No active instance.")
            self.destroy()
            return

        inst = self.app.global_cfg.instances.get(active_id)
        if not inst:
            messagebox.showerror(
                "Error", "Active instance data is corrupted or missing."
            )
            self.destroy()
            return
        self.projects_dir = os.path.join(inst.path, "gmos_data", "projects")
        self.vanilla_cache_dir = os.path.join(inst.path, "gmos_data", "vanilla_cache")
        os.makedirs(self.projects_dir, exist_ok=True)

        self.tool_manager = ToolManager()
        self.active_project: Optional[str] = None
        self.bridge: Optional[GodotBridge] = None

        self._setup_ui()
        self._refresh_project_list()
        self.bind("<<ThemeChanged>>", self._on_theme_change)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._busy = False

    def _update_tree_theme(self) -> None:
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#ffffff")
        self.diff_tree.tag_configure(
            "added",
            foreground=get_adaptive_color_variant(theme_bg, "#4caf50", "#1b5e20"),
        )
        self.diff_tree.tag_configure(
            "patched",
            foreground=get_adaptive_color_variant(theme_bg, "#ffb300", "#c29200"),
        )
        self.diff_tree.tag_configure(
            "replaced",
            foreground=get_adaptive_color_variant(theme_bg, "#f44336", "#b30000"),
        )

    def _generate_progress_bar(self, percent: float) -> str:
        blocks = 50
        filled = int((percent / 100) * blocks)
        empty = blocks - filled
        return f"{'▰' * filled}{'▱' * empty}  {int(percent)}%"

    def _on_theme_change(self, event: Any = None) -> None:
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#ffffff")
        fg_color = get_dynamic_text_color(theme_bg)
        if hasattr(self, "project_listbox") and self.project_listbox.winfo_exists():
            self.project_listbox.config(bg=theme_bg, fg=fg_color)
        if hasattr(self, "diff_text") and self.diff_text.winfo_exists():
            self.diff_text.config(bg=theme_bg, fg=fg_color, insertbackground=fg_color)
        if hasattr(self, "diff_tree") and self.diff_tree.winfo_exists():
            self._update_tree_theme()
        apply_window_theme(self)

    def _setup_ui(self) -> None:
        self.main_paned = ttk.PanedWindow(self, orient="vertical")
        self.main_paned.pack(fill="both", expand=True, padx=10, pady=10)

        self.top_paned = ttk.PanedWindow(self.main_paned, orient="horizontal")
        cast(Any, self.main_paned).add(self.top_paned, weight=3)

        # Left Pane: Project Selection
        left_frame = ttk.Frame(self.top_paned, width=250)
        cast(Any, self.top_paned).add(left_frame, weight=1)

        ttk.Label(left_frame, text="Mod Projects", font=("Segoe UI", 10, "bold")).pack(
            anchor="w"
        )

        toolbar = ttk.Frame(left_frame)
        toolbar.pack(side="top", fill="x", pady=4)

        self.ico_add = load_icon("plus.png", size=(16, 16))
        self.ico_rename = load_icon("pencil.png", size=(16, 16))
        self.ico_trash = load_icon("trash-2.png", size=(16, 16))
        self.ico_play = load_icon(
            "play.png", size=(16, 16), force_color_variant="light"
        )
        self.ico_scan = load_icon("refresh-cw.png", size=(16, 16))
        self.ico_build = load_icon(
            "hammer.png", size=(16, 16), force_color_variant="light"
        )
        self.ico_extract = load_icon(
            "folder-open.png", size=(16, 16), force_color_variant="light"
        )
        self.ico_import = load_icon("file-down.png", size=(16, 16))

        try:
            self.btn_add = ttk.Button(
                toolbar,
                text="Create",
                image=self.ico_add or "",
                compound="left",
                command=self._create_project,
                style="primary.TButton",
            )
        except Exception:
            self.btn_add = ttk.Button(
                toolbar,
                text="Create",
                image=self.ico_add or "",
                compound="left",
                command=self._create_project,
            )
        self.btn_add.pack(side="left", fill="x", expand=True, padx=(0, 2))
        ToolTip(self.btn_add, "New Project")

        self.btn_import = ttk.Button(
            toolbar,
            image=self.ico_import or "",
            text="Import" if not self.ico_import else "",
            command=self._import_project,
        )
        self.btn_import.pack(side="left", padx=2)
        ToolTip(self.btn_import, "Import Project")
        project_list_frame = ttk.Frame(left_frame)
        project_list_frame.pack(fill="both", expand=True, pady=5)
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#ffffff")
        self.project_listbox = tk.Listbox(
            project_list_frame,
            exportselection=False,
            bg=theme_bg,
            fg=get_dynamic_text_color(theme_bg),
        )
        project_list_vsb = AutoScrollbar(
            project_list_frame,
            orient="vertical",
            command=cast(Any, self.project_listbox).yview,
        )
        self.project_listbox.configure(yscrollcommand=project_list_vsb.set)
        project_list_vsb.pack(side="right", fill="y")
        self.project_listbox.pack(side="left", fill="both", expand=True)
        self.project_listbox.bind("<<ListboxSelect>>", self._on_project_select)

        # Right Pane: Workspace Context
        self.right_frame = ttk.Frame(self.top_paned)
        cast(Any, self.top_paned).add(self.right_frame, weight=3)
        self.header_frame = ttk.Frame(self.right_frame)
        self.var_project_name = tk.StringVar()
        self.lbl_header_name = ttk.Label(
            self.header_frame,
            textvariable=self.var_project_name,
            font=("Segoe UI", 16, "bold"),
            cursor="hand2",
        )
        self.lbl_header_name.pack(side="left", fill="x", anchor="w")
        self.lbl_header_name.bind("<Button-1>", self._enable_header_edit)
        self.ent_header_name = ttk.Entry(
            self.header_frame,
            textvariable=self.var_project_name,
            font=("Segoe UI", 16, "bold"),
        )
        self.ent_header_name.bind("<Return>", self._finish_header_edit)
        self.ent_header_name.bind("<FocusOut>", self._finish_header_edit)
        self.btn_delete = ttk.Button(
            self.header_frame,
            image=self.ico_trash or "",
            text="Delete" if not self.ico_trash else "",
            command=self._delete_project,
            style="Link.TButton",
            width=3,
        )
        self.btn_delete.pack(side="right", padx=2)
        ToolTip(self.btn_delete, "Delete Project")
        # Uninitialized
        self.init_frame = ttk.Frame(self.right_frame)
        ttk.Label(
            self.init_frame,
            text="Workspace is empty.\nTo begin modding, the game's PCK or Executable must be extracted and decompiled.",
            justify="center",
        ).pack(pady=20)
        self.btn_init = ttk.Button(
            self.init_frame,
            text="Decompile & Extract",
            image=self.ico_extract or "",
            compound="left",
            command=self._run_init,
        )
        self.btn_init.pack(ipady=2, ipadx=5)

        # Ready
        self.ready_frame = ttk.Frame(self.right_frame)

        # Diff Viewer Header
        diff_header = ttk.Frame(self.ready_frame)
        diff_header.pack(fill="x", pady=(0, 5))
        ttk.Label(
            diff_header,
            text="Modified / Added Files (Workspace vs Vanilla):",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        self.btn_scan = ttk.Button(
            diff_header,
            image=self.ico_scan or "",
            command=self._scan_changes,
            style="Link.TButton",
            width=3,
        )
        self.btn_scan.pack(side="left", padx=5)
        ToolTip(self.btn_scan, "Scan for changes")

        bot_ready_bar = ttk.Frame(self.ready_frame)
        bot_ready_bar.pack(side="bottom", fill="x", pady=5)
        self.btn_launch = ttk.Button(
            bot_ready_bar,
            text="Launch Godot Editor",
            image=self.ico_play or "",
            compound="left",
            command=self._launch_editor,
            style="primary.TButton",
        )
        self.btn_launch.pack(side="left")
        self.btn_build = ttk.Button(
            bot_ready_bar,
            text="Build Mod Package",
            image=self.ico_build or "",
            compound="left",
            command=self._build_mod,
            style="primary.TButton",
        )
        self.btn_build.pack(side="right")
        tree_container = ttk.Frame(self.ready_frame)
        tree_container.pack(side="top", fill="both", expand=True, pady=5)

        self.diff_tree = ttk.Treeview(tree_container, show="tree", selectmode="browse")
        self._update_tree_theme()
        self.diff_tree.bind("<<TreeviewSelect>>", self._on_diff_file_select)

        vsb_tree = AutoScrollbar(
            tree_container, orient="vertical", command=cast(Any, self.diff_tree).yview
        )
        self.diff_tree.configure(yscrollcommand=vsb_tree.set)
        self.diff_tree.pack(side="left", fill="both", expand=True)
        vsb_tree.pack(side="right", fill="y")
        # Bottom Pane: Diff Viewer + Patch Types
        self.bottom_paned = ttk.PanedWindow(self.main_paned, orient="horizontal")

        is_dark = detect_icon_theme() == "light"
        bg_color = "#1e1e1e" if is_dark else "#ffffff"
        fg_color = get_dynamic_text_color(bg_color)

        text_container = ttk.Frame(self.bottom_paned)
        cast(Any, self.bottom_paned).add(text_container, weight=7)

        self.diff_text = tk.Text(
            text_container,
            wrap="none",
            font=("Consolas", 10),
            state="disabled",
            height=15,
            bg=bg_color,
            fg=fg_color,
        )
        diff_vsb = AutoScrollbar(
            text_container, orient="vertical", command=cast(Any, self.diff_text).yview
        )
        diff_hsb = AutoScrollbar(
            text_container, orient="horizontal", command=cast(Any, self.diff_text).xview
        )
        self.diff_text.configure(
            yscrollcommand=diff_vsb.set, xscrollcommand=diff_hsb.set
        )
        diff_vsb.pack(side="right", fill="y")
        diff_hsb.pack(side="bottom", fill="x")
        self.diff_text.pack(fill="both", expand=True)

        # Interactive tags for diff code highlighting
        self.diff_text.tag_config("add", foreground="#4caf50")
        self.diff_text.tag_config("rem", foreground="#f44336")
        self.diff_text.tag_config("line_num", foreground="#808080")
        self.diff_text.tag_config(
            "patch_block", background="#2a4d2a" if is_dark else "#e6ffed"
        )
        self.diff_text.tag_bind(
            "patch_block", "<Enter>", lambda e: self.diff_text.config(cursor="hand2")
        )
        self.diff_text.tag_bind(
            "patch_block", "<Leave>", lambda e: self.diff_text.config(cursor="")
        )

        patch_container = ttk.Frame(self.bottom_paned)
        cast(Any, self.bottom_paned).add(patch_container, weight=3)

        ttk.Label(
            patch_container, text="Detected Patches", font=("Segoe UI", 9, "bold")
        ).pack(anchor="w", pady=(0, 2))

        self.patch_tree = ttk.Treeview(
            patch_container, show="headings", selectmode="browse", columns=("type",)
        )
        self.patch_tree.heading("type", text="Instruction")
        self.patch_tree.column("type", anchor="w", stretch=True)

        patch_vsb = AutoScrollbar(
            patch_container, orient="vertical", command=cast(Any, self.patch_tree).yview
        )
        patch_hsb = AutoScrollbar(
            patch_container,
            orient="horizontal",
            command=cast(Any, self.patch_tree).xview,
        )
        self.patch_tree.configure(
            yscrollcommand=patch_vsb.set, xscrollcommand=patch_hsb.set
        )
        patch_vsb.pack(side="right", fill="y")
        patch_hsb.pack(side="bottom", fill="x")
        self.patch_tree.pack(fill="both", expand=True)

    def _refresh_project_list(self) -> None:
        self.project_listbox.delete(0, "end")
        for d in os.listdir(self.projects_dir):
            if os.path.isdir(os.path.join(self.projects_dir, d)):
                self.project_listbox.insert("end", d)

    def _create_project(self) -> None:
        def _on_create(name: str) -> None:
            name = sanitize_filename(name)
            if not name:
                return
            path = os.path.join(self.projects_dir, name)
            os.makedirs(path, exist_ok=True)
            self._refresh_project_list()

            for i in range(self.project_listbox.size()):
                if cast(str, cast(Any, self.project_listbox).get(i)) == name:
                    self.project_listbox.selection_clear(0, "end")
                    self.project_listbox.selection_set(i)
                    self._on_project_select(None)
                    break

        NameInputDialog(
            self, title="New Project", prompt="Enter Project Name:", callback=_on_create
        )

    def _enable_header_edit(self, _event: Any = None) -> None:
        if not self.active_project or getattr(self, "_busy", False):
            return
        self.lbl_header_name.pack_forget()
        self.ent_header_name.pack(side="left", fill="x", expand=True)
        self.ent_header_name.focus_set()

    def _finish_header_edit(self, _event: Any = None) -> None:
        self.ent_header_name.pack_forget()
        self.lbl_header_name.pack(side="left", fill="x", anchor="w")
        if not self.active_project:
            return
        old_name = os.path.basename(self.active_project)
        new_name = self.var_project_name.get().strip()
        if not new_name or new_name == old_name:
            self.var_project_name.set(old_name)
            return
        new_name = sanitize_filename(new_name)
        old_path = self.active_project
        new_path = os.path.join(self.projects_dir, new_name)
        if os.path.exists(new_path):
            messagebox.showerror("Error", "Project name already exists.", parent=self)
            self.var_project_name.set(old_name)
            return
        try:
            os.rename(old_path, new_path)
            self.active_project = new_path
            self.bridge = GodotBridge(
                self.game_dir,
                self.active_project,
                tool_manager=self.tool_manager,
                vanilla_cache_dir=self.vanilla_cache_dir,
            )
            self._refresh_project_list()
            for i in range(self.project_listbox.size()):
                if cast(str, cast(Any, self.project_listbox).get(i)) == new_name:
                    self.project_listbox.selection_clear(0, "end")
                    self.project_listbox.selection_set(i)
                    break
        except Exception as e:
            messagebox.showerror("Rename Error", str(e), parent=self)
            self.var_project_name.set(old_name)

    def _delete_project(self) -> None:
        sel = cast(Tuple[int, ...], cast(Any, self.project_listbox).curselection())
        if not sel:
            return
        name = cast(str, cast(Any, self.project_listbox).get(int(sel[0])))
        path = os.path.join(self.projects_dir, name)
        if messagebox.askyesno(
            "Delete Project", f"Are you sure you want to delete '{name}'?", parent=self
        ):
            dlg = ProgressDialog(self, title="Deleting Project")
            dlg.set_text(f"Deleting {name}...")
            dlg.start()

            def _worker() -> None:
                try:
                    safe_rmtree(path)
                    if self.active_project == path:
                        self.active_project = None
                        self.bridge = None
                    self.after(0, self._update_state)
                    self.after(0, self._refresh_project_list)
                except Exception as e:
                    err_msg = str(e)

                    def _show_err() -> None:
                        messagebox.showerror("Delete Error", err_msg, parent=self)

                    self.after(0, _show_err)
                finally:
                    self.after(0, dlg.close)

            threading.Thread(target=_worker, daemon=True).start()

    def _import_project(self) -> None:
        mos_path = filedialog.askopenfilename(
            title="Select mod.mos",
            filetypes=[("Mod Manifest", "mod.mos"), ("All Files", "*.*")],
            parent=self,
        )
        if not mos_path:
            return

        src_dir = os.path.dirname(mos_path)
        mod_name = os.path.basename(src_dir)
        try:

            cfg = configparser.ConfigParser()
            cfg.read(mos_path, encoding="utf-8")
            if cfg.has_section("ModInfo") and cfg.has_option("ModInfo", "Name"):
                mod_name = cfg.get("ModInfo", "Name")
        except Exception:
            pass

        mod_name = sanitize_filename(mod_name)
        if not mod_name:
            mod_name = "ImportedMod"

        base_name = mod_name
        counter = 1
        dst_dir = os.path.join(self.projects_dir, mod_name)
        while os.path.exists(dst_dir):
            mod_name = f"{base_name} {counter}"
            dst_dir = os.path.join(self.projects_dir, mod_name)
            counter += 1

        try:
            os.makedirs(dst_dir, exist_ok=True)
            import_staging = os.path.join(dst_dir, ".gmos_import_staging")
            shutil.copytree(src_dir, import_staging)
            self._refresh_project_list()
            for i in range(self.project_listbox.size()):
                if cast(str, cast(Any, self.project_listbox).get(i)) == mod_name:
                    self.project_listbox.selection_clear(0, "end")
                    self.project_listbox.selection_set(i)
                    self._on_project_select(None)
                    break
            if messagebox.askyesno(
                "Complete Import",
                "To complete the import, the vanilla game files must be extracted into this workspace so the mod patches can be applied.\n\nProceed to extraction now?",
                parent=self,
            ):
                self._run_init()
        except Exception as e:
            messagebox.showerror(
                "Import Error", f"Failed to import mod:\n{e}", parent=self
            )

    def _on_project_select(self, event: Any) -> None:
        sel = cast(Tuple[int, ...], cast(Any, self.project_listbox).curselection())
        if not sel:
            return

        index = int(sel[0])
        name = cast(str, cast(Any, self.project_listbox).get(index))
        self.active_project = os.path.join(self.projects_dir, name)
        self.bridge = GodotBridge(
            self.game_dir,
            self.active_project,
            tool_manager=self.tool_manager,
            vanilla_cache_dir=self.vanilla_cache_dir,
        )

        self._update_state()

    def _update_state(self) -> None:
        self.init_frame.pack_forget()
        self.ready_frame.pack_forget()
        self.header_frame.pack_forget()
        panes_list = cast(Any, self.main_paned).panes()
        if str(self.bottom_paned) in panes_list:
            cast(Any, self.main_paned).forget(self.bottom_paned)
        if not self.active_project:
            self.diff_tree.delete(*self.diff_tree.get_children())
            if hasattr(self, "diff_text"):
                self.diff_text.config(state="normal")
                self.diff_text.delete("1.0", "end")
                self.diff_text.config(state="disabled")
            return
        self.header_frame.pack(fill="x", pady=(0, 10))
        self.var_project_name.set(os.path.basename(self.active_project))
        is_initialized = os.path.exists(
            os.path.join(self.active_project, "project.godot")
        )
        if is_initialized:
            self.ready_frame.pack(fill="both", expand=True)
            cast(Any, self.main_paned).add(self.bottom_paned, weight=2)
            self.diff_tree.delete(*self.diff_tree.get_children())

            cache_file = os.path.join(self.active_project, ".gmos_scan_cache.json")
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    last_scanned = data.get("last_scanned", "Unknown")
                    changes = data.get("changes", {})
                    self.diff_tree.insert(
                        "", "end", text=f"Last Scanned: {last_scanned}"
                    )
                    self._populate_diff_tree(changes)
                    self.btn_build.config(state="normal" if changes else "disabled")
                except Exception:
                    self.diff_tree.insert(
                        "",
                        "end",
                        text="Workspace loaded. Click 'Scan' to view modifications.",
                    )
                    self.btn_build.config(state="disabled")
            else:
                self.diff_tree.insert(
                    "",
                    "end",
                    text="Workspace loaded. Click 'Scan' to view modifications.",
                )
                self.btn_build.config(state="disabled")
        else:
            self.init_frame.pack(fill="both", expand=True)

    def _populate_diff_tree(self, changes: Dict[str, str]) -> None:
        if not changes:
            self.diff_tree.insert(
                "",
                "end",
                text="No modified files detected. Workspace matches Vanilla.",
            )
        else:
            display_changes = sorted(changes.keys())
            for ch in display_changes[:1000]:
                state = changes[ch]
                self.diff_tree.insert(
                    "", "end", text=f"[{state.upper()}] {ch}", tags=(state,)
                )
            if len(display_changes) > 1000:
                self.diff_tree.insert(
                    "",
                    "end",
                    text=f"... and {len(display_changes) - 1000} more files.",
                )

    def _ensure_gdre_tools(self) -> Optional[str]:
        if self.tool_manager.is_installed("gdre_tools"):
            return self.tool_manager.get_tool_path("gdre_tools")
        if not messagebox.askyesno(
            "Missing Tools",
            "GDRE Tools is required to extract the PCK.\nDownload and install now?",
            parent=self,
        ):
            return None
        try:
            self.config(cursor="watch")
            self.update_idletasks()
            path = self.tool_manager.install_tool("gdre_tools")
            messagebox.showinfo("Success", "Tools installed successfully.", parent=self)
            return path
        except Exception as e:
            messagebox.showerror("Install Error", str(e), parent=self)
            return None
        finally:
            self.config(cursor="")

    def _run_init(self) -> None:
        if not self.bridge or not self.active_project:
            return

        cache_ready = os.path.exists(
            os.path.join(self.vanilla_cache_dir, "project.godot")
        )
        source_path = None
        is_packaged = False
        gdre_path: Optional[str] = None
        if not cache_ready:
            res = self._prompt_cache_source()
            if not res:
                return
            source_path, is_packaged, gdre_path = res
        dlg = ProgressDialog(self, title="Extracting Workspace")
        dlg.set_text("Starting extraction process...")
        dlg.start()
        threading.Thread(
            target=lambda: self._execute_extraction(
                cache_ready, source_path, gdre_path, is_packaged, dlg
            ),
            daemon=True,
        ).start()

    def _prompt_cache_source(self) -> Optional[Tuple[str, bool, str]]:
        """Prompts for tools and path if cache is missing to reduce method complexity."""
        gdre_path = self._ensure_gdre_tools()
        if not gdre_path:
            return None
        is_packaged = messagebox.askyesno(
            "Global Workspace Cache",
            "The Global Vanilla Cache has not been generated yet.\n\n"
            "This extraction only happens once and will serve as the base for all your mod projects.\n\n"
            "Is the game packaged into a PCK or EXE file?\n"
            "Select 'Yes' to choose a PCK/EXE file.\n"
            "Select 'No' to select a loose game folder.",
            parent=self,
        )
        source_path = (
            filedialog.askopenfilename(
                title="Select Game Executable or PCK",
                filetypes=[("Godot PCK/EXE", "*.pck *.exe"), ("All Files", "*.*")],
                parent=self,
                initialdir=self.game_dir,
            )
            if is_packaged
            else filedialog.askdirectory(
                title="Select Loose Game Folder", parent=self, initialdir=self.game_dir
            )
        )
        if not source_path:
            return None
        if not messagebox.askyesno(
            "Confirm Extraction",
            f"Extracting {os.path.basename(source_path)} will take time and disk space, but will only be done once.\n\nContinue?",
            parent=self,
        ):
            return None
        return source_path, is_packaged, gdre_path

    def _execute_extraction(
        self,
        cache_ready: bool,
        source_path: Optional[str],
        gdre_path: Optional[str],
        is_packaged: bool,
        dlg: ProgressDialog,
    ) -> None:
        staging_dir = None
        try:
            if not cache_ready and source_path and gdre_path:
                target_source = source_path
                if not is_packaged:
                    self.after(
                        0, lambda: dlg.set_text("Copying game files to staging area...")
                    )
                    staging_dir = os.path.join(
                        self.game_dir, f".gmos_staging_{uuid.uuid4().hex}"
                    )

                    def ignore_gmos(dir_path: str, contents: list[str]) -> list[str]:
                        if source_path and os.path.abspath(dir_path) == os.path.abspath(
                            source_path
                        ):
                            return ["gmos_data", "mods"]
                        return []

                    shutil.copytree(source_path, staging_dir, ignore=ignore_gmos)
                    if dlg.cancelled():
                        raise Exception("Extraction cancelled by user.")
                    self.after(
                        0, lambda: dlg.set_text("Reverting modded files to vanilla...")
                    )
                    for root, _, files in os.walk(staging_dir):
                        if dlg.cancelled():
                            raise Exception("Extraction cancelled by user.")
                        for file in files:
                            if file.endswith(".bak"):
                                bak_path = os.path.join(root, file)
                                orig_path = bak_path[:-4]
                                if os.path.exists(orig_path):
                                    os.remove(orig_path)
                                os.rename(bak_path, orig_path)
                    target_source = staging_dir
                self.after(
                    0,
                    lambda: dlg.set_text("Decompiling global cache with GDRE Tools..."),
                )
                os.makedirs(self.vanilla_cache_dir, exist_ok=True)
                proc = cast(
                    subprocess.Popen[Any],
                    safe_spawn(
                        [
                            gdre_path,
                            "--headless",
                            f"--recover={target_source}",
                            f"--output-dir={self.vanilla_cache_dir}",
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.STDOUT,
                    ),
                )
                while proc.poll() is None:
                    if dlg.cancelled():
                        proc.terminate()
                        raise Exception("Extraction cancelled by user.")
                    time.sleep(0.5)
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(proc.returncode, proc.args)
            if dlg.cancelled():
                raise Exception("Extraction cancelled by user.")
            self.after(
                0, lambda: dlg.set_text("Cloning vanilla cache to project workspace...")
            )

            def ignore_cache_bloat(dir_path: str, contents: list[str]) -> list[str]:
                return [".godot"] if ".godot" in contents else []

            def _hardlink_copy(src: str, dst: str) -> None:
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)

            active_proj = self.active_project
            if active_proj:
                shutil.copytree(
                    self.vanilla_cache_dir,
                    active_proj,
                    dirs_exist_ok=True,
                    copy_function=_hardlink_copy,
                    ignore=ignore_cache_bloat,
                )
                if dlg.cancelled():
                    raise Exception("Extraction cancelled by user.")
                import_staging = os.path.join(active_proj, ".gmos_import_staging")
                if os.path.exists(import_staging):
                    self.after(
                        0, lambda: dlg.set_text("Applying imported mod patches...")
                    )
                    try:
                        self._patch_imported_mod(active_proj, import_staging, dlg)
                    except Exception as e:
                        logger.error("Failed to apply imported mod: %s", e)
                        err_msg = str(e)
                        self.after(
                            0,
                            lambda: messagebox.showerror(
                                "Import Patch Error",
                                f"Failed to apply mod patches:\n{err_msg}",
                                parent=self,
                            ),
                        )
                    finally:
                        if os.path.exists(import_staging):
                            safe_rmtree(import_staging)
            self.after(0, dlg.close)
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Success", "Workspace initialized successfully.", parent=self
                ),
            )
            self.after(0, self._update_state)
        except subprocess.CalledProcessError as e:
            err_out = (
                e.stderr.strip()
                if e.stderr
                else (e.stdout.strip() if e.stdout else "No output.")
            )
            ret_code = e.returncode
            self.after(0, dlg.close)
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Extraction Error",
                    f"GDRE Tools failed (Code {ret_code}):\n{err_out}",
                    parent=self,
                ),
            )
        except Exception as e:
            self.after(0, dlg.close)
            if str(e) != "Extraction cancelled by user.":
                exc_msg = str(e)
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Initialization Error", exc_msg, parent=self
                    ),
                )
        finally:
            if staging_dir and os.path.exists(staging_dir):
                safe_rmtree(staging_dir)

    def _patch_imported_mod(
        self, active_proj: str, import_staging: str, dlg: ProgressDialog
    ) -> None:
        """Applies configuration patches and additional assets for an imported mod workspace."""
        mod_config = None
        try:
            mod_config = parse_mod_config(import_staging)
        except Exception:
            pass
        if not mod_config:
            mod_config = parse_mod_config(os.path.join(import_staging, "mod.mos"))
        if not mod_config:
            raise Exception("Failed to parse mod.mos configuration.")

        plan = generate_patch_plan(import_staging, mod_config)
        known_patch_files: Set[str] = set()
        for _, op, _details in plan:
            details = cast(Any, _details)
            src_path = ""
            if op in ("FileReplace", "BinaryPatch") and len(details) >= 2:
                src_path = str(details[1])
            elif (
                op in ("FunctionPatch", "VariablePatch", "SmartPatch")
                and len(details) >= 3
            ):
                src_path = str(details[2])
            if src_path:
                known_patch_files.add(os.path.normpath(src_path))

            if op == "FileReplace":
                patch_file_replace(active_proj, details[0], details[1])
            elif op == "FunctionPatch":
                patch_function(
                    active_proj,
                    details[0],
                    details[1],
                    details[2],
                    details[3],
                    mode=details[4],
                )
            elif op == "VariablePatch":
                patch_variable(
                    active_proj,
                    details[0],
                    details[1],
                    details[2],
                    details[3],
                    mode=details[4],
                )
            elif op == "SmartPatch":
                patch_smart_inject(
                    active_proj,
                    details[0],
                    details[1],
                    details[2],
                    details[3],
                    anchor=(details[4] if len(details) > 4 else None),
                )
            elif op == "BinaryPatch":
                self._apply_binary_patch_safe(active_proj, details)

        mod_folder_name = os.path.basename(active_proj)
        for root_dir, _, import_files in os.walk(import_staging):
            for f in import_files:
                if f == "mod.mos" or f.endswith(".bak") or f.endswith(".bin"):
                    continue
                src_file = os.path.join(root_dir, f)
                rel_path = os.path.relpath(src_file, import_staging)
                if os.path.normpath(src_file) in known_patch_files:
                    continue
                path_parts = Path(rel_path).parts
                dst_file = (
                    os.path.join(active_proj, rel_path)
                    if path_parts and path_parts[0] == "mods"
                    else os.path.join(active_proj, "mods", mod_folder_name, rel_path)
                )
                if not os.path.exists(dst_file):
                    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                    shutil.copy2(src_file, dst_file)

    def _apply_binary_patch_safe(self, active_proj: str, details: Any) -> None:
        """Helper wrapper to cleanly isolate native bsdiff functionality outside operations loop."""
        try:
            base_path = os.path.join(active_proj, resolve_res_path(details[0]))
            if os.path.exists(base_path):
                base_bytes = Path(base_path).read_bytes()
                patch_bytes = Path(details[1]).read_bytes()
                patched_bytes = cast(
                    bytes, cast(Any, bsdiff4).patch(base_bytes, patch_bytes)
                )
                Path(base_path).write_bytes(patched_bytes)
        except (ImportError, NameError):
            logger.error("bsdiff4 not installed, skipping BinaryPatch")

    def _launch_editor(self) -> None:
        if not self.bridge:
            return
        exe = self.app.global_cfg.godot_editor_path
        if not exe or not os.path.exists(exe) or not os.path.isfile(exe):
            exe_cand = shutil.which("godot") or shutil.which("godot4")
            if not exe_cand:
                exe_cand = filedialog.askopenfilename(
                    title="Locate Godot Editor Executable",
                    filetypes=[("Executables", "*.exe"), ("All Files", "*.*")],
                    parent=self,
                )
            if not exe_cand:
                return
            exe = str(exe_cand)
            self.app.global_cfg.godot_editor_path = exe

            save_global_config(self.app.global_cfg)
        try:
            self.bridge.launch_editor(exe)
        except Exception as e:
            messagebox.showerror("Launch Error", str(e), parent=self)

    def _scan_changes(self) -> None:
        if not self.bridge:
            return
        self.diff_tree.delete(*self.diff_tree.get_children())
        item_id = self.diff_tree.insert(
            "", "end", text="Scanning for changes... This may take a moment."
        )

        self.diff_text.config(state="normal")
        self.diff_text.delete("1.0", "end")
        self.diff_text.config(state="disabled")
        self.config(cursor="watch")
        self._busy = True
        self.btn_scan.config(state="disabled")
        self.btn_build.config(state="disabled")
        self.project_listbox.config(state="disabled")
        self.btn_delete.config(state="disabled")
        self.btn_add.config(state="disabled")
        self.btn_import.config(state="disabled")
        self.update_idletasks()

        bridge = self.bridge

        def progress_cb(current: int, total: int, current_file: str) -> None:
            pct = (current / total * 100) if total > 0 else 0
            bar = self._generate_progress_bar(pct)
            if current % max(1, total // 50) == 0 or current == total:
                self.after(
                    0,
                    lambda: self.diff_tree.item(
                        item_id, text=f"{bar}  -  {current_file}"
                    ),
                )

        def _scan_task() -> Dict[str, str]:
            return bridge.scan_for_changes(progress_callback=progress_cb)

        def _on_scan_done(future: Any) -> None:
            def _update_ui() -> None:
                if hasattr(self, "build_dlg"):
                    self.build_dlg.close()
                self.config(cursor="")
                self._busy = False
                self.btn_scan.config(state="normal")
                self.project_listbox.config(state="normal")
                self.btn_delete.config(state="normal")
                self.btn_add.config(state="normal")
                self.btn_import.config(state="normal")
                self.diff_tree.delete(*self.diff_tree.get_children())
                try:
                    changes = future.result()
                    self.btn_build.config(state="normal" if changes else "disabled")
                    if self.active_project:
                        cache_file = os.path.join(
                            self.active_project, ".gmos_scan_cache.json"
                        )
                        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        try:
                            with open(cache_file, "w", encoding="utf-8") as f:
                                json.dump(
                                    {"last_scanned": now_str, "changes": changes},
                                    f,
                                    indent=2,
                                )
                            self.diff_tree.insert(
                                "", "end", text=f"Last Scanned: {now_str}"
                            )
                        except Exception:
                            pass
                    self._populate_diff_tree(changes)
                except Exception as e:
                    self.diff_tree.insert(
                        "", "end", text=f"Error scanning changes: {e}"
                    )
                    self.btn_build.config(state="disabled")

            self.after(0, _update_ui)

        get_io_executor().submit(_scan_task).add_done_callback(_on_scan_done)

    def _build_mod(self) -> None:
        if not self.bridge or not self.active_project:
            return
        choice = messagebox.askyesnocancel(
            "Export Format",
            "How would you like to build this mod?\n\n"
            "Yes: GMOS Mod Folder (Manifest + Smart Patches)\n"
            "No: Native Godot PCK Archive (Destructive overwrites)",
            parent=self,
        )
        if choice is None:
            return

        build_as_gmos = choice
        out_dir = ""

        if not build_as_gmos:
            out_dir = filedialog.askdirectory(
                title="Select Output Folder for Mod", parent=self
            )
            if not out_dir:
                return
        self.build_dlg = ProgressDialog(self, title="Building Mod Package")
        self.build_dlg.set_text("Scanning workspace and generating patch draft...")
        self.build_dlg.start()

        self._busy = True
        self.btn_scan.config(state="disabled")
        self.btn_build.config(state="disabled")
        self.project_listbox.config(state="disabled")
        self.btn_delete.config(state="disabled")
        self.btn_add.config(state="disabled")
        self.btn_import.config(state="disabled")
        self.update_idletasks()
        bridge = self.bridge
        mod_name = os.path.basename(self.active_project)
        active_proj = self.active_project

        def _build_task() -> Any:
            if build_as_gmos:
                return ("draft", bridge.build_patch_draft(mod_name))
            else:
                changes = bridge.scan_for_changes()
                if not changes:
                    return ("pck", "NO_CHANGES")
                files_to_pack: Dict[str, Union[str, Path]] = {}
                for res_path in changes.keys():
                    rel_os = res_path.replace("res://", "").replace("/", os.sep)
                    local_path = os.path.join(active_proj, rel_os)
                    files_to_pack[res_path] = local_path
                out_pck = os.path.join(out_dir, f"{mod_name}.pck")
                pack_pck(out_pck, files_to_pack)
                return ("pck", out_pck)

        def _on_build_done(future: Any) -> None:
            def _update_ui() -> None:
                self.config(cursor="")
                if hasattr(self, "build_dlg"):
                    self.build_dlg.close()
                self._busy = False
                self.btn_scan.config(state="normal")
                self.btn_build.config(state="normal")
                self.project_listbox.config(state="normal")
                self.btn_delete.config(state="normal")
                self.btn_add.config(state="normal")
                self.btn_import.config(state="normal")
                try:
                    task_type, result = future.result()
                    if task_type == "draft":
                        if not result.changed_res:
                            messagebox.showwarning(
                                "No Changes",
                                "No modified files detected. Mod was not built.",
                                parent=self,
                            )
                        else:
                            BuildPreviewDialog(
                                cast(tk.Misc, self), bridge, result, mod_name
                            )
                    elif task_type == "pck":
                        if result == "NO_CHANGES":
                            messagebox.showwarning(
                                "No Changes",
                                "No modified files detected. Mod was not built.",
                                parent=self,
                            )
                        else:
                            messagebox.showinfo(
                                "Success",
                                f"Mod package built successfully at:\n{result}",
                                parent=self,
                            )
                except Exception as e:
                    messagebox.showerror("Build Error", str(e), parent=self)

            self.after(0, _update_ui)

        get_io_executor().submit(_build_task).add_done_callback(_on_build_done)

    def _on_diff_file_select(self, event: Any) -> None:
        sel = self.diff_tree.selection()
        if not sel:
            return
        item_text = self.diff_tree.item(sel[0], "text")
        if "] " not in item_text:
            return

        rel_path = item_text.split("] ", 1)[1]
        tags = self.diff_tree.item(sel[0], "tags")
        state_tag = tags[0] if tags else "patched"
        rel_os_path = rel_path.replace("res://", "").replace("/", os.sep)

        vanilla_path = os.path.join(self.vanilla_cache_dir, rel_os_path)
        workspace_path = (
            os.path.join(self.active_project, rel_os_path)
            if self.active_project
            else ""
        )

        self.diff_text.config(state="normal")
        self.diff_text.delete("1.0", "end")
        self.patch_tree.delete(*self.patch_tree.get_children())
        # Setup font measurement for patch tree width
        from tkinter import font as tkfont

        tree_font = ttk.Style().lookup("Treeview", "font") or "TkDefaultFont"
        try:
            font_obj = (
                tkfont.nametofont(tree_font)
                if isinstance(tree_font, str)
                else tkfont.Font()
            )
            max_width = font_obj.measure("Instruction") + 20
        except Exception:
            font_obj = None
            max_width = 100

        def _insert_patch_raw(text: str) -> None:
            nonlocal max_width
            self.patch_tree.insert("", "end", values=(text,))
            if font_obj:
                w = font_obj.measure(text) + 20
                if w > max_width:
                    max_width = w
            self.patch_tree.column("type", minwidth=max_width)

        if state_tag == "added":
            self.diff_text.insert(
                "1.0",
                f"File ADDED: {rel_path}\n(No vanilla baseline exists to diff against)",
            )
            self.diff_text.config(state="disabled")
            _insert_patch_raw(f"Bundled Asset: {os.path.basename(rel_path)}")
            return
        elif state_tag == "replaced":
            self.diff_text.insert(
                "1.0",
                f"File REPLACED: {rel_path}\n(Entirely overwritten, likely a binary or non-text asset)",
            )
            self.diff_text.config(state="disabled")
            _insert_patch_raw(f"FR / BP: {os.path.basename(rel_path)}")
            return
        try:

            v_lines: List[str] = []
            if os.path.exists(vanilla_path):
                with open(vanilla_path, "r", encoding="utf-8", errors="ignore") as f:
                    v_lines = f.readlines()
            w_lines: List[str] = []
            if os.path.exists(workspace_path):
                with open(workspace_path, "r", encoding="utf-8", errors="ignore") as f:
                    w_lines = f.readlines()

            diff = list(
                difflib.unified_diff(
                    v_lines, w_lines, fromfile="Vanilla", tofile="Workspace", n=3
                )
            )
            if not diff:
                self.diff_text.insert(
                    "1.0", "Files are identical or differ only in binary content."
                )
            else:
                for line in diff:
                    tag = (
                        "line_num"
                        if line.startswith(("+++", "---", "@@"))
                        else (
                            "add"
                            if line.startswith("+")
                            else "rem" if line.startswith("-") else None
                        )
                    )
                    self.diff_text.insert("end", line, (tag,) if tag else ())
        except Exception as e:
            self.diff_text.insert("1.0", f"Error generating diff: {e}")

        self.diff_text.config(state="disabled")
        # Populate patch tree preview for GDScript files
        if state_tag == "patched" and hasattr(self, "bridge") and self.bridge:
            try:

                short_types = {
                    "VariablePatch": "VP",
                    "FunctionPatch": "FP",
                    "SmartPatch": "SP",
                    "FileReplace": "FR",
                    "BinaryPatch": "BP",
                }

                def _insert_patch(p_type: str, t_name: str, mode: str) -> None:
                    s_type = short_types.get(p_type, p_type)
                    text = (
                        f"{s_type}: {t_name} ({mode})"
                        if mode
                        else f"{s_type}: {t_name}"
                    )
                    _insert_patch_raw(text)

                var_patch = self.bridge.try_detect_variable_change(
                    None, rel_path, workspace_path
                )
                if var_patch:
                    target_info = var_patch.split("=", 1)[0].strip()
                    target_name = (
                        target_info.split("::")[-1]
                        if "::" in target_info
                        else target_info
                    )
                    mode_info = (
                        var_patch.split(";")[-1].strip()
                        if ";" in var_patch
                        else "replace"
                    )
                    _insert_patch("VariablePatch", target_name, mode_info)
                else:
                    patches, _ = self.bridge.try_detect_code_patch(
                        None, rel_path, workspace_path
                    )
                    for p_type, inst_list in patches.items():
                        for inst in inst_list:
                            target_info = inst.split("=", 1)[0].strip()
                            target_name = (
                                target_info.split("::")[-1]
                                if "::" in target_info
                                else target_info
                            )
                            mode_info = (
                                inst.split(";")[-1].strip() if ";" in inst else ""
                            )
                            _insert_patch(p_type, target_name, mode_info)
                    if not patches.get("FunctionPatch") and not patches.get(
                        "SmartPatch"
                    ):
                        _insert_patch_raw(f"FR / BP: {os.path.basename(rel_path)}")
            except Exception:
                pass

    def _on_close(self) -> None:
        if getattr(self, "_busy", False):
            if not messagebox.askyesno(
                "Operation in Progress",
                "The SDK is currently processing data in the background.\nClosing now may cause errors or crash the application. Force close anyway?",
                parent=self,
            ):
                return
        self.destroy()


class BuildPreviewDialog(tk.Toplevel):
    def __init__(
        self, parent: tk.Misc, bridge: GodotBridge, draft: Any, default_mod_name: str
    ):
        super().__init__(parent)
        self.bridge = bridge
        self.draft = draft
        self.title("Build Mod Package Preview")
        load_and_apply_app_icon_to_toplevel(self)
        setup_child_window(self, parent, width=1050, height=700, modal=True)
        self.bind("<<ThemeChanged>>", lambda e: apply_window_theme(self))

        self.var_name = tk.StringVar(value=default_mod_name)
        self.var_version = tk.StringVar(value="1.0.0")
        self.var_author = tk.StringVar(value="Modder")
        self.var_desc = tk.StringVar(value="")

        for var in (self.var_name, self.var_version, self.var_author, self.var_desc):
            var.trace_add("write", self._on_var_change)

        self.tree_data: Dict[str, Any] = {}
        self.folder_nodes: Dict[str, str] = {}
        self.ico_folder = load_icon("folder.png", size=(16, 16))
        self.ico_file = load_icon("file-code.png", size=(16, 16))
        self._setup_ui()
        self.populate_tree()
        self.update_preview()

    def _on_var_change(self, *args: Any) -> None:
        self.update_preview()

    def _setup_ui(self) -> None:
        top_frm = ttk.Frame(self, padding=10)
        top_frm.pack(fill="x")

        ttk.Label(top_frm, text="Mod Name:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(top_frm, textvariable=self.var_name, width=30).grid(
            row=0, column=1, padx=5, pady=2, sticky="w"
        )

        ttk.Label(top_frm, text="Version:").grid(
            row=0, column=2, sticky="w", pady=2, padx=(15, 0)
        )
        ttk.Entry(top_frm, textvariable=self.var_version, width=15).grid(
            row=0, column=3, padx=5, pady=2, sticky="w"
        )

        ttk.Label(top_frm, text="Author:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(top_frm, textvariable=self.var_author, width=30).grid(
            row=1, column=1, padx=5, pady=2, sticky="w"
        )

        ttk.Label(top_frm, text="Description:").grid(
            row=1, column=2, sticky="w", pady=2, padx=(15, 0)
        )
        ttk.Entry(top_frm, textvariable=self.var_desc, width=40).grid(
            row=1, column=3, padx=5, pady=2, sticky="w"
        )

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=5)

        left_split = ttk.PanedWindow(paned, orient="vertical")
        cast(Any, paned).add(left_split, weight=2)

        tree_frm = ttk.Frame(left_split)
        cast(Any, left_split).add(tree_frm, weight=4)
        ttk.Label(
            tree_frm,
            text="Mod Folder Structure (Double-click to rename/move)",
            font=("", 9, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        self.tree = ttk.Treeview(tree_frm, show="tree", selectmode="browse")

        vsb = AutoScrollbar(
            tree_frm, orient="vertical", command=cast(Any, self.tree).yview
        )
        hsb = AutoScrollbar(
            tree_frm, orient="horizontal", command=cast(Any, self.tree).xview
        )
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        prop_frm = ttk.LabelFrame(left_split, text="File Properties", padding=10)
        cast(Any, left_split).add(prop_frm, weight=1)
        prop_frm.columnconfigure(1, weight=1)

        ttk.Label(prop_frm, text="Target Resource:", font=("", 9, "bold")).grid(
            row=0, column=0, sticky="w", pady=2
        )
        self.lbl_target = ttk.Label(prop_frm, text="-")
        self.lbl_target.grid(row=0, column=1, sticky="ew", pady=2)

        ttk.Label(prop_frm, text="Patch Type:", font=("", 9, "bold")).grid(
            row=1, column=0, sticky="w", pady=2
        )
        self.lbl_type = ttk.Label(prop_frm, text="-", anchor="center")
        self.lbl_type.grid(row=1, column=1, sticky="ew", pady=2)

        ttk.Label(prop_frm, text="Metadata:", font=("", 9, "bold")).grid(
            row=2, column=0, sticky="w", pady=2
        )
        self.lbl_meta = ttk.Label(prop_frm, text="-", anchor="center")
        self.lbl_meta.grid(row=2, column=1, sticky="ew", pady=2)
        preview_frm = ttk.Frame(paned)
        cast(Any, paned).add(preview_frm, weight=1)
        ttk.Label(preview_frm, text="mod.mos Preview", font=("", 9, "bold")).pack(
            anchor="w", pady=(0, 5)
        )

        self.preview_text = tk.Text(
            preview_frm, wrap="none", font=("Consolas", 9), state="disabled"
        )
        pt_vsb = AutoScrollbar(
            preview_frm, orient="vertical", command=cast(Any, self.preview_text).yview
        )
        pt_hsb = AutoScrollbar(
            preview_frm, orient="horizontal", command=cast(Any, self.preview_text).xview
        )
        self.preview_text.configure(
            yscrollcommand=pt_vsb.set, xscrollcommand=pt_hsb.set
        )
        pt_vsb.pack(side="right", fill="y")
        pt_hsb.pack(side="bottom", fill="x")
        self.preview_text.pack(fill="both", expand=True)

        btn_frm = ttk.Frame(self, padding=10)
        btn_frm.pack(fill="x")
        ttk.Button(btn_frm, text="Cancel", command=self.destroy).pack(side="right")
        try:
            ttk.Button(
                btn_frm,
                text="Export Mod",
                style="success.TButton",
                command=self._export,
            ).pack(side="right", padx=10)
        except Exception:
            ttk.Button(btn_frm, text="Export Mod", command=self._export).pack(
                side="right", padx=10
            )

    def _parse_inst(self, inst: str) -> Tuple[str, str, str, str]:
        if "=" not in inst:
            return inst, "", "", ""
        target, rest = inst.split("=", 1)
        if ";" in rest:
            source_spec, meta = rest.split(";", 1)
        else:
            source_spec, meta = rest, ""
        source_spec = source_spec.strip()
        if "::" in source_spec:
            fname, src_func = source_spec.split("::", 1)
        else:
            fname, src_func = source_spec, ""
        return target.strip(), fname.strip(), src_func.strip(), meta.strip()

    def _on_tree_select(self, event: Any) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        data = self.tree_data.get(iid)
        if data:
            self.lbl_target.config(text=data.get("target", "-"))
            self.lbl_type.config(text=data.get("type", "-"))
            self.lbl_meta.config(text=data.get("meta", "-") or "-")
        else:
            self.lbl_target.config(text="-")
            self.lbl_type.config(text="Folder")
            self.lbl_meta.config(text="-")

    def populate_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.tree_data.clear()
        self.folder_nodes.clear()

        def get_folder_node(folder_path: str) -> str:
            if not folder_path or folder_path == ".":
                return ""
            if folder_path in self.folder_nodes:
                return self.folder_nodes[folder_path]
            parent, name = os.path.split(folder_path)
            parent_iid = get_folder_node(parent)
            iid = self.tree.insert(
                parent_iid,
                "end",
                text=f" {name}",
                open=True,
                image=self.ico_folder or "",
            )
            self.folder_nodes[folder_path] = iid
            return iid

        mos_iid = self.tree.insert(
            "", "end", text=" mod.mos", image=self.ico_file or ""
        )
        self.tree_data[mos_iid] = {
            "type": "Manifest",
            "target": "N/A",
            "meta": "-",
            "path": "mod.mos",
            "is_inst": False,
        }

        tracked_vfs: Set[str] = set()

        def _add(cat: str, lst: List[str]) -> None:
            for i, inst in enumerate(lst):
                t, f, src_f, m = self._parse_inst(inst)
                folder_path, file_name = os.path.split(f)
                parent_iid = get_folder_node(folder_path)
                iid = self.tree.insert(
                    parent_iid, "end", text=f" {file_name}", image=self.ico_file or ""
                )
                self.tree_data[iid] = {
                    "type": cat,
                    "target": t,
                    "meta": m,
                    "src_func": src_f,
                    "path": f,
                    "is_inst": True,
                    "lst": lst,
                    "idx": i,
                }
                tracked_vfs.add(f)

        _add("FileReplace", self.draft.file_replaces)
        _add("VariablePatch", self.draft.variable_patches)
        _add("FunctionPatch", self.draft.function_patches)
        _add("SmartPatch", self.draft.smart_patches)
        _add("BinaryPatch", self.draft.binary_patches)
        for vfs_path in self.draft.vfs.keys():
            if vfs_path not in tracked_vfs:
                folder_path, file_name = os.path.split(vfs_path)
                parent_iid = get_folder_node(folder_path)
                iid = self.tree.insert(
                    parent_iid, "end", text=f" {file_name}", image=self.ico_file or ""
                )
                self.tree_data[iid] = {
                    "type": "Bundled Asset",
                    "target": "N/A",
                    "meta": "-",
                    "path": vfs_path,
                    "is_inst": False,
                }

    def update_preview(self) -> None:
        lines = [
            f"# Generated by GMOS SDK for {self.var_name.get()}",
            "[ModInfo]",
            f"Name = {self.var_name.get()}",
            f"Version = {self.var_version.get()}",
            f"Author = {self.var_author.get()}",
        ]
        if self.var_desc.get():
            lines.append(f"Description = {self.var_desc.get()}")
        lines.append("")
        if self.draft.file_replaces:
            lines.append("[FileReplace]")
            lines.extend(self.draft.file_replaces)
            lines.append("")
        if self.draft.variable_patches:
            lines.append("[VariablePatch]")
            lines.extend(self.draft.variable_patches)
            lines.append("")
        if self.draft.function_patches:
            lines.append("[FunctionPatch]")
            lines.extend(self.draft.function_patches)
            lines.append("")
        if self.draft.smart_patches:
            lines.append("[SmartPatch]")
            lines.extend(self.draft.smart_patches)
            lines.append("")
        if self.draft.binary_patches:
            lines.append("[BinaryPatch]")
            lines.extend(self.draft.binary_patches)
            lines.append("")

        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", "\n".join(lines))
        self.preview_text.config(state="disabled")

    def _validate_path(self, p: str) -> bool:
        if not p or ".." in p or p.startswith("/") or p.startswith("\\"):
            return False
        invalid_chars = set('*?"<>|:')
        return not any(c in invalid_chars for c in p)

    def _on_double_click(self, event: Any) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid in self.tree_data:
            data = self.tree_data[iid]
            old_fname = data["path"]

            if old_fname == "mod.mos":
                messagebox.showinfo(
                    "Protected File", "mod.mos is automatically generated.", parent=self
                )
                return

            def _save_file(new_fname: str) -> None:
                new_fname = new_fname.strip().replace("\\", "/")
                if new_fname == old_fname:
                    return
                if not self._validate_path(new_fname):
                    messagebox.showerror(
                        "Invalid Path",
                        "The path contains invalid characters or traversal (..).",
                        parent=self,
                    )
                    return
                if new_fname in self.draft.vfs and new_fname != old_fname:
                    messagebox.showerror(
                        "Collision",
                        f"A file already exists at '{new_fname}'.",
                        parent=self,
                    )
                    return

                if old_fname in self.draft.vfs:
                    self.draft.vfs[new_fname] = self.draft.vfs.pop(old_fname)

                if data["is_inst"]:
                    lst = data["lst"]
                    idx = data["idx"]
                    target = data["target"]
                    src_func = data["src_func"]
                    meta = data["meta"]
                    new_inst = f"{target} = {new_fname}"
                    if src_func:
                        new_inst += f"::{src_func}"
                    if meta:
                        new_inst += f" ; {meta}"
                    lst[idx] = new_inst
                self.populate_tree()
                self.update_preview()

            NameInputDialog(
                self,
                title="Change File Path",
                prompt="New Path (e.g. folder/file.gd):",
                callback=_save_file,
                default_name=old_fname,
                action_text="Save",
            )
        else:
            old_folder = ""
            for path, n_iid in self.folder_nodes.items():
                if n_iid == iid:
                    old_folder = path
                    break
            if not old_folder:
                return

            def _save_folder(new_folder: str) -> None:
                new_folder = new_folder.strip().replace("\\", "/").rstrip("/")
                if new_folder == old_folder:
                    return
                if not self._validate_path(new_folder):
                    messagebox.showerror(
                        "Invalid Path",
                        "The path contains invalid characters or traversal (..).",
                        parent=self,
                    )
                    return

                new_vfs = {}
                for k, v in self.draft.vfs.items():
                    if k.startswith(old_folder + "/"):
                        new_k = new_folder + "/" + k[len(old_folder) + 1 :]
                        new_vfs[new_k] = v
                    else:
                        new_vfs[k] = v
                self.draft.vfs = new_vfs

                def _update_list(lst: List[str]) -> None:
                    for i in range(len(lst)):
                        t, f, src_f, m = self._parse_inst(lst[i])
                        if f.startswith(old_folder + "/"):
                            new_f = new_folder + "/" + f[len(old_folder) + 1 :]
                            new_inst = f"{t} = {new_f}"
                            if src_f:
                                new_inst += f"::{src_f}"
                            if m:
                                new_inst += f" ; {m}"
                            lst[i] = new_inst

                _update_list(self.draft.file_replaces)
                _update_list(self.draft.variable_patches)
                _update_list(self.draft.function_patches)
                _update_list(self.draft.smart_patches)
                _update_list(self.draft.binary_patches)

                self.populate_tree()
                self.update_preview()

            NameInputDialog(
                self,
                title="Rename/Move Folder",
                prompt="New Folder Path:",
                callback=_save_folder,
                default_name=old_folder,
                action_text="Save",
            )

    def _export(self) -> None:
        out_dir = filedialog.askdirectory(title="Select Output Folder", parent=self)
        if not out_dir:
            return
        mod_folder = os.path.join(out_dir, self.var_name.get())
        try:
            self.bridge.commit_mod_patch(
                mod_folder,
                self.var_name.get(),
                self.var_author.get(),
                self.var_version.get(),
                self.var_desc.get(),
                self.draft,
            )
            messagebox.showinfo(
                "Success", f"Mod exported to:\n{mod_folder}", parent=self.master
            )
            self.destroy()
        except Exception as e:
            messagebox.showerror("Export Error", str(e), parent=self)
