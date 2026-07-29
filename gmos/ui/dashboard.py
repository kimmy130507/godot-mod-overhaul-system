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
import os
import tkinter as tk
import webbrowser
import zipfile
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from gmos import utils
from gmos.core.patcher import apply_dependency_resolution
from gmos.state import policy
from gmos.ui.logs import LogView
from gmos.ui.widgets import (
    AutoScrollbar,
    EditExecutablesDialog,
    ToolTip,
    TreeHoverTip,
    UIModConfig,
    rebuild_mod_tree,
)
from gmos.utils import (
    get_adaptive_color_variant,
    get_dynamic_text_color,
    get_mod_name_from_config,
    logger,
)

try:
    from PIL import ImageTk as _ImageTk

    _dash_imgtk = _ImageTk
except ImportError:
    _dash_imgtk = cast(Any, None)

ImageTk = _dash_imgtk
Image = cast(Any, None)
if TYPE_CHECKING:
    from gmos.ui.app import App
    from gmos.utils import ModConfig


class ModInfoPane(tk.Frame):
    """
    Right-side inspector panel showing selected mod metadata and dependency errors.
    """

    def __init__(self, master: tk.Misc, width: int = 360, **kwargs: Any):
        super().__init__(master, width=width, **kwargs)
        self.app: Optional["App"] = None
        self._current_cfg: Optional[UIModConfig] = None
        self.ico_folder = utils.load_icon("folder-open.png", size=(18, 18))
        self.ico_delete = utils.load_icon("trash-2.png", size=(18, 18))
        self.tabs = ttk.Notebook(self)
        # Float close button on top of tabs
        self.close_btn = ttk.Button(
            self,
            text="✕",
            style="Link.TButton",
            width=3,
            command=self._on_close_clicked,
        )
        self.close_btn.place(relx=1.0, x=0, y=-2.45, anchor="ne")

        self.tabs.pack(in_=self, fill="both", expand=True)

        self.info_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(self.info_tab, text="Info")
        self._setup_info_tab()

        self.conflict_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(self.conflict_tab, text="Conflicts")
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#2b2b2b")
        err_fg = get_adaptive_color_variant(theme_bg, "#ff5252", "#c0392b")
        self.conflict_list = tk.Listbox(
            self.conflict_tab, bg=theme_bg, fg=err_fg, relief="flat"
        )
        self.conflict_list.pack(fill="both", expand=True)

        self.files_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(self.files_tab, text="Files")
        self.file_tree = ttk.Treeview(self.files_tab, show="tree", selectmode="browse")
        self.file_tree.pack(fill="both", expand=True)

        vsb = AutoScrollbar(
            self.files_tab, orient="vertical", command=cast(Any, self.file_tree).yview
        )
        vsb.pack(side="right", fill="y")
        self.file_tree.configure(yscrollcommand=vsb.set)
        self.bind("<<ThemeChanged>>", self._on_theme_change)

    def _setup_info_tab(self) -> None:
        # Container for content (to easily hide/show)

        self.info_content = ttk.Frame(self.info_tab)
        # Header
        self.lbl_name = ttk.Label(
            self.info_content, text="", font=("Segoe UI", 12, "bold")
        )
        self.lbl_name.pack(fill="x", anchor="w")
        # Metadata row
        self.meta_frame = ttk.Frame(self.info_content)
        self.meta_frame.pack(fill="x", anchor="w", pady=(0, 10))

        self.lbl_sub = ttk.Label(self.meta_frame, text="", foreground="gray")
        self.lbl_sub.pack(side="left")

        self.btn_delete = ttk.Button(
            self.meta_frame,
            text="",
            image=self.ico_delete or "",
            style="Link.TButton",
            width=3,
            command=self._delete_mod,
        )
        self.btn_delete.pack(side="right", padx=5)
        ToolTip(self.btn_delete, "Delete Mod")

        self.btn_folder_icon = ttk.Button(
            self.meta_frame,
            text="",
            image=self.ico_folder or "",
            style="Link.TButton",
            width=3,
            command=self._open_folder,
        )
        self.btn_folder_icon.pack(side="right", padx=0)
        ToolTip(self.btn_folder_icon, "Open Folder")
        # Description
        desc_frame = ttk.Frame(self.info_content)
        desc_frame.pack(fill="x", pady=5)
        self.txt_desc = tk.Text(
            desc_frame,
            height=12,
            wrap="word",
            relief="flat",
            bg=self.cget("bg") or "#2b2b2b",
            fg=get_dynamic_text_color(str(self.cget("bg") or "#2b2b2b")),
            state="disabled",
        )
        desc_vsb = AutoScrollbar(
            desc_frame, orient="vertical", command=cast(Any, self.txt_desc).yview
        )
        self.txt_desc.configure(yscrollcommand=desc_vsb.set)
        desc_vsb.pack(side="right", fill="y")
        self.txt_desc.pack(side="left", fill="both", expand=True)

        # Actions
        btn_frm = ttk.Frame(self.info_content)
        btn_frm.pack(fill="x", pady=10)
        self.btn_enable = ttk.Button(
            btn_frm, text="Enable", command=self._toggle_enable
        )
        self.btn_enable.pack(fill="x", expand=True)

    def _on_theme_change(self, event: Any = None) -> None:
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#2b2b2b")
        fg_color = get_dynamic_text_color(theme_bg)
        err_fg = get_adaptive_color_variant(theme_bg, "#ff5252", "#c0392b")

        self.config(bg=theme_bg)

        if hasattr(self, "conflict_list") and self.conflict_list.winfo_exists():
            self.conflict_list.config(bg=theme_bg, fg=err_fg)

        if hasattr(self, "txt_desc") and self.txt_desc.winfo_exists():
            self.txt_desc.config(bg=theme_bg, fg=fg_color)

    def _open_folder(self) -> None:
        if not self._current_cfg or "Path" not in self._current_cfg:
            return
        path = self._current_cfg["Path"]
        if not path:
            return
        try:
            p = os.fspath(path)
        except TypeError:
            return
        abs_path = os.path.abspath(p)
        if not os.path.exists(abs_path):
            return
        webbrowser.open(abs_path)

    def _toggle_enable(self) -> None:
        """Toggles enablement and restores selection after refresh."""
        cfg = self._current_cfg
        if not cfg:
            return

        # 1. Snapshot the Name
        current_mod_name = cfg.get("Name") or get_mod_name_from_config(cfg)

        cur = cfg.get("Enabled", True)
        new_state = not bool(cur)
        cfg["Enabled"] = new_state
        self.update_for_config(cfg)
        try:
            app = cast(Any, self.winfo_toplevel())
            if hasattr(app, "load_mods") and hasattr(app, "mod_configs"):
                app.load_mods(mod_configs_override=app.mod_configs)
                if hasattr(app, "update_patch_instructions"):
                    app.update_patch_instructions()
                if hasattr(app, "update_conflict_status"):
                    app.update_conflict_status()

                # 2. Restore Selection via Dashboard
                if hasattr(app, "dashboard") and app.dashboard:
                    app.dashboard.select_mod_by_name(current_mod_name)

        except Exception as e:
            logger.debug("Failed to refresh main app from inspector: %s", e)

    def _delete_mod(self) -> None:
        if not self._current_cfg or not self.app:
            return
        name = self._current_cfg.get("Name", "this mod")
        if messagebox.askyesno(
            "Delete Mod", f"Delete all files for '{name}'?\nThis cannot be undone."
        ):
            self.app.delete_mod_from_disk(self._current_cfg)

    def _on_close_clicked(self) -> None:
        top = cast("App", self.winfo_toplevel())
        if hasattr(top, "dashboard") and top.dashboard:
            top.dashboard.toggle_mod_info()

    def update_for_config(
        self, cfg: Optional[UIModConfig], app_ref: Optional["App"] = None
    ) -> None:
        self._current_cfg = cfg
        self.app = app_ref

        self.conflict_list.delete(0, tk.END)
        for i in self.file_tree.get_children():
            self.file_tree.delete(i)

        if not cfg:
            # Hide content, display nothing (blank)
            if self.info_content.winfo_ismapped():
                self.info_content.pack_forget()
            return
        # Show content
        if not self.info_content.winfo_ismapped():
            self.info_content.pack(fill="both", expand=True)
        name = cfg.get("Name", "Unknown")
        self.lbl_name.config(text=name)

        # 1. Info
        sections = cfg.get("Sections", {})
        mi = sections.get("ModInfo", {})
        if isinstance(mi, Dict):
            self.lbl_sub.config(
                text=f"v{mi.get('Version', '?')} by {mi.get('Author', '?')}"
            )
            self.txt_desc.config(state="normal")
            self.txt_desc.delete("1.0", tk.END)
            self.txt_desc.insert("1.0", mi.get("Description", "No description."))
            self.txt_desc.config(state="disabled")

        # 2. Conflicts
        if app_ref:
            conflicts = app_ref.get_conflicts_for_mod(name)
            if conflicts:
                for target, others in conflicts.items():
                    self.conflict_list.insert(
                        tk.END, f"{target}  <--  {', '.join(others)}"
                    )
            else:
                self.conflict_list.insert(tk.END, "No conflicts detected.")

        # 3. Files
        path = cfg.get("Path")
        if path and os.path.isdir(path):
            root_node = self.file_tree.insert(
                "", "end", text=os.path.basename(path), open=True
            )
            folder_nodes = {path: root_node}

            for dirpath, dirnames, filenames in os.walk(path):
                dirnames.sort()
                filenames.sort()
                parent_node = folder_nodes.get(dirpath, root_node)

                for d in dirnames:
                    full_dir = os.path.join(dirpath, d)
                    node = self.file_tree.insert(parent_node, "end", text=d, open=False)
                    folder_nodes[full_dir] = node

                for f in filenames:
                    self.file_tree.insert(parent_node, "end", text=f)

        is_enabled = cfg.get("Enabled", True)
        self.btn_enable.configure(text="Disable" if is_enabled else "Enable")


