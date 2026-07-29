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
import datetime
import os
import sys
import textwrap
import threading
import time
import tkinter as tk
import webbrowser
from collections import defaultdict
from tkinter import Button, Label, Toplevel, filedialog, messagebox, ttk
from tkinter.ttk import Progressbar
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Union, cast

from gmos import utils
from gmos.core import security
from gmos.core.patcher import generate_patch_plan
from gmos.io import (
    ReplaceDiagnostics,
    atomic_write_copy,
    replace_with_retries,
    start_replace_task,
)
from gmos.utils import (
    ModConfig,
    get_binary_contrast_color,
    get_dynamic_text_color,
    get_mod_name_from_config,
    logger,
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
try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired

if TYPE_CHECKING:
    from gmos.ui.app import App


class UIModConfig(ModConfig):
    """Extends ModConfig with UI state."""

    Enabled: NotRequired[bool]
    Valid: NotRequired[bool]
    Errors: NotRequired[Optional[List[str]]]
    _deps_tooltip: NotRequired[str]
    _security_risks: NotRequired[List[security.SecurityRisk]]
    _cached_summary: NotRequired[str]
    _cached_plan: NotRequired[List[Any]]  # Cache for patch instructions


class AutoScrollbar(ttk.Scrollbar):
    """A scrollbar that hides itself if it's not needed. Safely wraps itself to preserve layout order."""

    def __init__(self, master: Any = None, **kwargs: Any):
        self.wrapper = ttk.Frame(master)
        super().__init__(self.wrapper, **kwargs)
        super().pack(fill="both", expand=True)

    def set(self, first: Any, last: Any) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            super().pack_forget()
        else:
            super().pack(fill="both", expand=True)
        super().set(first, last)

    def pack(self, cnf: Any = None, **kwargs: Any) -> None:
        if isinstance(cnf, dict):
            typed_cnf = cast(Dict[str, Any], cnf)
            self.wrapper.pack(typed_cnf, **kwargs)
        else:
            self.wrapper.pack(**kwargs)

    def pack_forget(self) -> None:
        self.wrapper.pack_forget()

    def grid(self, cnf: Any = None, **kwargs: Any) -> None:
        if isinstance(cnf, dict):
            typed_cnf = cast(Dict[str, Any], cnf)
            self.wrapper.grid(typed_cnf, **kwargs)
        else:
            self.wrapper.grid(**kwargs)

    def grid_remove(self) -> None:
        self.wrapper.grid_remove()

    def grid_forget(self) -> None:
        self.wrapper.grid_forget()

    def place(self, cnf: Any = None, **kwargs: Any) -> None:
        if isinstance(cnf, dict):
            typed_cnf = cast(Dict[str, Any], cnf)
            self.wrapper.place(typed_cnf, **kwargs)
        else:
            self.wrapper.place(**kwargs)

    def place_forget(self) -> None:
        self.wrapper.place_forget()

    def destroy(self) -> None:
        super().destroy()
        self.wrapper.destroy()


class ImageCache:
    _cache: Dict[str, Any] = {}

    @classmethod
    def get_thumbnail(cls, path: str, size: tuple[int, int] = (64, 64)) -> Any:
        if not (Image and ImageTk):
            return None

        key = f"{path}_{size}"
        if key in cls._cache:
            return cls._cache[key]

        try:
            img = Image.open(path)
            img.thumbnail(size, Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            cls._cache[key] = photo
            return photo
        except Exception:
            return None


def res_to_path(res_path: str) -> str:
    """Converts 'res://path/to/file' to 'path/to/file'."""
    if res_path.startswith("res://"):
        return res_path[6:].lstrip("/")
    return res_path.lstrip("/")


class ToolTip:
    """Simple tooltip for a widget."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.id: str | None = None
        self.tw: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._cancel)
        widget.bind("<ButtonPress>", self._cancel)

    def _schedule(self, _ev: Optional["tk.Event[Any]"] = None) -> None:
        self._cancel()
        self.id = self.widget.after(self.delay, self._show)

    def _cancel(self, _ev: Optional["tk.Event[Any]"] = None) -> None:
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None
        if self.tw:
            try:
                self.tw.destroy()
            except Exception as e:
                logger.debug("Tooltip window destruction failed: %s", e, exc_info=True)
            self.tw = None

    def _show(self) -> None:
        if self.tw:
            return
        if hasattr(self.widget, "bbox"):
            bbox_coords = cast(Any, self.widget).bbox("insert")
        else:
            bbox_coords = None
        if bbox_coords is None:
            bbox_coords = (0, 0, 0, 0)
        x, y, _, _ = bbox_coords
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 20
        self.tw = tk.Toplevel(self.widget)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{x}+{y}")
        lbl = ttk.Label(
            self.tw, text=self.text, relief="solid", borderwidth=1, padding=(6, 3)
        )
        lbl.pack()


class TreeHoverTip:
    """
    Shows detailed info when hovering over Treeview items.
    """

    def __init__(self, tree: ttk.Treeview, app_ref: "App"):
        self.tree = tree
        self.app = app_ref
        self.tipwindow: Optional[tk.Toplevel] = None
        self.last_item: Optional[str] = None
        self.last_col: Optional[str] = None
        self.tree.bind("<Motion>", self._on_motion)
        self.tree.bind("<Leave>", self._hide_tip)

    def _on_motion(self, event: "tk.Event[Any]") -> None:
        item = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)

        if not item:
            self._hide_tip()
            return
        if item == self.last_item and col_id == self.last_col:
            return
        self.last_item = item
        self.last_col = col_id
        self._schedule_tip(item, col_id, event.x_root, event.y_root)

    def _schedule_tip(self, item_id: str, col_id: str, x: int, y: int) -> None:
        self._hide_tip()
        self.after_id = self.tree.after(
            400, lambda: self._show_tip(item_id, col_id, x, y)
        )

    def _hide_tip(self, _event: Any = None) -> None:
        if hasattr(self, "after_id"):
            self.tree.after_cancel(self.after_id)
        self.last_item = None
        self.last_col = None
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None

    def _show_tip(self, item_id: str, col_id: str, x: int, y: int) -> None:
        index = self.tree.index(item_id)
        mod_configs = cast(List[UIModConfig], getattr(self.app, "mod_configs", []))
        if index < 0 or index >= len(mod_configs):
            return

        cfg = mod_configs[index]
        mod_name = str(cfg.get("Name") or "Unknown")
        lines: List[str] = []
        style = ttk.Style()
        theme_bg = style.lookup("TFrame", "background")
        is_dark = get_binary_contrast_color(str(theme_bg)) == "#FFFFFF"

        bg_color = "#333333" if is_dark else "#ffffe0"
        fg_color = get_dynamic_text_color(bg_color)
        has_critical = False

        if col_id == "#0":
            conflicts = self.app.get_conflicts_for_mod(mod_name)
            if conflicts:
                has_critical = True
                lines.append(f"⚠️ {mod_name} Conflicts:")
                bg_color = "#5a1e1e" if is_dark else "#ffe0e0"
                fg_color = get_dynamic_text_color(bg_color)
                max_show = 10
                count = 0
                for target, others in conflicts.items():
                    if count >= max_show:
                        remaining = len(conflicts) - count
                        lines.append(f"   ... and {remaining} more conflicts.")
                        break
                    readable_target = target.split("/")[-1]
                    lines.append(f" • {readable_target}")
                    lines.append(f"   vs: {', '.join(others)}")
                    count += 1

            if not cfg.get("Valid", True):
                has_critical = True
                errors = (
                    cfg.get("Errors") or cfg.get("_deps_errors") or ["Unknown Error"]
                )
                if lines:
                    lines.append("----------------")
                lines.append("❌ Invalid Mod:")
                for e in errors:
                    lines.append(f" - {e}")
                bg_color = "#5a1e1e" if is_dark else "#ffe0e0"
                fg_color = get_dynamic_text_color(bg_color)

            # Description
            sections = cfg.get("Sections", {})
            mod_info = sections.get("ModInfo") or sections.get("modinfo") or {}
            full_desc = ""
            if isinstance(mod_info, dict):
                full_desc = str(mod_info.get("Description", ""))
            if full_desc:

                if has_critical:
                    lines.append("────────────────────────")
                lines = textwrap.wrap(full_desc, width=60)

        if not lines:
            return

        self.tipwindow = tw = tk.Toplevel(self.tree)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x+15}+{y+10}")

        label = tk.Label(
            tw,
            text="\n".join(lines),
            justify=tk.LEFT,
            background=bg_color,
            fg=fg_color,
            relief=tk.SOLID,
            borderwidth=1,
            font=("tahoma", 9, "normal"),
            padx=5,
            pady=3,
        )
        label.pack()


class ProgressDialog(Toplevel):
    def __init__(self, parent: tk.Misc, title: str = "Progress"):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        utils.setup_child_window(self, parent, width=350, height=150, modal=True)
        self.bind("<<ThemeChanged>>", lambda e: utils.apply_window_theme(self))
        self.title(title)
        self.resizable(False, False)
        self.label = Label(self, text="")
        self.label.pack(padx=12, pady=(12, 6))
        self.pb = Progressbar(self, mode="indeterminate", length=300)
        self.pb.pack(padx=12, pady=(0, 6))
        self.cancel_button = Button(self, text="Cancel", command=self._on_cancel)
        self.cancel_button.pack(padx=12, pady=(0, 12))
        self.cancel_event = threading.Event()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def start(self) -> None:
        self.pb.start(50)

    def stop(self) -> None:
        try:
            self.pb.stop()
        except Exception:
            pass

    def set_text(self, txt: str) -> None:
        self.label.config(text=txt)
        self.update_idletasks()

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def _on_cancel(self) -> None:
        self.cancel_event.set()

    def close(self) -> None:
        try:
            self.stop()
            self.destroy()
        except Exception:
            pass


class ProgressBarDialog(tk.Toplevel):
    def __init__(
        self, parent: tk.Misc, title: str = "Working...", max_value: int = 100
    ):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        utils.setup_child_window(self, parent, width=350, height=120, modal=True)
        self.bind("<<ThemeChanged>>", lambda e: utils.apply_window_theme(self))
        self.title(title)
        self.resizable(False, False)
        self.max_value = max_value
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        self.label = ttk.Label(frm, text="Starting...")
        self.label.pack(fill="x", pady=(0, 8))
        self.pb = ttk.Progressbar(
            frm,
            orient="horizontal",
            length=260,
            mode="determinate",
            maximum=max_value,
        )
        self.pb.pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self.update_idletasks()

    def update_progress(self, value: int, message: Optional[str] = None) -> None:
        if message is not None:
            self.label.config(text=message)
        self.pb["value"] = min(value, self.max_value)
        self.update_idletasks()

    def close(self) -> None:
        try:
            self.destroy()
        except Exception:
            pass


class PermissionErrorDialog(tk.Toplevel):
    def __init__(
        self, parent: Optional[tk.Widget], path: str | os.PathLike[str], exc: Exception
    ):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        if parent:
            utils.setup_child_window(self, parent, width=550, height=250, modal=True)
            self.bind("<<ThemeChanged>>", lambda e: utils.apply_window_theme(self))
        self.title("Permission error")
        self.resizable(False, False)
        self._choice: Any = None
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        msg = f"Permission error while accessing:\n{path}\n\n{exc}"
        ttk.Label(frm, text=msg, justify="left", wraplength=520).pack(
            padx=4, pady=(0, 12)
        )
        btn_fr = ttk.Frame(frm)
        btn_fr.pack(anchor="e")
        ttk.Button(btn_fr, text="Retry", command=self._on_retry).pack(
            side="left", padx=4
        )
        ttk.Button(btn_fr, text="Choose folder", command=self._on_choose).pack(
            side="left", padx=4
        )
        ttk.Button(btn_fr, text="Abort", command=self._on_abort).pack(
            side="left", padx=4
        )
        self.update_idletasks()

    def _on_retry(self) -> None:
        self._choice = "retry"
        self.destroy()

    def _on_choose(self) -> None:
        try:
            newdir = filedialog.askdirectory(parent=self)
            if newdir:
                self._choice = ("choose", newdir)
                self.destroy()
        except Exception:
            self._choice = "abort"
            self.destroy()

    def _on_abort(self) -> None:
        self._choice = "abort"
        self.destroy()

    def show(self) -> Any:
        try:
            self.wait_window(self)
        except Exception:
            pass
        return self._choice


class LegalDisclaimerDialog(tk.Toplevel):
    def __init__(self, parent: tk.Widget):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        utils.setup_child_window(self, parent, width=600, height=400, modal=True)
        self.title("Legal Notice — Read Before Using GMOS")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.bind("<<ThemeChanged>>", self._on_theme_change)
        self.update_idletasks()

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        lbl = ttk.Label(
            frame, text="GMOS modifies your game files.", font=("Segoe UI", 12, "bold")
        )
        lbl.pack(pady=(0, 10), anchor="w")
        body_text = (
            "GMOS modifies game data and dynamically injects an override package (gmos_override.pck) and Sandbox Autoload into the engine at runtime. While designed for compatibility, "
            "modding carries inherent risks including game instability and save data corruption.\n\n"
            "These engine-level injections and file modifications may also violate a game's End User License Agreement (EULA) or Terms of Service (ToS), "
            "potentially triggering anti-cheat mechanisms or account restrictions.\n\n"
            "GMOS is provided 'AS IS', without warranty of any kind. The authors are not liable "
            "for any damages, data loss, or account restrictions. You assume all responsibility for its use."
        )
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#f0f0f0")
        self.txt = tk.Text(
            frame,
            wrap="word",
            height=12,
            font=("Segoe UI", 10),
            bg=theme_bg,
            fg=get_dynamic_text_color(theme_bg),
            relief="flat",
        )
        self.txt.insert("1.0", body_text)
        self.txt.config(state="disabled")
        self.txt.pack(fill="x", pady=10)

        self.accepted_var = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(
            frame,
            text="I understand and wish to continue",
            variable=self.accepted_var,
            command=self.toggle_continue,
        )
        chk.pack(pady=10, anchor="w")
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", pady=20)
        ttk.Button(btn_frame, text="View License", command=self.show_legal_file).pack(
            side="left"
        )
        self.cont_btn = ttk.Button(
            btn_frame, text="Continue", command=self.on_accept, state="disabled"
        )
        self.cont_btn.pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Cancel", command=self.on_cancel).pack(side="right")
        self.result = False

    def _on_theme_change(self, event: Optional["tk.Event[Any]"] = None) -> None:
        theme_bg = str(ttk.Style().lookup("TFrame", "background") or "#f0f0f0")
        if hasattr(self, "txt") and self.txt.winfo_exists():
            self.txt.config(bg=theme_bg, fg=utils.get_dynamic_text_color(theme_bg))
        utils.apply_window_theme(self)

    def toggle_continue(self) -> None:
        if self.accepted_var.get():
            self.cont_btn.config(state="normal")
        else:
            self.cont_btn.config(state="disabled")

    def show_legal_file(self) -> None:

        # Determine the directory of the executable or current working directory
        exe_dir = (
            os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False)
            else os.getcwd()
        )

        possible_paths = ["LICENSE", os.path.join(exe_dir, "LICENSE")]

        # If running as a macOS app bundle (GMOS.app/Contents/MacOS/GMOS), the license sits next to the .app
        if sys.platform == "darwin" and getattr(sys, "frozen", False):
            possible_paths.append(
                os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(exe_dir))),
                    "LICENSE",
                )
            )

        for p in possible_paths:
            if os.path.exists(p):
                try:
                    webbrowser.open(os.path.abspath(p))
                except Exception:
                    pass
        try:
            webbrowser.open("https://www.gnu.org/licenses/gpl-3.0.html")
        except Exception:
            messagebox.showinfo(
                "Legal Info",
                "License file not found locally.\nGMOS is provided AS-IS under the GNU GPLv3.",
            )

    def on_accept(self) -> None:
        self.result = True
        self.destroy()

    def on_cancel(self) -> None:
        self.result = False
        self.destroy()


class Toast(tk.Toplevel):
    """
    Non-blocking notification window with fade-in animation.
    """

    def __init__(
        self, parent: tk.Widget, message: str, duration: int = 3000, kind: str = "info"
    ):
        super().__init__(parent)
        self.overrideredirect(True)
        # Theme-aware colors
        style = ttk.Style()
        is_dark = "dark" in style.theme_use()

        bg = "#333333" if is_dark else "#e0e0e0"
        if kind == "error":
            bg = "#d9534f" if is_dark else "#ffcccc"
        elif kind == "success":
            bg = "#5cb85c" if is_dark else "#d0f0c0"
        fg = get_dynamic_text_color(bg)
        self.configure(bg=bg)
        lbl = tk.Label(
            self, text=message, bg=bg, fg=fg, padx=20, pady=10, font=("Segoe UI", 10)
        )
        lbl.pack()
        # Fade-in Animation
        cast(Any, self).attributes("-alpha", 0.0)
        self._animate_fade_in()
        self.update_idletasks()
        self._position_window(parent)
        cast(Any, self).lift()
        self.after(duration, self._fade_out_and_destroy)

    def _position_window(self, parent: tk.Widget) -> None:
        pw = parent.winfo_width() if parent.winfo_width() > 1 else 1000
        ph = parent.winfo_height() if parent.winfo_height() > 1 else 800
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w = self.winfo_width()
        h = self.winfo_height()
        # Bottom-Right Corner
        x = px + (pw // 2) - (w // 2)
        y = py + ph - h - 50
        self.geometry(f"+{x}+{y}")

    def _animate_fade_in(self, alpha: float = 0.0) -> None:
        if alpha < 0.95:
            alpha += 0.1
            cast(Any, self).attributes("-alpha", alpha)
            self.after(20, lambda: self._animate_fade_in(alpha))
        else:
            cast(Any, self).attributes("-alpha", 1.0)

    def _fade_out_and_destroy(self) -> None:
        alpha = cast(Any, self).attributes("-alpha")
        if alpha > 0.0:
            alpha -= 0.1
            cast(Any, self).attributes("-alpha", alpha)
            self.after(30, self._fade_out_and_destroy)
        else:
            self.destroy()


class NameInputDialog(tk.Toplevel):
    """Generic modal dialog to input a string name (Profiles, Projects, etc.)."""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        prompt: str,
        callback: Optional[Callable[[str], None]],
        default_name: str = "",
        action_text: str = "Create",
    ):
        super().__init__(parent)
        self.callback = callback
        self.result: Optional[str] = None

        self.title(title)
        self.resizable(False, False)

        utils.load_and_apply_app_icon_to_toplevel(self)
        utils.setup_child_window(self, parent, width=300, height=130, modal=True)
        self.bind("<<ThemeChanged>>", lambda e: utils.apply_window_theme(self))
        self.columnconfigure(0, weight=1)

        lbl = ttk.Label(self, text=prompt, font=("Segoe UI", 9))
        lbl.pack(pady=(15, 5), padx=10, anchor="w")

        self.var = tk.StringVar(value=default_name)
        self.entry = ttk.Entry(self, textvariable=self.var)
        self.entry.pack(fill="x", padx=10, pady=5)
        self.entry.focus_set()
        self.entry.bind("<Return>", self._on_ok)
        if default_name:
            self.entry.select_range(0, tk.END)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", pady=10, padx=10)

        self.btn_cancel = ttk.Button(btn_frame, text="Cancel", command=self.destroy)
        self.btn_cancel.pack(side="right", padx=(5, 0))

        try:
            self.btn_ok = ttk.Button(
                btn_frame, text=action_text, command=self._on_ok, style="Accent.TButton"
            )
        except Exception:
            self.btn_ok = ttk.Button(btn_frame, text=action_text, command=self._on_ok)
        self.btn_ok.pack(side="right")

        self.wait_window(self)

    def _on_ok(self, event: Optional["tk.Event[Any]"] = None) -> None:
        val = self.var.get().strip()
        if val:
            self.result = val
            if self.callback:
                self.callback(val)
            self.destroy()


def show_progress(
    parent: tk.Misc, title: str = "Working...", max_value: int = 100
) -> ProgressBarDialog:
    dlg = ProgressBarDialog(parent, title=title, max_value=max_value)
    return dlg


def replace_with_progress(
    parent: tk.Misc,
    src: str,
    dst: str,
    *,
    attempts: int = 6,
    title: str = "Replacing file...",
) -> tuple[Any, threading.Event, Optional[threading.Thread]]:
    try:
        dlg: Union[ProgressDialog, Any] = ProgressDialog(parent, title=title)
    except Exception:

        class _NoUi:
            def __init__(self) -> None:
                self.cancel_event = threading.Event()

            def update_progress(
                self, value: int, message: Optional[str] = None
            ) -> None:
                pass

            def set_text(self, txt: str) -> None:
                pass

            def close(self) -> None:
                pass

            def cancelled(self) -> bool:
                return self.cancel_event.is_set()

            def start(self) -> None:
                pass

        dlg = _NoUi()

    cancel_event = (
        dlg.cancel_event if hasattr(dlg, "cancel_event") else threading.Event()
    )

    def progress_cb(frac: float) -> None:
        try:
            v = max(0.0, min(1.0, float(frac)))
            parent.after(
                0, lambda: dlg.set_text(f"Attempting replace... {int(v * 100)}%")
            )
        except Exception:
            pass

    def done_cb(diag: ReplaceDiagnostics) -> None:
        def _finish() -> None:
            try:
                if getattr(diag, "success", False):
                    dlg.set_text("Replace succeeded")
                else:
                    dlg.set_text("Replace failed")
                parent.after(350, dlg.close)
            except Exception:
                pass

        try:
            parent.after(0, _finish)
        except Exception:
            _finish()

    try:
        dlg.start()
    except Exception:
        pass

    try:

        diag, thread = start_replace_task(
            src,
            dst,
            done_cb=done_cb,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            attempts=attempts,
            base_delay=0.03,
            max_sleep=0.5,
            poll_interval=0.08,
        )
        return diag, cancel_event, thread
    except Exception:

        def _sync_worker(diag_obj: ReplaceDiagnostics) -> None:

            diag_obj.start_time = time.time()
            try:
                replace_with_retries(src, dst)
                diag_obj.success = True
                diag_obj.end_time = time.time()
                done_cb(diag_obj)
            except Exception as exc:
                diag_obj.last_exception = exc
                diag_obj.end_time = time.time()
                done_cb(diag_obj)

        diag = ReplaceDiagnostics(src=src, dst=dst, attempts_allowed=0)
        thr = threading.Thread(
            target=_sync_worker, args=(diag,), daemon=True, name="gmos-replace-fallback"
        )
        thr.start()
        return diag, cancel_event, thr


def _generate_mod_summary(mod_config: ModConfig) -> str:
    #  Return cached summary if available
    uicfg = cast(UIModConfig, mod_config)
    if "_cached_summary" in uicfg:
        return str(uicfg.get("_cached_summary", ""))

    # If plan is cached (by App), derive summary from it without disk I/O
    if "_cached_plan" in uicfg:
        plan = uicfg["_cached_plan"]
    else:
        path = mod_config.get("Path")
        if not path:
            return ""
        try:
            plan = generate_patch_plan(path, mod_config)
        except Exception:
            return ""
    tags: Set[str] = set()
    for _, op, details in plan:
        if op == "FileReplace":
            tags.add("File")
        elif op == "FunctionPatch":
            s_func = str(details[3]) if len(details) > 3 and details[3] else ""
            mode = str(details[4]) if len(details) > 4 and details[4] else ""
            if mode == "create":
                tags.add("Fn:new")
            elif s_func.startswith("prefix_"):
                tags.add("Fn:pre")
            elif s_func.startswith("postfix_"):
                tags.add("Fn:post")
            else:
                tags.add("Fn:rep")
        elif op == "VariablePatch":
            mode = str(details[4]) if len(details) > 4 and details[4] else "replace"
            if mode in ("create", "dataadd"):
                tags.add("Data")
            elif mode == "add":
                tags.add("Var:add")
            else:
                tags.add("Var:rep")
    result = ""
    if tags:
        result = f"({', '.join(sorted(tags))})"

    # Cache the result
    cast(UIModConfig, mod_config)["_cached_summary"] = result
    return result


def rebuild_mod_tree(
    tree: ttk.Treeview,
    mod_configs: List[UIModConfig],
    name_getter: Callable[[ModConfig], str] = get_mod_name_from_config,
    icon_map: Optional[Dict[bool, Any]] = None,
    app_ref: Optional[Any] = None,
) -> None:
    try:
        for item in tree.get_children():
            tree.delete(item)
    except Exception:
        return

    for _idx, cfg in enumerate(mod_configs):
        name = name_getter(cfg)
        summary = _generate_mod_summary(cfg)
        name_display = f"   {name}  {summary}" if summary else f"   {name}"

        sections = cfg.get("Sections", {})
        mod_info = sections.get("ModInfo") or sections.get("modinfo")
        author = "Unknown"
        version = "-"
        desc = ""
        if isinstance(mod_info, dict):
            author = str(mod_info.get("Author", author))
            version = str(mod_info.get("Version", version))
            desc = str(mod_info.get("Description", desc))

        is_invalid = bool(cfg.get("_deps_errors") or cfg.get("Errors"))
        if is_invalid:
            name_display = f"{name_display} [INVALID]"
        enabled = cfg.get("Enabled")
        if enabled is None:
            enabled = cast(Optional[bool], cfg.get("enabled", True))
        risks = cfg.get("_security_risks", [])
        if risks:
            name_display = f"⚠️ {name_display}"

        tags: List[str] = []
        if not enabled:
            tags.append("disabled")
        if is_invalid:
            tags.append("invalid")
        if risks:
            tags.append("risk")
        # Load Thumbnail if available
        img = icon_map.get(bool(enabled)) if icon_map else None

        # Try to load mod-specific thumbnail if present
        mod_path = cfg.get("Path")
        if mod_path and Image:
            for thumb_name in ["icon.png", "preview.png", "logo.jpg"]:
                thumb_file = os.path.join(mod_path, thumb_name)
                if os.path.exists(thumb_file):
                    img = ImageCache.get_thumbnail(thumb_file)
                    break
        img = icon_map.get(bool(enabled)) if icon_map else None

        tree.insert(
            "",
            "end",
            text=name_display,
            values=(version,),
            tags=tuple(tags),
            image=img or "",
        )
        if is_invalid:
            cfg["_deps_tooltip"] = "\n".join(
                cfg.get("_deps_errors", []) or cfg.get("Errors") or []
            )
        else:
            cfg.pop("_deps_tooltip", None)


class EditExecutablesDialog(tk.Toplevel):
    """
    Dialog to manage the list of executables (Game, Tools, etc.) similar to MO2.
    """

    def __init__(
        self,
        parent: tk.Widget,
        current_list: List[Dict[str, str]],
        default_game_conf: Dict[str, str],
        on_save: Callable[[List[Dict[str, str]], Dict[str, str]], None],
    ):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        self.title("Executable Manager")
        utils.setup_child_window(self, parent, width=700, height=500, modal=True)
        self.bind("<<ThemeChanged>>", lambda e: utils.apply_window_theme(self))
        self.exec_list: List[Dict[str, Any]] = [
            dict(x) for x in current_list
        ]  # Deep copy
        self.on_save = on_save
        self.default_game = default_game_conf
        self.display_list: List[Dict[str, Any]] = [self.default_game] + self.exec_list
        self.icon_map: Dict[int, Any] = (
            {}
        )  # Cache icons by list index to avoid polluting data dicts
        self._current_index: Optional[int] = None

        main_paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_paned.pack(fill="both", expand=True, padx=10, pady=10)

        left_frame = ttk.Frame(main_paned, width=200)
        cast(Any, main_paned).add(left_frame, weight=1)

        self.tree = ttk.Treeview(left_frame, show="tree", selectmode="browse")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = AutoScrollbar(
            left_frame, orient="vertical", command=cast(Any, self.tree).yview
        )
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        toolbar = ttk.Frame(left_frame)
        toolbar.pack(side="top", fill="x")
        self.tree.pack_forget()
        sb.pack_forget()

        self.ico_add = utils.load_icon("plus.png", size=(16, 16))
        self.ico_del = utils.load_icon("minus.png", size=(16, 16))
        self.btn_add = ttk.Button(
            toolbar,
            image=self.ico_add or "",
            text="Add" if not self.ico_add else "",
            command=self._add_item,
            width=4,
        )
        self.btn_add.pack(side="left", padx=2)
        self.btn_del = ttk.Button(
            toolbar,
            image=self.ico_del or "",
            text="Del" if not self.ico_del else "",
            command=self._del_item,
            width=4,
        )
        self.btn_del.pack(side="left", padx=2)

        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        right_frame = ttk.Frame(main_paned, padding=10)
        cast(Any, main_paned).add(right_frame, weight=3)

        self.var_title = tk.StringVar()
        self.var_path = tk.StringVar()
        self.var_cwd = tk.StringVar()
        self.var_args = tk.StringVar()

        def _entry(
            row: int, label: str, var: tk.StringVar, browse_mode: str = ""
        ) -> None:
            ttk.Label(right_frame, text=label).grid(
                row=row, column=0, sticky="e", pady=8, padx=(10, 5)
            )
            e = ttk.Entry(right_frame, textvariable=var)
            e.grid(row=row, column=1, sticky="ew", pady=4, ipady=3)
            e.bind("<FocusOut>", self._save_current_field)
            if browse_mode:
                btn = ttk.Button(
                    right_frame,
                    text="...",
                    width=3,
                    command=lambda: self._browse(var, browse_mode),
                )
                btn.grid(row=row, column=2, padx=5)

        right_frame.columnconfigure(1, weight=1)
        _entry(0, "Title:", self.var_title)
        _entry(1, "Binary:", self.var_path, "file")
        _entry(2, "Start In:", self.var_cwd, "dir")
        _entry(3, "Arguments:", self.var_args)

        foot = ttk.Frame(self)
        foot.pack(fill="x", pady=10, padx=10)
        ttk.Button(foot, text="Apply", command=self._on_apply).pack(
            side="right", padx=5
        )
        ttk.Button(foot, text="OK", command=self._on_ok).pack(side="right", padx=5)
        ttk.Button(foot, text="Cancel", command=self.destroy).pack(side="right")

        self._refresh_list()
        if self.display_list:
            # Select first item
            if self.tree.get_children():
                first = self.tree.get_children()[0]
                self.tree.selection_set(first)
            self._on_select(None)

    def _browse(self, var: tk.StringVar, mode: str) -> None:
        if mode == "file":
            res = filedialog.askopenfilename()
        else:
            res = filedialog.askdirectory()
        if res:
            var.set(safe_norm(res))

            self._save_current_field()

    def _add_item(self) -> None:
        self.exec_list.append(
            {"title": "New Executable", "path": "", "cwd": "", "args": ""}
        )
        self._refresh_list()
        # Select last
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[-1])

    def _del_item(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        idx = self.tree.index(iid)
        if idx == 0:
            return  # Cannot delete default

        real_idx = idx - 1
        self.exec_list.pop(real_idx)
        self._refresh_list()
        self._clear_vars()

    def _refresh_list(self) -> None:
        self.display_list = [self.default_game] + self.exec_list
        self.icon_map.clear()
        for item_id in self.tree.get_children():
            self.tree.delete(item_id)
        for _, item in enumerate(self.display_list):

            self.tree.insert(
                "",
                "end",
                text=str(item.get("title", "Untitled")),
            )

    def _on_select(self, event: Optional["tk.Event[Any]"] = None) -> None:
        sel = self.tree.selection()
        if not sel:
            self._current_index = None
            self._clear_vars()
            return

        iid = sel[0]
        idx = self.tree.index(iid)
        self._current_index = idx
        data = self.display_list[idx]
        self.var_title.set(data.get("title", ""))
        self.var_path.set(data.get("path", ""))
        self.var_cwd.set(data.get("cwd", ""))
        self.var_args.set(data.get("args", ""))

        # Browse buttons
        for btn_name in ["btn_binary", "btn_start_in"]:
            b = getattr(self, btn_name, None)
            if b:
                b.config(state="normal")

        self.btn_del.config(state="disabled" if idx == 0 else "normal")

    def _save_current_field(self, _event: Optional["tk.Event[Any]"] = None) -> None:
        if self._current_index is None:
            return
        # Handle Default Game (Index 0)
        if self._current_index == 0:
            target_dict = self.default_game
        else:
            idx = self._current_index - 1
            if 0 <= idx < len(self.exec_list):
                target_dict = self.exec_list[idx]
            else:
                return

        # Update Data
        target_dict["title"] = self.var_title.get()
        target_dict["path"] = self.var_path.get()
        target_dict["cwd"] = self.var_cwd.get()
        target_dict["args"] = self.var_args.get()

        # Update Listbox Label
        children = self.tree.get_children()
        if self._current_index < len(children):
            self.tree.item(children[self._current_index], text=self.var_title.get())

    def _clear_vars(self) -> None:
        self.var_title.set("")
        self.var_path.set("")
        self.var_cwd.set("")
        self.var_args.set("")

    def _on_apply(self) -> None:
        self._save_current_field()
        self.on_save(
            [cast(Dict[str, str], x) for x in self.exec_list], self.default_game
        )

    def _on_ok(self) -> None:
        self._on_apply()
        self.destroy()


class RollbackDialog(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Widget,
        game_dir: str,
        bak_list: List[str],
        on_success: Callable[[str], None],
        append_log: Callable[[str], None],
    ) -> None:
        super().__init__(parent)
        self.game_dir = game_dir
        self.bak_list = bak_list
        self.on_success = on_success
        self.append_log = append_log

        utils.load_and_apply_app_icon_to_toplevel(self)
        self.title("Rollback — Restore Game Files")
        utils.setup_child_window(self, parent, width=750, height=500, modal=True)

        utils.apply_window_theme(self)
        self.bind("<<ThemeChanged>>", self._on_theme_change)

        cast(Any, self).lift()

        self.ico_folder: Any = utils.load_icon("folder-open.png", size=(16, 16))

        ttk.Label(
            self, text="Select backup files to restore.", font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=10, pady=(10, 5))

        style = ttk.Style()
        theme_bg = str(style.lookup("TFrame", "background") or "#333333")
        is_dark = get_binary_contrast_color(theme_bg) == "#FFFFFF"
        border_color = "#555555" if is_dark else "#cccccc"

        self.frm = tk.Frame(self, bg=border_color, padx=1, pady=1)
        self.frm.pack(fill="both", expand=True, padx=10, pady=5)
        self.canvas = tk.Canvas(self.frm, bg=theme_bg, highlightthickness=0)

        self.sb = ttk.Scrollbar(
            self.frm, orient="vertical", command=cast(Any, self.canvas).yview
        )
        self.inner = ttk.Frame(self.canvas)

        self.canvas_window: int = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )

        def _on_inner_configure(e: "tk.Event[Any]") -> None:
            self.canvas.configure(scrollregion=self.canvas.bbox("all"))

        self.inner.bind("<Configure>", _on_inner_configure)

        def _on_canvas_configure(e: "tk.Event[Any]") -> None:
            self.canvas.itemconfig(self.canvas_window, width=e.width)

        self.canvas.bind("<Configure>", _on_canvas_configure)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")

        def _on_mousewheel(event: "tk.Event[Any]") -> None:
            if self.inner.winfo_reqheight() > self.canvas.winfo_height():
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_to_mousewheel(widget: tk.Misc) -> None:
            widget.bind("<MouseWheel>", _on_mousewheel)
            for child in widget.winfo_children():
                _bind_to_mousewheel(child)

        self.vars_map: Dict[str, tk.BooleanVar] = {}
        self.folder_vars: Dict[str, tk.BooleanVar] = {}

        groups: Dict[str, List[str]] = defaultdict(list)
        for rel in self.bak_list:
            d_name = os.path.dirname(rel)
            groups[d_name if d_name else "/"].append(rel)

        row = 0
        for folder in sorted(groups.keys()):
            files = sorted(groups[folder])
            f_var = tk.BooleanVar(value=True)
            self.folder_vars[folder] = f_var

            hf = ttk.Frame(self.inner)
            hf.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 2), padx=5)

            def make_folder_toggler(
                f_name: str, v: tk.BooleanVar
            ) -> Callable[..., None]:
                def _toggler() -> None:
                    state = v.get()
                    for r in groups[f_name]:
                        self.vars_map[r].set(state)

                return _toggler

            cb = ttk.Checkbutton(
                hf, variable=f_var, command=make_folder_toggler(folder, f_var)
            )
            cb.pack(side="left")

            lbl = ttk.Label(
                hf,
                text=f" {folder} ({len(files)} files)",
                image=self.ico_folder if self.ico_folder else "",
                compound="left",
                font=("Segoe UI", 10, "bold"),
            )
            lbl.pack(side="left", padx=2)
            row += 1

            for rel in files:
                v = tk.BooleanVar(value=True)
                self.vars_map[rel] = v

                def make_file_tracer(
                    f_name: str, p_var: tk.BooleanVar
                ) -> Callable[..., None]:
                    def _tracer(*args: Any) -> None:
                        all_checked = all(
                            self.vars_map[r].get() for r in groups[f_name]
                        )
                        p_var.set(all_checked)
                        self._update_restore_btn_text()

                    return _tracer

                v.trace_add("write", make_file_tracer(folder, f_var))

                bak = os.path.join(self.game_dir, rel)
                try:
                    mtime = os.path.getmtime(bak)
                    dt = datetime.datetime.fromtimestamp(mtime)
                    now = datetime.datetime.now()
                    if dt.date() == now.date():
                        ts_str = f"Today, {dt.strftime('%I:%M %p')}"
                    elif dt.date() == (now - datetime.timedelta(days=1)).date():
                        ts_str = f"Yesterday, {dt.strftime('%I:%M %p')}"
                    else:
                        ts_str = dt.strftime("%Y-%m-%d %I:%M %p")
                except Exception:
                    ts_str = "Unknown"

                cf = ttk.Frame(self.inner)
                cf.grid(row=row, column=0, sticky="w", padx=(30, 5), pady=1)

                ttk.Checkbutton(cf, text=os.path.basename(rel), variable=v).pack(
                    side="left"
                )

                tf = ttk.Frame(self.inner)
                tf.grid(row=row, column=1, sticky="e", padx=(10, 15), pady=1)
                ttk.Label(
                    tf, text=ts_str, foreground="gray", font=("Segoe UI", 9)
                ).pack(side="right")

                row += 1

        self.inner.columnconfigure(0, weight=1)
        self.inner.columnconfigure(1, weight=0)

        _bind_to_mousewheel(self.inner)
        self.canvas.bind("<MouseWheel>", _on_mousewheel)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=10, pady=10)

        self.btn_restore: Optional[ttk.Button] = None
        try:
            self.btn_restore = cast(Any, ttk.Button)(
                btn_frame,
                text=f"Restore Selected ({len(self.bak_list)})",
                command=self._restore_selected,
                bootstyle="danger",
            )
        except Exception:
            self.btn_restore = ttk.Button(
                btn_frame,
                text=f"Restore Selected ({len(self.bak_list)})",
                command=self._restore_selected,
            )

        if self.btn_restore:
            self.btn_restore.pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(btn_frame, text="Cancel", command=self.destroy).pack(
            side="right", padx=4
        )

        self.grab_set()

    def _on_theme_change(self, event: Optional["tk.Event[Any]"] = None) -> None:
        style = ttk.Style()
        theme_bg = str(style.lookup("TFrame", "background") or "#333333")
        is_dark = utils.get_binary_contrast_color(theme_bg) == "#FFFFFF"
        border_color = "#555555" if is_dark else "#cccccc"
        if hasattr(self, "canvas") and self.canvas.winfo_exists():
            self.canvas.config(bg=theme_bg)
        if hasattr(self, "frm") and self.frm.winfo_exists():
            self.frm.config(bg=border_color)
        utils.apply_window_theme(self)

    def _update_restore_btn_text(self) -> None:
        sel_count = sum(1 for chk in self.vars_map.values() if chk.get())
        if self.btn_restore is not None:
            self.btn_restore.config(text=f"Restore Selected ({sel_count})")

    def _restore_selected(self) -> None:
        selected = [r for r, var in self.vars_map.items() if var.get()]
        if not selected:
            messagebox.showinfo("Rollback", "No files selected.", parent=self)
            return
        if not messagebox.askyesno(
            "Confirm Restore",
            f"Restore {len(selected)} files?\nThis will overwrite the modified files with their backups.",
            parent=self,
        ):
            return

        restored = 0
        errors: List[str] = []
        for rel in selected:
            bak = os.path.join(self.game_dir, rel)
            orig = os.path.join(self.game_dir, rel[:-4])
            try:
                if not os.path.commonpath([self.game_dir, orig]).startswith(
                    os.path.normpath(self.game_dir)
                ):
                    raise RuntimeError("path traversal detected")
                atomic_write_copy(bak, orig)
                restored += 1
                self.append_log(f"Restored {rel}")
            except Exception as e:
                errors.append(f"{rel}: {e}")
                self.append_log(f"Error restoring {rel}: {e}")

        msg = f"Restored {restored} files."
        if errors:
            msg += f" {len(errors)} errors (see log)."
        messagebox.showinfo("Rollback", msg, parent=self)
        self.destroy()
        self.on_success(msg)
