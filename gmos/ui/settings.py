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
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, cast

from gmos import utils
from gmos.state.config import save_global_config

if TYPE_CHECKING:
    from gmos.ui.app import App


class SettingsDialog(tk.Toplevel):
    """
    Dialog for Global Settings and Current Instance Configuration.
    """

    # Define standard ttkbootstrap themes
    LIGHT_THEMES = [
        "cosmo",
        "flatly",
        "journal",
        "litera",
        "lumen",
        "minty",
        "pulse",
        "sandstone",
        "united",
        "yeti",
        "morph",
        "simplex",
        "cerculean",
    ]
    DARK_THEMES = ["solar", "superhero", "darkly", "cyborg", "vapor"]

    def __init__(self, parent: tk.Widget, app: "App"):
        super().__init__(parent)
        self.app = app
        self.global_cfg = self.app.global_cfg
        self.title("Settings")

        utils.load_and_apply_app_icon_to_toplevel(self)
        utils.setup_child_window(self, parent, width=700, height=500, modal=True)
        self.bind("<<ThemeChanged>>", lambda e: utils.apply_window_theme(self))
        self._setup_ui()

    def _setup_ui(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        tab_general = ttk.Frame(notebook, padding=15)
        notebook.add(tab_general, text="General")
        self._build_general_tab(tab_general)

        tab_theme = ttk.Frame(notebook, padding=15)
        notebook.add(tab_theme, text="Appearance")
        self._build_theme_tab(tab_theme)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill="x", side="bottom")

        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right")
        cast(Any, ttk.Button)(
            btn_frame,
            text="Save Global Settings",
            command=self._save_global,
            bootstyle="primary",
        ).pack(side="right", padx=5)

    def _build_general_tab(self, parent: ttk.Frame) -> None:
        lbl_frame = ttk.Labelframe(parent, text="Nexus Mods Integration", padding=10)
        lbl_frame.pack(fill="x", pady=5)

        ttk.Label(lbl_frame, text="API Key:").pack(anchor="w")
        self.nexus_key_var = tk.StringVar(value=self.global_cfg.nexus_api_key)
        ttk.Entry(lbl_frame, textvariable=self.nexus_key_var, show="*").pack(
            fill="x", pady=(5, 10)
        )

        ttk.Button(
            lbl_frame,
            text="Register as Link Handler (nxm://)",
            command=self.app.register_protocols,
        ).pack(fill="x", pady=5)

        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=15)
        ttk.Label(parent, text="Godot Editor", font=("", 10, "bold")).pack(anchor="w")
        path_frame = ttk.Frame(parent)
        path_frame.pack(fill="x", pady=5)
        self.godot_path_var = tk.StringVar(
            value=getattr(self.global_cfg, "godot_editor_path", "")
        )
        ttk.Label(path_frame, text="Executable Path:").pack(side="left")
        ttk.Entry(path_frame, textvariable=self.godot_path_var).pack(
            side="left", fill="x", expand=True, padx=5
        )
        ttk.Button(path_frame, text="Browse...", command=self._browse_godot_path).pack(
            side="left"
        )
        ttk.Separator(parent, orient="horizontal").pack(fill="x", pady=15)

        ttk.Label(parent, text="Behavior", font=("", 10, "bold")).pack(anchor="w")

        self.var_sandbox = tk.BooleanVar(
            value=getattr(self.global_cfg, "sandbox_enabled", True)
        )
        ttk.Checkbutton(
            parent, text="Enable Automatic Sandbox Injection", variable=self.var_sandbox
        ).pack(anchor="w", pady=5)

    def _browse_godot_path(self) -> None:

        path = filedialog.askopenfilename(
            title="Select Godot Editor Executable",
            filetypes=[("Executables", "*.exe"), ("All Files", "*.*")],
            parent=self,
        )
        if path:
            self.godot_path_var.set(path)

    def _build_theme_tab(self, parent: ttk.Frame) -> None:
        lbl_frame = ttk.Labelframe(parent, text="User Interface", padding=10)
        lbl_frame.pack(fill="x", pady=5)

        self.var_theme_mode = tk.StringVar(value="Dark")

        # Detect current theme to set initial mode
        current_theme = self.global_cfg.theme_preference
        if current_theme in self.LIGHT_THEMES:
            self.var_theme_mode.set("Light")
        else:
            self.var_theme_mode.set("Dark")

        lbl_mode = ttk.Label(lbl_frame, text="Color Mode:")
        lbl_mode.pack(anchor="w", pady=(0, 5))

        mode_frame = ttk.Frame(lbl_frame)
        mode_frame.pack(fill="x", pady=(0, 15))

        rad_dark = ttk.Radiobutton(
            mode_frame,
            text="Dark",
            variable=self.var_theme_mode,
            value="Dark",
            command=self._update_theme_options,
        )
        rad_dark.pack(side="left", padx=(0, 15))

        rad_light = ttk.Radiobutton(
            mode_frame,
            text="Light",
            variable=self.var_theme_mode,
            value="Light",
            command=self._update_theme_options,
        )
        rad_light.pack(side="left")

        lbl_theme = ttk.Label(lbl_frame, text="Theme Variant:")
        lbl_theme.pack(anchor="w", pady=(0, 5))

        self.theme_var = tk.StringVar(value=current_theme)
        self.cb_theme = ttk.Combobox(
            lbl_frame, textvariable=self.theme_var, state="readonly"
        )
        self.cb_theme.pack(fill="x")

        # Initialize options
        self._update_theme_options(init=True)

        lbl_icons = ttk.Label(lbl_frame, text="Icon Set:")
        lbl_icons.pack(anchor="w", pady=(10, 5))

        # Get current set safely
        current_icon_set = getattr(self.global_cfg, "icon_set", "Default")
        self.icon_set_var = tk.StringVar(value=current_icon_set)

        icon_sets = utils.get_available_icon_sets()

        self.cb_icons = ttk.Combobox(
            lbl_frame,
            textvariable=self.icon_set_var,
            values=icon_sets,
            state="readonly",
        )
        self.cb_icons.pack(fill="x")

        # Restart Warning
        ttk.Label(
            lbl_frame,
            text="Note: Restart GMOS to fully apply theme and icon changes.",
            font=("", 9, "italic"),
            foreground="orange",
        ).pack(anchor="w", pady=10)

    def _update_theme_options(self, init: bool = False) -> None:
        """Updates the theme dropdown based on the selected mode (Light/Dark)."""
        mode = self.var_theme_mode.get()

        if mode == "Light":
            options = sorted(self.LIGHT_THEMES)
        else:
            options = sorted(self.DARK_THEMES)

        self.cb_theme.configure(values=options)

        # If the currently selected theme isn't in the new list, pick a default
        current = self.theme_var.get()
        if current not in options:
            if mode == "Light":
                self.theme_var.set("cosmo")
            else:
                self.theme_var.set("solar")

    def _save_global(self) -> None:

        self.global_cfg.nexus_api_key = self.nexus_key_var.get().strip()
        self.global_cfg.theme_preference = self.theme_var.get()
        if hasattr(self, "var_sandbox"):
            self.global_cfg.sandbox_enabled = self.var_sandbox.get()
        if hasattr(self, "godot_path_var"):
            self.global_cfg.godot_editor_path = self.godot_path_var.get().strip()
        # Save icon set and apply immediately
        val = self.icon_set_var.get()
        self.global_cfg.icon_set = val
        utils.set_active_icon_set(val)

        save_global_config(self.global_cfg)
        messagebox.showinfo("Saved", "Global settings saved.")
