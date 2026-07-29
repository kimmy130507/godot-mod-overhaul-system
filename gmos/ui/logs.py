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
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Any, Optional, cast

from gmos.ui.browser import DownloadsView
from gmos.ui.widgets import AutoScrollbar
from gmos.utils import get_dynamic_text_color

if TYPE_CHECKING:
    from gmos.ui.app import App


class LogView(ttk.Frame):
    """View component for Logs."""

    def __init__(self, parent: tk.Widget, app: "App"):
        super().__init__(parent)
        self.app = app
        self.download_view: Optional[DownloadsView] = None
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.close_btn = ttk.Button(
            self.notebook,
            text="✕",
            style="Link.TButton",
            width=3,
            command=self._on_close_clicked,
        )
        self.close_btn.place(relx=1.0, x=0, y=-2.45, anchor="ne")
        log_tab = ttk.Frame(self.notebook)
        self.notebook.add(log_tab, text="Patch Log")
        bg_color = str(ttk.Style().lookup("TFrame", "background") or "#333333")
        fg_color = get_dynamic_text_color(bg_color)
        self.log_txt = tk.Text(
            log_tab,
            wrap=tk.WORD,
            height=15,
            bg=bg_color,
            fg=fg_color,
            insertbackground=fg_color,
        )
        log_vsb = AutoScrollbar(
            log_tab, orient="vertical", command=cast(Any, self.log_txt).yview
        )
        self.log_txt.configure(yscrollcommand=log_vsb.set)
        log_vsb.pack(side="right", fill="y")
        self.log_txt.pack(side="left", fill="both", expand=True)

        dl_tab = ttk.Frame(self.notebook)
        self.notebook.add(dl_tab, text="Downloads")
        self.download_view = DownloadsView(dl_tab, self.app)
        self.download_view.pack(fill="both", expand=True)
        self.bind("<<ThemeChanged>>", self._on_theme_change)

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("[%H:%M:%S]")
        self.log_txt.insert(tk.END, f"{timestamp} {message}\n")
        self.log_txt.see(tk.END)
        # Truncate if over 2000 lines
        if int(self.log_txt.index("end-1c").split(".")[0]) > 2000:
            self.log_txt.delete("1.0", "2.0")
        self.update_idletasks()

    def _on_theme_change(self, event: Optional["tk.Event[tk.Misc]"] = None) -> None:
        bg_color = str(ttk.Style().lookup("TFrame", "background") or "#333333")
        fg_color = get_dynamic_text_color(bg_color)
        if hasattr(self, "log_txt") and self.log_txt.winfo_exists():
            self.log_txt.config(bg=bg_color, fg=fg_color, insertbackground=fg_color)

    def _on_close_clicked(self) -> None:
        if self.app and hasattr(self.app, "dashboard") and self.app.dashboard:
            self.app.dashboard.toggle_bottom_panel()
