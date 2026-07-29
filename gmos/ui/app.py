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
import ctypes
import datetime
import difflib
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
import tkinter as tk
import traceback
import webbrowser
import zipfile
from collections import defaultdict, deque
from pathlib import Path
from tkinter import Toplevel, filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Deque, Dict, List, Optional, Set, Tuple, cast

import psutil

from gmos import utils
from gmos.core.injection import SandboxInjector
from gmos.core.patcher import (
    analyze_mods_for_conflicts,
    apply_dependency_resolution,
    generate_patch_plan,
    run_patcher,
    save_dryrun_artifact,
)
from gmos.core.protocol import LinkListener, register_url_handler
from gmos.core.session import GmosSession
from gmos.io import cache, get_io_executor, safe_read_bytes, safe_rmtree
from gmos.io import pck as pck_tools
from gmos.state import policy, profiles
from gmos.state.config import (
    INSTANCE_CONFIG_FILENAME,
    load_global_config,
    load_instance_config_dict,
    save_global_config,
    save_instance_config_dict,
)
from gmos.ui.dashboard import DashboardView, ModInfoPane
from gmos.ui.dev import DeveloperToolsDialog
from gmos.ui.logs import LogView
from gmos.ui.widgets import (
    LegalDisclaimerDialog,
    ProgressDialog,
    RollbackDialog,
    Toast,
    UIModConfig,
    rebuild_mod_tree,
    res_to_path,
)
from gmos.utils import (
    LOG_DIR,
    ROOT_DIR,
    ModConfig,
    get_mod_name_from_config,
    handle_permission_error,
    logger,
    safe_norm,
    safe_spawn,
)

try:
    from tkinterdnd2 import (  # type: ignore[reportMissingTypeStubs, unused-ignore]
        DND_FILES as _DND_FILES,
    )
    from tkinterdnd2 import (  # type: ignore[reportMissingTypeStubs, unused-ignore]
        TkinterDnD as _TkinterDnD,
    )

    _dnd_files_val = _DND_FILES
    _tkdnd_val = _TkinterDnD
except ImportError:
    _dnd_files_val = cast(Any, None)
    _tkdnd_val = cast(Any, None)

DND_FILES = _dnd_files_val
TkinterDnD = _tkdnd_val

if TYPE_CHECKING:
    from gmos.ui.browser import DownloadsView

    BaseTk = tk.Tk
elif TkinterDnD:
    BaseTk = TkinterDnD.Tk
else:
    BaseTk = tk.Tk
try:
    import ttkbootstrap as _ttkb

    _ttkb_val = _ttkb
except ImportError:
    _ttkb_val = cast(Any, None)

ttkb = _ttkb_val


