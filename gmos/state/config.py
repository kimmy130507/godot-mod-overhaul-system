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
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, cast

from gmos import utils
from gmos.io import atomic_replace
from gmos.utils import get_logger

logger = get_logger()
if TYPE_CHECKING:
    import tkinter as _tk

    Tk = _tk.Tk
    Toplevel = _tk.Toplevel
    Misc = _tk.Misc
DEFAULTS = {
    "game_dir": ".",  # Single game directory
    "mods_dir": "./mods",
    "game_executable": "game.exe",  # The game's executable name
    "launch_override": "",
}

# Global lock for thread-safe config I/O
_config_lock = threading.RLock()


def get_config_path(config_dir: Optional[str] = None) -> str:
    """Return full path to config.json, always prioritizing the CWD.
    Accept override for tests or custom path saving.
    """
    if config_dir:
        # Allows for overrides (e.g., in testing)
        return os.path.join(config_dir, "config.json")
    return os.path.abspath(os.path.join(os.getcwd(), "config.json"))


class SetupWizard:
    """Simple modal setup wizard for first-run configuration.

    This class creates the real `tk.Toplevel` lazily inside __init__ so importing
    gmos.config does not require tkinter to be available or to be imported.
    """

    # 'Tk' inherits from 'Wm' (for transient) and 'Misc' (for Toplevel master and winfo_*).
    def __init__(self, parent: "Tk", config_path: Optional[str] = None):
        # Delayed tkinter imports so headless imports/tests remain cheap
        import tkinter as tk
        from tkinter import filedialog, messagebox

        self._tk = tk
        self._filedialog = filedialog
        self._messagebox = messagebox
        # Create a real Toplevel instance attached to the provided parent.
        # Annotate so static analyzers know the attribute's type.
        self._toplevel: "Toplevel" = tk.Toplevel(parent)
        utils.load_and_apply_app_icon_to_toplevel(self._toplevel)
        self.parent = parent
        self.config_path = config_path or get_config_path()
        self.result: Optional[Dict[str, Any]] = None

        self._toplevel.title("GMOS Setup")
        self._toplevel.resizable(False, False)

        frm = tk.Frame(self._toplevel, padx=12, pady=12)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="Game Executable:").grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar()
        tk.Entry(frm, textvariable=self.path_var, width=60).grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(0, 6)
        )
        tk.Button(frm, text="Browse", command=self._browse_exe).grid(
            row=1, column=2, padx=(6, 0), sticky="w"
        )
        btn_frame = tk.Frame(frm)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=(8, 0))
        tk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(
            side="right", padx=4
        )
        tk.Button(btn_frame, text="OK", command=self._on_ok).pack(side="right")

        # Try to make modal; fall back to non-modal focused window if grab fails
        self._grab_acquired = False
        try:
            self._toplevel.transient(parent)
            self._toplevel.grab_set()
            self._grab_acquired = True
        except tk.TclError:
            logger.warning(
                "SetupWizard: could not acquire grab; proceeding without modality."
            )
            try:
                self._toplevel.focus_set()
                try:
                    self._toplevel.attributes("-topmost", True)  # type: ignore[unknownMemberType]
                    self._toplevel.after(200, lambda: self._toplevel.attributes("-topmost", False))  # type: ignore[unknownMemberType]
                except Exception:
                    pass
            except Exception:
                pass

        self._toplevel.protocol("WM_DELETE_WINDOW", self._on_cancel)

        # Center dialog
        try:
            self._toplevel.update_idletasks()
            wiz_w = self._toplevel.winfo_reqwidth()
            wiz_h = self._toplevel.winfo_reqheight()
            scr_w = self._toplevel.winfo_screenwidth()
            scr_h = self._toplevel.winfo_screenheight()

            def _is_parent_sane(p: "Tk") -> bool:
                try:
                    if not getattr(p, "winfo_ismapped", lambda: False)():
                        return False
                    p.update_idletasks()
                    px = p.winfo_rootx()
                    py = p.winfo_rooty()
                    pw = p.winfo_width() or p.winfo_reqwidth()
                    ph = p.winfo_height() or p.winfo_reqheight()
                    if (
                        pw <= 0
                        or ph <= 0
                        or abs(px) > (scr_w * 4)
                        or abs(py) > (scr_h * 4)
                    ):
                        return False
                    return True
                except Exception:
                    return False

            use_parent = _is_parent_sane(parent)
            if use_parent:
                px = parent.winfo_rootx()
                py = parent.winfo_rooty()
                pw = parent.winfo_width() or parent.winfo_reqwidth() or scr_w // 2
                ph = parent.winfo_height() or parent.winfo_reqheight() or scr_h // 2
                x = px + (pw - wiz_w) // 2
                y = py + (ph - wiz_h) // 2
            else:
                x = (scr_w - wiz_w) // 2
                y = (scr_h - wiz_h) // 2

            x = int(max(-scr_w, min(x, scr_w - 20)))
            y = int(max(-scr_h, min(y, scr_h - 20)))
            self._toplevel.geometry(f"{wiz_w}x{wiz_h}+{x}+{y}")
        except Exception:
            logger.debug("SetupWizard centering failed", exc_info=True)

        try:
            self._toplevel.deiconify()
        except Exception:
            pass

        try:
            self._toplevel.lift()  # type: ignore[unknownMemberType]
            try:
                self._toplevel.attributes("-topmost", True)  # type: ignore[unknownMemberType]
                self._toplevel.after(200, lambda: self._toplevel.attributes("-topmost", False))  # type: ignore[unknownMemberType]
            except Exception:
                pass
        except Exception:
            pass

    def __getattr__(self, name: str) -> Any:
        """
        Proxy unknown attributes to the underlying Toplevel instance.
        This makes SetupWizard behave like a widget for tests and code that
        expects Toplevel methods (update_idletasks, wait_window, etc.).
        """
        # avoid infinite recursion if _toplevel isn't set
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return getattr(self._toplevel, name)
        except Exception as e:
            # expose a clearer AttributeError for callers
            raise AttributeError(name) from e

    def update_idletasks(self) -> None:
        """Expose update_idletasks on the wrapper for compatibility with tests."""
        return self._toplevel.update_idletasks()

    @property
    def window(self) -> "Toplevel":
        """Return the underlying Toplevel widget."""
        return self._toplevel

    def _browse_exe(self) -> None:
        p = self._filedialog.askopenfilename(
            title="Select Game Executable",
            filetypes=[("Executables", "*.exe"), ("All Files", "*.*")],
        )
        if p:
            self.path_var.set(p)

    def _on_cancel(self) -> None:
        # Return None and close the wizard. Do NOT quit the parent/root mainloop.
        self.result = None
        try:
            if getattr(self, "_grab_acquired", False):
                try:
                    self._toplevel.grab_release()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._toplevel.destroy()
        except Exception:
            # best-effort: ensure dialog teardown doesn't crash app
            pass

    def _on_ok(self) -> None:
        raw_path = self.path_var.get().strip()

        game_dir = ""
        exe_name = ""

        # Logic 1: User picked a file (The "Aggressive" Path)
        if os.path.isfile(raw_path):
            game_dir = os.path.dirname(raw_path)
            exe_name = os.path.basename(raw_path)

        # Logic 2: User picked a directory (Legacy fallback)
        elif os.path.isdir(raw_path):
            game_dir = raw_path
            # Try to detect largest exe
            largest_size = 0
            try:
                for f in os.listdir(game_dir):
                    if f.lower().endswith(".exe") and "unins" not in f.lower():
                        full_path = os.path.join(game_dir, f)
                        size = os.path.getsize(full_path)
                        if size > largest_size:
                            largest_size = size
                            exe_name = f
            except Exception:
                pass
        else:
            self._messagebox.showerror(
                "Error", "Please select a valid executable file."
            )
            return

        if not os.access(game_dir, os.W_OK):
            self._messagebox.showerror(
                "Permission Error", f"Directory not writable:\n{game_dir}"
            )
            return

        if not exe_name:
            exe_name = "game.exe"

        cfg = {
            "game_dir": game_dir,
            "game_executable": exe_name,
            "mods_dir": os.path.join(game_dir, "mods"),
        }

        try:
            write_config(cfg, self.config_path)
            self.result = cfg
            self._toplevel.destroy()
        except Exception as e:
            self._messagebox.showerror(
                "Save Error", f"Failed to save configuration: {e}"
            )


