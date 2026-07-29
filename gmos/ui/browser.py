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
Downloads View
Features a bandwidth graph and a list for managing downloads.
"""

import os
import tkinter as tk
import webbrowser
from collections import deque
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, cast

from gmos import utils
from gmos.net.api import get_rate_limits
from gmos.state.config import load_global_config
from gmos.ui.widgets import AutoScrollbar

if TYPE_CHECKING:
    from gmos.ui.app import App


class NetworkGraph(tk.Canvas):
    def __init__(self, parent: tk.Widget, height: int = 60, **kwargs: Any):
        super().__init__(
            parent, height=height, bg="#222222", highlightthickness=0, **kwargs
        )
        self.maxlen = 200
        self.data: deque[float] = deque(maxlen=self.maxlen)
        self.max_val: float = 0.5

        self._width = 1
        self._height = 1
        self._ch_height = 1.0  # Cached chart height (height * 0.9)
        self._x_step = 1.0

        self._low_rgb: Tuple[int, int, int] = (0, 0, 0)
        self._high_rgb: Tuple[int, int, int] = (0, 0, 0)

        self._mid_line = self.create_line(0, 0, 0, 0, fill="#333333", dash=(2, 2))
        self._top_line = self.create_line(0, 0, 0, 0, fill="#2a2a2a")
        self._label = self.create_text(
            0, 0, anchor="ne", fill="#666666", font=("Segoe UI", 7)
        )
        self._poly_line = self.create_line(0, 0, 0, 0, width=2, smooth=False)

        self.bind("<Configure>", self._on_resize)
        self.bind("<<ThemeChanged>>", self._update_theme)
        self._update_theme()

    def add_value(self, val: float) -> None:
        self.data.append(val)

        # Dynamic scaling
        target_max = max(0.5, val * 1.2)
        if target_max > self.max_val:
            self.max_val = target_max
        else:
            self.max_val = (self.max_val * 0.98) + (target_max * 0.02)

        self._draw_line()

    def _update_theme(self, event: Optional["tk.Event[Any]"] = None) -> None:
        style = ttk.Style()
        bg = style.lookup("TFrame", "background")
        try:
            self.configure(bg=bg)
        except Exception:
            pass

        is_dark = utils.get_binary_contrast_color(str(bg)) == "#FFFFFF"

        if is_dark:
            low = "#E040FB"
            high = "#B388FF"
            grid_mid = "#333333"
            grid_top = "#2a2a2a"
        else:
            low = "#880E4F"
            high = "#673AB7"
            grid_mid = "#CCCCCC"
            grid_top = "#999999"
        text_col = utils.get_dynamic_text_color(str(bg))
        self.itemconfig(self._mid_line, fill=grid_mid)
        self.itemconfig(self._top_line, fill=grid_top)
        self.itemconfig(self._label, fill=text_col)

        def to_rgb(h: str) -> Tuple[int, int, int]:
            h_clean = h.lstrip("#")
            return (int(h_clean[0:2], 16), int(h_clean[2:4], 16), int(h_clean[4:6], 16))

        self._low_rgb = to_rgb(low)
        self._high_rgb = to_rgb(high)
        self._draw_line()

    def _on_resize(self, event: "tk.Event[Any]") -> None:
        w, h = event.width, event.height
        self._width = w
        self._height = h
        self._ch_height = h * 0.9
        self._x_step = w / self.maxlen

        self.coords(self._mid_line, 0, h / 2, w, h / 2)
        self.coords(self._top_line, 0, 0, w, 0)
        self.coords(self._label, w - 5, 2)
        self._draw_line()

    def _draw_line(self) -> None:
        if not self.data:
            return

        self.itemconfig(self._label, text=f"{self.max_val:.1f} MB/s")

        current_val = self.data[-1]
        ratio = current_val / self.max_val
        if ratio > 1.0:
            ratio = 1.0

        r = int(self._low_rgb[0] + (self._high_rgb[0] - self._low_rgb[0]) * ratio)
        g = int(self._low_rgb[1] + (self._high_rgb[1] - self._low_rgb[1]) * ratio)
        b = int(self._low_rgb[2] + (self._high_rgb[2] - self._low_rgb[2]) * ratio)

        count = len(self.data)
        if count < 2:
            return

        scale_y = self._ch_height / self.max_val
        h = self._height
        start_x = self._width - (count * self._x_step)
        step = self._x_step

        points = [
            coord
            for i, val in enumerate(self.data)
            for coord in (start_x + (i * step), h - (val * scale_y))
        ]

        self.coords(self._poly_line, *points)
        self.itemconfig(self._poly_line, fill=f"#{r:02x}{g:02x}{b:02x}")


class DownloadsView(ttk.Frame):
    """The main view for the 'Downloads' tab."""

    def __init__(self, parent: tk.Widget, app: "App"):
        super().__init__(parent)
        self.app = app
        self._task_values: Dict[str, List[str]] = {}
        self._tasks: Dict[str, str] = {}

        self.ico_play = utils.load_icon("play.png", size=(16, 16))
        self.ico_stop = utils.load_icon("x-circle.png", size=(16, 16))
        self.ico_folder = utils.load_icon("folder.png", size=(16, 16))

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.graph = NetworkGraph(self, height=50)
        self.graph.pack(side="top", fill="x", pady=(0, 0))

        cols = ("file", "game", "status", "progress", "speed")
        self.tree = ttk.Treeview(
            self, columns=cols, show="headings", selectmode="browse"
        )

        self.tree.heading("file", text="Mod Name")
        self.tree.heading("game", text="Game")
        self.tree.heading("status", text="Status")
        self.tree.heading("progress", text="Progress")
        self.tree.heading("speed", text="Speed / Info")

        self.tree.column("file", width=300, anchor="w")
        self.tree.column("game", width=120, anchor="center")
        self.tree.column("status", width=120, anchor="center")
        self.tree.column("progress", width=200, anchor="center")
        self.tree.column("speed", width=120, anchor="center")

        sb = AutoScrollbar(self, orient="vertical", command=cast(Any, self.tree).yview)
        self.tree.configure(yscrollcommand=sb.set)

        self.tree.pack(side="top", fill="both", expand=True)
        sb.pack(side="right", fill="y", in_=self.tree)

        self.detail_frame = ttk.Frame(self, padding=5, relief="sunken")
        self.detail_frame.pack(side="bottom", fill="x")
        self.lbl_details = ttk.Label(
            self.detail_frame,
            text="Select a download to view details.",
            font=("Segoe UI", 9),
        )
        self.lbl_details.pack(side="left", anchor="w")

        self.api_rate_var = tk.StringVar(
            value="API Limits: Waiting for network activity..."
        )
        self.lbl_api_rate = ttk.Label(
            self.detail_frame, textvariable=self.api_rate_var, font=("Segoe UI", 9)
        )
        self.lbl_api_rate.pack(side="right", anchor="e")
        self.context_menu = tk.Menu(self, tearoff=0)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.bind("<<ThemeChanged>>", self._update_tree_theme)
        self._update_tree_theme()
        self._poll_api_limits()

    def _update_tree_theme(self, event: Optional["tk.Event[Any]"] = None) -> None:
        style = ttk.Style()
        bg = style.lookup("TFrame", "background")

        def adapt(light_variant: str, dark_variant: str) -> str:
            return utils.get_adaptive_color_variant(
                str(bg), light_variant, dark_variant
            )

        self.tree.tag_configure("downloading", foreground=adapt("#5dade2", "#2980b9"))
        self.tree.tag_configure("scanning", foreground=adapt("#f1c40f", "#c29200"))
        self.tree.tag_configure("ready", foreground=adapt("#00e676", "#2e7d32"))
        self.tree.tag_configure("installed", foreground=adapt("#b2fab4", "#1b5e20"))
        self.tree.tag_configure("error", foreground=adapt("#ff5252", "#c0392b"))
        self.tree.tag_configure(
            "risk", foreground=adapt("#ff5252", "#c0392b")
        )  # Dangerous
        self.tree.tag_configure("interrupted", foreground=adapt("#f39c12", "#e67e22"))

    def _on_select(self, event: Optional["tk.Event[Any]"] = None) -> None:
        tid = self._get_selected_task_id()
        if not tid:
            self.lbl_details.config(text="Select a download to view details.")
            return

        vals = self._task_values.get(tid)
        if vals:
            name = vals[0]
            status = vals[2]
            self.lbl_details.config(text=f"Task: {name} | Status: {status}")

    def _show_context_menu(self, event: "tk.Event[Any]") -> None:
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)

        tid = self._get_selected_task_id()
        if not tid:
            return

        status = str(self._task_values[tid][2])

        self.context_menu.delete(0, "end")

        if "Interrupted" in status or "Error" in status:
            self.context_menu.add_command(
                label="Resume",
                command=self._on_resume,
                image=self.ico_play or "",
                compound="left",
            )

        self.context_menu.add_command(
            label="Clear / Delete",
            command=self._on_clear,
            image=self.ico_stop or "",
            compound="left",
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Open Downloads Folder",
            command=self._open_dl_folder,
            image=self.ico_folder or "",
            compound="left",
        )

        self.context_menu.post(event.x_root, event.y_root)

    def add_task(self, task_id: str, mod_name: str, game_domain: str = "") -> None:
        """Adds or updates a task in the list."""
        display_game = game_domain.title() if game_domain else "Unknown"

        if task_id in self._tasks:
            # Update existing metadata if it was a placeholder
            current_vals = self._task_values[task_id]
            current_name = str(current_vals[0])
            is_placeholder = (
                current_name.startswith("Mod ") or current_name == "Resolving..."
            )

            updated = False
            if (
                mod_name
                and mod_name != current_name
                and (is_placeholder or "Unknown" in current_name)
            ):
                current_vals[0] = mod_name
                updated = True
            if display_game != "Unknown" and current_vals[1] == "Unknown":
                current_vals[1] = display_game
                updated = True

            if updated:
                self.tree.item(self._tasks[task_id], values=current_vals)
            return

        initial_values = [
            mod_name,
            display_game,
            "Queued",
            self._generate_progress_bar(0),
            "--",
        ]
        iid = self.tree.insert(
            "",
            0,
            values=initial_values,
            tags=("downloading",),
        )
        self._tasks[task_id] = iid
        self._task_values[task_id] = initial_values

    def _generate_progress_bar(self, percent: float) -> str:
        blocks = 10
        filled = int((percent / 100) * blocks)
        empty = blocks - filled
        return f"{'▰' * filled}{'▱' * empty}  {int(percent)}%"

    def update_task_progress(
        self, task_id: str, percent: float, speed: str, eta: str = ""
    ) -> None:
        if task_id not in self._tasks:
            return
        iid = self._tasks[task_id]

        # Don't overwrite error states with progress updates
        current_status = self._task_values[task_id][2]
        if current_status in ["Error ❌", "Dangerous ⚠️", "Installed 📂"]:
            return

        bar_text = f"{self._generate_progress_bar(percent)}"
        if eta:
            bar_text += f" • {eta}"

        current = self._task_values[task_id]
        # Only update progress columns, preserve Status
        current[3] = bar_text
        current[4] = speed

        self.tree.item(iid, values=current)  # Tags preserved unless status changes

        try:
            val_str = speed.split()[0]
            val = float(val_str)
            if "KB" in speed:
                val /= 1024.0
            self.graph.add_value(val)
        except Exception:
            pass

    def update_task_name(self, task_id: str, name: str) -> None:
        if task_id in self._tasks:
            iid = self._tasks[task_id]
            current = self._task_values[task_id]
            current[0] = name
            self.tree.item(iid, values=current)

    def update_task_state(self, task_id: str, state: str, message: str = "") -> None:
        if task_id not in self._tasks:
            return
        iid = self._tasks[task_id]
        current = self._task_values[task_id]

        status_map = {
            "downloading": "Downloading",
            "scanning": "Scanning 🛡️",
            "ready": "Ready ✔️",
            "installed": "Installed 📂",
            "error": "Error ❌",
            "risk": "Dangerous ⚠️",  # Mapped for security
            "interrupted": "Interrupted ⏸️",
            "extracting": "Extracting 📦",
        }

        display_status = status_map.get(state, state.title())
        current[2] = display_status

        if state == "ready":
            current[3] = self._generate_progress_bar(100)
            current[4] = "Click Install"
        elif state == "installed":
            current[3] = self._generate_progress_bar(100)
            current[4] = "Done"
        elif state == "error":
            current[3] = "Failed"
            current[4] = "--"
            if message:
                self.lbl_details.config(text=f"Error: {message}")
        elif state == "risk":
            current[3] = "Security Risk"
            current[4] = "Blocked"
        elif state == "interrupted":
            current[4] = "--"  # Clear speed column

        self.tree.item(iid, values=current, tags=(state,))
        self._on_select(None)  # Refresh buttons

    def remove_task(self, task_id: str) -> None:
        if task_id in self._tasks:
            self.tree.delete(self._tasks[task_id])
            del self._tasks[task_id]
            if task_id in self._task_values:
                del self._task_values[task_id]

    def _get_selected_task_id(self) -> Optional[str]:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        for tid, item_id in self._tasks.items():
            if item_id == iid:
                return tid
        return None

    def _on_resume(self) -> None:
        tid = self._get_selected_task_id()
        if tid and hasattr(self.app, "resume_download_task"):
            self.app.resume_download_task(tid)

    def _on_clear(self) -> None:
        tid = self._get_selected_task_id()
        if not tid:
            return

        if messagebox.askyesno(
            "Confirm", "Remove this task and delete the partial file?"
        ):
            # Call App Removal (session handles file deletion)
            if hasattr(self.app, "remove_download_task"):
                self.app.remove_download_task(tid)
            else:
                self.remove_task(tid)

            self.app.show_toast("Download cleared.")

    def _open_dl_folder(self) -> None:
        mods_dir = self.app.vars["mods_dir"].get()
        if mods_dir:
            dl_dir = os.path.join(mods_dir, "_downloads")
            if not os.path.exists(dl_dir):
                os.makedirs(dl_dir)
            try:
                webbrowser.open(dl_dir)
            except Exception:
                pass

    def _poll_api_limits(self) -> None:
        """Periodically update the API rate limit string variable."""
        daily_rem, daily_lim, hourly_rem, hourly_lim = get_rate_limits()

        if not load_global_config().nexus_api_key:
            self.api_rate_var.set("API Limits: No API Key set in Settings.")
        elif daily_lim > 0:
            self.api_rate_var.set(
                f"API Limits - Hourly: {hourly_rem}/{hourly_lim} | Daily: {daily_rem}/{daily_lim}"
            )
        else:
            self.api_rate_var.set("API Limits: Waiting for network activity...")

        self.after(5000, self._poll_api_limits)