class App(BaseTk):
    def __init__(self, config_path: Optional[str] = None) -> None:
        super().__init__()
        # Load Global Config (Must be first for theme/scaling prefs)

        self.global_cfg = load_global_config()
        icon_set = getattr(self.global_cfg, "icon_set", "Default")
        utils.set_active_icon_set(icon_set)
        # High-DPI Awareness for Windows (Prevents blurry text/icons)
        if sys.platform == "win32":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass
        self.config_path_override = config_path  # Store injected config path
        # Baseline: 1920px width. +30% baseline scale.
        screen_width = self.winfo_screenwidth()
        self.ui_scale = (
            max(1.0, (screen_width / 3840) * 1.5) if screen_width > 2560 else 1.0
        )

        base_w, base_h = 1280, 800
        scaled_w = int(base_w * self.ui_scale)
        scaled_h = int(base_h * self.ui_scale)
        self.geometry(f"{scaled_w}x{scaled_h}")

        self._apply_scaling_style()
        self.title("Godot Mod Overhaul System (GMOS)")
        self.after(200, lambda: utils.apply_window_theme(self))
        self.bind("<<ThemeChanged>>", lambda e: utils.apply_window_theme(self))
        self.browser_view: Optional["DownloadsView"] = None
        self.browser_window: Optional[tk.Tk | tk.Toplevel] = self
        self.api_rate_var = tk.StringVar()
        self.log_view: Optional[LogView] = None
        self.dashboard: Optional[DashboardView] = None
        self._heal_instances()
        if not self.config_path_override and self.global_cfg.default_instance_id:
            def_id = self.global_cfg.default_instance_id
            meta = self.global_cfg.instances.get(def_id)
            if meta and os.path.exists(meta.path):
                possible_conf = os.path.join(
                    meta.path, "gmos_data", INSTANCE_CONFIG_FILENAME
                )
                self.config_path_override = possible_conf
                logger.info(
                    "Auto-loaded default instance: %s (%s)", meta.name, possible_conf
                )
        # Centralized Variables (Shared by Dashboard & Settings)
        self.vars: Dict[str, tk.StringVar] = {
            "game_dir": tk.StringVar(),
            "mods_dir": tk.StringVar(),
            "game_executable": tk.StringVar(),
            "launch_override": tk.StringVar(),
        }
        self.download_status_var = tk.StringVar(value="📥 Manage Downloads")
        utils.load_and_apply_app_icon(self)
        self.cfg: Dict[str, Any] = {}
        self.mod_configs: List[UIModConfig] = []  # Stores parsed mod info
        self.file_rules: Dict[str, str] = {}
        self.instructions: List[Tuple[str, str, Any]] = (
            []
        )  # The final, ordered list of patches (mod_name, op, details)
        self.session = GmosSession(game_dir="", mods_dir="")
        self.drag_index: int | None = None
        self.mod_info: Optional[ModInfoPane] = None
        self.mod_info_visible = False
        self.dev_tools_window: Optional[Toplevel] = None
        self.mod_info_toggle_btn: Optional[tk.Button] = None
        self.mod_tree: ttk.Treeview
        self.log_notebook: ttk.Notebook
        self.load_config()
        self._is_busy = False  # Track if a critical task is running
        self.active_tasks: Set[str] = set()
        self.setup_ui()
        # Show Legal Check
        if not self.global_cfg.legal_accepted:
            self.show_legal_check()
        self.load_mods()  # Initial load
        if DND_FILES and hasattr(self, "drop_target_register"):
            try:
                dnd_self = cast(Any, self)
                dnd_self.drop_target_register(DND_FILES)
                dnd_self.dnd_bind("<<Drop>>", self._on_file_drop)
            except Exception as e:
                print(f"Warning: Drag-and-drop initialization failed: {e}")
        # Start IPC Listener for protocol links
        self.ipc_listener = LinkListener(self._on_external_link)
        # Restore previous session state (Persistence)
        self._update_instance_icon()
        if self.session:
            self.session.restore_tasks(self._on_download_progress)

    def _apply_scaling_style(self) -> None:
        """Configures fonts and sizes based on self.ui_scale."""
        default_size = int(10 * self.ui_scale)

        self.default_font = ("Segoe UI", default_size)
        self.bold_font = ("Segoe UI", default_size, "bold")

        theme_name = self.global_cfg.theme_preference or "darkly"
        _ttkb_proxy: Any = ttkb
        self.style = (
            _ttkb_proxy.Style(theme=theme_name) if _ttkb_proxy else ttk.Style(self)
        )

        if ttkb:
            # Bootstrap handling
            self.configure(bg=cast(Any, self.style).colors.bg)
            cast(Any, self.style).configure(
                "Treeview", rowheight=int(24 * self.ui_scale)
            )
        else:
            # Standard fallback
            cast(Any, self.style).configure(".", font=self.default_font)
            cast(Any, self.style).configure(
                "Treeview", font=self.default_font, rowheight=int(22 * self.ui_scale)
            )
            cast(Any, self.style).configure("Treeview.Heading", font=self.bold_font)
            cast(Any, self.style).configure("TButton", font=self.bold_font)
            cast(Any, self.style).configure(
                "Accent.TButton",
                foreground="green",
                background="black",
                font=("Arial", int(10 * self.ui_scale), "bold"),
            )

        # We define a custom layout that excludes the "Menubutton.indicator"
        try:
            self.style.layout(
                "Sleek.TMenubutton",
                cast(
                    Any,
                    [
                        ("Menubutton.background", {}),
                        (
                            "Menubutton.button",
                            {
                                "children": [
                                    (
                                        "Menubutton.focus",
                                        {
                                            "children": [
                                                (
                                                    "Menubutton.padding",
                                                    {
                                                        "children": [
                                                            (
                                                                "Menubutton.label",
                                                                {
                                                                    "side": "left",
                                                                    "expand": 1,
                                                                },
                                                            )
                                                        ]
                                                    },
                                                )
                                            ]
                                        },
                                    )
                                ]
                            },
                        ),
                    ],
                ),
            )
            cast(Any, self.style).configure(
                "Sleek.TMenubutton",
                padding=(10, 6),
                font=self.default_font,
                background=cast(Any, self.style).lookup("TFrame", "background"),
                relief="flat",
                borderwidth=0,
            )
        except Exception:
            pass
        # We manually configure this to look like a 'Link' button (flat, no border) but with 0 padding.
        # Note: We rely on the theme's default background transparency for 'TButton' or 'Link.TButton'.
        cast(Any, self.style).configure(
            "Compact.Link.TButton",
            padding=0,
            relief="flat",
            borderwidth=0,
            shiftrelief=0,
        )
        if ttkb:
            self.style.map(
                "Compact.Link.TButton",
                background=[("active", "!disabled", cast(Any, self.style).colors.bg)],
            )

    def _heal_instances(self) -> None:
        """
        Scans for and repairs stale sandbox configurations.
        If gmos_sandbox.gd is missing but override.cfg still references it,
        the game will crash. We must remove the reference.
        """
        for inst in self.global_cfg.instances.values():
            try:
                # Check status without locking the instance
                injector = SandboxInjector(inst.path)
                if injector.is_injected():
                    script_path = os.path.join(inst.path, "gmos_sandbox.gd")
                    if not os.path.exists(script_path):
                        logger.warning(
                            f"Self-healing: Removing stale sandbox config from '{inst.name}'"
                        )
                        injector.remove()
            except Exception as e:
                logger.warning(f"Self-healing check failed for '{inst.name}': {e}")

    def switch_instance(self, path: str) -> None:
        """Helper to switch instances and reload config (Used by SettingsView)."""

        self.config_path_override = os.path.join(
            path, "gmos_data", INSTANCE_CONFIG_FILENAME
        )
        self.load_config()  # Refreshes self.cfg
        self.vars["game_dir"].set(safe_norm(self.cfg.get("game_dir", path)))
        self.vars["mods_dir"].set(
            safe_norm(self.cfg.get("mods_dir", os.path.join(path, "mods")))
        )
        self.vars["game_executable"].set(self.cfg.get("game_executable", "game.exe"))
        self.vars["launch_override"].set(self.cfg.get("launch_override", ""))
        # Reload
        # Auto-set game title from instance metadata if missing or default
        if (
            not self.cfg.get("game_title")
            or self.cfg.get("game_title") == "Game (Default)"
        ):
            meta_name = os.path.basename(path)
            # Try to find pretty name from global config
            for meta in self.global_cfg.instances.values():
                if os.path.normpath(meta.path) == os.path.normpath(path):
                    meta_name = meta.custom_name or meta.name
                    break
            self.cfg["game_title"] = meta_name
        self._update_instance_icon()
        self.load_mods()
        # Refresh Dashboard Executables & Icon
        if self.dashboard:
            self.dashboard.refresh_exec_list()
            self.dashboard.exec_combo.current(0)
            self.dashboard.on_exec_change(None)
        self.show_toast(f"Switched to {os.path.basename(path)}")

    def _update_instance_icon(self) -> None:
        """Updates the main toolbar icon based on the current game executable."""
        if not self.dashboard or not hasattr(self.dashboard, "icon_label"):
            return

        exe = self.vars["game_executable"].get()
        game_dir = self.vars["game_dir"].get()
        full_path = os.path.join(game_dir, exe)

        icon = None
        if os.path.exists(full_path) and utils.Image:
            try:
                pil = utils.extract_icon_from_exe(full_path)
                if pil:
                    pil.thumbnail((32, 32), utils.Image.Resampling.LANCZOS)
                    icon = utils.ImageTk.PhotoImage(pil)
            except Exception:
                pass

        if not icon:
            icon = utils.load_icon("play.png", size=(32, 32))

        self.current_icon = icon
        cast(Any, getattr(self.dashboard, "icon_label", None)).config(
            image=icon if icon else ""
        )

    def load_config(self) -> None:
        """Load configuration into self.cfg."""
        try:
            # Strict mode: must have config path
            target_path = self.config_path_override
            cfg = load_instance_config_dict(target_path) if target_path else {}

            self.cfg.clear()
            if cfg:
                self.cfg.update(cfg)
                # Hydrate UI variables
                self.vars["game_dir"].set(safe_norm(self.cfg.get("game_dir", "")))
                self.vars["mods_dir"].set(safe_norm(self.cfg.get("mods_dir", "")))
                self.vars["game_executable"].set(
                    self.cfg.get("game_executable", "game.exe")
                )
                self.vars["launch_override"].set(self.cfg.get("launch_override", ""))
        except Exception as e:
            logger.debug("Config load failed: %s", e, exc_info=True)

    def save_config(self) -> None:
        try:
            # Update the main config dictionary with current UI variables
            for k, sv in getattr(self, "vars", {}).items():
                self.cfg[k] = sv.get()

            # Save to the specific instance path
            target_path = self.config_path_override
            if target_path:
                save_instance_config_dict(self.cfg, path=target_path)
        except Exception as e:
            print(f"Error saving config: {e}")

    def _on_file_drop(self, event: "tk.Event[tk.Misc]") -> None:
        """Handle files dropped from OS Explorer."""
        event_data = getattr(event, "data", None)
        if not event_data:
            return

        # tkinterdnd2 returns paths in {curly braces} if they contain spaces
        raw_files = cast(List[str], cast(Any, self.tk).splitlist(event_data))
        for fpath in raw_files:
            if (
                fpath.endswith(".zip")
                or fpath.endswith(".rar")
                or fpath.endswith(".7z")
            ):
                self.show_toast(f"Queued install: {os.path.basename(fpath)}")
                get_io_executor().submit(self._install_dropped_file, fpath)

    def _install_dropped_file(self, fpath: str) -> None:
        try:
            self.session.install_mod_from_archive(fpath)
            self.after(0, lambda: self.show_toast("Install Complete", kind="success"))
            self.after(0, self.load_mods)
        except Exception as e:
            err_msg = str(e)
            self.after(0, lambda: messagebox.showerror("Install Error", err_msg))

    def _on_external_link(self, link: str) -> None:
        """Called from IPC thread. Marshal to UI thread."""
        self.after(0, lambda: self.handle_protocol_link(link))

    def handle_protocol_link(self, link: str) -> None:
        """Handle NXM links intercepted from browser."""
        logger.info(f"Processing intercepted link: {link}")
        try:
            if self.state() == "iconic":
                self.deiconify()
            cast(Any, self).lift()
            cast(Any, self).attributes("-topmost", True)
            self.focus_force()
            self.after(200, lambda: cast(Any, self).attributes("-topmost", False))
            if link == "FOCUS":
                return
            self.open_downloads_window()

            # We hash only the resource identifiers (game/mod/file) so retries
            # reuse the same Task ID.
            try:
                # Expected: nxm://game/mods/mod_id/files/file_id?key=...
                # parts: ['nxm:', '', 'game', 'mods', 'mod_id', 'files', 'file_id']
                parts = link.split("?")[0].split("/")
                if len(parts) >= 7:
                    unique_key = f"{parts[2]}_{parts[4]}_{parts[6]}"  # game_mod_file
                else:
                    unique_key = link.split("?")[0]  # Fallback to base URL
            except Exception:
                unique_key = link

            task_id = hashlib.md5(
                unique_key.encode(), usedforsecurity=False
            ).hexdigest()[:8]

            # Parse URL for better defaults: nxm://game/mods/id/...
            # Example: nxm://stardewvalley/mods/123/...
            game_domain = "Unknown"
            placeholder_name = "Resolving..."

            try:
                parts = link.split("/")
                if len(parts) > 2:
                    game_domain = parts[2]  # stardewvalley
                    placeholder_name = (
                        f"Mod {parts[4]}" if len(parts) > 4 else "Mod File"
                    )
            except Exception:
                pass
            self.active_tasks.add(task_id)
            if self.browser_view:
                # Robust State Management (Explicit States)
                self.browser_view.add_task(task_id, placeholder_name, game_domain)
                self.browser_view.update_task_state(
                    task_id, "downloading", "Connecting to API..."
                )

            get_io_executor().submit(
                self.session.handle_nxm_link, link, task_id, self._on_download_progress
            )

        except Exception as e:
            err_msg = f"Failed to handle link:\n{e}\n\n{traceback.format_exc()}"
            logger.error(err_msg)
            messagebox.showerror("Link Handler Error", err_msg)

    def _on_download_progress(
        self, task_id: str, current: int, total: int, status: str, mod_name: str = ""
    ) -> None:
        """Callback from Session to update UI from background thread."""

        # Initialize tracking for speed calculation if new
        if not hasattr(self, "_dl_stats"):
            # Map task_id -> deque of (time, bytes) snapshots
            self._dl_stats: Dict[str, Deque[Tuple[float, int]]] = {}

        now = time.time()

        def _update() -> None:
            if self.dashboard:
                # Auto-expand panel on new download
                self.dashboard.ensure_bottom_panel_visible()
                # Switch to Downloads Tab
                if self.log_view:
                    cast(Any, self.log_view.notebook).select(1)

            pct = 0.0
            if total > 0:
                pct = (current / total) * 100
            # Calculate Speed
            speed_str = "--"
            if task_id not in self._dl_stats:
                self._dl_stats[task_id] = deque()

            history = self._dl_stats[task_id]
            history.append((now, current))

            # Prune history older than 5 seconds
            while len(history) > 1 and (now - history[0][0]) > 5.0:
                history.popleft()

            # Calculate average speed over the window
            rate = 0.0
            eta_str = ""
            if len(history) > 1:
                # Compare current vs oldest snapshot in window
                old_time, old_bytes = history[0]
                dt = now - old_time
                db = current - old_bytes

                if dt > 0:
                    rate = db / dt

            # Format
            if rate > 0:
                if rate > 1024 * 1024:
                    speed_str = f"{rate / (1024 * 1024):.1f} MB/s"
                else:
                    speed_str = f"{rate / 1024:.1f} KB/s"
                # Calculate ETA
                remaining_bytes = total - current
                if remaining_bytes > 0:
                    seconds = int(remaining_bytes / rate)
                    if seconds > 3600:
                        eta_str = f"{seconds // 3600}h {(seconds % 3600) // 60}m left"
                    elif seconds > 60:
                        eta_str = f"{seconds // 60}m {seconds % 60}s left"
                    else:
                        eta_str = f"{seconds}s left"
            # Update Footer
            if (
                status
                in (
                    "Complete",
                    "Installed",
                    "Error",
                    "Risk",
                    "Extracted",
                    "Interrupted",
                )
                or "Error" in status
                or "Dangerous" in status
            ):
                self.active_tasks.discard(task_id)
                if task_id in self._dl_stats:
                    del self._dl_stats[task_id]
            else:
                self.active_tasks.add(task_id)
            # Auto-Refresh Mod List if installed
            if "Installed" in status:
                self.load_mods()
                if self.browser_view:
                    self.browser_view.remove_task(task_id)
                # We return early so we don't re-add it to the list below
                return
            # Refresh label based on count
            count = len(self.active_tasks)
            if count > 0:
                self.download_status_var.set(f"📥 {count} Downloads Active")
            else:
                self.download_status_var.set("📥 Manage Downloads")

            # Update Window content if open
            if (
                self.browser_view
                and self.browser_window
                and self.browser_window.winfo_exists()
            ):
                # Note: add_task is idempotent, safe to call repeatedly
                self.browser_view.add_task(task_id, mod_name or "Unknown Mod")
                if total > 0:
                    self.browser_view.update_task_progress(
                        task_id, pct, speed_str, eta_str
                    )
                elif status == "Extracting":
                    self.browser_view.update_task_state(
                        task_id, "extracting", "Extracting Archive..."
                    )
                elif "Dangerous" in status or "Risk" in status:
                    self.browser_view.update_task_state(task_id, "risk", status)
                elif status == "Interrupted":
                    self.browser_view.update_task_state(
                        task_id, "interrupted", "Interrupted ⏸️"
                    )
                else:
                    self.browser_view.update_task_state(
                        task_id,
                        "scanning" if "Scan" in status else "downloading",
                        status,
                    )

                if status == "Complete":
                    self.browser_view.update_task_state(
                        task_id, "ready", "Ready to Install"
                    )

        self.after(0, _update)

    def _on_theme_change(self, event: Optional["tk.Event[tk.Misc]"] = None) -> None:
        """Called when the user (or system) switches the UI theme."""
        pass

    def register_protocols(self) -> None:
        """Register GMOS as the handler for nxm:// links (Admin)."""
        try:
            register_url_handler("nxm")
            messagebox.showinfo(
                "Success", "GMOS registered as handler for nxm:// links."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Registration failed: {e}")

    def show_legal_check(self) -> None:
        """Blocks execution until legal accepted or cancelled."""
        # We need to wait for visibility
        self.update_idletasks()
        dlg = LegalDisclaimerDialog(cast(tk.Widget, self))
        self.wait_window(dlg)

        if not dlg.result:
            # User cancelled
            self.destroy()
            sys.exit(0)
        else:
            self.global_cfg.legal_accepted = True
            save_global_config(self.global_cfg)

    def setup_menu(self) -> None:
        """Initializes a custom dark menu bar using ttk widgets (replacing native)."""
        # Accessed by Dashboard to inject toolbar buttons
        self.menubar_frame = ttk.Frame(self)
        self.menubar_frame.pack(side="top", fill="x")

        # Load Menu Icons
        self.ico_menu_file = utils.load_icon("menu.png", size=(20, 20))
        self.ico_menu_game = utils.load_icon("wrench.png", size=(20, 20))

        def create_menu(
            icon: Optional[tk.PhotoImage], fallback_text: str
        ) -> Tuple[ttk.Menubutton, tk.Menu]:
            menu = tk.Menu(self.menubar_frame, tearoff=0)

            # Use Sleek style to hide arrow and add standard padding
            btn = ttk.Menubutton(
                self.menubar_frame,
                text=fallback_text if not icon else "",
                image=icon or "",
                menu=menu,
                direction="below",
                style="Sleek.TMenubutton",
            )
            btn.pack(side="left", padx=(0, 4))
            return btn, menu

        _, file_menu = create_menu(self.ico_menu_file, "File")
        file_menu.add_command(label="Toggle Logs Panel", command=self._toggle_logs)
        file_menu.add_command(
            label="Toggle Details Panel", command=self._toggle_details
        )
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)

        _, game_menu = create_menu(self.ico_menu_game, "Game")
        game_menu.add_command(
            label="Open Game Directory",
            command=lambda: webbrowser.open(safe_norm(self.vars["game_dir"].get())),
        )
        game_menu.add_separator()
        game_menu.add_command(
            label="Revert Game Files (Rollback)", command=self.rollback_game_files
        )
        # Maintenance items
        game_menu.add_separator()
        game_menu.add_command(label="Clear Cache", command=self.clear_cache_action)
        game_menu.add_command(
            label="View Runtime Manifest", command=self.view_runtime_manifest
        )

    def setup_ui(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.setup_menu()
        # Main Layout: No Tabs, Single Dashboard
        # Instantiate Dashboard View directly into main window
        self.dashboard = DashboardView(cast(tk.Widget, self), app=self)
        self.dashboard.pack(fill="both", expand=True)
        # Link browser view from dashboard
        self.browser_view = self.dashboard.download_view
        self.mod_tree = self.dashboard.mod_tree
        self.conflict_label = self.dashboard.conflict_label

        self.conflict_cache: Dict[str, Dict[str, List[str]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.patch_btn = self.dashboard.patch_btn

        self.after(100, lambda: self.append_log("Application loaded."))

        self.settings_window: Optional[tk.Toplevel] = None
        # Initial icon load
        self._update_instance_icon()

    def _toggle_logs(self) -> None:
        if self.dashboard:
            self.dashboard.toggle_bottom_panel()

    def _toggle_details(self) -> None:
        """Toggles the right-side Mod Info pane."""
        if self.dashboard:
            self.dashboard.toggle_mod_info()

    def open_downloads_window(self) -> None:
        """Switches to downloads tab."""
        self._hydrate_downloads_window()
        # Switch to Downloads tab (index 2 in LogView notebook)
        if self.dashboard and self.log_view:
            cast(Any, self.log_view.notebook).select(2)

    def _hydrate_downloads_window(self) -> None:
        """Restores the visual list of downloads from the backend session."""
        if not self.dashboard or not self.dashboard.download_view:
            return
        view = self.dashboard.download_view
        tasks = self.session.get_active_tasks()
        for tid, info in tasks.items():
            name = str(info.get("name", "Unknown"))
            game = str(info.get("game_domain", "Unknown")).title()
            state = str(info.get("state", "queued"))
            # If app opens and task says "downloading", it was obviously interrupted (crash/close).
            if state == "downloading" or state == "extracting":
                state = "interrupted"
            # Restore bytes so progress bar renders correctly on reopen
            curr = int(info.get("current_bytes", 0))
            total = int(info.get("total_bytes", 0))
            progress = float(info.get("progress", 0.0))

            # Add item (idempotent)
            view.add_task(tid, name, game)

            # Update status
            if state == "installed":
                view.update_task_state(tid, "installed", "Installed 📂")
            elif state == "error":
                err = str(info.get("error", "Error"))
                view.update_task_state(tid, "error", f"Error: {err}")
            elif state == "interrupted":
                view.update_task_state(tid, "interrupted", "Interrupted ⏸️")
            elif state in ("downloading", "extracting"):
                view.update_task_progress(tid, progress, state.title())
            else:
                # Restore progress bar
                pct = (curr / total * 100) if total > 0 else 0
                if pct > 0:
                    view.update_task_progress(tid, pct, "Resumed")
                view.update_task_state(tid, state, state.title())

    def resume_download_task(self, task_id: str) -> None:
        self.session.resume_task(task_id, self._on_download_progress)
        self.show_toast("Resuming download...")

    def remove_download_task(self, task_id: str) -> None:
        """
        Centralized removal method called by BrowserView.
        Removes from Session AND updates App footer state (Bug #1).
        """
        # 1. Remove from Session
        if hasattr(self.session, "remove_task"):
            self.session.remove_task(task_id)
        elif hasattr(self.session, "active_tasks") and self.session.active_tasks:
            self.session.active_tasks.pop(task_id, None)

        # 2. Remove from App active list
        self.active_tasks.discard(task_id)
        if task_id in self._dl_stats:
            del self._dl_stats[task_id]

        # 3. Remove from Browser View
        if self.dashboard and self.dashboard.download_view:
            self.dashboard.download_view.remove_task(task_id)

    def _update_downloads_ui(
        self, task_id: str, percent: float, status: str, mod_name: str
    ) -> None:
        """Helper to update the downloads view if it is open."""
        # Update the view if window is open
        if self.dashboard and self.dashboard.download_view:
            view = self.dashboard.download_view
            view.update_task_name(task_id, mod_name)
            view.update_task_progress(task_id, percent, status)

    def open_settings_dialog(self) -> None:
        """Opens the settings in a separate window."""
        if self.settings_window is None or not self.settings_window.winfo_exists():
            from gmos.ui.settings import SettingsDialog

            self.settings_window = SettingsDialog(cast(tk.Widget, self), self)
        else:
            cast(Any, self.settings_window).lift()

    def open_instance_manager(self) -> None:
        """Opens the Instance Manager."""
        from gmos.ui.instances import InstanceManager

        InstanceManager.create_or_show(cast(tk.Widget, self), self)

    def open_profile_manager(self) -> None:
        """Opens the profile manager."""
        from gmos.ui.profiles import ProfileManagerDialog

        ProfileManagerDialog(self, self)

    # --- GUI Handlers ---
    def get_conflicts_for_mod(self, mod_name: str) -> Dict[str, List[str]]:
        """Returns dict of {target: [other_mod_names]} for the given mod."""
        return self.conflict_cache.get(mod_name, {})

    def update_conflict_status(self) -> None:
        """Checks for conflicts, updates GUI label, and REBUILDS CONFLICT CACHE."""
        active_mods = [
            m
            for m in self.mod_configs
            if m.get("Enabled", True) and m.get("Valid", True)
        ]
        active_mod_names = {str(m.get("Name")) for m in active_mods}
        raw_conflicts = analyze_mods_for_conflicts(cast(List[ModConfig], active_mods))

        # 1. Rebuild Cache for Tooltips
        self.conflict_cache.clear()
        if raw_conflicts:
            for target_key, instructions in raw_conflicts.items():
                # instructions is list of (mod_name, op, details)
                involved_mods = [instr[0] for instr in instructions]
                # For each mod involved, record the conflict
                for mod in involved_mods:
                    others = [m for m in involved_mods if m != mod]
                    if others:
                        # target_key format is Type::res://path::var
                        # We extract just the "res://path" part, then normalize it to "path"
                        parts = target_key.split("::")

                        # Find the part that looks like a res:// path
                        raw_res = ""
                        if len(parts) > 1:
                            raw_res = parts[1]  # Usually index 1
                        else:
                            raw_res = parts[0]

                        # Normalize immediately: "res://script.gd" -> "script.gd"
                        clean_path = res_to_path(raw_res)

                        # Store in cache using the CLEAN path
                        self.conflict_cache[mod][clean_path] = others

        # 2. Update Dashboard UI status
        total_conflicts = len(raw_conflicts)
        unresolved_count = 0
        if raw_conflicts:
            for target_key in raw_conflicts:
                parts = target_key.split("::")
                raw_res = parts[1] if len(parts) > 1 else parts[0]
                clean_path = res_to_path(raw_res)
                winner = self.file_rules.get(clean_path)
                if not winner or winner not in active_mod_names:
                    unresolved_count += 1

        if self.dashboard:
            self.dashboard.update_conflict_status(unresolved_count, total_conflicts)

        # 3. Refresh Listbox colors (Red text for conflicting mods)
        self.refresh_listbox_colors()

    def _apply_listbox_colors(self, tree: ttk.Treeview) -> None:
        """Helper to apply colors to a given treeview based on App state."""
        children = tree.get_children()
        # Configure a new tag for "Resolved Conflicts"
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#333333")
        tree.tag_configure(
            "resolved",
            foreground=utils.get_adaptive_color_variant(theme_bg, "#e6b800", "#c29200"),
        )
        active_mod_names = {
            str(m.get("Name"))
            for m in self.mod_configs
            if m.get("Enabled", True) and m.get("Valid", True)
        }
        for i, item_id in enumerate(children):
            if i >= len(self.mod_configs):
                break

            cfg = self.mod_configs[i]
            name = cfg.get("Name")
            tags = list(tree.item(item_id, "tags"))

            # Reset tags
            tags = [t for t in tags if t not in ("conflict", "disabled", "resolved")]

            if not cfg.get("Enabled", True):
                tags.append("disabled")

            # Check Conflicts
            if name in self.conflict_cache:
                is_fully_resolved = True
                conflicts_map = self.conflict_cache[name]

                for clean_path, _ in conflicts_map.items():
                    winner = self.file_rules.get(clean_path)
                    if not winner or winner not in active_mod_names:
                        is_fully_resolved = False
                        break

                if is_fully_resolved:
                    tags.append("resolved")
                else:
                    tags.append("conflict")

            # Winner Label Logic
            is_winner = name in self.file_rules.values()
            current_text = tree.item(item_id, "text")
            base_text = current_text.replace(" (Winner)", "")

            if is_winner and name in active_mod_names:
                tree.item(item_id, text=f"{base_text} (Winner)", tags=tuple(tags))
            else:
                tree.item(item_id, text=base_text, tags=tuple(tags))

    def refresh_listbox_colors(self) -> None:
        """Updates Treeview tags. Red=Danger, Orange=Resolved."""
        if self.dashboard:
            self._apply_listbox_colors(self.dashboard.mod_tree)

    def _on_mod_double_click(self, event: Any | None = None) -> None:
        sel = self.mod_tree.selection()
        if not sel:
            return

        # 1. Snapshot the current Display Name (to restore selection later)
        current_display_name = self.mod_tree.item(sel[0], "text")

        idx = self.mod_tree.index(sel[0])
        mod = self.mod_configs[idx]

        # 2. Toggle State
        mod["Enabled"] = not mod.get("Enabled", True)

        self.append_log(
            f"Mod '{mod.get('Name', 'Unknown')}' {'enabled' if mod['Enabled'] else 'disabled'} via double-click."
        )

        # 3. Trigger Full Refresh (Sorts dependencies, saves policy, updates UI)
        self.load_mods(mod_configs_override=self.mod_configs)

        # 4. Restore Selection
        if hasattr(self, "dashboard") and self.dashboard:
            self.dashboard.select_mod_by_name(current_display_name)

    def browse_directory(self, var: tk.StringVar) -> None:
        directory = filedialog.askdirectory()
        if directory:
            var.set(safe_norm(directory))

    def browse_file(self, var: tk.StringVar) -> None:
        file_path = filedialog.askopenfilename()
        if file_path:
            var.set(safe_norm(file_path))

    def append_log(self, message: str) -> None:
        if self.log_view:
            self.log_view.append_log(message)

    def show_toast(self, message: str, kind: str = "info") -> None:
        """Display a non-blocking toast notification."""
        try:
            Toast(cast(tk.Widget, self), message, kind=kind)
        except Exception:
            # Fallback to log if toast fails
            pass

    def load_mods(
        self, mod_configs_override: Optional[List[UIModConfig]] = None
    ) -> None:
        """Loads mod configurations via Session (async) or applies override (sync)."""
        # Path 1: Override (Reordering/Resolution) - synchronous update
        if mod_configs_override is not None:
            self.mod_configs = mod_configs_override

            # Sync enabled state back to session mods
            for session_mod in self.session.mods:
                for cfg in self.mod_configs:
                    if session_mod.path == cfg.get("Path"):
                        session_mod.is_enabled = cfg.get("Enabled", True)
                        break

            self.refresh_ui_after_load(save_policy=True)
            return

        # 1. Set busy cursor and force UI update
        self.configure(cursor="watch")
        self.update_idletasks()

        # 2. Update session paths
        self.session.game_dir = safe_norm(self.vars["game_dir"].get())
        self.session.mods_dir = safe_norm(self.vars["mods_dir"].get())

        # Ensure dependencies are injected if session was empty
        if not self.session.game_dir:
            # If config is empty, we can't load mods.
            self.configure(cursor="")
            return

        # 3. Offload to background thread
        get_io_executor().submit(self._load_mods_bg)

    def _load_mods_bg(self) -> None:
        """Background worker for mod loading."""
        try:
            for status in self.session.refresh_mods():
                self.after(0, self.append_log, status)
            self.after(0, self._on_mods_loaded)
        except Exception as e:
            self.after(0, self._on_load_error, e)

    def _on_load_error(self, exc: Exception) -> None:
        self.configure(cursor="")
        logger.exception("Error loading mods", exc_info=exc)
        self.append_log(f"Error loading mods: {exc}")
        messagebox.showerror("Load Error", str(exc))

    def _on_mods_loaded(self) -> None:
        """Callback when session finishes loading mods."""
        # Convert RuntimeMod objects back to UIModConfig for compatibility
        self.mod_configs = []
        for rmod in self.session.mods:
            cfg = cast(UIModConfig, rmod.config)
            # Data Integrity: Deduplication Strategy
            # Use the canonical path as the primary key to prevent duplicate entries
            # caused by rescanning the same directory.
            if any(
                os.path.samefile(c.get("Path", ""), rmod.path)
                for c in self.mod_configs
                if c.get("Path")
            ):
                continue
            # Ensure crucial keys are synchronized
            cfg["Path"] = rmod.path
            cfg["Enabled"] = rmod.is_enabled
            cfg["Valid"] = rmod.is_valid
            cfg["Errors"] = rmod.errors if rmod.errors else None
            cfg["_security_risks"] = rmod.security_risks
            self.mod_configs.append(cfg)

        try:
            gd = safe_norm(self.vars["game_dir"].get())
            if gd:
                # Attempt to load the saved order using policy module
                # If policy.load_load_order doesn't exist in your version,
                # we wrap this in try/except to be safe.
                saved_order = policy.load_load_order(game_dir=gd)
                if saved_order:
                    # Create a map: Name -> Index
                    order_map = {
                        entry["name"]: i for i, entry in enumerate(saved_order)
                    }
                    # Sort: Known mods first (by index), Unknown mods last
                    self.mod_configs.sort(
                        key=lambda m: order_map.get(str(m.get("Name", "")), 999999)
                    )
        except Exception as e:
            logger.debug("Failed to apply saved load order: %s", e)

        self.configure(cursor="")
        self.refresh_ui_after_load(save_policy=True)

    def refresh_ui_after_load(self, save_policy: bool = False) -> None:
        """Updates UI elements based on current self.mod_configs."""
        try:
            gd = safe_norm(self.vars["game_dir"].get())
            if gd and os.path.exists(gd):
                self.file_rules = policy.load_file_rules(game_dir=gd)
            else:
                self.file_rules = {}
            # Rebuild instructions from only valid mods
            self.update_patch_instructions()
            # Save the potentially updated list (e.g. new mods added)
            if save_policy:
                try:
                    gd = safe_norm(self.vars["game_dir"].get())
                    policy.save_load_order(
                        cast(List[Dict[str, Any]], self.mod_configs), game_dir=gd
                    )
                except Exception:
                    pass

            # Apply Dashboard Filter (Search)
            if self.dashboard:
                visible_mods = self.dashboard.filter_mods(self.mod_configs)
                icons = self.dashboard.icons
            else:
                visible_mods = self.mod_configs
                icons = None

            # Rebuild Tree
            rebuild_mod_tree(
                self.mod_tree,
                visible_mods,
                get_mod_name_from_config,
                icon_map=icons,
                app_ref=self,
            )
            # Trigger Empty State Overlay if needed
            if self.dashboard:
                self.dashboard.set_empty_state(len(visible_mods) == 0)
            self.update_conflict_status()
            invalid_count = sum(1 for m in self.mod_configs if not m.get("Valid", True))
            self.append_log(
                f"Loaded {len(self.mod_configs)} mods ({invalid_count} invalid)."
            )
            # Auto-select the first item if nothing is selected
            if not self.mod_tree.selection() and self.mod_configs:
                first_item = self.mod_tree.get_children()[0]
                self.mod_tree.selection_set(first_item)
                self.mod_tree.focus(first_item)
                # Manually trigger the selection event to update the side panel
                if self.dashboard:
                    self.dashboard.on_mod_selection_change()
            # Update ModInfoPane to reflect current selection (or clear it)
            try:
                # Access mod_info through dashboard if needed, or rely on dashboard update
                pass
            except Exception:
                pass

        except Exception as e:
            logger.error("Error refreshing UI: %s", e)

    def update_ui_after_rollback(self, message: str) -> None:
        """
        Updates the UI elements after a successful rollback operation.
        Calls rebuild_mod_tree to refresh the mod list
        to reflect the (now clean) state of the game directory.
        :param message: The success message from the rollback operation.
        """
        try:
            # Call the global rebuild function
            rebuild_mod_tree(
                self.mod_tree, self.mod_configs, get_mod_name_from_config, app_ref=self
            )

            # Update main status and application title
            self.conflict_label.configure(
                text="Rollback successful. Ready to patch.", fg="green"
            )
            self.conflict_label.winfo_toplevel().wm_title("GMOS - Ready")

            self.append_log(f"INFO: Rollback complete. {message}")
            self.show_toast("Rollback Successful", kind="success")

        except Exception as e:
            logger.exception("update_ui_after_rollback failed: %s", e)
            self.append_log(f"ERROR: Failed to fully refresh UI after rollback: {e}")
            messagebox.showerror(
                "UI Update Error", "Failed to refresh UI after rollback."
            )

    def rollback_game_files(self) -> None:
        """Preview and selectively restore *.bak files in game_dir."""
        try:
            self.append_log("Rollback: invoked")
        except Exception as e:
            logger.debug("Rollback init log failed: %s", e, exc_info=True)

        game_dir = self.vars.get("game_dir", tk.StringVar()).get()
        if not game_dir or not os.path.isdir(game_dir):
            messagebox.showinfo("Rollback", f"No game directory found: {game_dir}")
            self.append_log(f"Rollback: missing dir: {game_dir}")
            return
        if not messagebox.askyesno(
            "Confirm Scan",
            "Scanning for backup files can take some time.\n\nStart scan?",
        ):
            return

        dlg = ProgressDialog(cast(tk.Widget, self), title="Scanning")
        dlg.set_text("Scanning for backup files...")
        dlg.start()

        bak_list: List[str] = []

        def _scan_worker() -> None:
            try:
                for root, _, files in os.walk(game_dir):
                    if dlg.cancelled():
                        break
                    for fn in files:
                        if fn.endswith(".bak"):
                            full = os.path.join(root, fn)
                            rel = os.path.relpath(full, game_dir)
                            bak_list.append(rel)
            except Exception as err:
                err_msg = str(err)

                def _log_and_show() -> None:
                    messagebox.showerror(
                        "Rollback Error", f"Failed scanning game_dir: {err_msg}"
                    )
                    self.append_log(f"Rollback error: {err_msg}")

                self.after(0, _log_and_show)
            finally:
                self.after(0, _on_scan_done)

        def _on_scan_done() -> None:
            dlg.close()
            if dlg.cancelled():
                self.append_log("Rollback scan cancelled.")
                return

            self.append_log(f"Rollback: found {len(bak_list)} .bak files")
            if not bak_list:
                messagebox.showinfo(
                    "Rollback", f"No backup (.bak) files found in {game_dir}."
                )
                return

            try:
                RollbackDialog(
                    parent=cast(tk.Widget, self),
                    game_dir=game_dir,
                    bak_list=bak_list,
                    on_success=self.update_ui_after_rollback,
                    append_log=self.append_log,
                )
            except Exception as e:
                messagebox.showerror(
                    "Rollback Error", f"Cannot create preview window: {e}"
                )

        threading.Thread(target=_scan_worker, daemon=True).start()

    def create_support_bundle(self) -> None:
        """Create a support zip containing logs and runtime_manifest from game_dir (if present)."""
        try:
            game_dir = safe_norm(self.vars["game_dir"].get())
        except Exception:
            game_dir = None

        # default filename
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        default_dir = os.path.join(os.path.expanduser("~"), "Documents")
        os.makedirs(default_dir, exist_ok=True)
        default = os.path.join(default_dir, f"gmos_support_{ts}.zip")
        try:
            out = filedialog.asksaveasfilename(
                defaultextension=".zip", initialfile=os.path.basename(default)
            )
            if not out:
                return
        except Exception:
            logger.exception("create_support_bundle: file dialog failed")
            return

        try:
            with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                # include main log
                main_log = os.path.join(LOG_DIR, "gmos.log")
                if os.path.exists(main_log):
                    zf.write(main_log, os.path.join("logs", "gmos.log"))

                # include any recent dryrun bundles (zip) from LOG_DIR
                for fn in sorted(os.listdir(LOG_DIR)):
                    if fn.startswith("dryrun_bundle_") and fn.endswith(".zip"):
                        zf.write(os.path.join(LOG_DIR, fn), os.path.join("logs", fn))

                # include runtime_manifest from game_dir if exists
                if game_dir:
                    candidate = os.path.join(game_dir, "runtime_manifest.json")
                    if os.path.exists(candidate):
                        zf.write(
                            candidate,
                            os.path.join("game_dir", "runtime_manifest.json"),
                        )

                # include patch.log if present in game_dir or ROOT_DIR
                for candidate in [
                    os.path.join(game_dir or "", "patch.log"),
                    os.path.join(ROOT_DIR, "patch.log"),
                ]:
                    if candidate and os.path.exists(candidate):
                        zf.write(
                            candidate,
                            os.path.join("game_dir", os.path.basename(candidate)),
                        )

            messagebox.showinfo("Support Bundle", f"Support bundle saved: {out}")
            logger.info("Support bundle created: %s", out)
        except Exception as e:
            logger.exception("Failed creating support bundle: %s", e)
            try:
                messagebox.showerror(
                    "Support Bundle Error", f"Failed to create bundle: {e}"
                )
            except Exception as e:
                logger.debug(
                    "Failed to append log message after support bundle creation: %s",
                    e,
                    exc_info=True,
                )

    def export_mod_order(self) -> None:
        """Export the current setup as a standardized GMOS Profile."""
        if not self.mod_configs:
            messagebox.showinfo("Export Mod Order", "No mods to export.")
            return
        save_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            title="Save mod order",
        )
        if not save_path:
            return
        try:
            # Use standardized profile export
            profile_data = profiles.create_profile_data(
                cast(List[Dict[str, Any]], self.mod_configs), self.cfg
            )
            profiles.save_profile_to_disk(profile_data, save_path)
            messagebox.showinfo("Export Complete", f"Profile exported to:\n{save_path}")
            self.append_log(f"Exported mod order to {save_path}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export: {e}")
            self.append_log(f"Export mod order failed: {e}")

    def import_mod_order(self) -> None:
        """Import a GMOS Profile and apply it."""
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")], title="Import mod order"
        )
        if not path:
            return
        try:
            # Use standardized profile import
            profile = profiles.load_profile_from_disk(path)

            # Apply profile logic (reorder, enable/disable)
            # Cast to Any to satisfy the type checker for the generic dict list
            current_raw = cast(List[Dict[str, Any]], self.mod_configs)
            new_order_raw, warnings = profiles.apply_profile_to_configs(
                profile, current_raw
            )

            # Cast back to UIModConfig for the App
            new_order = cast(List[UIModConfig], new_order_raw)

            self.load_mods(mod_configs_override=new_order)

            missing = len(profile["mods"]) - sum(
                1 for m in new_order if m.get("Enabled")
            )
            msg = "Profile applied successfully."
            if missing > 0:
                msg += f"\n\nWarning: {missing} mods from the profile were not found locally."
            if warnings:
                msg += "\n\n⚠️ Version Warnings:\n" + "\n".join(warnings[:10])
                if len(warnings) > 10:
                    msg += "\n... and more."

            messagebox.showinfo("Import Complete", msg)
            self.append_log(f"Imported profile from {path}")
            # Save the new order to policy immediately
            try:
                gd = safe_norm(self.vars["game_dir"].get())
                policy.save_load_order(
                    cast(List[Dict[str, Any]], self.mod_configs), game_dir=gd
                )
            except Exception:
                pass
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to import profile: {e}")
            self.append_log(f"Import profile failed: {e}")
            return

    def update_patch_instructions(self) -> None:
        """
        Generates the single, combined patch plan based on current mod order.
        Skips invalid mods and logs per-mod validation errors.
        """
        self.instructions = []
        skipped: List[str] = []
        for mod_config in self.mod_configs:
            if not mod_config.get("Valid", True):
                skipped.append(str(mod_config.get("Name", "Unknown")))
                continue
            if not mod_config.get("Enabled", True):
                skipped.append(str(mod_config.get("Name", "Unknown")) + " (disabled)")
                continue
            mod_path = mod_config.get("Path")
            if not mod_path:
                skipped.append(
                    str(mod_config.get("Name", "Unknown")) + " (missing path)"
                )
                continue
            try:
                # Use cached plan if available to avoid re-parsing scripts on every move
                if "_cached_plan" in mod_config:
                    plan = mod_config["_cached_plan"]
                else:
                    plan = generate_patch_plan(mod_path, mod_config)
                    mod_config["_cached_plan"] = plan
                # Keep the plan in mod order
                self.instructions.extend(plan)
            except Exception as e:
                # Mark mod invalid and record error
                mod_config["Valid"] = False
                mod_config["Errors"] = [str(e)]
                skipped.append(str(mod_config.get("Name", "Unknown")))
                self.append_log(
                    f"ERROR: Failed to generate patch plan for '{mod_config.get('Name', 'Unknown')}': {e}"
                )
        self.append_log(
            f"Generated {len(self.instructions)} patch instructions. Skipped mods: {', '.join(skipped) if skipped else 'none'}."
        )

    def move_selected_mod(self, direction: int) -> None:
        """Moves the selected mod up (-1) or down (1) in the list."""
        try:
            selection = self.mod_tree.selection()
            if not selection:
                return
            index = self.mod_tree.index(selection[0])
            new_index = index + direction

            if 0 <= new_index < len(self.mod_configs):
                # Update internal config order
                mod_config_to_move = self.mod_configs.pop(index)
                self.mod_configs.insert(new_index, mod_config_to_move)

                ordered_mods, _ = apply_dependency_resolution(
                    cast(List[ModConfig], self.mod_configs)
                )
                rebuild_mod_tree(
                    self.mod_tree,
                    cast(List[UIModConfig], ordered_mods),
                    get_mod_name_from_config,
                    app_ref=self,
                )
                # The treeview creates new IDs on rebuild, so we select by integer index
                children = self.mod_tree.get_children()
                if new_index < len(children):
                    new_item = children[new_index]
                    self.mod_tree.selection_set(new_item)
                    self.mod_tree.see(new_item)  # Scroll to ensure visible
                    self.mod_tree.focus(new_item)
                self.update_patch_instructions()
                self.update_conflict_status()
                # Auto-save policy on move
                gd = safe_norm(self.vars["game_dir"].get())
                policy.save_load_order(
                    cast(List[Dict[str, Any]], self.mod_configs), game_dir=gd
                )
        except Exception as e:
            self.append_log(f"Error reordering mod: {e}")

    def open_mod_folder(self, mod_name: str) -> None:
        """Open the mod folder in file explorer for convenience."""
        mod = next(
            (m for m in self.mod_configs if m.get("Name") == mod_name),
            None,
        )
        if not mod or "Path" not in mod:
            self.append_log(f"Open folder failed: mod not found: {mod_name}")
            return
        try:
            webbrowser.open(mod["Path"])
        except Exception as e:
            self.append_log(f"Failed to open mod folder {mod_name}: {e}")

    def delete_mod_from_disk(self, mod_cfg: UIModConfig) -> None:
        """Permanently deletes the mod folder from the disk."""
        path = mod_cfg.get("Path")
        if not path or not os.path.exists(path):
            return
            # 1. Clear selection in dashboard to ensure no UI elements are holding locks
            if self.dashboard:
                self.dashboard.mod_tree.selection_remove(
                    self.dashboard.mod_tree.selection()
                )
                self.dashboard.mod_info.set_empty()

            # 2. Aggressive deletion
        try:
            if os.path.isfile(path):
                os.chmod(path, stat.S_IWRITE)
                os.remove(path)
            else:
                safe_rmtree(path)

            self.show_toast(f"Deleted {mod_cfg.get('Name')}", kind="success")
            self.load_mods()
        except Exception as e:
            messagebox.showerror("Delete Error", f"Could not delete mod: {e}")
            logger.error(f"Delete failed for {path}: {e}")

    def open_current_instance_website(self) -> None:
        """Opens the website configured for the current instance."""
        url = self.cfg.get("mod_website", "")
        if url:
            webbrowser.open(url)
        else:
            if messagebox.askyesno(
                "No Website Set",
                "This instance has no mod website configured.\n\nOpen Instance Manager to set one?",
            ):
                self.open_instance_manager()

    def install_mod_from_archive(self, zip_path: str) -> None:
        """Unpacks a validated zip file into the mods directory."""
        try:

            mods_dir = self.vars["mods_dir"].get()

            # Create a folder name based on the zip filename (stripped of .zip)
            mod_name = os.path.splitext(os.path.basename(zip_path))[0]
            target_dir = os.path.join(mods_dir, mod_name)

            os.makedirs(target_dir, exist_ok=True)

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(target_dir)

            self.show_toast(f"Installed {mod_name}", kind="success")
            self.load_mods()

        except Exception as e:
            messagebox.showerror("Install Error", f"Failed to install mod:\n{e}")

    def select_mod_in_main_list(self, mod_name: str) -> None:
        """Select and focus a mod by name in the main mod list box."""
        for idx, m in enumerate(self.mod_configs):
            if m.get("Name") == mod_name:
                try:
                    # Find child by index
                    children = self.mod_tree.get_children()
                    if idx < len(children):
                        item = children[idx]
                        self.mod_tree.selection_set(item)
                        self.mod_tree.see(item)
                        self.mod_tree.focus(item)
                except Exception as e:
                    # Listbox selection/view operation failed (often due to platform/race condition)
                    logger.debug(
                        "Failed to select/focus mod in main listbox: %s",
                        e,
                        exc_info=True,
                    )
                return
        self.append_log(f"Select failed. Mod not found: {mod_name}")

    def toggle_selected_mod(self) -> None:
        """Enable/disable the currently selected mod."""
        try:
            sel = self.mod_tree.selection()
            if not sel:
                return
            idx = self.mod_tree.index(sel[0])
            mod = self.mod_configs[idx]

            # 1. Toggle State
            mod["Enabled"] = not mod.get("Enabled", True)

            self.append_log(
                f"Mod '{mod.get('Name')}' {'enabled' if mod['Enabled'] else 'disabled'}."
            )

            # 2. Trigger Full Refresh (Sorts dependencies, saves policy, updates UI)
            self.load_mods(mod_configs_override=self.mod_configs)

        except Exception as e:
            self.append_log(f"Error toggling mod: {e}")

    def toggle_mod_enabled_by_name(self, mod_name: str) -> None:
        """Toggle Enabled flag for a mod by name and refresh lists."""
        for m in self.mod_configs:
            if m.get("Name") == mod_name:
                m["Enabled"] = not m.get("Enabled", True)
                self.append_log(
                    f"Mod '{mod_name}' {'enabled' if m['Enabled'] else 'disabled'} (toggle)."
                )
                self.load_mods(mod_configs_override=self.mod_configs)
                return
        self.append_log(f"Toggle failed. Mod not found: {mod_name}")

    def resolve(self, file_path: str, orig_text: str, new_text: str) -> Optional[str]:
        """Fallback conflict resolution to fulfill ConflictDelegate protocol."""
        logger.warning(
            "Unresolved conflict hit at runtime for %s. Defaulting to overwrite.",
            file_path,
        )
        return new_text

    def run_patcher_action(self) -> None:
        """
        Orchestrates the patching process via the Session.
        """
        if not self.mod_configs:
            messagebox.showwarning("No Mods", "No mods are loaded to patch.")
            return

        exe_name = self.vars["game_executable"].get()
        if exe_name:
            exe_basename = os.path.basename(exe_name).lower()
            is_running = False
            try:
                for proc in psutil.process_iter(["name"]):
                    if proc.info["name"] and proc.info["name"].lower() == exe_basename:
                        is_running = True
                        break
            except ImportError:
                if os.name == "nt":
                    try:
                        output = subprocess.check_output(
                            f'tasklist /FI "IMAGENAME eq {os.path.basename(exe_name)}"',
                            shell=True,
                            text=True,
                        )
                        if exe_basename in output.lower():
                            is_running = True
                    except Exception:
                        pass

            if is_running:
                messagebox.showerror(
                    "Error",
                    f"The game ({exe_name}) is currently running.\nPlease close it before patching.",
                )
                return

        if not messagebox.askyesno(
            "Confirm Patch",
            "This will revert the game directory to vanilla and apply all enabled mods.\n\nContinue?",
        ):
            return

        self.configure(cursor="watch")
        self._is_busy = True  # Lock exit
        self.patch_btn.config(state=tk.DISABLED)
        self.append_log("\n--- Starting Patch Process ---")
        self.update_idletasks()

        # Execute in background using the shared IO executor
        get_io_executor().submit(self._patch_worker_session)

    def _patch_worker_session(self) -> None:
        """
        Background worker that drives the session.apply_changes generator.
        This replaces the old nested _patch_worker closure.
        """
        try:
            exe_name = self.vars["game_executable"].get()
            is_packed = self.cfg.get("is_packed", False)
            # self.session.apply_changes yields log strings.
            # We pass 'self' as the conflict_delegate because App implements resolve().
            for msg in self.session.apply_changes(
                game_executable=exe_name, is_packed=is_packed, conflict_delegate=self
            ):
                self.after(0, self.append_log, msg)

            self.after(0, lambda: self.set_status("Patching complete."))
            self.after(
                0, lambda: messagebox.showinfo("Success", "Patching run finished.")
            )
        except Exception as e:
            # Re-use the load error handler for consistency
            self.after(0, self._on_load_error, e)
        finally:
            # Clean up UI state on the main thread
            def _cleanup() -> None:
                self.configure(cursor="")
                self._is_busy = False  # Unlock exit
                self.patch_btn.config(state=tk.NORMAL)

            self.after(0, _cleanup)

    def set_status(self, text: str) -> None:
        self.conflict_label.configure(text=text, fg="blue")

    def launch_executable_generic(self, exe_path: str, args: str, cwd: str) -> None:
        """
        Generic helper to launch any tool/executable from the dashboard.
        """
        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            if not exe_path:
                raise FileNotFoundError("No executable path provided.")

            # Resolve relative paths against game dir if possible, otherwise absolute
            game_dir = safe_norm(self.vars["game_dir"].get())

            final_exe = exe_path
            if not os.path.isabs(exe_path) and game_dir:
                possible = os.path.join(game_dir, exe_path)
                if os.path.exists(possible):
                    final_exe = possible

            final_cwd = (
                cwd if cwd else (game_dir if game_dir else os.path.dirname(final_exe))
            )

            cmd = [final_exe] + shlex.split(args)

            self.append_log(f"Launching tool: {final_exe}")
            safe_spawn(
                cmd,
                cwd=final_cwd,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            messagebox.showerror("Launch Error", str(e))
            self.append_log(f"Error launching tool: {e}")
        finally:
            self.configure(cursor="")

    def start_game_action(self) -> None:
        """Launches the game executable from the working directory to ensure all modded files are used."""

        # 1. Set busy cursor and force UI update while we prepare paths
        self.configure(cursor="watch")
        self.update_idletasks()

        # Auto-Inject Sandbox if enabled
        if self.global_cfg.sandbox_enabled:
            try:
                injector = SandboxInjector(safe_norm(self.vars["game_dir"].get()))
                if not injector.is_injected():
                    self.append_log("Auto-injecting sandbox...")
                    injector.inject()
            except Exception as e:
                self.append_log(f"Auto-injection warning: {e}")

        # Update Last Played

        self.cfg["last_played"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.save_config()
        game_dir = safe_norm(self.vars.get("game_dir", tk.StringVar()).get())
        executable_name = self.vars["game_executable"].get()
        launch_override = self.vars["launch_override"].get()
        game_exe_path: str | None = None

        try:
            # 2. Define and validate paths
            # Force absolute path.
            # This solves the issue where os.path.join(".", "game.exe") results in ".\game.exe",
            # which shutil.which() fails to find because "." is not in the system PATH.
            game_exe_path = os.path.abspath(os.path.join(game_dir, executable_name))

            # 3. Determine launch command (Godot-specific arguments included for context)
            command: List[str] = [game_exe_path, "--path", game_dir]
            if launch_override:
                command.extend(shlex.split(launch_override))
            # Apply Profile Isolation if active
            active_profile = self.cfg.get("active_profile")
            if active_profile:
                profile_path = os.path.join(
                    game_dir, "profiles", f"{active_profile}.json"
                )
                if os.path.exists(profile_path):
                    try:
                        with open(profile_path, "r", encoding="utf-8") as f:
                            prof_data = json.load(f)
                        iso = prof_data.get("isolation", {})
                        if iso.get("isolate_data"):
                            user_dir = os.path.join(
                                game_dir, "profiles", active_profile, "userdata"
                            )
                            os.makedirs(user_dir, exist_ok=True)
                            command.extend(["--user-data-dir", user_dir])
                    except Exception as e:
                        logger.error("Failed to read profile isolation config: %s", e)
            self.append_log("--- Attempting to Launch Game ---")
            self.append_log(f"Executable: {game_exe_path}")
            self.append_log(f"Game Directory: {game_dir}")

            self.append_log(f"Launch Command: {shlex.join(command)}")

            # 4. Launch process
            command_list = list(command)

            if not command_list:
                raise RuntimeError("Empty launch command.")

            # Try to resolve executable path safely
            exe = command_list[0]
            exe_path: str | None = exe
            if not os.path.isabs(exe):
                exe_path = shutil.which(exe, path=os.environ.get("PATH"))
            if exe_path is None:
                raise RuntimeError(f"Cannot locate executable: {exe}")

            # Use safe_spawn to launch asynchronously and cleanly detach I/O
            safe_spawn(
                [exe_path] + command_list[1:],
                cwd=game_dir,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.append_log("SUCCESS: Game launched from the modded directory.")

        except FileNotFoundError as e:
            messagebox.showerror("Launch Error: File Not Found", str(e))
            self.append_log(f"ERROR: File missing during launch preparation: {e}")

        except PermissionError as e:
            # Handle specific permission issues during copy or execution
            handle_permission_error(
                RuntimeError(
                    f"Launch Error: Permission Denied. Failed to copy or execute file: {e}\n\nEnsure GMOS has read/write access and the file isn't locked by another program."
                ),
                game_dir,
                parent=self,
            )
            self.append_log(f"ERROR: Permission error during launch: {e}")

        except Exception as e:
            # Catch all other critical errors (RuntimeError, OS errors, etc.)
            logger.exception("FATAL ERROR during game launch: %s", e)
            messagebox.showerror("Launch Error", f"Failed to launch game:\n{e}")
            self.append_log(f"FATAL ERROR during game launch: {e}")

        finally:
            # 6. Always reset the cursor
            self.configure(cursor="")
            self.update_idletasks()

    def clear_cache_action(self) -> None:
        """Purges the Godot import cache to fix stale asset issues."""
        game_dir = safe_norm(self.vars["game_dir"].get())
        if not os.path.isdir(game_dir):
            messagebox.showerror("Error", "Game directory not set.")
            return

        if not messagebox.askyesno(
            "Clear Cache",
            "This will delete the Godot internal asset cache (.import).\nThe game will re-import assets on next launch (which may take a moment).\n\nProceed?",
        ):
            return

        self.configure(cursor="watch")
        get_io_executor().submit(self._clear_cache_worker, game_dir)

    def _clear_cache_worker(self, game_dir: str) -> None:
        try:
            count = cache.purge_cache(game_dir)
            self.after(
                0, lambda: self.append_log(f"Cache cleared. Removed ~{count} files.")
            )
            self.after(
                0, lambda: messagebox.showinfo("Success", "Cache cleared successfully.")
            )
        except Exception as e:
            logger.exception("Cache clear failed: %s", e)
            err_str = str(e)
            self.after(
                0,
                lambda: messagebox.showerror(
                    "Error", f"Failed to clear cache: {err_str}"
                ),
            )
            self.after(
                0, lambda: self.append_log(f"ERROR: Cache clear failed: {err_str}")
            )
        finally:
            self.after(0, lambda: self.configure(cursor=""))

    def simulate_and_diff_action(self) -> None:
        """
        Simulate the patch in a temp dir.
        (Visual diff preview is now handled by Merge Studio, this just generates the dryrun artifact).
        """
        if not self.instructions:
            messagebox.showwarning("Warning", "No mod instructions loaded.")
            return

        game_dir = safe_norm(self.vars["game_dir"].get())
        if not os.path.isdir(game_dir):
            messagebox.showerror("Error", f"Game directory not found: {game_dir}")
            return

        self.configure(cursor="watch")
        self.update_idletasks()
        self.append_log("--- Starting Patch Simulation ---")
        # Offload to background
        get_io_executor().submit(self._simulate_worker, game_dir)

    def _simulate_worker(self, game_dir: str) -> None:
        """Background worker for simulation and diff generation."""
        try:
            touched_by: Dict[str, Set[str]] = defaultdict(set)
            for entry in self.instructions:
                tr: str | None = None
                try:
                    # instructions expected as (mod_name, op, details)
                    mod_name = entry[0]
                    op = entry[1]
                    details = entry[2] if len(entry) > 2 else None

                    if op in ("FileReplace", "VariablePatch", "FunctionPatch"):
                        tr = details[0] if details else None
                    elif details and len(details) > 0:
                        tr = details[0]

                    if tr:
                        touched_by[res_to_path(tr)].add(mod_name)
                except Exception as e:
                    logger.debug(
                        "Failed to register instruction touch for target %s: %s",
                        tr,
                        e,
                        exc_info=True,
                    )
                    continue

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_work_root = os.path.join(temp_dir, "sim_work")
                Path(temp_work_root).mkdir(parents=True, exist_ok=True)

                # We also create a dummy project.godot to pass the sanity check.
                Path(os.path.join(temp_work_root, "project.godot")).touch()

                for rel_path in touched_by.keys():
                    # Determine best source: Backup > Loose > PCK
                    src_file = os.path.join(game_dir, rel_path)
                    bak_file = src_file + ".bak"
                    dest_file = os.path.join(temp_work_root, rel_path)

                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)

                    try:
                        if os.path.exists(bak_file):
                            shutil.copy2(bak_file, dest_file)
                        elif os.path.exists(src_file):
                            shutil.copy2(src_file, dest_file)
                        else:
                            # We use the game_dir to find PCKs, but write to temp_work_root
                            res_path = f"res://{rel_path.replace(os.sep, '/')}"
                            # Scan game dir for PCKs
                            pck_found = False
                            with os.scandir(game_dir) as it:
                                for pck_entry in it:
                                    if pck_entry.name.endswith(".pck"):
                                        content = pck_tools.get_file_content(
                                            pck_entry.path, res_path
                                        )
                                        if content:
                                            with open(dest_file, "wb") as f:
                                                f.write(content)
                                            pck_found = True
                                            break
                            if not pck_found:
                                pass  # Might be a 'create' operation, so missing source is fine
                    except Exception as e:
                        logger.debug(
                            f"Failed to pre-seed {rel_path} for simulation: {e}"
                        )

                # Run patcher on the pre-seeded temp directory
                sim_log = run_patcher(temp_work_root, self.instructions)

                patched_rel_paths: List[str] = []
                manifest_path = os.path.join(temp_work_root, "runtime_manifest.json")

                try:
                    # Prefer runtime_manifest.json for modified file list (more reliable).
                    if os.path.exists(manifest_path):
                        with open(manifest_path, "r", encoding="utf-8") as mf:
                            manifest = json.load(mf)
                            patched_rel_paths = manifest.get("modified_files", []) or []

                            def _log_manifest() -> None:
                                self.append_log(
                                    f"Used runtime_manifest.json for diff (found {len(patched_rel_paths)} files)."
                                )

                            self.after(0, _log_manifest)

                except Exception as sim_exc:
                    logger.exception("Failed to read manifest or parse sim_log.")
                    err_msg = f"Warning: failed to read runtime_manifest.json or parse sim_log: {str(sim_exc)}"
                    self.after(0, lambda: self.append_log(err_msg))
                    if not patched_rel_paths:
                        self.after(
                            0,
                            lambda: self.append_log(
                                "No files were modified during the simulation."
                            ),
                        )
                        return

                # Deduplicate while preserving order
                seen: Set[str] = set()
                dedup_paths: List[str] = []
                for p in patched_rel_paths:
                    if p not in seen:
                        seen.add(p)
                        dedup_paths.append(p)

                combined_parts: List[str] = []
                for rel in dedup_paths:
                    # Compare the Result (Temp) against the Source (Game Dir / Backup)
                    # We prioritize the .bak file in the Game Dir as the "Original" reference
                    # to ensure the diff shows changes vs Vanilla, not changes vs Dirty State.
                    real_path = os.path.join(game_dir, rel)
                    bak_path = real_path + ".bak"

                    if os.path.exists(bak_path):
                        orig_path = bak_path
                        label_orig = f"original/{rel} (backup)"
                    else:
                        orig_path = real_path
                        label_orig = f"original/{rel} (current)"

                    patched_path = os.path.join(temp_work_root, rel)

                    header = f"\n===== File: {rel} =====\n"
                    mods = touched_by.get(rel, set())
                    mods_list = sorted(mods)
                    header += f"Mods touching this file: {', '.join(mods_list) if mods_list else 'unknown'}\n\n"
                    header_view = self.log_view
                    if header_view:

                        def _append_diff(
                            h: str = header, hv: Any = header_view
                        ) -> None:
                            if hv:
                                hv.append_diff(h)

                        self.after(0, _append_diff)
                    combined_parts.append(header)

                    def _is_safe_text(p: str) -> bool:
                        if not os.path.exists(p):
                            return True
                        if os.path.getsize(p) > 5 * 1024 * 1024:
                            return False
                        try:
                            with open(p, "rb") as f:
                                chunk = f.read(4096)
                                if b"\0" in chunk:
                                    return False
                        except Exception:
                            return False
                        return True

                    orig_lines: List[str] = []
                    try:
                        if _is_safe_text(orig_path):
                            with open(
                                orig_path, "r", encoding="utf-8", errors="ignore"
                            ) as f_orig:
                                orig_lines = f_orig.readlines()
                    except Exception:
                        orig_lines = []

                    patched_lines: List[str] = []
                    try:
                        if _is_safe_text(patched_path):
                            with open(
                                patched_path, "r", encoding="utf-8", errors="ignore"
                            ) as f_patch:
                                patched_lines = f_patch.readlines()
                    except Exception:
                        patched_lines = []
                    diff_iter = difflib.unified_diff(
                        orig_lines,
                        patched_lines,
                        fromfile=label_orig,
                        tofile=f"simulated/{rel}",
                        lineterm="",
                    )
                    diff_text = "\n".join(diff_iter)

                    if not diff_text:
                        # Heuristic binary detection: check for NUL in first 4KiB
                        is_binary = False
                        try:

                            def _sample_has_nul(p: str | os.PathLike[str]) -> bool:
                                if not os.path.exists(p):
                                    return False
                                s = safe_read_bytes(os.fspath(p))[:4096]
                                return b"\0" in s

                            if _sample_has_nul(orig_path) or _sample_has_nul(
                                patched_path
                            ):
                                is_binary = True
                        except Exception:
                            is_binary = False

                        if is_binary:
                            diff_text = "(BINARY FILE — no textual diff available.)"
                        else:
                            diff_text = "(No textual diff; files identical or only formatting changes.)"

                    # Log generation only - no visual update in legacy tab
                    combined_parts.append(diff_text + "\n")

                combined = "\n".join(combined_parts)
                try:
                    # Call the consolidated save_dryrun_artifact from patcher
                    save_dryrun_artifact(
                        sim_log,
                        temp_work_root,
                        game_dir,
                        out_dir=LOG_DIR,
                        combined_diff=combined,  # Pass the combined diff
                    )
                    self.after(
                        0, lambda: self.append_log("Dryrun artifact saved to logs.")
                    )
                except Exception:
                    logger.exception(
                        "Failed to save combined diff into dryrun artifact"
                    )

        except Exception as e:
            logger.exception("Critical error during simulate_and_diff_action: %s", e)
            err_str = str(e)

            def _show_err() -> None:
                messagebox.showerror(
                    "Simulation Error",
                    f"An unexpected error occurred during the patch simulation:\n\n{err_str}\n\nPlease check the log file for details.",
                )

            self.after(0, _show_err)

        finally:
            self.after(0, lambda: self.configure(cursor=""))

    def view_runtime_manifest(self) -> None:
        """Open runtime_manifest.json from game_dir in system viewer or show an error."""
        game_dir = safe_norm(self.vars["game_dir"].get())
        manifest_path = os.path.join(game_dir, "runtime_manifest.json")
        if not os.path.exists(manifest_path):
            messagebox.showinfo(
                "Runtime Manifest", f"No runtime_manifest.json found in {game_dir}"
            )
            return
        try:
            webbrowser.open(manifest_path)
            self.append_log(f"Opened runtime_manifest: {manifest_path}")
        except Exception as e:
            messagebox.showerror("Runtime Manifest", f"Failed to open manifest: {e}")
            self.append_log(f"Open manifest failed: {e}")

    def open_developer_tools(self) -> None:
        """Opens the Developer Tools window, ensuring only one instance exists."""
        # Prevent multiple instances
        if self.dev_tools_window is not None and self.dev_tools_window.winfo_exists():
            cast(Any, self.dev_tools_window).lift()
            self.dev_tools_window.focus_force()
            return

        if not self.vars["game_dir"].get():
            messagebox.showerror("Error", "Please select a game directory first.")
            return

        self.dev_tools_window = DeveloperToolsDialog(self)

    def on_close(self) -> None:
        # Background Downloader
        if getattr(self, "_is_busy", False):
            messagebox.showwarning(
                "Cannot Close",
                "GMOS is currently working (patching/restoring).\nPlease wait until the process finishes to prevent file corruption.",
            )
            return
        if self.active_tasks:
            # Offer to run in background
            ans = messagebox.askyesnocancel(
                "Downloads Active",
                "Downloads are still in progress.\n\nYes: Finish in background and exit.\nNo: Quit immediately (interrupts downloads).",
            )
            if ans is None:  # Cancel
                return
            if ans:  # Yes -> Background Mode
                self.withdraw()  # Hide window
                self.show_toast("GMOS is downloading in background...", kind="info")
                self._background_monitor()
                return
            # No -> Proceed to standard exit (tasks will be marked interrupted on next boot)
        if hasattr(self, "ipc_listener"):
            self.ipc_listener.stop()
        self.save_config()
        self.destroy()
        os._exit(0)

    def _background_monitor(self) -> None:
        """Checks if downloads are done while running in background mode."""
        if not self.active_tasks:
            self.save_config()
            self.destroy()
            os._exit(0)
        else:
            # Keep checking every second
            self.after(1000, self._background_monitor)
