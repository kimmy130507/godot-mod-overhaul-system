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
import json
import os
import tkinter as tk
import uuid
import webbrowser
from dataclasses import asdict
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, cast

from gmos import utils
from gmos.core.godot_project import validate_game_folder
from gmos.core.injection import SandboxInjector
from gmos.io import atomic_replace, safe_rmtree
from gmos.io.cache import detect_godot_version
from gmos.io.pck import get_main_pck_path
from gmos.state.config import (
    INSTANCE_CONFIG_FILENAME,
    InstanceConfig,
    InstanceMetadata,
    load_instance_config_dict,
    save_global_config,
    save_instance_config_dict,
)
from gmos.ui.widgets import AutoScrollbar, ToolTip
from gmos.utils import (
    apply_window_theme,
    extract_icon_from_exe,
    get_adaptive_color_variant,
    get_dynamic_text_color,
    load_icon,
    safe_norm,
)

try:
    from PIL import Image as _Image
    from PIL import ImageTk as _ImageTk

    _img_val = _Image
    _imgtk_val = _ImageTk
except ImportError:
    _img_val = cast(Any, None)
    _imgtk_val = cast(Any, None)

Image = _img_val
ImageTk = _imgtk_val


if TYPE_CHECKING:
    from gmos.ui.app import App