def ensure_config(
    config_path: Optional[str] = None,
    headless_defaults: Optional[Dict[str, Any]] = None,
    force_setup: bool = False,
) -> Dict[str, Any]:
    """
    Ensure a configuration exists. If not present or force_setup is True:
      - if headless_defaults provided, write them and return
      - otherwise show the GUI SetupWizard in an isolated root (only if needed)
        and return the resulting config or {}.
    """
    p = config_path or get_config_path()

    if not force_setup:
        cfg = load_config(p)
        if cfg and cfg.get("game_dir"):
            return cfg

    if headless_defaults is not None:
        write_config(headless_defaults, p)
        return headless_defaults

    import tkinter as tk

    # Prefer existing application root if one exists.
    created_root = False

    try:
        # Check for _default_root which is typically Tk or None
        _existing_root: Optional[Tk] = getattr(tk, "_default_root", None)
        if _existing_root and _existing_root.winfo_exists():
            root = _existing_root
        else:
            # Create a new Tk root
            root = tk.Tk()
            root.withdraw()
            created_root = True
    except Exception:
        # Create a new Tk root if getattr fails
        root = tk.Tk()
        root.withdraw()
        created_root = True

    # Create the modal dialog as a Toplevel attached to `root`.
    # Pylance now knows `root` is of type `Tk`
    wiz = SetupWizard(root, config_path=p)

    # Block until dialog closed. Use wait_window so we don't run a second mainloop.
    try:
        root.wait_window(wiz.window)
    except Exception as e:
        logger.debug("wait_window for SetupWizard failed (ignored): %s", e)

    res = wiz.result or {}

    # If we created a temporary root we must destroy it.
    if created_root:
        try:
            # Pylance now knows root is not None here
            if root.winfo_exists():
                root.destroy()
        except Exception as e:
            logger.debug("temporary root.destroy() failed (ignored): %s", e)

    return res


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load JSON config. Returns {} when file missing or invalid."""
    p = path or get_config_path()
    with _config_lock:
        # If the file doesn't exist at the CWD location, return an empty config.
        if not os.path.exists(p):
            return {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return cast(Dict[str, Any], json.load(f))
        except Exception:
            return {}


def write_config(cfg: Dict[str, Any], path: Optional[str] = None) -> None:
    """Write JSON config to the specified path, defaulting to CWD."""
    p = path or get_config_path()
    with _config_lock:
        try:
            # ensure parent dir exists (fixes FileNotFoundError in tests)
            parent = Path(p).parent
            parent.mkdir(parents=True, exist_ok=True)
            # Use atomic_replace for data integrity (Phase 1, Sec 3.3)
            json_str = json.dumps(cfg, indent=2)
            atomic_replace(p, json_str)
        except Exception as e:
            logger.error("Failed to write config file to %s: %s", p, e)
            raise