class DashboardView(ttk.Frame):
    """
    The main 'Local Mods' view.
    """

    def __init__(self, parent: tk.Widget, app: "App"):
        super().__init__(parent)
        self.app = app
        self.drag_index: int | None = None
        self.drag_item: str | None = None  # Track the visual item ID
        self._drag_col: str | None = None
        self.bottom_panel_visible = True
        self.patch_btn: ttk.Button
        self.search_var = tk.StringVar()
        self._search_timer: Optional[str] = None
        self.mod_info: Optional[ModInfoPane] = None
        self.mod_info_visible = False
        self.mod_info_toggle_btn: Optional[tk.Button] = None
        self.current_exec_icon: Optional[Any] = None
        # Programmatic Icons for Checkbox Simulation
        # (Standard 16x16 pixel data to avoid external file dependencies)
        self.icons = {
            True: tk.PhotoImage(width=16, height=16),
            False: tk.PhotoImage(width=16, height=16),
        }
        self._update_checkbox_icons()

        # Drop Indicator Line (Orange Separator)
        self.drop_indicator = tk.Frame(self, height=2, bg="#ff9800")
        self.empty_state_frame: Optional[ttk.Frame] = None
        # Bind theme change to update overlay color
        self.bind("<<ThemeChanged>>", self._on_theme_change)
        self._setup_ui()
        self._update_overlay_style()

    def _update_checkbox_icons(self) -> None:
        """Dynamically redraws the checkbox icons to match the current theme."""
        style = ttk.Style()
        bg = str(style.lookup("TFrame", "background") or "#333333")
        fg = utils.get_dynamic_text_color(bg)

        has_colors = hasattr(self.app, "style") and hasattr(self.app.style, "colors")
        accent = (
            str(cast(Any, self.app.style).colors.primary) if has_colors else "#4caf50"
        )

        self.icons[True].blank()
        self.icons[False].blank()

        # Both states get the same hollow foreground border
        for state in (True, False):
            self.icons[state].put((fg,), to=(2, 2, 14, 14))
            self.icons[state].put((bg,), to=(3, 3, 13, 13))

        # Checked state: Filled accent box inside the border
        self.icons[True].put((accent,), to=(4, 4, 12, 12))

    def _setup_ui(self) -> None:
        top_bar = self.app.menubar_frame

        ttk.Separator(top_bar, orient="vertical").pack(
            side="left", fill="y", padx=(2, 2), pady=2
        )

        # Instances
        self.btn_instances = ttk.Button(
            top_bar,
            text="",
            command=self.app.open_instance_manager,
            style="Link.TButton",
        )
        self.btn_instances.pack(side="left", padx=2)
        ToolTip(self.btn_instances, "Instance Manager")

        # Profiles
        self.btn_profiles = ttk.Button(
            top_bar,
            text="",
            command=self.app.open_profile_manager,
            style="Link.TButton",
        )
        self.btn_profiles.pack(side="left", padx=2)
        ToolTip(self.btn_profiles, "Profiles")

        # Settings
        self.btn_settings = ttk.Button(
            top_bar,
            text="",
            command=self.app.open_settings_dialog,
            style="Link.TButton",
        )
        self.btn_settings.pack(side="left", padx=2)
        ToolTip(self.btn_settings, "Settings")

        # SDK / DevTools
        self.btn_devtools = ttk.Button(
            top_bar,
            text="",
            command=self.app.open_developer_tools,
            style="Link.TButton",
        )
        self.btn_devtools.pack(side="left", padx=2)
        ToolTip(self.btn_devtools, "Developer Tools / SDK")

        # Mod Website
        self.btn_website = ttk.Button(
            top_bar,
            text="",
            command=self.app.open_current_instance_website,
            style="Link.TButton",
        )
        self.btn_website.pack(side="left", padx=2)
        ToolTip(self.btn_website, "Open Mod Source Website")
        self._setup_run_panel(parent=top_bar)

        # Main Split
        self.main_paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        self.main_paned.pack(fill="both", expand=True, pady=(5, 0))

        self.mid_h_paned = ttk.PanedWindow(self.main_paned, orient=tk.HORIZONTAL)
        cast(Any, self.main_paned).add(self.mid_h_paned, weight=3)

        self.filter_frame = ttk.Frame(self.mid_h_paned, width=200)

        self.filter_tree = ttk.Treeview(
            self.filter_frame, selectmode="browse", show="tree"
        )
        self.filter_tree.pack(fill="both", expand=True, padx=(5, 0), pady=(0, 5))
        self._init_filters()
        self.filters_visible = False
        mod_list_frame = ttk.LabelFrame(
            self.mid_h_paned,
            text="Loaded Mods (Order determines Patch Priority - Last Wins)",
            padding=0,
        )
        cast(Any, self.mid_h_paned).add(mod_list_frame, weight=3)

        self.command_bar = ttk.Frame(mod_list_frame)
        self.command_bar.pack(fill="x", pady=2, padx=2)

        self.filter_btn = ttk.Button(
            self.command_bar,
            text="",
            command=self._toggle_filters,
            style="Compact.Link.TButton",
        )
        self.filter_btn.pack(side="left", padx=(0, 10))

        self.search_container = tk.Frame(self.command_bar, highlightthickness=1)
        self.search_container.pack(
            side="left", fill="x", expand=True, padx=(0, 5), pady=1
        )

        self.search_icon_lbl = tk.Label(
            self.search_container, text="🔍", borderwidth=0, relief="flat"
        )
        self.search_icon_lbl.pack(side="left", padx=(5, 5))
        # Separator 1 (Icon | Entry)
        self.search_sep_1 = tk.Frame(self.search_container, width=1)
        self.search_sep_1.pack(side="left", fill="y")
        self.search_entry = tk.Entry(
            self.search_container,
            textvariable=self.search_var,
            font=("Segoe UI", 10),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
        )
        self.search_entry.pack(
            side="left", padx=(5, 5), fill="both", expand=True, ipady=4
        )
        self.search_entry.bind("<KeyRelease>", self.on_search_change)
        # Separator 2 (Entry | Status)
        self.search_sep_2 = tk.Frame(self.search_container, width=1)
        self.search_sep_2.pack(side="left", fill="y")
        # Conflict Status Icon (Docks in search bar)
        self.conflict_icon_lbl = tk.Label(
            self.search_container, borderwidth=0, relief="flat"
        )
        self.conflict_icon_lbl.pack(side="right", padx=(5, 5))

        # Patch - Primary Action (Separated)
        self.patch_btn = ttk.Button(
            self.command_bar,
            text="Patch",
            command=self.app.run_patcher_action,
            compound="left",
            width=8,
            style="success.Outline.TButton",
        )
        self.patch_btn.pack(side="right", padx=(5, 0))

        # Refresh
        self.btn_refresh = ttk.Button(
            self.command_bar, text="", command=self.app.load_mods, style="Link.TButton"
        )
        self.btn_refresh.pack(side="right", padx=2)
        ToolTip(self.btn_refresh, "Refresh Mod List")

        # Add Mod
        self.btn_add = ttk.Button(
            self.command_bar, text="", command=self._add_mod, style="Link.TButton"
        )
        self.btn_add.pack(side="right", padx=2)
        ToolTip(self.btn_add, "Add Mod from File")

        # Merge Studio
        self.btn_merge = ttk.Button(
            self.command_bar,
            text="",
            command=self._open_merge_studio,
            style="Link.TButton",
        )
        self.btn_merge.pack(side="right", padx=2)
        ToolTip(self.btn_merge, "Merge Studio")

        # Label is deprecated in favor of icon, keeping reference for backward compat if needed, but not packing
        self.conflict_label = tk.Label(self)

        self.cols_def: Dict[str, Dict[str, Any]] = {
            "version": {
                "text": "Version",
                "width": 60,
                "minwidth": 60,
                "anchor": "center",
            },
        }
        cols = tuple(self.cols_def.keys())
        self.display_cols_order = ["version"]
        self.mod_tree = ttk.Treeview(
            mod_list_frame,
            columns=cols,
            show="tree headings",
            selectmode="extended",
            height=10,
        )
        self.mod_tree.heading("#0", text="Name", anchor="center")
        self.mod_tree.column("#0", stretch=True, width=675, minwidth=300, anchor="w")

        for c, cdef in self.cols_def.items():
            self.mod_tree.heading(c, text=cdef["text"], anchor="center")
            self.mod_tree.column(
                c,
                stretch=True,
                width=cdef["width"],
                minwidth=cdef.get("minwidth", 60),
                anchor=cdef["anchor"],
            )

        self.visible_cols = {c: tk.BooleanVar(value=True) for c in cols}
        self._update_display_columns()

        self.header_menu = tk.Menu(self, tearoff=0)
        for c, cdef in self.cols_def.items():
            self.header_menu.add_checkbutton(
                label=cdef["text"],
                variable=self.visible_cols[c],
                command=self._update_display_columns,
            )

        vsb = AutoScrollbar(
            mod_list_frame,
            orient="vertical",
            command=cast(Any, self.mod_tree).yview,
        )
        self.mod_tree.configure(yscrollcommand=vsb.set)

        self.mod_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._update_tree_theme()
        self.listbox_tip = TreeHoverTip(self.mod_tree, self.app)
        self.mod_tree.bind("<Button-1>", self.on_mod_list_click)
        self.mod_tree.bind("<B1-Motion>", self.on_drag_motion)
        self.mod_tree.bind(
            "<ButtonRelease-1>", self.on_drag_release
        )  # Commit on release
        self.mod_tree.bind("<<TreeviewSelect>>", self.on_mod_selection_change)
        self.mod_tree.bind("<Double-1>", lambda e: self.toggle_selected_mod())
        self.mod_tree.bind("<Button-3>", self.show_context_menu)
        self.mod_tree.bind("<Button-2>", self.show_context_menu)

        # Keyboard Shortcuts
        self.mod_tree.bind("<space>", lambda e: self.toggle_selected_mod())

        def _move_up(_e: Any) -> str:
            self.move_selected_mod(-1)
            return "break"

        def _move_down(_e: Any) -> str:
            self.move_selected_mod(1)
            return "break"

        self.mod_tree.bind("<Control-Up>", _move_up)
        self.mod_tree.bind("<Control-Down>", _move_down)

        # Context Menu
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(
            label="Toggle Enabled", command=self.toggle_selected_mod
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Move Up", command=lambda: self.move_selected_mod(-1)
        )
        self.context_menu.add_command(
            label="Move Down", command=lambda: self.move_selected_mod(1)
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Open Mod Folder", command=self.open_selected_mod_folder
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Delete Mod", command=self._delete_selected_mod, foreground="red"
        )
        self.right_sidebar = ttk.Frame(self.mid_h_paned)
        cast(Any, self.mid_h_paned).add(self.right_sidebar, weight=1)

        # Mod Info (Bottom of sidebar)
        try:
            self.mod_info = ModInfoPane(self.right_sidebar)
            self.mod_info.pack(fill="both", expand=True, pady=(10, 0))
            self.mod_info_visible = True
        except Exception:
            self.mod_info = None

        self.app.log_view = LogView(self.main_paned, app=self.app)
        cast(Any, self.main_paned).add(self.app.log_view, weight=1)

        # Expose download view alias for App
        self.download_view = self.app.log_view.download_view
        self._update_icons()  # Initial Load

    def _update_display_columns(self) -> None:
        disp = [c for c in self.display_cols_order if self.visible_cols[c].get()]
        self.mod_tree.configure(displaycolumns=disp)
        self.mod_tree.column("#0", stretch=False)
        self.mod_tree.column("#0", stretch=True)
        self.app.update_idletasks()

    def toggle_bottom_panel(self) -> None:
        """Toggles visibility of the LogView/Downloads panel."""
        if self.bottom_panel_visible:
            cast(Any, self.main_paned).forget(self.app.log_view)
            self.bottom_panel_visible = False
        else:
            cast(Any, self.main_paned).add(self.app.log_view, weight=3)
            self.bottom_panel_visible = True
        self.app.update_idletasks()

    def _delete_selected_mod(self) -> None:
        """Deletes the mod currently selected in the TreeView."""
        sel = self.mod_tree.selection()
        if not sel:
            return
        idx = self.mod_tree.index(sel[0])
        if 0 <= idx < len(self.app.mod_configs):
            cfg = self.app.mod_configs[idx]
            if messagebox.askyesno(
                "Delete Mod",
                f"Delete '{cfg.get('Name')}' from disk?\nThis cannot be undone.",
            ):
                self.app.delete_mod_from_disk(cfg)

    def ensure_bottom_panel_visible(self) -> None:
        """Force-expands the bottom panel if it is currently collapsed."""
        if not self.bottom_panel_visible:
            self.toggle_bottom_panel()

    def set_empty_state(self, is_empty: bool) -> None:
        """Toggles the 'No Mods Loaded' placeholder overlay."""
        if not is_empty:
            if self.empty_state_frame:
                self.empty_state_frame.place_forget()
            return

        # Lazy initialization of the empty state frame
        if self.empty_state_frame is None:
            self._update_overlay_style()
            # Parent to the treeview's container so it sits on top
            self.empty_state_frame = ttk.Frame(
                self.mod_tree.master, style="Overlay.TFrame"
            )

            ttk.Label(
                self.empty_state_frame,
                text="No Mods Loaded",
                font=("Segoe UI", 14, "bold"),
                style="Overlay.TLabel",
            ).pack(pady=(0, 5))

            ttk.Label(
                self.empty_state_frame,
                text="Drag and drop archives here or click Add",
                font=("Segoe UI", 10),
                style="Overlay.TLabel",
            ).pack(pady=(0, 15))

            ttk.Button(
                self.empty_state_frame,
                text="Add Mod",
                command=self._add_mod,
                style="Primary.TButton",
            ).pack()

        # Center the overlay in the treeview area
        self.empty_state_frame.place(relx=0.5, rely=0.5, anchor="center")

    def _update_overlay_style(self) -> None:
        """Updates the overlay style to match the current Treeview background."""
        style = ttk.Style()
        tree_bg = str(style.lookup("TFrame", "background") or "#333333")
        tree_fg = utils.get_dynamic_text_color(tree_bg)
        style.configure("Treeview", foreground=tree_fg)
        unified_bg = "#454545"  # Default safe grey
        border_color: str = "#6c757d"
        if hasattr(self.app, "style") and hasattr(self.app.style, "colors"):
            ibg = cast(Any, self.app.style).colors.inputbg

            # If theme is LIGHT (input is white/light), use a standard dark border
            if str(ibg).lower() in ["#ffffff", "white", "#f8f9fa"]:
                unified_bg = ibg
                border_color = "#ced4da"  # Bootstrap standard light-mode border
            # If theme is DARK, keep our #454545 bg but ensure border is visible
            elif str(ibg).lower() not in ["#000000", "#1e1e1e", "black"]:
                unified_bg = ibg
        fg_color = get_dynamic_text_color(unified_bg)

        # Helper to toggle highlight on the container when entry is focused
        def on_focus_in(_: Any) -> None:
            if hasattr(self.app, "style"):
                self.search_container.configure(
                    highlightbackground=str(cast(Any, self.app.style).colors.primary)
                )

        def on_focus_out(_: Any) -> None:
            self.search_container.configure(highlightbackground=border_color)

        if hasattr(self, "search_container"):
            self.search_container.configure(
                bg=unified_bg,
                highlightbackground=border_color,  # Unfocused border
                highlightcolor=str(cast(Any, self.app.style).colors.primary),
                takefocus=0,  # Prevent double-focus issues
            )
            # Bind focus events to manually trigger container highlight
            self.search_entry.bind("<FocusIn>", on_focus_in, add="+")
            self.search_entry.bind("<FocusOut>", on_focus_out, add="+")
            self.search_icon_lbl.configure(bg=unified_bg, fg=fg_color)

            self.search_entry.configure(
                bg=unified_bg,
                fg=fg_color,
                insertbackground=fg_color,
                highlightthickness=0,
            )
            # Apply border color to the new separators
            if hasattr(self, "search_sep_1"):
                self.search_sep_1.configure(bg=border_color)
                self.search_sep_2.configure(bg=border_color)
            self.conflict_icon_lbl.configure(bg=unified_bg)

        style.configure("Overlay.TFrame", background=unified_bg)
        style.configure("Overlay.TLabel", background=unified_bg, foreground="gray")

        if self.empty_state_frame:
            self.empty_state_frame.configure(style="Overlay.TFrame")
            for child in self.empty_state_frame.winfo_children():
                if isinstance(child, ttk.Label):
                    child.configure(style="Overlay.TLabel")

    def _setup_run_panel(self, parent: tk.Widget) -> None:
        """Builds the MO2-style Executable Selector and Run Button."""
        self.run_frame = ttk.Frame(parent)
        self.run_frame.pack(side="right", padx=(2, 5))

        # Executable Icon
        self.exec_icon_label = ttk.Label(self.run_frame)
        self.exec_icon_label.pack(side="left", padx=(0, 5))

        # Executable Dropdown
        self.selected_exec_var = tk.StringVar()
        self.exec_combo = ttk.Combobox(
            self.run_frame,
            textvariable=self.selected_exec_var,
            state="readonly",
            height=25,
        )
        self.exec_combo.pack(side="left", padx=(0, 5))
        self.exec_combo.bind("<<ComboboxSelected>>", self.on_exec_change)

        # Run Button
        self.run_btn = ttk.Button(
            self.run_frame,
            text="Run",
            command=self._run_selected_executable,
            compound="left",
            style="success.Outline.TButton",
        )
        self.run_btn.pack(side="left", fill="none", ipadx=5, padx=(5, 0))

        # Refresh list initially
        self.refresh_exec_list()

    def refresh_exec_list(self) -> None:
        custom_execs = self.app.cfg.get("executables", [])  # Per-instance
        def_title = self.app.cfg.get("game_title", "Game (Default)")
        names = [def_title] + [e["title"] for e in custom_execs] + ["<Edit...>"]
        self.exec_combo["values"] = names

        if (
            not self.selected_exec_var.get()
            or self.selected_exec_var.get() not in names
        ):
            self.exec_combo.current(0)
            self.on_exec_change(None)

    def on_exec_change(self, event: Optional["tk.Event[Any]"] = None) -> None:
        sel = self.selected_exec_var.get()
        if sel == "<Edit...>":
            # Reset selection to default to avoid "running" the edit command
            self.exec_combo.current(0)
            self._open_edit_executables_dialog()
            return

        # Update Icon
        self.current_exec_icon = None
        self.exec_icon_label.configure(image="")

        target_path = ""
        def_title = self.app.cfg.get("game_title", "Game (Default)")
        if sel == def_title:
            target_path = self.app.vars["game_executable"].get()
        else:
            custom_execs = self.app.cfg.get("executables", [])
            target = next((e for e in custom_execs if e["title"] == sel), None)
            if target:
                target_path = target.get("path", "")
        if target_path and not os.path.isabs(target_path):
            gd = self.app.vars["game_dir"].get()
            target_path = os.path.join(gd, target_path)
        if target_path and target_path.lower().endswith(".exe"):
            try:
                pil = utils.extract_icon_from_exe(target_path)
                if pil and ImageTk:
                    new_icon = ImageTk.PhotoImage(pil)
                    self.current_exec_icon = new_icon
                    self.exec_icon_label.configure(image=new_icon)
            except Exception:
                pass

    def _open_edit_executables_dialog(self) -> None:
        current = self.app.cfg.get("executables", [])  # Per-instance
        # Fetch existing title or default
        current_title = self.app.cfg.get("game_title", "Game (Default)")
        # Construct default metadata for display
        default_game: Dict[str, str] = {
            "title": current_title,
            "path": self.app.vars["game_executable"].get(),
            "cwd": self.app.vars["game_dir"].get(),
            "args": self.app.vars["launch_override"].get(),
        }

        def _save(new_list: List[Dict[str, str]], new_default: Dict[str, str]) -> None:
            # 1. Save Custom Executables
            self.app.cfg["executables"] = new_list
            # 2. Save Default Game Path
            new_path = new_default.get("path", "")
            if new_path and new_path != self.app.vars["game_executable"].get():
                self.app.vars["game_executable"].set(new_path)

            # 3. Save Default Game Title
            new_title = new_default.get("title", "Game (Default)")
            self.app.cfg["game_title"] = new_title
            # 4. Save Default Game Args
            self.app.vars["launch_override"].set(new_default.get("args", ""))
            self.app.save_config()
            self.refresh_exec_list()

        EditExecutablesDialog(self, current, default_game, _save)

    def _run_selected_executable(self) -> None:
        sel = self.selected_exec_var.get()
        def_title = self.app.cfg.get("game_title", "Game (Default)")
        if sel == def_title or not sel:
            self.app.start_game_action()
        else:
            # Find custom executable
            custom_execs = self.app.cfg.get("executables", [])  # Per-instance
            target = next((e for e in custom_execs if e["title"] == sel), None)
            if target:
                self.app.launch_executable_generic(
                    target.get("path", ""),
                    target.get("args", ""),
                    target.get("cwd", ""),
                )

    def _add_mod(self) -> None:
        """Opens a file dialog to add a new mod from a ZIP archive."""
        target_file = filedialog.askopenfilename(
            title="Add Mod from Archive",
            filetypes=[("Zip Archives", "*.zip"), ("All Files", "*.*")],
        )
        if not target_file:
            return

        # 1. Validate Archive Integrity & Structure
        try:
            with zipfile.ZipFile(target_file, "r") as zf:
                # Security / Validity Check: Must contain a .mos file
                # We scan the namelist without extracting
                file_list = zf.namelist()
                mos_files = [f for f in file_list if f.endswith(".mos")]

                if not mos_files:
                    messagebox.showerror(
                        "Invalid Mod Archive",
                        "This archive does not appear to be a valid GMOS mod.\n\nMissing '.mos' definition file.",
                    )
                    return

                # (Placeholder) Run Security Pipeline
                # self.app.security.scan_archive(target_file)
                # For now, we assume if it opens and has .mos, it passes the basic check

        except zipfile.BadZipFile:
            messagebox.showerror(
                "Error", "The selected file is not a valid zip archive."
            )
            return
        except Exception as e:
            messagebox.showerror(
                "Security Check Failed", f"Failed to validate mod archive:\n{e}"
            )
            return

        # 2. Hand off to App to install
        self.app.install_mod_from_archive(target_file)

    def _open_merge_studio(self) -> None:
        from gmos.ui.merger import MergeStudio

        MergeStudio(self, self.app)

    def _init_filters(self) -> None:
        self.filter_tree.insert("", "end", "all", text="All Mods", open=True)
        self.filter_tree.insert("", "end", "enabled", text="Enabled")
        self.filter_tree.insert("", "end", "disabled", text="Disabled")
        self.filter_tree.insert("", "end", "conflicts", text="Conflicted")
        self.filter_tree.insert("", "end", "issues", text="Has Issues/Risks")
        self.filter_tree.selection_set("all")
        self.filter_tree.bind("<<TreeviewSelect>>", self._on_filter_change)

    def _toggle_filters(self) -> None:
        if self.filters_visible:
            cast(Any, self.mid_h_paned).forget(self.filter_frame)
            self.filters_visible = False
            if self.ico_panel_open:
                self.filter_btn.config(image=self.ico_panel_open)
        else:
            cast(Any, self.mid_h_paned).insert(0, self.filter_frame, weight=0)
            self.filters_visible = True
            if self.ico_panel_close:
                self.filter_btn.config(image=self.ico_panel_close)

    def toggle_mod_info(self) -> None:
        if self.mod_info_visible:
            cast(Any, self.mid_h_paned).forget(self.right_sidebar)
            self.mod_info_visible = False
            if self.mod_info_toggle_btn:
                self.mod_info_toggle_btn.configure(text="Show Mod Info")
        else:
            cast(Any, self.mid_h_paned).add(self.right_sidebar, weight=1)
            if self.mod_info:
                sel = self.mod_tree.selection()
                if sel:
                    idx = self.mod_tree.index(sel[0])
                    if 0 <= idx < len(self.app.mod_configs):
                        self.mod_info.update_for_config(
                            self.app.mod_configs[idx], app_ref=self.app
                        )
            self.mod_info_visible = True
            if self.mod_info_toggle_btn:
                self.mod_info_toggle_btn.configure(text="Hide Mod Info")

    def on_mod_list_click(self, event: "tk.Event[Any]") -> Optional[str]:
        self._drag_col = None
        # Checkbox Toggle Logic
        # Identify the specific element clicked (text, image, cell, etc.)
        region = self.mod_tree.identify_element(event.x, event.y)
        if region == "heading":
            col = self.mod_tree.identify_column(event.x)
            if col != "#0":
                self._drag_col = col
            return None
        item = self.mod_tree.identify_row(event.y)
        # If user clicks the icon ("image") or just left of text ("tree"), toggle mod
        if item and region == "image":
            self.mod_tree.selection_set(item)
            self.toggle_selected_mod()
            return "break"  # Stop event propagation (prevent standard select behavior)
        if item:
            self.drag_index = self.mod_tree.index(item)
            self.drag_item = item
        else:
            self.drag_index = None
            self.drag_item = None
        return None

    def show_context_menu(self, event: "tk.Event[Any]") -> None:
        region = self.mod_tree.identify_region(event.x, event.y)
        if region == "heading":
            self.header_menu.post(event.x_root, event.y_root)
            return
        item = self.mod_tree.identify_row(event.y)
        if item:
            self.mod_tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def open_selected_mod_folder(self) -> None:
        sel = self.mod_tree.selection()
        if sel:
            idx = self.mod_tree.index(sel[0])
            if 0 <= idx < len(self.app.mod_configs):
                mod_cfg = self.app.mod_configs[idx]
                self.app.open_mod_folder(str(mod_cfg.get("Name", "")))

    def on_search_change(self, event: "tk.Event[Any]") -> None:
        if self._search_timer:
            self.after_cancel(self._search_timer)
        self._search_timer = self.after(
            400, lambda: self.app.refresh_ui_after_load(save_policy=False)
        )

    def _on_filter_change(self, event: "tk.Event[Any]") -> None:
        self.app.refresh_ui_after_load(save_policy=False)

    def filter_mods(self, mod_configs: List["UIModConfig"]) -> List["UIModConfig"]:
        # 1. Search Filter
        query = self.search_var.get().lower().strip()
        filtered = mod_configs
        if query:
            filtered = [
                m
                for m in filtered
                if query in str(m.get("Name", "")).lower()
                or query in str(m.get("author", "")).lower()
            ]

        sel = self.filter_tree.selection()
        if not sel:
            return filtered

        cat = sel[0]
        if cat == "all":
            return filtered
        if cat == "enabled":
            return [m for m in filtered if m.get("Enabled", True)]
        if cat == "disabled":
            return [m for m in filtered if not m.get("Enabled", True)]
        if cat == "conflicts":
            return [m for m in filtered if m.get("Name") in self.app.conflict_cache]
        if cat == "issues":
            return [
                m
                for m in filtered
                if not m.get("Valid", True) or m.get("_security_risks")
            ]

        return filtered

    def on_drag_motion(self, event: "tk.Event[Any]") -> None:
        if getattr(self, "_drag_col", None):
            return
        if not self.drag_item:
            return

        target_item = self.mod_tree.identify_row(event.y)
        if not target_item:
            self.drop_indicator.place_forget()
            return

        # Draw Drop Line instead of live swapping
        bbox = self.mod_tree.bbox(target_item)
        if bbox:
            _, y, _, h = bbox

            # Decide if dropping above or below the target
            offset_y = y + h if event.y > y + (h // 2) else y

            self.drop_indicator.place(
                in_=self.mod_tree, x=0, y=offset_y, relwidth=1.0, width=0
            )
            cast(Any, self.drop_indicator).lift()

    def on_drag_release(self, event: "tk.Event[Any]") -> None:
        self.drop_indicator.place_forget()
        drag_col_id = self._drag_col
        if drag_col_id is not None:
            self._drag_col = None
            drop_col_id = str(self.mod_tree.identify_column(event.x))
            if drop_col_id and drop_col_id != drag_col_id and drop_col_id != "#0":
                raw_drag = self.mod_tree.column(drag_col_id, option="id")
                raw_drop = self.mod_tree.column(drop_col_id, option="id")

                drag_name = str(raw_drag)
                drop_name = str(raw_drop)

                if (
                    drag_name in self.display_cols_order
                    and drop_name in self.display_cols_order
                ):
                    self.display_cols_order.remove(drag_name)
                    drop_idx = self.display_cols_order.index(drop_name)
                    self.display_cols_order.insert(drop_idx, drag_name)
                    self._update_display_columns()

            return
        # Commit the heavy logic only once at the end.
        if not self.drag_item or self.drag_index is None:
            return

        # Calculate final index based on drop position
        target_item = self.mod_tree.identify_row(event.y)
        if target_item:
            final_index = self.mod_tree.index(target_item)
        else:
            final_index = self.drag_index  # No valid drop target

        if final_index != self.drag_index:
            if 0 <= self.drag_index < len(self.app.mod_configs):
                cfg = self.app.mod_configs.pop(self.drag_index)
                self.app.mod_configs.insert(final_index, cfg)

                ordered_mods, _ = apply_dependency_resolution(
                    cast(List["ModConfig"], self.app.mod_configs)
                )

                rebuild_mod_tree(
                    self.mod_tree,
                    cast(List[UIModConfig], ordered_mods),
                    get_mod_name_from_config,
                    icon_map=self.icons,
                    app_ref=self.app,
                )
                self.app.update_patch_instructions()
            self.app.update_conflict_status()

            gd = utils.safe_norm(self.app.vars["game_dir"].get())
            if gd:
                policy.save_load_order(
                    cast(List[Dict[str, Any]], self.app.mod_configs), game_dir=gd
                )
                # Restore selection
                try:
                    self.mod_tree.selection_set(self.drag_item)
                except Exception:
                    pass

        self.drag_item = None
        self.drag_index = None

    def move_selected_mod(self, direction: int) -> None:
        self.app.move_selected_mod(direction)

    def toggle_selected_mod(self) -> None:
        sel = self.mod_tree.selection()
        if not sel:
            return
        current_name = self.mod_tree.item(sel[0], "text")
        self.app.toggle_selected_mod()
        self.select_mod_by_name(current_name)

    def select_mod_by_name(self, name: str) -> None:
        if not name:
            return
        target = name.lower()
        for item_id in self.mod_tree.get_children():
            txt = self.mod_tree.item(item_id, "text").lower()
            if txt == target:
                self._select_item(item_id)
                return
            clean_txt = txt.replace("⚠️ ", "").strip()
            if clean_txt.startswith(target):
                remainder = clean_txt[len(target) :]
                if (
                    (not remainder)
                    or remainder.startswith("  (")
                    or remainder.startswith(" [")
                    or remainder.startswith(" (")
                ):
                    self._select_item(item_id)
                    return

    def _select_item(self, item_id: str) -> None:
        self.mod_tree.selection_set(item_id)
        self.mod_tree.focus(item_id)
        self.mod_tree.see(item_id)
        self.on_mod_selection_change()

    def on_mod_selection_change(self, _ev: Optional["tk.Event[Any]"] = None) -> None:
        sel = self.mod_tree.selection()
        if not sel:
            if self.mod_info:
                self.mod_info.update_for_config(None, app_ref=self.app)
            return
        item_id = sel[0]
        idx = self.mod_tree.index(item_id)
        if self.mod_info:
            cfg = (
                self.app.mod_configs[idx]
                if 0 <= idx < len(self.app.mod_configs)
                else None
            )
            self.mod_info.update_for_config(cfg, app_ref=self.app)

    def update_conflict_status(
        self, unresolved_count: int, total_conflicts: int
    ) -> None:
        if total_conflicts == 0:
            self.conflict_icon_lbl.config(image=self.ico_check_circle or "")
            ToolTip(self.conflict_icon_lbl, "No Conflicts Detected")
        elif unresolved_count == 0:
            self.conflict_icon_lbl.config(image=self.ico_check_circle or "")
            ToolTip(self.conflict_icon_lbl, "All conflicts resolved")
        else:
            self.conflict_icon_lbl.config(image=self.ico_alert or "")
            ToolTip(self.conflict_icon_lbl, f"{unresolved_count} Conflicts Found!")

    def _update_tree_theme(self) -> None:
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#333333")
        self.mod_tree.tag_configure("disabled", foreground="gray")
        self.mod_tree.tag_configure(
            "invalid",
            foreground=get_adaptive_color_variant(theme_bg, "#ff5252", "#b30000"),
        )
        self.mod_tree.tag_configure(
            "conflict",
            foreground=get_adaptive_color_variant(theme_bg, "#ff5252", "#b30000"),
        )
        self.mod_tree.tag_configure(
            "resolved",
            foreground=get_adaptive_color_variant(theme_bg, "#e6b800", "#c29200"),
        )

    def _on_theme_change(self, event: Optional["tk.Event[Any]"] = None) -> None:
        self._update_checkbox_icons()
        self._update_overlay_style()
        self._update_tree_theme()

    def _update_icons(self) -> None:
        """Reloads icons based on theme."""

        # Load Icons
        self.ico_instances = utils.load_icon("layers.png", size=(20, 20))
        self.ico_profiles = utils.load_icon("user.png", size=(20, 20))
        self.ico_settings = utils.load_icon("settings.png", size=(20, 20))
        self.ico_sdk = utils.load_icon("hammer.png", size=(20, 20))
        self.ico_merge = utils.load_icon("merge.png", size=(20, 20))
        self.ico_add = utils.load_icon("folder-plus.png", size=(20, 20))
        self.ico_refresh = utils.load_icon("refresh-cw.png", size=(20, 20))
        self.ico_patch = utils.load_icon("file-pen.png", size=(20, 20))
        self.ico_globe = utils.load_icon("globe.png", size=(20, 20))
        self.ico_panel_open = utils.load_icon("panel-left-open.png", size=(24, 24))
        self.ico_panel_close = utils.load_icon("panel-left-close.png", size=(24, 24))
        self.ico_search = utils.load_icon("search.png", size=(16, 16))
        self.ico_run = utils.load_icon("play.png", size=(16, 16))
        self.ico_check_circle = utils.load_icon("check.png", size=(16, 16))
        self.ico_alert = utils.load_icon("triangle-alert.png", size=(16, 16))

        # Update Buttons
        # Filter (Panel Toggle)
        if self.ico_panel_open:
            # Default state is hidden (False) -> Show "Open" icon
            current_icon = (
                self.ico_panel_close if self.filters_visible else self.ico_panel_open
            )
            self.filter_btn.config(image=cast(Any, current_icon))
        # Search
        if self.ico_search:
            self.search_icon_lbl.config(image=self.ico_search, text="")
        # Instances
        if self.ico_instances:
            self.btn_instances.config(image=self.ico_instances)
        # Profiles
        if self.ico_profiles:
            self.btn_profiles.config(image=self.ico_profiles)
        # Add Mod
        if self.ico_add:
            self.btn_add.config(image=self.ico_add)
        # Merge
        if self.ico_merge:
            self.btn_merge.config(image=self.ico_merge)
        # Refresh
        if self.ico_refresh:
            self.btn_refresh.config(image=self.ico_refresh)
        # Website
        if self.ico_globe:
            self.btn_website.config(image=self.ico_globe)
        # Settings
        if self.ico_settings:
            self.btn_settings.config(image=self.ico_settings)
        # SDK
        if self.ico_sdk:
            self.btn_devtools.config(image=self.ico_sdk)
        # Run
        if self.ico_run:
            self.run_btn.config(image=self.ico_run, text="Run")
        # Patch
        if self.ico_patch:
            self.patch_btn.config(image=self.ico_patch, text="Patch")