class InstanceManager(tk.Toplevel):
    """Advanced Instance Manager with icon support."""

    _instance: Optional["InstanceManager"] = None

    @classmethod
    def create_or_show(cls, parent: tk.Widget, app: "App") -> None:
        """Brings existing window to front or creates a new one."""
        if cls._instance and cls._instance.winfo_exists():
            try:
                cast(Any, cls._instance).lift()
                cls._instance.focus_force()
                return
            except Exception:
                cls._instance = None

        # Create new instance
        win = cls(parent, app)

        # Center the window
        win.update_idletasks()
        w, h = win.winfo_width(), win.winfo_height()
        # Fallback dimensions if unmapped
        if w < 100:
            w = 900
        if h < 100:
            h = 550

        cast(Any, win).lift()
        win.focus_force()

    def __init__(self, parent: tk.Widget, app: "App"):
        super().__init__(parent)
        InstanceManager._instance = self
        self.app = app
        self.global_cfg = self.app.global_cfg
        self.title("Instance Manager")
        self.protocol("WM_DELETE_WINDOW", self.close_window)
        self.bind("<<ThemeChanged>>", self._on_theme_change)

        utils.setup_child_window(self, parent, width=900, height=550, modal=True)
        utils.load_and_apply_app_icon_to_toplevel(self)

        self.current_uid: Optional[str] = None
        self.icon_cache: Dict[str, Any] = {}  # Keep references to PhotoImages

        self.var_custom_name = tk.StringVar()
        self.search_var = tk.StringVar()
        self.edit_vars: Dict[str, Any] = {
            "game_dir": tk.StringVar(),
            "mods_dir": tk.StringVar(),
            "mod_website": tk.StringVar(),
            "game_executable": tk.StringVar(),
            "is_packed": tk.BooleanVar(value=False),
        }

        self._setup_ui()
        self._refresh_list()

    def _on_theme_change(self, event: Optional["tk.Event[Any]"] = None) -> None:
        self._apply_search_style()
        apply_window_theme(self)

    def close_window(self) -> None:
        InstanceManager._instance = None
        self.destroy()

    def _setup_ui(self) -> None:
        main_layout = ttk.Frame(self, padding=10)
        main_layout.pack(fill="both", expand=True)

        paned = ttk.PanedWindow(main_layout, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left_pane = ttk.Frame(paned, width=220)
        cast(Any, paned).add(left_pane, weight=1)

        # Toolbar
        top_bar = ttk.Frame(left_pane, padding=(0, 0, 0, 5))
        top_bar.pack(fill="x")
        self.ico_plus = load_icon("plus.png", size=(16, 16))
        try:
            self.btn_add = ttk.Button(
                top_bar,
                text="Add Instance",
                image=self.ico_plus or "",
                compound="left",
                command=self._add_instance,
                style="primary.TButton",
            )
        except Exception:
            self.btn_add = ttk.Button(
                top_bar,
                text="Add Instance",
                image=self.ico_plus or "",
                compound="left",
                command=self._add_instance,
            )
        self.btn_add.pack(side="left")

        # Search Field Container
        self.search_container = tk.Frame(top_bar, highlightthickness=1)
        self.search_container.pack(
            side="left", fill="x", expand=True, padx=(8, 2), pady=1
        )

        # Search Icon
        self.ico_search = load_icon("search.png", size=(14, 14))
        self.lbl_search_icon = tk.Label(
            self.search_container,
            image=self.ico_search if self.ico_search else "",
            text="🔍" if not self.ico_search else "",
            borderwidth=0,
            relief="flat",
        )
        self.lbl_search_icon.pack(side="left", padx=(8, 6))
        # Separator
        self.search_sep = tk.Frame(self.search_container, width=1)
        self.search_sep.pack(side="left", fill="y")
        # Entry
        self.search_entry = tk.Entry(
            self.search_container,
            textvariable=self.search_var,
            font=("Segoe UI", 10),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(5, 0), ipady=4)
        self.search_entry.bind("<KeyRelease>", lambda e: self._refresh_list())

        # Apply Styles
        self._apply_search_style()

        list_container = ttk.Frame(left_pane)
        list_container.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            list_container, show="tree", selectmode="browse", style="Instance.Treeview"
        )
        self.tree.column("#0", width=300, anchor="w")

        # Tags for active instance
        self.tree.tag_configure("active_row", font=("Segoe UI", 9, "bold"))
        self.tree.tag_configure("normal_row", font=("Segoe UI", 9))

        sb = AutoScrollbar(
            list_container, orient="vertical", command=cast(Any, self.tree).yview
        )
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Context Menu
        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.tree.bind("<Button-3>", self._show_context_menu)

        # Details
        right_pane = ttk.Frame(paned, padding=(15, 0, 0, 0))
        cast(Any, paned).add(right_pane, weight=3)

        # Header
        header_frame = ttk.Frame(right_pane)
        header_frame.pack(fill="x", pady=(10, 20))
        # Load Icons
        self.ico_shield_ok = load_icon("shield-check.png", size=(20, 20))
        self.ico_shield_err = load_icon("shield-x.png", size=(20, 20))

        action_col = ttk.Frame(header_frame)
        action_col.pack(side="right", anchor="n")
        self.status_container = ttk.Frame(action_col)
        self.status_container.pack(side="top", anchor="center", pady=(0, 5))
        self.ico_activate = load_icon("check.png", size=(16, 16))
        self.ico_active_label = load_icon("square-check.png", size=(18, 18))
        self.btn_activate = cast(Any, ttk.Button)(
            self.status_container,
            text=" Activate",
            image=self.ico_activate or "",
            compound="left",
            bootstyle="success",
            command=self._switch_instance,
        )

        self.lbl_active = cast(Any, ttk.Label)(
            self.status_container,
            text=" Active",
            image=self.ico_active_label or "",
            compound="left",
            bootstyle="success",
            font=("Segoe UI", 10, "bold"),
        )

        tools_row = ttk.Frame(action_col)
        tools_row.pack(side="top", anchor="center")

        self.header_container = ttk.Frame(header_frame)
        self.header_container.pack(side="left", fill="both", expand=True, anchor="n")
        # Title Row
        self.title_row = ttk.Frame(self.header_container)
        self.title_row.pack(fill="x", anchor="w")

        # Icon
        self.lbl_sandbox_status = ttk.Label(self.title_row, text="", cursor="hand2")
        self.lbl_sandbox_status.pack(side="left", padx=(0, 5), pady=(5, 0), anchor="n")
        self.lbl_sandbox_status.bind("<Button-1>", self._toggle_instance_sandbox)
        self.sandbox_tooltip = ToolTip(self.lbl_sandbox_status, "Sandbox Status")
        # Name
        self.lbl_header_name = ttk.Label(
            self.title_row,
            textvariable=self.var_custom_name,
            font=("Segoe UI", 16, "bold"),
            cursor="hand2",
        )
        self.lbl_header_name.pack(side="left", fill="x", anchor="w")
        self.lbl_header_name.bind("<Button-1>", self._enable_header_edit)

        self.ent_header_name = ttk.Entry(
            self.title_row,
            textvariable=self.var_custom_name,
            font=("Segoe UI", 16, "bold"),
        )
        self.ent_header_name.bind("<Return>", self._finish_header_edit)
        self.ent_header_name.bind("<FocusOut>", self._finish_header_edit)

        # Subtitle

        style = ttk.Style()
        bg = style.lookup("TFrame", "background")
        sub_color = get_adaptive_color_variant(str(bg), "#aaaaaa", "#555555")

        self.lbl_header_sub = ttk.Label(
            self.header_container,
            text="Select an instance to edit",
            foreground=sub_color,
            font=("Segoe UI", 9),
        )
        self.lbl_header_sub.pack(anchor="w", pady=(2, 0))
        # Metadata Row
        self.lbl_header_stats = ttk.Label(
            self.header_container, text="", font=("Segoe UI", 8), foreground="gray"
        )
        self.lbl_header_stats.pack(anchor="w", pady=(2, 0))

        row2 = ttk.Frame(right_pane)
        row2.pack(fill="x", pady=(0, 15))

        self.ico_trash = load_icon("trash-2.png", size=(18, 18))
        self.btn_del = cast(Any, ttk.Button)(
            tools_row,
            image=self.ico_trash or "",
            command=self._remove_instance,
            bootstyle="link-danger",
            cursor="hand2",
        )
        self.btn_del.pack(side="right", padx=(5, 0))

        self.ico_folder = load_icon("folder-open.png", size=(18, 18))
        self.btn_folder = cast(Any, ttk.Button)(
            tools_row,
            image=self.ico_folder or "",
            command=self._open_instance_folder,
            bootstyle="link-info",
            cursor="hand2",
        )
        self.btn_folder.pack(side="right")

        # Collapsible Paths Container
        self.paths_container = ttk.Frame(right_pane)
        self.paths_container.pack(fill="x", anchor="n", pady=5)

        # Toggle Button for Paths
        self.show_paths_var = tk.BooleanVar(value=False)
        self.btn_toggle_paths = ttk.Checkbutton(
            self.paths_container,
            text=" ▶ Configuration & Paths",
            variable=self.show_paths_var,
            style="Toolbutton",
            command=self._toggle_paths_visibility,
        )
        self.btn_toggle_paths.pack(fill="x", anchor="w")

        # The actual frame containing fields
        self.paths_frame = ttk.Frame(
            self.paths_container, padding=10, relief="solid", borderwidth=1
        )
        # Initially hidden, so we don't pack it here. See _toggle_paths_visibility.

        # Website Edit Field (Standardized)
        self._add_path_row(
            self.paths_frame, "Mod Source URL:", "mod_website", is_dir=False
        )

        # Paths
        self._add_path_row(self.paths_frame, "Game Directory:", "game_dir", is_dir=True)
        self._add_path_row(self.paths_frame, "Mods Directory:", "mods_dir", is_dir=True)
        self._add_path_row(
            self.paths_frame, "Game Executable:", "game_executable", is_file=True
        )
        # Deployment Mode Option
        packed_row = ttk.Frame(self.paths_frame)
        packed_row.pack(fill="x", pady=(6, 2))
        self.chk_packed = ttk.Checkbutton(
            packed_row,
            text=" Packed Deployment Mode (.pck archive)",
            variable=self.edit_vars["is_packed"],
            command=self._save_changes,
        )
        self.chk_packed.pack(side="left", padx=5)
        ToolTip(
            self.chk_packed,
            "When enabled, modified Godot assets are packed into an override .pck archive.\nWhen disabled, mods are deployed directly to disk as loose files.",
        )

    def _toggle_paths_visibility(self) -> None:
        if self.show_paths_var.get():
            self.paths_frame.pack(fill="x", expand=True, pady=(5, 0))
            self.btn_toggle_paths.configure(text=" ▼ Configuration & Paths")
        else:
            self.paths_frame.pack_forget()
            self.btn_toggle_paths.configure(text=" ▶ Configuration & Paths")

    def _apply_search_style(self) -> None:
        """Applies manual coloring to the search bar to fix ttk dark mode issues."""
        style = ttk.Style()

        # Defaults
        unified_bg = "#454545"
        border_color = "#6c757d"
        highlight_color = border_color

        # Check Theme
        has_bootstrap = hasattr(self.app, "style") and hasattr(self.app.style, "colors")

        if has_bootstrap:
            try:
                app_style = cast(Any, self.app.style)
                highlight_color = app_style.colors.primary

                input_bg = style.lookup("TEntry", "fieldbackground")

                # Check if input_bg is a valid string
                if input_bg:
                    s_bg = str(input_bg).lower()

                    if s_bg in ["#ffffff", "white", "#f8f9fa"]:
                        unified_bg = input_bg
                        border_color = "#ced4da"
                    elif s_bg not in ["black", "#000000"]:
                        unified_bg = input_bg
            except Exception:
                pass
        fg_color = get_dynamic_text_color(unified_bg)

        # Helper to toggle highlight on the container when entry is focused
        def on_focus_in(_: Any) -> None:
            self.search_container.configure(highlightbackground=highlight_color)

        def on_focus_out(_: Any) -> None:
            self.search_container.configure(highlightbackground=border_color)

        # Apply to Widgets
        self.search_container.configure(
            bg=unified_bg,
            highlightbackground=border_color,
            highlightcolor=highlight_color,
            takefocus=0,
        )
        self.lbl_search_icon.configure(bg=unified_bg, fg=fg_color)
        self.search_entry.configure(
            bg=unified_bg, fg=fg_color, insertbackground=fg_color, highlightthickness=0
        )
        self.search_sep.configure(bg=border_color)

        # Bind focus events
        self.search_entry.bind("<FocusIn>", on_focus_in, add="+")
        self.search_entry.bind("<FocusOut>", on_focus_out, add="+")

    def _add_path_row(
        self,
        parent: tk.Widget,
        label: str,
        var_key: str,
        is_dir: bool = False,
        is_file: bool = False,
    ) -> None:
        container = ttk.Frame(parent)
        container.pack(fill="x", pady=3)

        lbl = ttk.Label(
            container, text=label, font=("Segoe UI", 8), foreground="gray", width=15
        )
        lbl.pack(side="left", padx=2)
        input_box = ttk.Frame(container)
        input_box.pack(side="left", fill="x", expand=True)
        # Read-only entry
        ent = ttk.Entry(
            input_box, textvariable=self.edit_vars[var_key], state="readonly"
        )
        ent.pack(side="left", fill="x", expand=True, padx=5)

        # Auto-scroll to end on focus
        def _scroll_to_end(*_: Any) -> None:
            ent.xview_moveto(1.0)

        ent.bind("<FocusIn>", _scroll_to_end)
        self.after(100, _scroll_to_end)

        # Store widget ref
        setattr(self, f"ent_{var_key}", ent)

        cmd: Optional[Callable[[], None]] = None
        if is_dir:

            def _cmd_dir() -> None:
                self._browse_path(var_key, filedialog.askdirectory)

            cmd = _cmd_dir
        elif is_file:

            def _cmd_file() -> None:
                self._browse_path(var_key, filedialog.askopenfilename)

            cmd = _cmd_file

        if cmd:
            btn = ttk.Button(input_box, text="...", width=4, command=cmd)
            btn.pack(side="left")
            setattr(self, f"btn_{var_key}", btn)

    def _browse_path(self, key: str, func: Any) -> None:
        current_val = self.edit_vars[key].get().strip()
        initial_dir = None

        if current_val:
            if os.path.isdir(current_val):
                initial_dir = current_val
            elif os.path.isdir(os.path.dirname(current_val)):
                initial_dir = os.path.dirname(current_val)

        kwargs: Dict[str, Any] = {"parent": self}
        if initial_dir:
            kwargs["initialdir"] = initial_dir
        if "filename" in func.__name__:
            kwargs["filetypes"] = [("Executables", "*.exe"), ("All Files", "*.*")]

        res = func(**kwargs)

        cast(Any, self).lift()
        self.focus_force()

        if res:
            self.edit_vars[key].set(safe_norm(res))
            self._save_changes()

    def _enable_header_edit(self, _event: Optional["tk.Event[Any]"] = None) -> None:
        self.lbl_header_name.pack_forget()
        self.ent_header_name.pack(side="left", fill="x", expand=True)
        self.ent_header_name.focus_set()

    def _finish_header_edit(
        self, _event: Optional["tk.Event[Any]"] = None, save: bool = True
    ) -> None:
        self.ent_header_name.pack_forget()
        self.lbl_header_name.pack(side="left", fill="x", anchor="w")
        if save:
            self._save_changes()

    def _refresh_list(self) -> None:
        """Refreshes the treeview. Preserves selection if valid, otherwise selects active."""
        self.tree.delete(*self.tree.get_children())
        self.icon_cache.clear()
        query = self.search_var.get().lower().strip()

        active_id = self.global_cfg.default_instance_id

        for uid, meta in self.global_cfg.instances.items():
            display_name = meta.custom_name if meta.custom_name else meta.name
            if query and query not in display_name.lower():
                continue
            tags = ("normal_row",)
            if uid == active_id:
                tags = ("active_row",)

            # Icon logic
            icon_img = None
            conf_path = os.path.join(meta.path, "gmos_data", INSTANCE_CONFIG_FILENAME)
            exe_path = ""
            if os.path.exists(conf_path):
                try:
                    cfg = load_instance_config_dict(conf_path)
                    exe_name = cfg.get("game_executable", "game.exe")
                    exe_path = os.path.join(meta.path, exe_name)
                except Exception:
                    pass

            if exe_path and os.path.exists(exe_path) and Image and ImageTk:
                try:
                    pil = extract_icon_from_exe(exe_path)
                    if pil:
                        pil.thumbnail((20, 20), Image.Resampling.LANCZOS)
                        icon_img = ImageTk.PhotoImage(pil)
                        self.icon_cache[uid] = icon_img
                except Exception:
                    pass

            self.tree.insert(
                "",
                "end",
                iid=uid,
                text=f"  {display_name}",
                image=icon_img if icon_img else "",
                tags=tags,
            )

        # Logic to preserve selection or fallback to active
        target_id = (
            self.current_uid
            if (self.current_uid and self.tree.exists(self.current_uid))
            else active_id
        )

        if target_id and self.tree.exists(target_id):
            self.tree.selection_set(target_id)
            self.tree.see(target_id)
            # Ensure the form updates to reflect this selection
            self._on_select(None)
        else:
            self._clear_form()

    def _update_header_state(self, uid: str) -> None:
        self.btn_activate.pack_forget()
        self.lbl_active.pack_forget()

        if uid == self.global_cfg.default_instance_id:
            self.lbl_active.pack(side="right", anchor="center")
        else:
            self.btn_activate.pack(side="right", anchor="center")

    def _on_select(self, event: Optional["tk.Event[Any]"] = None) -> None:
        sel = self.tree.selection()
        if not sel:
            self._clear_form()
            return

        uid = sel[0]
        self.current_uid = uid
        meta = self.global_cfg.instances.get(uid)

        if meta:
            # Re-detect version if missing or uninitialized
            if meta.godot_version == 0:
                ver = detect_godot_version(meta.path)
                meta.godot_version = ver if ver > 0 else -1
                save_global_config(self.global_cfg)

            conf_path = os.path.join(meta.path, "gmos_data", INSTANCE_CONFIG_FILENAME)
            cfg = load_instance_config_dict(conf_path)
            is_packed = bool(cfg.get("is_packed", False))
            mode_text = "Packed" if is_packed else "Loose"
            g_ver_text = (
                str(meta.godot_version) if meta.godot_version > 0 else "Unknown"
            )

            self.var_custom_name.set(meta.custom_name or meta.name)
            self.lbl_header_sub.config(
                text=f"Original: {meta.name}  |  Godot v{g_ver_text}  |  ID: {meta.id}"
            )
            self._finish_header_edit(save=False)
            self.edit_vars["game_dir"].set(cfg.get("game_dir") or meta.path)
            self.edit_vars["mods_dir"].set(
                cfg.get("mods_dir") or os.path.join(meta.path, "mods")
            )
            self.edit_vars["mod_website"].set(cfg.get("mod_website", ""))
            self.edit_vars["is_packed"].set(is_packed)
            # Count mods
            mod_count = 0
            mods_path = str(self.edit_vars["mods_dir"].get())
            if mods_path and os.path.isdir(mods_path):
                try:
                    entries = os.listdir(mods_path)
                    mod_count = len(
                        [
                            f
                            for f in entries
                            if os.path.isdir(os.path.join(mods_path, str(f)))
                            or str(f).endswith(".pck")
                        ]
                    )
                except Exception:
                    pass
            lp = cfg.get("last_played", "")
            lp_text = f"Last Played: {lp}" if lp else "Never Played"
            self.lbl_header_stats.config(
                text=f"{lp_text}   |   Mode: {mode_text}   |   Total Mods: {mod_count}"
            )

            # Verify Sandbox Status logic

            try:
                inj = SandboxInjector(meta.path)
                is_active = inj.is_injected()
                img_val = self.ico_shield_ok if is_active else self.ico_shield_err
                self.lbl_sandbox_status.config(
                    image=img_val or "",
                    text=" Sandbox Active" if is_active else " Sandbox Off",
                )
                self.sandbox_tooltip.text = (
                    "Sandbox is INSTALLED and protecting this instance."
                    if is_active
                    else "Sandbox is NOT installed. Mod scripts run without sanitization."
                )
            except Exception:
                self.lbl_sandbox_status.config(text="Unknown Status")
                self.sandbox_tooltip.text = "Cannot determine Sandbox status."
            self.edit_vars["game_executable"].set(
                cfg.get("game_executable") or "game.exe"
            )

            self._update_header_state(uid)

            for k in ["game_dir", "mods_dir", "game_executable", "mod_website"]:
                w = getattr(self, f"ent_{k}", None)
                if w:
                    w.xview_moveto(1.0)

        self.btn_del.pack(side="right", padx=(5, 0))
        self.btn_folder.pack(side="right")
        for k in ["game_dir", "mods_dir", "game_executable", "mod_website"]:
            btn = getattr(self, f"btn_{k}", None)
            if btn:
                btn.configure(state="normal")

    def _clear_form(self) -> None:
        self.current_uid = None
        self.var_custom_name.set("")
        self.lbl_header_sub.config(text="Select an instance to edit")
        self.btn_activate.pack_forget()
        self.btn_del.pack_forget()
        self.btn_folder.pack_forget()
        self.lbl_active.pack_forget()
        for v in self.edit_vars.values():
            v.set("")
        for k in ["game_dir", "mods_dir", "game_executable", "mod_website"]:
            btn = getattr(self, f"btn_{k}", None)
            if btn:
                btn.configure(state="disabled")

    def _save_changes(self) -> None:
        if not self.current_uid:
            return
        # Capture ID because _refresh_list uses current_uid
        saved_uid = self.current_uid

        meta = self.global_cfg.instances.get(self.current_uid)
        if not meta:
            return

        meta.custom_name = self.var_custom_name.get().strip() or None
        save_global_config(self.global_cfg)

        conf_path = os.path.join(meta.path, "gmos_data", INSTANCE_CONFIG_FILENAME)
        # Load existing config
        current_cfg = load_instance_config_dict(conf_path)

        current_cfg["game_dir"] = self.edit_vars["game_dir"].get()
        current_cfg["mods_dir"] = self.edit_vars["mods_dir"].get()
        current_cfg["game_executable"] = self.edit_vars["game_executable"].get()
        current_cfg["mod_website"] = self.edit_vars["mod_website"].get()
        current_cfg["is_packed"] = bool(self.edit_vars["is_packed"].get())

        save_instance_config_dict(current_cfg, conf_path)
        if self.current_uid == self.global_cfg.default_instance_id:
            for k, v in current_cfg.items():
                if k in self.app.vars:
                    self.app.vars[k].set(v)
            self.app.cfg.update(current_cfg)

        self._refresh_list()

        # Ensure header matches logic after refresh
        if saved_uid and self.tree.exists(saved_uid):
            self._update_header_state(saved_uid)

    def _open_instance_folder(self) -> None:
        if not self.current_uid:
            return
        meta = self.global_cfg.instances.get(self.current_uid)
        if not meta or not os.path.isdir(meta.path):
            return
        path = os.path.normpath(meta.path)
        try:
            webbrowser.open(path)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n{e}")

    def _add_instance(self) -> None:
        raw_path = filedialog.askdirectory(title="Select Game Directory", parent=self)

        cast(Any, self).lift()
        self.focus_force()

        if not raw_path:
            return
        path = safe_norm(os.path.abspath(raw_path))
        # Soft Validation (Allow Bypass)
        if not validate_game_folder(path):
            if not messagebox.askyesno(
                "Validation Failed",
                "This directory lacks a project.godot or Godot binary signature.\n\nOverride and add instance anyway?",
                parent=self,
            ):
                return
        for inst in self.global_cfg.instances.values():
            if safe_norm(inst.path) == path:
                messagebox.showinfo(
                    "Duplicate", f"Instance exists: {inst.name}", parent=self
                )
                return

        uid = str(uuid.uuid4())[:8]
        name = os.path.basename(path) or "New Instance"

        data_dir = os.path.join(path, "gmos_data")
        os.makedirs(data_dir, exist_ok=True)
        local_conf_path = os.path.join(data_dir, INSTANCE_CONFIG_FILENAME)

        if not os.path.exists(local_conf_path):

            exe_name = "game.exe"
            try:
                candidates = [
                    f
                    for f in os.listdir(path)
                    if f.lower().endswith(".exe")
                    and "uninstall" not in f.lower()
                    and f != "GMOS.exe"
                ]
                if len(candidates) == 1:
                    exe_name = candidates[0]
                elif len(candidates) > 1:
                    exe_name = ""
                    messagebox.showerror(
                        "Multiple Executables",
                        f"Detected {len(candidates)} executables.\nPlease select the correct game executable manually.",
                        parent=self,
                    )
            except Exception:
                pass
            has_pck = get_main_pck_path(path) is not None
            inst_cfg = InstanceConfig(
                game_dir=path,
                mods_dir=os.path.join(path, "mods"),
                game_executable=exe_name,
                is_packed=has_pck,
            )
            atomic_replace(local_conf_path, json.dumps(asdict(inst_cfg), indent=2))

        g_ver = detect_godot_version(path)
        meta = InstanceMetadata(id=uid, name=name, path=path, godot_version=g_ver)

        self.global_cfg.instances[uid] = meta
        save_global_config(self.global_cfg)

        self._refresh_list()
        self.tree.selection_set(uid)

    def _show_context_menu(self, event: "tk.Event[Any]") -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)
        uid = item

        self.context_menu.delete(0, "end")

        keys = list(self.global_cfg.instances.keys())
        idx = keys.index(uid)

        if idx > 0:
            self.context_menu.add_command(
                label="Move Up", command=lambda: self._move_instance(uid, -1)
            )
        else:
            self.context_menu.add_command(label="Move Up", state="disabled")

        if idx < len(keys) - 1:
            self.context_menu.add_command(
                label="Move Down", command=lambda: self._move_instance(uid, 1)
            )
        else:
            self.context_menu.add_command(label="Move Down", state="disabled")

        self.context_menu.add_separator()

        if uid == self.global_cfg.default_instance_id:
            self.context_menu.add_command(label="Already Active", state="disabled")
        else:
            self.context_menu.add_command(
                label="Activate", command=self._switch_instance
            )

        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Delete Instance", command=self._remove_instance
        )
        self.context_menu.post(event.x_root, event.y_root)

    def _move_instance(self, uid: str, direction: int) -> None:
        keys = list(self.global_cfg.instances.keys())
        if uid not in keys:
            return
        idx = keys.index(uid)
        new_idx = idx + direction
        if 0 <= new_idx < len(keys):
            keys[idx], keys[new_idx] = keys[new_idx], keys[idx]
            new_instances = {k: self.global_cfg.instances[k] for k in keys}
            self.global_cfg.instances = new_instances
            save_global_config(self.global_cfg)
            self._refresh_list()
            self.tree.selection_set(uid)

    def _remove_instance(self) -> None:
        if not self.current_uid:
            return
        if self.current_uid == self.global_cfg.default_instance_id:
            messagebox.showwarning(
                "Cannot Delete",
                "The active instance cannot be removed.\nPlease switch to another instance first.",
                parent=self,
            )
            return

        meta = self.global_cfg.instances[self.current_uid]
        if messagebox.askyesno(
            "Confirm",
            f"Remove '{meta.name}' from list?",
            parent=self,
        ):
            if messagebox.askyesno(
                "Cleanup",
                "Do you want to clean up and remove GMOS files from this game directory?\n\nThis restores vanilla files and deletes 'mods', 'gmos_data', 'profiles', and the Sandbox Autoload.",
                parent=self,
            ):
                # 1. Restore all vanilla files from .bak
                for root, _dirs, files in os.walk(meta.path):
                    for f in files:
                        if f.endswith(".bak"):
                            bak_path = os.path.join(root, f)
                            orig_path = bak_path[:-4]
                            try:
                                if os.path.exists(orig_path):
                                    os.remove(orig_path)
                                os.rename(bak_path, orig_path)
                            except Exception:
                                pass

                # 2. Remove Sandbox Autoload
                try:
                    SandboxInjector(meta.path).remove()
                except Exception:
                    pass

                # 3. Delete GMOS directories
                try:
                    for folder in ["gmos_data", "mods", "profiles"]:
                        fpath = os.path.join(meta.path, folder)
                        if os.path.exists(fpath):
                            safe_rmtree(fpath)
                except Exception:
                    pass

                # 4. Delete GMOS injected root files
                try:
                    for f in [
                        "gmos_sandbox.gd",
                        "gmos_sandbox.tscn",
                        "gmos_override.pck",
                    ]:
                        fpath = os.path.join(meta.path, f)
                        if os.path.exists(fpath):
                            os.remove(fpath)
                except Exception:
                    pass

            del self.global_cfg.instances[self.current_uid]
            save_global_config(self.global_cfg)
            self._refresh_list()

    def _switch_instance(self) -> None:
        if not self.current_uid:
            return
        self._save_changes()
        meta = self.global_cfg.instances.get(self.current_uid)
        if meta:
            self.global_cfg.default_instance_id = self.current_uid
            save_global_config(self.global_cfg)
            self.app.switch_instance(meta.path)
            self.app.show_toast(
                f"Activated: {meta.custom_name or meta.name}", kind="success"
            )
            self._refresh_list()

    def _toggle_instance_sandbox(self, event: Optional["tk.Event[Any]"] = None) -> None:
        if not self.current_uid:
            return
        meta = self.global_cfg.instances.get(self.current_uid)
        if not meta:
            return

        try:
            inj = SandboxInjector(meta.path)
            if inj.is_injected():
                if inj.remove() and hasattr(self.app, "show_toast"):
                    self.app.show_toast(
                        f"Sandbox removed from {meta.name}", kind="info"
                    )
            else:
                if inj.inject() and hasattr(self.app, "show_toast"):
                    self.app.show_toast(
                        f"Sandbox injected into {meta.name}", kind="success"
                    )

            # Refresh the UI header to reflect the new state
            self._on_select(None)
        except Exception as e:
            messagebox.showerror(
                "Sandbox Error", f"Failed to toggle sandbox:\n{e}", parent=self
            )
