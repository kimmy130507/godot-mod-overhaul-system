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

import argparse
import atexit
import configparser
import datetime
import difflib
import errno
import hashlib
import json
import logging
import logging.handlers
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess  # nosec B404
import sys
import tempfile
import time
import tkinter as tk
import webbrowser
import zipfile
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:
    import fcntl
except Exception:
    fcntl = None
try:
    import msvcrt
except Exception:
    msvcrt = None

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(__file__)

# Project identity
__title__ = "Godot Mod Overhaul System"
__shortname__ = "GMOS"
__version__ = "1.0"

# Prefer per-user writable location for logs and artifacts
if os.name == "nt":
    APPDATA_BASE = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or BASE_DIR
else:
    APPDATA_BASE = os.path.expanduser("~/.local/share")

LOG_DIR = os.path.join(APPDATA_BASE, "gmos", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

ROOT_DIR = BASE_DIR

logger = logging.getLogger("gmos")
logger.setLevel(logging.DEBUG)
rot = logging.handlers.RotatingFileHandler(
    os.path.join(LOG_DIR, "gmos.log"),
    maxBytes=5 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
rot.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
logger.addHandler(rot)


def _excepthook(exc_type, exc, tb):
    # log full traceback and notify user in UI if possible
    logger.error("Uncaught exception", exc_info=(exc_type, exc, tb))
    try:
        from tkinter import messagebox

        messagebox.showerror(
            "Fatal Error", "An unexpected error occurred. See logs/gmos.log"
        )
    except Exception as e:
        logger.debug("ignored exception: %s", e)


sys.excepthook = _excepthook


def _safe_spawn(command, cwd=None):
    """Safely spawn an external command.

    - Accepts a string or list. If string, splits with shlex.
    - Locates executable with shutil.which when not absolute.
    - Always spawns without a shell, closes fds and silences stdio by default.
    - Raises RuntimeError when the executable cannot be located or command empty.
    """
    if isinstance(command, str):
        cmd = shlex.split(command)
    else:
        cmd = list(command)

    if not cmd:
        raise RuntimeError("Empty command")

    exe = cmd[0]
    if not os.path.isabs(exe):
        exe_path = shutil.which(exe, path=os.environ.get("PATH"))
    else:
        exe_path = exe if os.path.exists(exe) else None

    if exe_path is None:
        raise RuntimeError(f"Cannot locate executable: {exe}")

    return subprocess.Popen(
        [exe_path] + cmd[1:],
        cwd=cwd,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ----------------------------
# Config / First-run setup
# ----------------------------


def _get_user_config_dir(appname: str = "gmos") -> str:
    """Return platform-appropriate config directory for storing settings."""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, appname)


def get_config_path(config_dir: Optional[str] = None) -> str:
    """Return full path to config.json. Accept override for tests."""
    cfgdir = config_dir or _get_user_config_dir()
    return os.path.join(cfgdir, "config.json")


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load JSON config. Returns {} when file missing or invalid."""
    p = path or get_config_path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_config(cfg: Dict[str, Any], path: Optional[str] = None) -> None:
    """Write JSON config, creating parent directory as needed."""
    p = path or get_config_path()
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


class SetupWizard(tk.Toplevel):
    """Simple modal setup wizard for first-run configuration."""

    def __init__(self, parent, config_path: Optional[str] = None):
        super().__init__(parent)
        self.parent = parent
        self.config_path = config_path or get_config_path()
        self.result: Optional[Dict[str, Any]] = None
        self.title("GMOS Setup")
        self.resizable(False, False)

        frm = tk.Frame(self, padx=12, pady=12)
        frm.pack(fill="both", expand=True)

        tk.Label(frm, text="Game executable:").grid(row=0, column=0, sticky="w")
        self.exe_var = tk.StringVar()
        tk.Entry(frm, textvariable=self.exe_var, width=60).grid(
            row=1, column=0, columnspan=2, sticky="we", pady=(0, 6)
        )
        tk.Button(frm, text="Browse", command=self._choose_exe).grid(
            row=1, column=2, padx=(6, 0)
        )

        tk.Label(frm, text="Work root (game mod folder):").grid(
            row=2, column=0, sticky="w"
        )
        self.work_var = tk.StringVar()
        tk.Entry(frm, textvariable=self.work_var, width=60).grid(
            row=3, column=0, columnspan=2, sticky="we", pady=(0, 6)
        )
        tk.Button(frm, text="Browse", command=self._choose_work).grid(
            row=3, column=2, padx=(6, 0)
        )

        btn_frame = tk.Frame(frm)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=(8, 0))
        tk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(
            side="right", padx=4
        )
        tk.Button(btn_frame, text="OK", command=self._on_ok).pack(side="right")

        # center on parent
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window(self)

    def _choose_exe(self):
        p = filedialog.askopenfilename(title="Select game executable")
        if p:
            self.exe_var.set(p)
            # suggest a default work root near the executable if none set yet
            if not self.work_var.get().strip():
                self.work_var.set(suggest_work_root(p))

    def _choose_work(self):
        p = filedialog.askdirectory(title="Select game work root")
        if p:
            self.work_var.set(p)

    def _on_cancel(self):
        self.result = None
        self.destroy()

    def _on_ok(self):
        exe = self.exe_var.get().strip()
        wr = self.work_var.get().strip()
        if not wr:
            messagebox.showerror("Invalid", "Please select a work root folder.")
            return
        # create work_root if it doesn't exist
        try:
            os.makedirs(wr, exist_ok=True)
        except Exception as e:
            messagebox.showerror(
                "Folder Error", f"Failed to create or access work root:\n{e}"
            )
            return
        # optional sanity: ensure it's a directory and writable
        if not os.path.isdir(wr) or not os.access(wr, os.W_OK):
            messagebox.showerror(
                "Folder Error", "Work root must be a writable directory."
            )
            return

        # warn but allow non-existing exe (user may install later)
        if exe and not os.path.exists(exe):
            if not messagebox.askyesno(
                "Executable missing",
                f"The executable does not exist:\n{exe}\nProceed anyway?",
            ):
                return
        cfg = {"game_exe": exe, "work_root": wr}
        try:
            write_config(cfg, self.config_path)
            self.result = cfg
            self.destroy()
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save configuration: {e}")


def suggest_work_root(exe_path: str) -> str:
    """
    Return a suggested work_root given a chosen game executable path.
    Current policy: suggest a 'mods' directory next to the executable.
    """
    if not exe_path:
        return os.path.join(os.path.expanduser("~"), "mods")
    exe_dir = os.path.dirname(exe_path)
    if not exe_dir:
        exe_dir = os.path.expanduser("~")
    return os.path.join(exe_dir, "mods")


def ensure_config(
    config_path: Optional[str] = None,
    headless_defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Ensure a configuration exists. If not present:
      - if headless_defaults provided, write them and return
      - otherwise show the GUI SetupWizard and return the resulting config or {}
    """
    p = config_path or get_config_path()
    cfg = load_config(p)
    if cfg:
        return cfg
    if headless_defaults is not None:
        write_config(headless_defaults, p)
        return headless_defaults
    # show GUI wizard (require a root if none exists)
    root = None
    created_root = False
    try:
        if not tk._default_root:
            root = tk.Tk()
            root.withdraw()
            created_root = True
        else:
            root = tk._default_root
    except Exception:
        # fallback: create ephemeral root
        root = tk.Tk()
        root.withdraw()
        created_root = True

    wiz = SetupWizard(root, config_path=p)
    res = wiz.result or {}
    if created_root:
        try:
            root.destroy()
        except Exception:
            pass
    return res


# ----------------------------
# Permission checks and friendly error handling
# ----------------------------
def check_write_permission(path: str) -> Tuple[bool, Optional[str]]:
    """
    Check whether we can write to `path` (file or directory).
    Returns (True, None) when writable. Otherwise returns (False, message).

    Best-effort checks:
      - if path is a file, check parent dir writability.
      - os.access for write permission.
      - try to create+remove a temp file to detect ACL/UAC issues.
    """
    try:
        if not path:
            return False, "empty path"
        if os.path.isdir(path):
            parent = path
        else:
            parent = os.path.dirname(path) or "."

        # quick check
        if not os.access(parent, os.W_OK):
            return False, f"No write permission to '{parent}'"

        # try creating a temp file inside parent
        import tempfile

        fd = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".gmos_check_", dir=parent)
            os.close(fd)
            os.remove(tmp)
        except PermissionError as pe:
            return False, f"Permission denied writing to '{parent}': {pe}"
        except Exception:
            # best-effort: if mkstemp fails for other reasons, warn but allow
            pass

        return True, None
    except Exception as e:
        return False, f"permission check error: {e}"


def handle_permission_error(
    exc: Exception, path: str, parent: Optional[object] = None
) -> None:
    """
    Friendly handling of permission errors. Logs and optionally shows a GUI messagebox.

    parent: if a Tk parent window is available, a messagebox will be shown.
    """
    msg = f"Permission error while accessing '{path}': {exc}\n\n"
    msg += "Common fixes:\n - choose a different work folder\n - run GMOS as administrator/sudo\n - check folder ACLs / antivirus\n"
    logger.error(msg)
    # show GUI alert if possible
    try:
        if parent is not None:
            from tkinter import messagebox

            messagebox.showerror("Permission Error", msg, parent=parent)
    except Exception:
        # headless: print to debug only
        logger.debug("Permission dialog not shown (headless or no tkinter available).")


def safe_atomic_copy_with_bak(src: str, dst: str, *args, **kwargs):
    """
    Safe wrapper that verifies write permissions before attempting the atomic copy
    that produces a .bak file. Replace direct calls to atomic_copy_with_single_bak(...)
    with this wrapper to get friendlier errors and early checks.
    """
    try:
        parent = os.path.dirname(dst) or "."
        ok, err = check_write_permission(parent)
        if not ok:
            raise RuntimeError(err or "no write permission")
        # call existing atomic copy function if present
        if "atomic_copy_with_single_bak" in globals():
            return atomic_copy_with_single_bak(src, dst, *args, **kwargs)
        else:
            # fallback: attempt an atomic write via replace
            tmp = dst + ".tmp"
            with open(tmp, "wb") as fsrc, open(src, "rb") as s:
                fsrc.write(s.read())
            os.replace(tmp, dst)
            return ["SUCCESS"]
    except Exception as e:
        handle_permission_error(e, dst)
        raise


# ----------------------------
# Safe replace shim (global)
# ----------------------------
def _safe_replace(src: str, dst: str):
    """
    Wrapper around os.replace that performs a write-permission check and
    surfaces friendly errors via handle_permission_error before re-raising.

    This protects existing code that calls os.replace directly by performing
    a pre-flight permission check and presenting a clearer message if the
    operation would fail due to ACL/UAC/readonly issues.
    """
    try:
        parent = os.path.dirname(dst) or "."
        ok, err = check_write_permission(parent)
        if not ok:
            raise RuntimeError(err or f"No write permission to '{parent}'")
        return _orig_os_replace(src, dst)
    except Exception as e:
        # Surface a friendly dialog/logging and re-raise the original error.
        try:
            handle_permission_error(e, dst)
        except Exception:
            # ensure problems in the handler do not swallow the original error
            logger.debug("handle_permission_error failed while reporting: %s", e)
        raise


# Install shim for os.replace so existing replace calls are guarded.
# Keep a backing reference to the original implementation.
try:
    _orig_os_replace = os.replace
    os.replace = _safe_replace
except Exception:
    # Non-fatal if monkey-patching is not allowed in some embedded environments.
    logger.debug("Failed to install os.replace shim; continuing without global guard.")

# ----------------------------
# Diff / hunk utilities (non-GUI)
# ----------------------------

_UNIFIED_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def generate_unified_diff(
    orig_text: str,
    new_text: str,
    fromfile: str = "orig",
    tofile: str = "new",
    n: int = 3,
) -> str:
    """Return unified diff string between orig_text and new_text."""
    a = orig_text.splitlines(keepends=True)
    b = new_text.splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=fromfile, tofile=tofile, n=n))


def parse_unified_diff_hunks(diff_text: str) -> List[Dict[str, Any]]:
    """
    Parse unified diff text into a list of hunks.
    Each hunk is a dict:
      {
        'old_start': int, 'old_count': int,
        'new_start': int, 'new_count': int,
        'lines': List[str],         # raw diff lines for the hunk (including leading ' ', '-', '+')
        'old_lines': List[str],     # original-context lines for the hunk (without prefixes)
        'new_lines': List[str],     # new-context lines for the hunk (without prefixes)
      }
    """
    lines = diff_text.splitlines()
    hunks: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        m = _UNIFIED_HUNK_RE.match(lines[i])
        if not m:
            i += 1
            continue
        old_start = int(m.group(1))
        old_count = int(m.group(2) or "1")
        new_start = int(m.group(3))
        new_count = int(m.group(4) or "1")
        i += 1
        hunk_lines: List[str] = []
        old_lines: List[str] = []
        new_lines: List[str] = []
        while i < len(lines) and not _UNIFIED_HUNK_RE.match(lines[i]):
            ln = lines[i]
            if ln.startswith("+") or ln.startswith("-") or ln.startswith(" "):
                hunk_lines.append(ln)
                if ln.startswith("-") or ln.startswith(" "):
                    old_lines.append(ln[1:])
                if ln.startswith("+") or ln.startswith(" "):
                    new_lines.append(ln[1:])
            else:
                # context or file header lines might appear; include as-is
                hunk_lines.append(ln)
            i += 1
        hunks.append(
            {
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "lines": hunk_lines,
                "old_lines": old_lines,
                "new_lines": new_lines,
            }
        )
    return hunks


def apply_hunks(
    original_text: str, hunks: List[Dict[str, Any]], selected_hunk_indices: List[int]
) -> str:
    """
    Apply selected hunks (by index) to original_text and return merged text.
    selected_hunk_indices is list of indices into hunks to accept (others remain as original).
    This operates at hunk granularity and does not attempt patch fuzzing.
    """
    orig_lines = original_text.splitlines(keepends=False)
    result: List[str] = []
    # pointer in orig_lines (0-based)
    ptr = 0
    for idx, h in enumerate(hunks):
        old_start = h["old_start"] - 1  # convert to 0-based
        old_count = h["old_count"]
        # copy unchanged lines up to hunk
        while ptr < old_start and ptr < len(orig_lines):
            result.append(orig_lines[ptr])
            ptr += 1
        # now at start of hunk area
        if idx in selected_hunk_indices:
            # accept new_lines
            for nl in h["new_lines"]:
                result.append(nl.rstrip("\n"))
        else:
            # keep original old_lines (if present). If old_count==0, nothing to add.
            for j in range(old_count):
                if (old_start + j) < len(orig_lines):
                    result.append(orig_lines[old_start + j])
        ptr = old_start + old_count
    # append remaining tail
    while ptr < len(orig_lines):
        result.append(orig_lines[ptr])
        ptr += 1
    # reassemble with newlines
    return "\n".join(result) + ("\n" if original_text.endswith("\n") else "")


# ----------------------------
# HunkViewer UI (Tkinter)
# ----------------------------
class HunkViewer(tk.Toplevel):
    """
    Modal dialog to inspect unified-diff hunks and select which hunks to apply.

    Usage:
        hv = HunkViewer(parent, orig_text, new_text)
        merged = hv.show_modal()  # returns merged text or None if cancelled
    """

    def __init__(self, parent, orig_text: str, new_text: str):
        super().__init__(parent)
        self.parent = parent
        self.orig_text = orig_text
        self.new_text = new_text
        self.transient(parent)
        self.title("Resolve conflicts - Hunk viewer")
        self.resizable(True, True)
        self.result = None

        # generate diff and hunks
        self.diff_text = generate_unified_diff(
            orig_text, new_text, fromfile="original", tofile="replacement"
        )
        self.hunks = parse_unified_diff_hunks(self.diff_text)
        self.hunk_vars = [tk.IntVar(value=1) for _ in self.hunks]

        # layout: left pane with hunk list + checkboxes, right pane preview
        left = tk.Frame(self)
        left.pack(side="left", fill="y", padx=6, pady=6)
        tk.Label(left, text="Hunks (check to apply)").pack(anchor="w")
        list_frame = tk.Frame(left)
        list_frame.pack(fill="y", expand=True)
        # create a scrollable frame for many hunks
        canvas = tk.Canvas(list_frame, width=320, height=300)
        vs = tk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")

        for idx, h in enumerate(self.hunks):
            cb = tk.Checkbutton(
                inner,
                text=f"Hunk {idx+1}: old@{h['old_start']}+{h['old_count']} -> new@{h['new_start']}+{h['new_count']}",
                variable=self.hunk_vars[idx],
                anchor="w",
                justify="left",
                wraplength=300,
            )
            cb.pack(anchor="w", fill="x", pady=2)
            # tiny preview snippet
            snippet = (
                h["old_lines"][:1] + ["..."]
                if len(h["old_lines"]) > 1
                else h["old_lines"]
            )
            tk.Label(inner, text=" ".join(snippet)[:200], fg="gray").pack(
                anchor="w", padx=12
            )

        inner.update_idletasks()
        canvas.config(scrollregion=canvas.bbox("all"))

        right = tk.Frame(self)
        right.pack(side="right", fill="both", expand=True, padx=6, pady=6)
        tk.Label(right, text="Merged preview").pack(anchor="w")
        self.preview = tk.Text(right, width=80, height=30, wrap="none")
        self.preview.pack(fill="both", expand=True)
        self.preview.configure(state="disabled")

        # buttons
        btnf = tk.Frame(self)
        btnf.pack(fill="x", padx=6, pady=6)
        tk.Button(btnf, text="Apply Selected", command=self._on_apply).pack(
            side="right", padx=4
        )
        tk.Button(btnf, text="Apply All", command=self._on_apply_all).pack(
            side="right", padx=4
        )
        tk.Button(btnf, text="Cancel", command=self._on_cancel).pack(
            side="right", padx=4
        )

        # update preview when checkboxes change
        for v in self.hunk_vars:
            v.trace_add("write", lambda *a: self._update_preview())

        # initial preview
        self._update_preview()

        # modal behavior
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _selected_indices(self) -> List[int]:
        return [i for i, v in enumerate(self.hunk_vars) if v.get()]

    def _update_preview(self):
        try:
            sel = self._selected_indices()
            merged = apply_hunks(self.orig_text, self.hunks, sel)
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", merged)
            self.preview.configure(state="disabled")
        except Exception:
            # non-fatal; leave preview unchanged
            pass

    def _on_apply(self):
        self.result = apply_hunks(self.orig_text, self.hunks, self._selected_indices())
        self.destroy()

    def _on_apply_all(self):
        self.result = apply_hunks(
            self.orig_text, self.hunks, list(range(len(self.hunks)))
        )
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()

    def show_modal(self):
        self.wait_window(self)
        return self.result


# ----------------------------
# Mod Info Pane (Tkinter)
# ----------------------------
class ModInfoPane(tk.Frame):
    """
    Right-side inspector panel showing selected mod metadata and dependency errors.
    Usage:
        self.mod_info = ModInfoPane(parent_frame)
        self.mod_info.pack(side="right", fill="y")
        # when selection changes:
        self.mod_info.update_for_config(cfg)
    """

    def __init__(self, master, width=360, **kwargs):
        super().__init__(master, width=width, **kwargs)
        self.columnconfigure(1, weight=1)
        self._widgets = {}

        def _label(row, text, bold=False):
            lbl = tk.Label(self, text=text, anchor="w", justify="left")
            if bold:
                lbl.config(font=("TkDefaultFont", 9, "bold"))
            lbl.grid(
                row=row, column=0, sticky="nw", padx=6, pady=(6 if row == 0 else 2)
            )
            return lbl

        # Basic metadata keys
        _label(0, "Name:", bold=True)
        self._widgets["name"] = tk.Label(
            self, text="", anchor="w", justify="left", wraplength=260
        )
        self._widgets["name"].grid(row=0, column=1, sticky="nw", padx=6, pady=6)

        _label(1, "Version:")
        self._widgets["version"] = tk.Label(self, text="", anchor="w")
        self._widgets["version"].grid(row=1, column=1, sticky="nw", padx=6)

        _label(2, "Author:")
        self._widgets["author"] = tk.Label(self, text="", anchor="w")
        self._widgets["author"].grid(row=2, column=1, sticky="nw", padx=6)

        _label(3, "Description:", bold=True)
        self._widgets["desc"] = tk.Text(self, height=6, wrap="word")
        self._widgets["desc"].grid(
            row=3, column=0, columnspan=2, sticky="nsew", padx=6, pady=6
        )
        self._widgets["desc"].configure(state="disabled")

        # Dependencies and Errors
        _label(4, "Dependencies:", bold=True)
        self._widgets["deps"] = tk.Label(
            self, text="", anchor="w", justify="left", fg="black", wraplength=260
        )
        self._widgets["deps"].grid(row=4, column=1, sticky="nw", padx=6, pady=2)

        _label(5, "Dependency Errors:", bold=True)
        self._widgets["errors"] = tk.Label(
            self, text="", anchor="w", justify="left", fg="red", wraplength=260
        )
        self._widgets["errors"].grid(
            row=5, column=0, columnspan=2, sticky="nw", padx=6, pady=(2, 6)
        )

        # Action buttons
        btnf = tk.Frame(self)
        btnf.grid(row=6, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        self._widgets["open_folder_btn"] = tk.Button(
            btnf, text="Open Mod Folder", command=self._open_mod_folder
        )
        self._widgets["open_folder_btn"].pack(side="left")
        self._widgets["toggle_enable_btn"] = tk.Button(
            btnf, text="Toggle Enable", command=self._toggle_enable
        )
        self._widgets["toggle_enable_btn"].pack(side="left", padx=6)

        # internal state
        self._current_cfg = None

    def _get_cfg_path(self):
        cfg = self._current_cfg or {}
        return cfg.get("Path", "")

    def _open_mod_folder(self):
        path = self._get_cfg_path()
        if not path:
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", path])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            handle_permission_error(
                Exception("failed to open folder"), path, parent=self
            )

    def _toggle_enable(self):
        cfg = self._current_cfg
        if not cfg:
            return
        # toggle a convention key 'Enabled'
        cur = cfg.get("Enabled", True)
        cfg["Enabled"] = not bool(cur)
        # update visual state immediately
        self.update_for_config(cfg)
        # caller UI should refresh list ordering/appearance afterwards
        try:
            if hasattr(self.master, "refresh_mod_list"):
                self.master.refresh_mod_list()
        except Exception:
            # ignore if no refresh available
            pass

    def _safe_set_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text or "")
        widget.configure(state="disabled")

    def update_for_config(self, cfg: Optional[Dict[str, Any]]):
        """
        Populate pane from config dict. Accepts None to clear the pane.
        Expected config has:
          - Path
          - Sections (optional) where 'Metadata' may include Name, Version, Author, Description
          - _deps_errors list produced by apply_dependency_resolution
        """
        self._current_cfg = cfg
        if not cfg:
            for k in self._widgets:
                if k == "desc":
                    self._safe_set_text(self._widgets["desc"], "")
                else:
                    self._widgets[k].config(text="")
            return

        # read metadata from Sections['Metadata'] lines or direct keys
        name = cfg.get("Name") or _get_mod_name_from_config(cfg)
        version = ""
        author = ""
        description = ""
        sections = cfg.get("Sections") or {}
        for sec_k, lines in sections.items():
            if sec_k.lower() == "metadata":
                for line in lines:
                    try:
                        k, v = [p.strip() for p in line.split("=", 1)]
                    except Exception:
                        continue
                    lk = k.lower()
                    if lk == "name" and v:
                        name = v
                    elif lk == "version":
                        version = v
                    elif lk == "author":
                        author = v
                    elif lk in ("desc", "description"):
                        description = v

        self._widgets["name"].config(text=name or "")
        self._widgets["version"].config(text=version or "")
        self._widgets["author"].config(text=author or "")
        self._safe_set_text(self._widgets["desc"], description or "")

        deps = []
        for sec_k, lines in sections.items():
            if sec_k.lower() == "dependencies":
                for line in lines:
                    try:
                        _, val = [p.strip() for p in line.split("=", 1)]
                    except Exception:
                        val = line.strip()
                    for part in (p.strip() for p in val.split(",") if p.strip()):
                        deps.append(part)
        self._widgets["deps"].config(text=", ".join(deps) or "(none)")

        errlist = cfg.get("_deps_errors") or cfg.get("Errors") or []
        if errlist:
            self._widgets["errors"].config(text="\n".join(errlist))
        else:
            self._widgets["errors"].config(text="")


def _get_mod_name_from_config(mod_config: Dict[str, Any]) -> str:
    """Determine mod name. Prefer Metadata 'Name' then folder basename."""
    # try metadata section lines like "Name = value"
    sections = mod_config.get("Sections", {}) or {}
    # case-insensitive lookup
    for sec_k in sections.keys():
        if sec_k.lower() == "metadata":
            for line in sections[sec_k]:
                try:
                    k, v = [p.strip() for p in line.split("=", 1)]
                except Exception:
                    continue
                if k.lower() == "name" and v:
                    return v
    # fallback to folder name of the mod path
    path = mod_config.get("Path", "") or ""
    return os.path.basename(path) or path


def _parse_dependencies_from_config(mod_config: Dict[str, Any]) -> Set[str]:
    """Return a set of dependency names declared by this mod config."""
    deps: Set[str] = set()
    sections = mod_config.get("Sections", {}) or {}
    for sec_k in sections.keys():
        if sec_k.lower() == "dependencies":
            for line in sections[sec_k]:
                try:
                    _, val = [p.strip() for p in line.split("=", 1)]
                except Exception:
                    val = line.strip()
                for part in (p.strip() for p in val.split(",") if p.strip()):
                    if part:
                        deps.add(part)
    return deps


def resolve_mod_dependencies(
    mod_configs: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """
    Topologically sort mods by declared dependencies.

    Returns (ordered_mod_configs, errors_dict)
    - ordered_mod_configs: list in load order (deps first). If cycles exist the
      returned list will be partial (nodes outside cycles).
    - errors_dict: mapping mod_name -> list[str] of error messages (missing deps or cycle)
    """
    # map name -> config
    name_to_cfg: Dict[str, Dict[str, Any]] = {}
    for cfg in mod_configs:
        name = _get_mod_name_from_config(cfg)
        name_to_cfg[name] = cfg

    # build adjacency: dep -> set(dependents)
    adj: Dict[str, Set[str]] = {n: set() for n in name_to_cfg}
    # track missing deps
    errors: Dict[str, List[str]] = {}
    deps_of: Dict[str, Set[str]] = {}

    for name, cfg in name_to_cfg.items():
        deps = _parse_dependencies_from_config(cfg)
        deps_of[name] = deps
        for d in deps:
            if d not in name_to_cfg:
                errors.setdefault(name, []).append(f"missing dependency: {d}")
            else:
                adj[d].add(name)

    # Kahn's algorithm for topological sort
    # compute in-degree
    indeg: Dict[str, int] = {n: 0 for n in name_to_cfg}
    for src, targets in adj.items():
        for t in targets:
            indeg[t] += 1

    queue = [n for n, d in indeg.items() if d == 0]
    order: List[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for m in sorted(adj.get(n, [])):
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)

    # detect cycles (nodes not in order and not already errored for missing deps)
    remaining = [n for n in name_to_cfg.keys() if n not in order]
    if remaining:
        # find strongly connected components minimal reporter: list remaining as cycle nodes
        for n in remaining:
            errors.setdefault(n, []).append(
                f"dependency cycle detected (involving: {', '.join(sorted(remaining))})"
            )

    # produce ordered configs for nodes in order
    ordered_cfgs = [name_to_cfg[n] for n in order if n in name_to_cfg]
    return ordered_cfgs, errors


def apply_dependency_resolution(
    mod_configs: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """
    Annotate mod_configs with dependency resolution results.

    Adds per-config keys:
      - '_deps_errors': list[str] of human messages (missing deps, cycles)
      - '_resolved_order_rank': int (0-based) if placed in topo order

    Returns (ordered_configs, errors_dict) where ordered_configs is the list
    sorted in load order (dependencies first). Mods involved in cycles or with missing
    deps may be missing a rank and will be placed after resolved mods.
    """
    ordered, errors = resolve_mod_dependencies(mod_configs)

    # clear previous annotations
    for cfg in mod_configs:
        cfg.pop("_deps_errors", None)
        cfg.pop("_resolved_order_rank", None)

    # assign ranks for ordered mods
    rank = 0
    for cfg in ordered:
        name = _get_mod_name_from_config(cfg)
        cfg["_resolved_order_rank"] = rank
        rank += 1

    # attach errors from resolver to original configs
    for name, errs in errors.items():
        # find matching config by name and set _deps_errors
        for cfg in mod_configs:
            if _get_mod_name_from_config(cfg) == name:
                cfg["_deps_errors"] = list(errs)
                break

    # place unresolved configs (cycles/missing deps) after resolved ones, preserve relative order
    def _sort_key(cfg):
        r = cfg.get("_resolved_order_rank")
        if r is None:
            # place after resolved; keep stable order by mod folder name
            return (1, _get_mod_name_from_config(cfg).lower())
        return (0, r)

    ordered_all = sorted(mod_configs, key=_sort_key)
    return ordered_all, errors


def rebuild_mod_listbox_from_configs(
    listbox, mod_configs: List[Dict[str, Any]], name_getter=_get_mod_name_from_config
):
    """
    Utility to rebuild a Tk Listbox (or similar widget) showing mods according to resolved order.

    - listbox: a Tkinter Listbox, or any object implementing delete(0,END) and insert(idx, text) and itemconfig.
    - mod_configs: list of mod config dicts (will be sorted by _resolved_order_rank if present).
    - name_getter: function(cfg) -> display name.

    Behavior:
    - Resolved mods appear first.
    - Mods with dependency errors are marked with ' [INVALID]' and colored red.
    - Mods that are disabled (if they have key 'Enabled' == False) are dimmed (gray).
    """
    try:
        # Clear listbox
        listbox.delete(0, "end")
    except Exception:
        # not a real listbox; caller may handle UI insertion differently
        return

    for cfg in mod_configs:
        name = name_getter(cfg)
        label = name
        is_invalid = bool(cfg.get("_deps_errors"))
        if is_invalid:
            label = f"{label} [INVALID]"
        # detect disabled flag conventionally stored as Enabled or enabled
        enabled = cfg.get("Enabled")
        if enabled is None:
            # try lowercase key in sections metadata if present
            enabled = cfg.get("enabled", True)

        idx = listbox.size()
        listbox.insert(idx, label)
        try:
            if not enabled:
                listbox.itemconfig(idx, fg="gray")
            elif is_invalid:
                listbox.itemconfig(idx, fg="red")
        except Exception:
            # some listbox implementations don't support itemconfig; ignore
            pass

        # attach tooltip text in cfg for UI to consume if desired
        if is_invalid:
            cfg["_deps_tooltip"] = "\n".join(cfg.get("_deps_errors", []))
        else:
            cfg.pop("_deps_tooltip", None)


# ----------------- single-instance lock helpers ---------------------------
LOCK_FD = None
LOCK_PATH = os.path.join(LOG_DIR, "gmos.lock")
CURRENT_LOCK_PATH = None  # realpath of lock we currently hold (global or per-workroot)
_PLATFORM_HANDLE = None


def _pid_running(pid: int) -> bool:
    """Return True if a process with pid exists on this system."""
    try:
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except OSError as e:
        if getattr(e, "errno", None) in (errno.ESRCH, errno.ENOENT):
            return False
        # On Windows permission error means process exists
        if isinstance(e, PermissionError):
            return True
        return False
    except Exception:
        return False
    return True


def _acquire_file_lock(fd):
    """Platform-specific non-blocking exclusive lock on open file descriptor."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    if msvcrt is not None:
        # lock first byte
        try:
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            raise
    raise RuntimeError("No locking mechanism available on this platform")


def _release_file_lock(fd):
    if fd is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            try:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception as e:
                logger.debug("ignored exception: %s", e)
    except Exception as e:
        logger.debug("ignored exception: %s", e)


def try_acquire_lock_fd(lock_path: str):
    """
    Try to acquire an exclusive non-blocking lock on `lock_path`.
    Returns an open binary file object if lock acquired, otherwise None.
    Does NOT touch global LOCK_FD or CURRENT_LOCK_PATH.
    """
    try:
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        fd = open(lock_path, "a+b")
        try:
            _acquire_file_lock(fd)
            return fd
        except Exception:
            try:
                fd.close()
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            return None
    except Exception:
        return None


def acquire_app_lock(lock_path: str = LOCK_PATH, retry_once: bool = True) -> bool:
    """Acquire single-instance lock. Returns True if acquired, False otherwise.
    On failure the lock owner PID is logged/returned via messagebox or stdout.
    """
    global LOCK_FD, CURRENT_LOCK_PATH
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    # Fast path: try atomic create of the lock file. This is O_EXCL and
    # will fail if another process is creating the file at the same time.
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        fd_os = os.open(lock_path, flags)
        # wrap into a binary file object to allow flock on it subsequently
        fd = os.fdopen(fd_os, "w+b")
        try:
            # write our PID and fsync; we already own the file by creation
            pid_bytes = str(os.getpid()).encode("utf-8")
            fd.write(pid_bytes)
            fd.flush()
            os.fsync(fd.fileno())
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        # adopt as our lock descriptor (no flock needed; creation is atomic)
        LOCK_FD = fd
        CURRENT_LOCK_PATH = os.path.realpath(lock_path)
        atexit.register(release_app_lock)
        return True
    except FileExistsError:
        # another process created the file concurrently. Fall back to flock path.
        logger.debug(
            "concurrent file create detected while creating lock file (ignored)"
        )
    except Exception:
        # If atomic create fails for another reason, fall back as well.
        logger.exception(
            "Atomic create of lock file failed, falling back to flock method"
        )

    # Fallback: open existing file and try to acquire flock (existing behavior)
    try:
        fd = open(lock_path, "a+b")
        try:
            _acquire_file_lock(fd)
        except Exception:
            # read existing PID
            try:
                fd.seek(0)
                data = fd.read()
                data = data.decode("utf-8").strip() if data else ""
                owner_pid = int(data) if data else None
            except Exception:
                owner_pid = None

            if owner_pid and _pid_running(owner_pid):
                try:
                    fd.close()
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                return False
            if owner_pid is None:
                try:
                    fd.close()
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                return False
            if retry_once:
                try:
                    fd.close()
                    os.remove(lock_path)
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                time.sleep(0.05)
                return acquire_app_lock(lock_path, retry_once=False)
            else:
                try:
                    fd.close()
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                return False

        # we hold the flock; write our PID (truncate + write)
        try:
            fd.seek(0)
            fd.truncate(0)
            pid_bytes = str(os.getpid()).encode("utf-8")
            fd.write(pid_bytes)
            fd.flush()
            os.fsync(fd.fileno())
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        LOCK_FD = fd
        atexit.register(release_app_lock)
        try:
            CURRENT_LOCK_PATH = os.path.realpath(lock_path)
        except Exception as e:
            CURRENT_LOCK_PATH = lock_path
            logger.debug("failed to set CURRENT_LOCK_PATH to %s: %s", lock_path, e)
        return True
    except Exception:
        try:
            fd.close()
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        return False


def release_app_lock():
    """Release held locks and remove the lock file if it contains our PID."""
    global LOCK_FD, CURRENT_LOCK_PATH
    lockfpath = CURRENT_LOCK_PATH or LOCK_PATH
    try:
        # release any file lock we hold
        if LOCK_FD:
            try:
                _release_file_lock(LOCK_FD)
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            try:
                LOCK_FD.close()
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            LOCK_FD = None

        # release any platform-native lock
        try:
            release_platform_lock()
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        # If the lock file exists read it directly and act if it contains our PID.
        try:
            if os.path.exists(lockfpath):
                try:
                    with open(lockfpath, "rb") as fh:
                        content = fh.read()
                        cur = content.decode("utf-8").strip() if content else ""
                except Exception:
                    cur = ""
                if cur == str(os.getpid()):
                    # truncate then remove (best-effort)
                    try:
                        with open(lockfpath, "r+b") as fh:
                            fh.truncate(0)
                            fh.flush()
                            try:
                                os.fsync(fh.fileno())
                            except Exception as e:
                                logger.debug("ignored exception: %s", e)
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    try:
                        os.remove(lockfpath)
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
        except Exception as e:
            logger.debug("ignored exception: %s", e)
    finally:
        try:
            CURRENT_LOCK_PATH = None
        except Exception as e:
            logger.debug("ignored exception: %s", e)


def acquire_workroot_lock(work_root: str) -> bool:
    """Acquire lock for the given work_root.
    Try platform-native lock first. Fall back to file-based lock if needed.
    """
    if not work_root:
        return acquire_app_lock()
    wr = os.path.realpath(work_root)
    os.makedirs(wr, exist_ok=True)

    # Try platform lock first
    try:
        ph = acquire_platform_lock_for_workroot(wr)
        if ph:
            # record current lock path for consistency with file-based code
            try:
                global CURRENT_LOCK_PATH
                CURRENT_LOCK_PATH = os.path.join(wr, ".gmos.lock")
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            atexit.register(release_platform_lock)
            return True
    except Exception:
        logger.exception("Platform lock attempt failed; falling back to file lock")

    # fallback to file-based lock
    return acquire_app_lock(os.path.join(wr, ".gmos.lock"))


def wire_workroot_locking(app):
    """
    Watch app.vars['work_root_dir'] and switch the lock automatically
    to workroot/.gmos.lock when the user selects a work root.
    """
    try:
        if not hasattr(app, "vars") or "work_root_dir" not in app.vars:
            return
        var = app.vars["work_root_dir"]

        def _on_change(*_):
            # declare globals up-front
            global LOCK_FD, CURRENT_LOCK_PATH
            try:
                new_wr = safe_norm(var.get())
                if not new_wr:
                    app.append_log("Workroot cleared; keeping current lock.")
                    return

                # If already locked to this path do nothing.
                try:
                    rp = os.path.realpath(new_wr)
                    if CURRENT_LOCK_PATH and os.path.realpath(CURRENT_LOCK_PATH) == rp:
                        return
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                # Prepare new lock path and attempt to acquire it with short retries
                new_lock_path = os.path.join(os.path.realpath(new_wr), ".gmos.lock")
                fd_new = None
                attempts = 5
                for _ in range(attempts):
                    fd_new = try_acquire_lock_fd(new_lock_path)
                    if fd_new:
                        break
                    time.sleep(0.05)

                if not fd_new:
                    # Failed to acquire the workroot lock. Inform user and close this instance.
                    app.append_log(
                        f"Failed to acquire workroot lock for {new_wr}. Another instance may hold it."
                    )
                    try:
                        from tkinter import messagebox

                        messagebox.showwarning(
                            "Lock Failed",
                            f"Cannot acquire lock for work root:\n{new_wr}\n\n"
                            "Another instance may be running for that work root.\n"
                            "This instance will now exit.",
                        )
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    try:
                        # Close GUI after dialog
                        app.after(0, app.destroy)
                    except Exception:
                        try:
                            sys.exit(2)
                        except Exception as e:
                            logger.debug("ignored exception: %s", e)
                    return

                # We successfully locked the new workroot file. Write our PID to it.
                try:
                    fd_new.seek(0)
                    fd_new.truncate(0)
                    pid_bytes = str(os.getpid()).encode("utf-8")
                    fd_new.write(pid_bytes)
                    fd_new.flush()
                    os.fsync(fd_new.fileno())
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                # Remember old lock path so we can remove it after switching
                old_lock_path = None
                try:
                    old_lock_path = CURRENT_LOCK_PATH
                except Exception:
                    old_lock_path = None

                # Release the previous platform lock (if any) and then the app/file lock.
                try:
                    try:
                        release_platform_lock()
                    except Exception:
                        logger.exception(
                            "release_platform_lock failed during workroot switch"
                        )
                    release_app_lock()
                except Exception:
                    logger.exception("release_app_lock failed during workroot switch")

                # Adopt new lock descriptor as the global lock handle.
                try:
                    LOCK_FD = fd_new
                    CURRENT_LOCK_PATH = os.path.realpath(new_lock_path)
                    try:
                        atexit.register(release_app_lock)
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    app.append_log(f"Acquired workroot lock: {new_wr}")

                    # attempt best-effort removal of the old lock file
                    try:
                        if old_lock_path and os.path.exists(old_lock_path):
                            try:
                                os.remove(old_lock_path)
                                app.append_log(
                                    f"Removed old lock file: {old_lock_path}"
                                )
                            except Exception:
                                # if removal fails ignore; it will be cleaned later
                                logger.exception(
                                    "Failed removing old lock file: %s", old_lock_path
                                )
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                except Exception:
                    # Fallback: ensure we release platform lock we just created, then close fd_new
                    try:
                        release_platform_lock()
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    try:
                        fd_new.close()
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    try:
                        acquire_app_lock()
                    except Exception:
                        logger.exception(
                            "Failed to re-acquire global lock after adopting new lock failed"
                        )
                    app.append_log(
                        f"Failed to adopt workroot lock for {new_wr}; retained previous lock"
                    )
            except Exception as e:
                logger.exception("workroot change handler failed: %s", e)

        # attach trace; support both trace_add and older trace
        try:
            var.trace_add("write", _on_change)
        except Exception:
            try:
                var.trace("w", _on_change)
            except Exception:
                logger.exception("Failed to attach trace to work_root_dir var")

        # call once to apply current value immediately
        try:
            _on_change()
        except Exception:
            logger.exception("Initial workroot lock attempt failed")
    except Exception:
        logger.exception("wire_workroot_locking failed")


# ---------- Windows named mutex (ctypes) ----------
def _try_windows_mutex(name: str):
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        CreateMutexW = kernel32.CreateMutexW
        CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        CreateMutexW.restype = wintypes.HANDLE
        GetLastError = kernel32.GetLastError

        h = CreateMutexW(None, False, name)
        if not h:
            return None
        # ERROR_ALREADY_EXISTS = 183
        if GetLastError() == 183:
            # somebody else already has/created it
            try:
                kernel32.CloseHandle(h)
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            return None
        return ("win_mutex", h, kernel32)
    except Exception:
        return None


def _release_windows_mutex(handle_tuple):
    try:
        _, h, kernel32 = handle_tuple
        kernel32.ReleaseMutex(h)
        kernel32.CloseHandle(h)
    except Exception as e:
        logger.debug("ignored exception: %s", e)


# ---------- Unix AF_UNIX socket ----------
def _try_unix_socket(sock_path: str):
    try:
        # ensure parent dir exists
        parent = os.path.dirname(sock_path)
        os.makedirs(parent, exist_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        # unlink stale socket if it exists and is not bound
        if os.path.exists(sock_path):
            try:
                os.unlink(sock_path)
            except Exception as e:
                logger.debug("ignored exception: %s", e)
        s.bind(sock_path)
        return ("unix_sock", s, sock_path)
    except Exception:
        try:
            s.close()
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        return None


def _release_unix_socket(handle_tuple):
    try:
        _, s, path = handle_tuple
        s.close()
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception as e:
            logger.debug("ignored exception: %s", e)
    except Exception as e:
        logger.debug("ignored exception: %s", e)


# ---------- TCP bind fallback ----------
def _try_tcp_port_from_hash(workroot: str):
    try:
        h = int(hashlib.sha256(workroot.encode("utf-8")).hexdigest(), 16)
        port = 20000 + (h % 30000)  # 20000..49999
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return ("tcp_port", s, port)
    except Exception:
        try:
            s.close()
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        return None


def _release_tcp(handle_tuple):
    try:
        _, s, _ = handle_tuple
        s.close()
    except Exception as e:
        logger.debug("ignored exception: %s", e)


# ---------- Platform lock orchestrator ----------
def acquire_platform_lock_for_workroot(workroot: str):
    """
    Try platform-native locks for the given workroot.
    Returns handle tuple on success, None on failure.
    """
    global _PLATFORM_HANDLE
    if not workroot:
        return None
    # prefer Windows mutex
    if os.name == "nt":
        name = f"gmos_{abs(hash(workroot))}"
        h = _try_windows_mutex(name)
        if h:
            _PLATFORM_HANDLE = ("win", h)
            return _PLATFORM_HANDLE
    # try AF_UNIX (Linux, macOS)
    sock_path = os.path.join(os.path.realpath(workroot), ".gmos.sock")
    h = _try_unix_socket(sock_path)
    if h:
        _PLATFORM_HANDLE = ("unix", h)
        return _PLATFORM_HANDLE
    # fall back to tcp bind on loopback
    h = _try_tcp_port_from_hash(workroot)
    if h:
        _PLATFORM_HANDLE = ("tcp", h)
        return _PLATFORM_HANDLE
    return None


def release_platform_lock():
    """Release whichever platform lock was acquired."""
    global _PLATFORM_HANDLE
    if not _PLATFORM_HANDLE:
        return
    kind, payload = _PLATFORM_HANDLE
    try:
        if kind == "win":
            _release_windows_mutex(payload)
        elif kind == "unix":
            _release_unix_socket(payload)
        elif kind == "tcp":
            _release_tcp(payload)
    except Exception as e:
        logger.debug("ignored exception: %s", e)
    _PLATFORM_HANDLE = None


# ---------------------------------------------------------------------------


# Utility function for PyInstaller/frozen builds
def resource_path(rel_path):
    """Return absolute path to resource, whether frozen or not."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # pyinstaller temp bundle root
    else:
        base = os.path.dirname(__file__)
    return os.path.join(base, rel_path)


def save_dryrun_artifact(sim_log, temp_work_root, original_root, out_dir=LOG_DIR):
    """Persist sim_log and runtime_manifest.json to logs/dryrun_TIMESTAMP/ and zip it."""
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dry_dir = os.path.join(out_dir, f"dryrun_{ts}")
    os.makedirs(dry_dir, exist_ok=True)

    try:
        with open(os.path.join(dry_dir, "sim_log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(sim_log))
    except Exception:
        logger.exception("Failed writing sim_log text")

    manifest_src = os.path.join(temp_work_root, "runtime_manifest.json")
    if os.path.exists(manifest_src):
        try:
            dest_manifest = os.path.join(dry_dir, "runtime_manifest.json")
            atomic_write_copy(manifest_src, dest_manifest)
        except Exception:
            logger.exception("Failed copying runtime_manifest.json")

    meta = {
        "timestamp_utc": ts,
        "original_root": original_root,
        "temp_work_root": temp_work_root,
    }
    try:
        with open(os.path.join(dry_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
    except Exception:
        logger.exception("Failed writing dryrun meta.json")

    bundle_path = None
    try:
        bundle_path = os.path.join(out_dir, f"dryrun_bundle_{ts}.zip")
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(dry_dir):
                for fn in files:
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, dry_dir)
                    zf.write(full, arc)
        logger.info("Dry-run artifact written: %s", bundle_path)
    except Exception:
        logger.exception("Failed creating dryrun bundle")

    return bundle_path


def headless_dryrun(original_root, work_root, instructions):
    """Run run_patcher in a temp workspace and persist dry-run artifact.
    instructions should be a Python list (as run_patcher expects).
    Returns path to created bundle or None.
    """
    if not os.path.isdir(original_root):
        raise FileNotFoundError(f"Original root not found: {original_root}")

    # Create a temporary simulate work dir and run patcher
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_work_root = os.path.join(temp_dir, "sim_work")
        Path(temp_work_root).mkdir(parents=True, exist_ok=True)
        sim_log = run_patcher(original_root, temp_work_root, instructions)

        # Save dryrun artifact to LOG_DIR
        bundle = save_dryrun_artifact(
            sim_log, temp_work_root, original_root, out_dir=LOG_DIR
        )
        return bundle


def _load_instructions_from_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _cli_main(argv=None):
    p = argparse.ArgumentParser(description="GMOS headless dry-run")
    p.add_argument("--original", "-o", required=True, help="Original game directory")
    p.add_argument(
        "--workroot",
        "-w",
        required=True,
        help="Work root directory (unused, informational)",
    )
    p.add_argument(
        "--instructions", "-i", required=False, help="JSON file with instructions list"
    )
    p.add_argument(
        "--out",
        "-O",
        required=False,
        help="Optional output path for created support bundle (.zip)",
    )
    args = p.parse_args(argv)

    instr = []
    if args.instructions:
        try:
            instr = _load_instructions_from_json(args.instructions)
        except Exception as e:
            logger.exception("Failed loading instructions file: %s", e)
            print(f"Error: failed to load instructions: {e}", file=sys.stderr)
            return 2

    try:
        bundle = headless_dryrun(args.original, args.workroot, instr)
        if args.out and bundle:
            # copy to requested path
            try:
                atomic_write_copy(bundle, args.out)
                print(args.out)
            except Exception as e:
                logger.exception("Failed copying bundle to out path: %s", e)
                print(
                    f"Error: failed copying bundle to {args.out}: {e}", file=sys.stderr
                )
                return 3
        else:
            print(bundle or "")
        return 0
    except Exception as e:
        logger.exception("Headless dry-run failed: %s", e)
        print(f"Error: {e}", file=sys.stderr)
        return 1


# Set a higher recursion limit for complex parsing/diffing
sys.setrecursionlimit(20000)

# ---------------------- Configuration & Defaults ----------------------
DEFAULTS = {
    "original_game_dir": "./game_files",  # The clean, unmodded files
    "work_root_dir": "./game_runtime",  # The patched/cached files used for launching
    "mods_dir": "./mods",
    "game_executable": ".exe",  # The game's executable name
    "launch_override": "",
    "mos_module": "mos_patcher.py",  # Name of this file, used for tracking
}


def safe_norm(p: str) -> str:
    """Normalize path (expand user, normalize separators)."""
    return os.path.normpath(os.path.expanduser(p)) if p else p


# ---------------------- Core GDScript Analysis Utilities ----------------------


def _leading_whitespace(line: str) -> str:
    """Returns the leading whitespace of a line."""
    return line[: len(line) - len(line.lstrip("\t "))]


def _is_line_comment(line: str) -> bool:
    """Checks if a line is entirely a comment (after leading whitespace)."""
    return line.lstrip().startswith("#")


def get_var_block(lines: List[str], var_name: str) -> Optional[Tuple[int, int]]:
    """
    Return (start, end) indices of a var or const block named var_name in lines.
    """
    # Updated regex to match 'var' or 'const'
    pat = re.compile(rf"^\s*(var|const)\s+{re.escape(var_name)}\s*=\s*")
    start = -1
    for i, ln in enumerate(lines):
        if pat.match(ln):
            start = i
            break
    if start == -1:
        return None

    # Balanced counts for common grouping tokens.
    balance = {"{": 0, "[": 0, "(": 0}
    opening = {"{": "}", "[": "]", "(": ")"}
    closing = {v: k for k, v in opening.items()}

    # Track string state. Can be one-char quote (' or ") or triple quotes (''' or """)
    in_string = None
    escaped = False

    # Indentation of the var declaration line (helps detect end by dedent)
    var_indent = len(_leading_whitespace(lines[start]))

    # Whether the RHS has started (value may start on following lines)
    rhs_started = "=" in lines[start]
    i = start
    while i < len(lines):
        line = lines[i]

        # If assignment doesn't start on declaration line, wait for a non-empty, non-comment line.
        if not rhs_started and not line.strip().startswith("#") and line.strip():
            rhs_started = True

        if not rhs_started:
            i += 1
            continue

        # iterate characters with index to detect triple-quote sequences
        j = 0
        L = len(line)
        while j < L:
            ch = line[j]

            # If inside a string literal
            if in_string:
                # triple-quoted string termination
                if len(in_string) == 3:
                    if line[j : j + 3] == in_string:
                        in_string = None
                        j += 3
                        continue
                    else:
                        j += 1
                        continue
                else:
                    # single-quoted string: handle escapes
                    if escaped:
                        escaped = False
                        j += 1
                        continue
                    if ch == "\\":
                        escaped = True
                        j += 1
                        continue
                    if ch == in_string:
                        in_string = None
                        j += 1
                        continue
                    j += 1
                    continue

            # Not in a string: detect string starts (triple or single)
            if line[j : j + 3] in ('"""', "'''"):
                in_string = line[j : j + 3]
                j += 3
                continue
            if ch in ('"', "'"):
                in_string = ch
                j += 1
                continue

            # Track bracket/brace/paren balance
            if ch in balance:
                balance[ch] += 1
            elif ch in closing:
                balance[closing[ch]] -= 1
            j += 1

        # After scanning the line, if not in a string and balances are zero, candidate end
        if not in_string and all(v == 0 for v in balance.values()):
            # Look ahead for next significant line (skip blanks/comments)
            next_line = ""
            k = i + 1
            while k < len(lines):
                if lines[k].strip() == "" or lines[k].lstrip().startswith("#"):
                    k += 1
                    continue
                next_line = lines[k]
                break

            # If no next line, treat this as end.
            if not next_line:
                return start, i

            next_indent = len(_leading_whitespace(next_line))
            # If next significant line dedents to var level or less, end block here.
            if next_indent <= var_indent:
                # avoid ending if current line ends with comma (likely still in list/dict entries)
                if not line.rstrip().endswith(","):
                    return start, i

        i += 1

    # Exhausted file; return last scanned line as conservative fallback.
    return start, i - 1


def get_function_block(lines: List[str], func_name: str) -> Optional[Tuple[int, int]]:
    """
    Return (start, end) indices of a function body block named func_name in lines.
    Excludes the 'func ...' line and the final 'return' or 'pass' lines if they are not indented.
    """
    # allow optional return type between ')' and ':' (e.g. "-> void")
    pat = re.compile(
        rf"^\s*func\s+{re.escape(func_name)}\s*\(.*?\)\s*(?:->\s*[^:]+)?\s*:"
    )
    start = -1
    for i, ln in enumerate(lines):
        if pat.match(ln):
            start = i
            break
    if start == -1:
        return None

    # Function body starts one line after the signature
    body_start = start + 1
    # If the signature contains an inline body after the colon (e.g. `func foo(): return 1`)
    # treat the signature line itself as the function body.
    header_line = lines[start]
    if ":" in header_line:
        after = header_line.split(":", 1)[1]
        if after.strip() != "":
            # return the signature line as the (single-line) body
            return start, start

    # Find end of function block by checking indentation
    # Function body ends when indentation reverts to the level of the 'func' keyword
    func_indent = len(_leading_whitespace(lines[start]))
    end = len(lines) - 1

    for i in range(body_start, len(lines)):
        line = lines[i]
        stripped = line.lstrip()

        # Skip blank lines and full-line comments
        if not stripped or stripped.startswith("#"):
            continue

        # If indentation is less than or equal to the function signature's indentation,
        # and it's not a line immediately after the signature (where the first body line might be),
        # then the function has ended.
        line_indent = len(_leading_whitespace(line))
        if line_indent <= func_indent:
            end = i - 1
            break

        end = i

    # Clean up the end index: if the last line of the block is a comment or blank, back up
    while end >= body_start and (
        _is_line_comment(lines[end]) or not lines[end].strip()
    ):
        end -= 1

    # Return line numbers of function body (excluding signature line)
    return body_start, end


# ---------------------- Mod Configuration Parsing (INI-Style) ----------------------


def parse_mod_config(mod_path: str) -> Optional[Dict[str, Any]]:
    """Parses a mod configuration file (INI-style) into a structured dictionary."""
    config_file = next(
        (f for f in ["mod.mos"] if os.path.exists(os.path.join(mod_path, f))),
        None,
    )
    if not config_file:
        return None

    config = {"Name": Path(mod_path).name, "Sections": {}}
    current_section = None

    try:
        with open(os.path.join(mod_path, config_file), "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            section_match = re.match(r"^\[(.+)\]$", line)
            if section_match:
                current_section = section_match.group(1).strip()
                config["Sections"][current_section] = []
                if current_section == "General":
                    config["Sections"]["General"] = {}
                continue

            if current_section == "General":
                if "=" in line:
                    key, value = [p.strip() for p in line.split("=", 1)]
                    if key == "Name":
                        config["Name"] = value.strip('"')  # Special handling for Name
                    else:
                        config["Sections"]["General"][key] = value.strip('"')
            elif current_section:
                if "=" in line:
                    config["Sections"][current_section].append(line)

        return config

    except Exception as e:
        print(f"Error parsing mod config {os.path.join(mod_path, config_file)}: {e}")
        return None


def generate_patch_plan(
    mod_path: str, mod_config: Dict[str, Any]
) -> List[Tuple[str, str, Tuple]]:
    """
    Produces a normalized list of (mod_name, operation, details) tuples.
    details:
      - FileReplace -> (target_res, source_path)
      - VariablePatch -> (target_res, target_var, source_path, source_var, mode)  # mode in ('replace','add','create')
      - FunctionPatch -> (target_res, target_func, source_path, source_func)
    DataPatch and DataAdd are emitted as VariablePatch with mode='create'.
    """
    plan: List[Tuple[str, str, Tuple]] = []
    mod_name = mod_config.get("Name", Path(mod_path).name)
    sections = mod_config.get("Sections", {}) or {}

    # FileReplace
    for line in sections.get("FileReplace", []):
        target, source = [p.strip() for p in line.split("=", 1)]
        plan.append((mod_name, "FileReplace", (target, os.path.join(mod_path, source))))

    # VariablePatch (explicit mode required via '; mode=...') -> normalized to 5-tuple
    for line in sections.get("VariablePatch", []):
        target, source_spec = [p.strip() for p in line.split("=", 1)]
        t_res, t_var = [p.strip() for p in target.split("::", 1)]
        s_res, s_var, meta = _parse_source_with_meta(source_spec)
        s_path = os.path.join(mod_path, s_res)
        mode = meta.get("mode")
        if mode not in ("add", "replace", "create"):
            raise ValueError(
                f"VariablePatch in mod '{mod_name}' must include '; mode=add|replace|create' in line: {line}"
            )
        # Disallow rename attempts on replace
        if mode == "replace" and s_var and s_var != t_var:
            raise ValueError(
                f"VariablePatch replace in mod '{mod_name}' attempts rename '{t_var}' -> '{s_var}'. Keep original name."
            )
        plan.append((mod_name, "VariablePatch", (t_res, t_var, s_path, s_var, mode)))

    # FunctionPatch: allow optional target function and inline metadata like '; mode=create'
    for line in sections.get("FunctionPatch", []):
        # split LHS = RHS
        target, source_spec = [p.strip() for p in line.split("=", 1)]
        # target may be "res://path/file.gd::func" or just "res://path/file.gd"
        if "::" in target:
            t_res, t_func = [p.strip() for p in target.split("::", 1)]
            if not t_func:
                t_func = None
        else:
            t_res = target
            t_func = None
        # parse source (handles metadata like ; mode=create)
        s_res, s_func, meta = _parse_source_with_meta(source_spec)
        s_path = os.path.join(mod_path, s_res)
        mode = meta.get("mode")  # may be None or 'create'
        # store normalized 5-tuple for FunctionPatch: (t_res, t_func_or_None, s_path, s_func, mode)
        plan.append((mod_name, "FunctionPatch", (t_res, t_func, s_path, s_func, mode)))

    # DataAdd / DataPatch: treat as request to create a new top-level variable/table
    for line in sections.get("DataPatch", []) + sections.get("DataAdd", []):
        target, source = [p.strip() for p in line.split("=", 1)]
        t_res, t_var = [p.strip() for p in target.split("::", 1)]
        s_res, s_var = [p.strip() for p in source.split("::", 1)]
        s_path = os.path.join(mod_path, s_res)
        plan.append(
            (mod_name, "VariablePatch", (t_res, t_var, s_path, s_var, "create"))
        )

    return plan


def _validate_mod_config_dict(mod_config: Dict[str, Any]) -> List[str]:
    """
    Validate the provided mod_config dict.
    Returns a list of error strings (empty if no errors).
    This is a helper used by validate_mod_config which dispatches based on
    whether the caller passed a .mos path (string) or a parsed dict.
    """
    errors: List[str] = []
    try:
        mod_path = mod_config.get("Path", "")
        raw_sections = mod_config.get("Sections", {}) or {}
        sections = {k.lower(): v for k, v in raw_sections.items()}

        # Enforce allowed section names. Any unexpected section is considered invalid.
        allowed = {
            "filereplace",
            "variablepatch",
            "functionpatch",
            "datapatch",
            "dataadd",
            "dependencies",
            "metadata",
        }
        for sec in raw_sections.keys():
            if sec.lower() not in allowed:
                errors.append(f"disallowed section: [{sec}]")

        def validate_resource_target(res_target: str) -> Optional[str]:
            try:
                _res_to_path(res_target)
                return None
            except Exception as e:
                return f"Invalid resource target '{res_target}': {e}"

        # FileReplace
        for line in sections.get("filereplace", []):
            try:
                target, source = [p.strip() for p in line.split("=", 1)]
            except Exception:
                errors.append(f"Malformed FileReplace line: {line}")
                continue
            err = validate_resource_target(target)
            if err:
                errors.append(err)
            if os.path.isabs(source):
                errors.append(
                    f"FileReplace source must be relative inside mod: {source}"
                )
            else:
                abs_src = os.path.join(mod_path, source)
                try:
                    ensure_within(mod_path, abs_src)
                except Exception as e:
                    errors.append(
                        f"FileReplace source path outside mod: {source} ({e})"
                    )
                    continue
                if not os.path.exists(abs_src):
                    errors.append(f"FileReplace source not found: {abs_src}")

        # VariablePatch
        for line in sections.get("variablepatch", []):
            try:
                target, source_spec = [p.strip() for p in line.split("=", 1)]
            except Exception:
                errors.append(f"Malformed VariablePatch line: {line}")
                continue
            try:
                s_res, s_var, meta = _parse_source_with_meta(source_spec)
            except Exception:
                errors.append(f"Malformed VariablePatch source spec: {source_spec}")
                continue
            if "mode" not in meta:
                errors.append(
                    f"VariablePatch missing '; mode=add|replace|create' in line: {line}"
                )
            s_path = os.path.join(mod_path, s_res)
            try:
                ensure_within(mod_path, s_path)
            except Exception as e:
                errors.append(f"VariablePatch source outside mod: {s_res} ({e})")
                continue
            if not os.path.exists(s_path):
                errors.append(f"VariablePatch source not found: {s_path}")
            try:
                t_res, t_var = [p.strip() for p in target.split("::", 1)]
            except Exception:
                errors.append(f"Malformed VariablePatch target: {target}")
                continue
            if meta.get("mode") == "replace" and s_var and s_var != t_var:
                errors.append(
                    f"VariablePatch replace attempts rename '{t_var}' -> '{s_var}' in line: {line}"
                )

        # FunctionPatch
        for line in sections.get("functionpatch", []):
            try:
                _, source = [p.strip() for p in line.split("=", 1)]
                s_res, _ = [p.strip() for p in source.split("::", 1)]
            except Exception:
                errors.append(f"Malformed FunctionPatch line: {line}")
                continue
            s_path = os.path.join(mod_path, s_res)
            try:
                ensure_within(mod_path, s_path)
            except Exception as e:
                errors.append(f"FunctionPatch source outside mod: {s_res} ({e})")
                continue
            if not os.path.exists(s_path):
                errors.append(f"FunctionPatch source not found: {s_path}")

        # DataAdd / DataPatch
        for line in sections.get("datapatch", []) + sections.get("dataadd", []):
            try:
                _, source = [p.strip() for p in line.split("=", 1)]
                s_res, _ = [p.strip() for p in source.split("::", 1)]
            except Exception:
                errors.append(f"Malformed Data line: {line}")
                continue
            s_path = os.path.join(mod_path, s_res)
            try:
                ensure_within(mod_path, s_path)
            except Exception as e:
                errors.append(f"Data source outside mod: {s_res} ({e})")
                continue
            if not os.path.exists(s_path):
                errors.append(f"Data source not found: {s_path}")

        # Attempt to generate plan to catch other semantic errors
        try:
            _ = generate_patch_plan(mod_config["Path"], mod_config)
        except Exception as e:
            errors.append(f"generate_patch_plan error: {e}")

    except Exception as e:
        return [str(e)]

    return errors


def validate_mod_config(
    mod_config: Union[str, Dict[str, Any]],
) -> Tuple[bool, Optional[object]]:
    """
    Validate a mod config or a path to a .mos manifest.

    - If passed a string path to a .mos file, returns (ok: bool, errors: List[str]).
    - If passed a parsed mod_config dict, preserves the legacy return shape:
        (True, None) or (False, "error message").
    """
    # If caller provided a path string, parse it into a mod_config-like dict
    if isinstance(mod_config, str):
        mos_path = mod_config
        if not mos_path or not os.path.exists(mos_path):
            return False, [f"manifest missing: {mos_path}"]
        cp = configparser.ConfigParser(delimiters=("=",))
        cp.optionxform = str
        try:
            cp.read(mos_path, encoding="utf-8")
        except Exception as e:
            return False, [f"failed to parse manifest: {e}"]
        sections = {}
        for sec in cp.sections():
            # create lines in the form "key = value" mirroring prior tests/layout
            lines = [f"{k} = {v}" for k, v in cp.items(sec)]
            sections[sec] = lines
        cfg = {"Path": os.path.dirname(os.path.abspath(mos_path)), "Sections": sections}
        errs = _validate_mod_config_dict(cfg)
        return (len(errs) == 0, errs)

    # Otherwise assume dict and preserve legacy return type
    if not isinstance(mod_config, dict):
        return False, "invalid argument to validate_mod_config"

    errs = _validate_mod_config_dict(mod_config)
    if errs:
        # return first error for backward compatibility
        return False, errs[0]
    return True, None


def _mod_mode_summary(mod_path: str, mod_config: dict) -> str:
    """Return a short mode summary string for display like '(V:add,F:replace)'."""
    try:
        plan = generate_patch_plan(mod_path, mod_config)
    except Exception:
        return ""
    parts = []
    for _, op, details in plan:
        if op == "VariablePatch":
            try:
                mode = details[4]
            except Exception:
                mode = "replace"
            parts.append(f"V:{mode}")
        elif op == "FunctionPatch":
            try:
                sfunc = details[3]
                if sfunc.startswith("prefix_"):
                    parts.append("F:pref")
                elif sfunc.startswith("postfix_"):
                    parts.append("F:post")
                else:
                    parts.append("F:rep")
            except Exception:
                parts.append("F:?")
        elif op == "FileReplace":
            parts.append("S:rep")
        else:
            parts.append(op[:3])
    seen = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return "(" + ",".join(seen) + ")" if seen else ""


def _parse_source_with_meta(spec: str):
    parts = [p.strip() for p in spec.split(";") if p.strip()]
    main = parts[0] if parts else ""
    meta = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            meta[k.strip()] = v.strip()
    if "::" in main:
        res, name = [x.strip() for x in main.split("::", 1)]
    else:
        res, name = main.strip(), ""
    return res, name, meta


# ---------------------- Core Patcher Functions (with Wrapping Logic) ----------------------


def _res_to_path(res_path: str) -> str:
    """Convert a Godot `res://` resource path to a safe filesystem-relative path.

    Security rules:
    - Accepts strings beginning with 'res://' or plain relative paths.
    - Rejects any path that attempts to traverse above the resource root (leading '..').
    - Collapses '.' and '..' segments safely. Raises RuntimeError on invalid traversal.

    Returns a platform-native relative path (no leading slash).
    """
    if not res_path:
        return ""

    # strip prefix if present
    if res_path.startswith("res://"):
        rel = res_path[len("res://") :]
    else:
        rel = res_path

    # unify separators and drop leading slashes
    rel = rel.replace("\\", "/").lstrip("/")

    # collapse segments while rejecting escapes that would pop past root
    parts = []
    for segment in rel.split("/"):
        if segment == "" or segment == ".":
            continue
        if segment == "..":
            if not parts:
                # trying to escape above the res:// root
                raise RuntimeError(f"Invalid resource path traversal: {res_path}")
            parts.pop()
            continue
        parts.append(segment)

    # return platform-native relative path
    return os.path.join(*parts) if parts else ""


def _realpath(path: str) -> str:
    """Compatibility shim for older code.

    Returns a normalized absolute path resolving symlinks. Implemented with
    pathlib.Path.resolve(strict=False). This function is deprecated and kept
    only for backward compatibility. New code should use Path(...).resolve().
    """
    if path is None:
        return ""
    # Use pathlib to canonicalize while allowing non-existent targets.
    try:
        return str(Path(path).resolve(strict=False))
    except Exception:
        # fallback to os.path behavior in case of unexpected errors
        return os.path.realpath(os.path.abspath(path))


def ensure_within(base: str, target: str):
    """
    Ensure `target` is within `base` after resolving symlinks.
    Raises RuntimeError on violation.

    Uses Path.resolve(strict=False) so non-existent targets are still resolved
    in a canonical manner where possible. Treats the base itself as allowed.
    """
    if not base:
        raise RuntimeError("ensure_within: base path empty")

    # use pathlib resolve to canonicalize symlinks
    base_p = Path(base).resolve(strict=False)
    target_p = Path(target).resolve(strict=False)

    # exact match is allowed
    if target_p == base_p:
        return True

    base_str = str(base_p)
    target_str = str(target_p)

    # ensure trailing separator on base for prefix check
    if not base_str.endswith(os.sep):
        base_str = base_str + os.sep

    if not target_str.startswith(base_str):
        raise RuntimeError(f"Path escape detected. base={base_p} target={target_p}")

    return True


def atomic_write_bytes(dst_path: str, bdata: bytes, *, mode: int = 0o644):
    """Write bytes to dst_path atomically in same directory then os.replace."""
    ddir = os.path.dirname(dst_path) or "."
    os.makedirs(ddir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".atomic-", dir=ddir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bdata)
        os.chmod(tmp, mode)
        os.replace(tmp, dst_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception as e:
                logger.debug("ignored exception: %s", e)


def atomic_write_copy(src_path: str, dst_path: str):
    """
    Atomically copy src_path -> dst_path by copying to a temp file in the destination
    directory then os.replace. Preserves mode where possible.
    """
    ensure_within(os.path.dirname(dst_path) or ".", dst_path)
    ddir = os.path.dirname(dst_path) or "."
    os.makedirs(ddir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".atomic-", dir=ddir)
    os.close(fd)
    try:
        # use shutil.copyfileobj to avoid metadata until we replace
        with open(src_path, "rb") as fr, open(tmp, "wb") as fw:
            shutil.copyfileobj(fr, fw)
        try:
            st = os.stat(src_path)
            os.chmod(tmp, stat.S_IMODE(st.st_mode))
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        os.replace(tmp, dst_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception as e:
                logger.debug("ignored exception: %s", e)


def read_source_for_patching(work_path):
    """
    Reads the current content of the file at work_path to ensure that
    sequential patches are applied cumulatively. The .bak file is ignored
    during the patching process and is only used for rollbacks.
    """
    p = Path(work_path)
    if not p.exists():
        # This case should be handled by lazy_copy_file before this function is called.
        raise FileNotFoundError(
            f"Target file does not exist in working directory: {work_path}"
        )
    return p.read_text(encoding="utf-8")


def atomic_write_with_backup(target_path, new_text):
    """
    Write new_text to target_path atomically. Create a single backup file of the original
    named target + '.bak' only if it does not already exist.
    """
    p = Path(target_path)
    bak = p.with_name(p.name + ".bak")

    # Ensure parent exists
    p.parent.mkdir(parents=True, exist_ok=True)

    # Create single backup if original exists and no .bak exists yet
    if p.exists() and not bak.exists():
        # copy2 preserves metadata; copy atomically by writing to temp then replace bak
        tmp_bak_fd, tmp_bak = tempfile.mkstemp(dir=str(p.parent))
        os.close(tmp_bak_fd)
        try:
            atomic_write_copy(str(p), tmp_bak)
            os.replace(tmp_bak, str(bak))
        finally:
            if os.path.exists(tmp_bak):
                try:
                    os.remove(tmp_bak)
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
    # Now write new content atomically to target
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp_path, str(p))
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                logger.debug("ignored exception: %s", e)


def atomic_replace(target_path, text):

    p = Path(target_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, str(p))


def atomic_copy_with_single_bak(src: str, dst: str):
    src_p = Path(src).resolve()
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    bak = dst_p.with_name(dst_p.name + ".bak")
    # create .bak if dst exists and bak missing
    if dst_p.exists() and not bak.exists():
        tmp_bak_fd, tmp_bak = tempfile.mkstemp(dir=str(dst_p.parent))
        os.close(tmp_bak_fd)
        try:
            atomic_write_copy(str(dst_p), tmp_bak)
            os.replace(tmp_bak, str(bak))
        finally:
            if os.path.exists(tmp_bak):
                os.remove(tmp_bak)
    # copy to temp then replace
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(dst_p.parent))
    os.close(tmp_fd)
    try:
        atomic_write_copy(str(src_p), tmp_path)
        os.replace(tmp_path, str(dst_p))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def lazy_copy_file(
    original_root: str, work_root: str, res_path: str
) -> Tuple[str, str]:
    """Copies a file from original_root to work_root if it doesn't exist in work_root, and returns the work path."""
    relative_path = _res_to_path(res_path)
    original_path = os.path.join(original_root, relative_path)
    work_path = os.path.join(work_root, relative_path)

    # Security check: ensure target path is within the working directory root.
    ensure_within(work_root, work_path)
    ensure_within(original_root, original_path)

    if not os.path.exists(original_path):
        raise FileNotFoundError(f"Original file not found: {original_path}")

    if not os.path.exists(work_path):
        # Use atomic copy. This helper doesn't create a .bak because the target doesn't exist yet,
        # which is correct for a lazy-copy.
        # use safe wrapper to surface permission errors early and show friendly messages
        safe_atomic_copy_with_bak(original_path, work_path)
        return work_path, f"Copied {relative_path} to working directory."

    return work_path, f"Used existing {relative_path} in working directory."


def patch_variable(
    original_root: str,
    work_root: str,
    target_res: str,
    target_var: str,
    source_path: str,
    source_var: str,
    mode: str = "replace",
) -> List[str]:
    log = []
    try:
        work_path, copy_log = lazy_copy_file(original_root, work_root, target_res)
        log.append(copy_log)
        ensure_within(work_root, work_path)
        try:
            target_text = read_source_for_patching(work_path)
            target_lines = target_text.splitlines(keepends=True)
        except Exception as e:
            log.append(f"ERROR: Failed to read target file '{work_path}': {e}")
            return log
        with open(source_path, "r", encoding="utf-8") as f:
            source_lines = f.readlines()

        src_range = get_var_block(source_lines, source_var)
        tgt_range = get_var_block(target_lines, target_var)

        if not src_range:
            log.append(
                f"ERROR: Source var '{source_var}' not found in '{source_path}'."
            )
            return log

        src_block = source_lines[src_range[0] : src_range[1] + 1]

        if mode == "replace":
            if not tgt_range:
                log.append(
                    f"ERROR: Target var '{target_var}' not found for replace in {target_res}. Skipping."
                )
                return log
            # Ensure source signature uses target name to avoid renames.
            if src_block and source_var != target_var:
                # Correctly substitute the source variable name with the target one.
                # Handles both 'var' and 'const'
                pat = re.compile(
                    rf"(^\s*(var|const)\s+){re.escape(source_var)}(\s*[:=])"
                )
                src_block[0] = pat.sub(rf"\1{target_var}\3", src_block[0], count=1)

            new_lines = (
                target_lines[: tgt_range[0]]
                + src_block
                + target_lines[tgt_range[1] + 1 :]
            )
            ensure_within(work_root, work_path)
            atomic_write_with_backup(work_path, "".join(new_lines))
            log.append(f"SUCCESS: Replaced var '{target_var}' in {target_res}.")
            return log

        if mode == "add":
            if not tgt_range:
                log.append(
                    f"ERROR: Target var '{target_var}' not present; 'add' requires existing var. Skipping."
                )
                return log
            inner = src_block[1:-1] if len(src_block) > 2 else []
            if not inner:
                log.append(f"NOTICE: No inner lines to append from '{source_var}'.")
                return log
            insert_at = tgt_range[1]
            new_lines = target_lines[:insert_at] + inner + target_lines[insert_at:]
            ensure_within(work_root, work_path)
            atomic_write_with_backup(work_path, "".join(new_lines))
            log.append(
                f"SUCCESS: Appended {len(inner)} lines into '{target_var}' in {target_res}."
            )
            return log

        if mode == "create":
            if tgt_range:
                log.append(
                    f"ERROR: Target var '{target_var}' already exists; DataAdd/create will not overwrite. Skipping."
                )
                return log

            # Rename the variable in the source block if names differ.
            if source_var != target_var:
                pat = re.compile(
                    rf"(^\s*(var|const)\s+){re.escape(source_var)}(\s*[:=])"
                )
                src_block[0] = pat.sub(rf"\1{target_var}\3", src_block[0], count=1)

            new_lines = target_lines + ["\n"] + src_block
            ensure_within(work_root, work_path)
            atomic_write_with_backup(work_path, "".join(new_lines))
            log.append(f"SUCCESS: Created new var '{target_var}' in {target_res}.")
            return log

        log.append(f"ERROR: Unknown variable patch mode: {mode}")
        return log

    except FileNotFoundError as e:
        log.append(f"ERROR: File not found during Variable patch: {e}")
        return log
    except OSError as e:
        log.append(f"ERROR: I/O error during Variable patch: {e}")
        return log
    except Exception as e:
        log.append(f"FATAL ERROR during Variable patch ({mode}): {e}")
        return log


def patch_function(
    original_root: str,
    work_root: str,
    target_res: str,
    target_func: Optional[str],
    source_path: str,
    source_func: str,
    mode: Optional[str] = None,
) -> List[str]:
    """Patches a function in the target file with code from the source file, supporting prefix/postfix wrapping and creation."""
    log = []
    try:
        work_path, copy_log = lazy_copy_file(original_root, work_root, target_res)
        log.append(copy_log)
        ensure_within(work_root, work_path)
        try:
            target_text = read_source_for_patching(work_path)
            target_lines = target_text.splitlines(keepends=True)
        except Exception as e:
            log.append(f"ERROR: Failed to read target file '{work_path}': {e}")
            return log
        with open(source_path, "r", encoding="utf-8") as f:
            source_lines = f.readlines()

        # Determine effective patch mode
        effective_mode = mode
        if source_func.startswith("prefix_"):
            effective_mode = "prefix"
        elif source_func.startswith("postfix_"):
            effective_mode = "postfix"
        elif not effective_mode:
            effective_mode = "replace"

        if effective_mode == "create":
            # For 'create', the target function must NOT exist.
            if get_function_block(target_lines, target_func):
                log.append(
                    f"ERROR: Target function '{target_func}' already exists. 'create' mode will not overwrite. Skipping."
                )
                return log

            # Find the source function signature and body
            source_sig_idx = -1
            for i, ln in enumerate(source_lines):
                if re.match(rf"^\s*func\s+{re.escape(source_func)}\s*\(.*?\):", ln):
                    source_sig_idx = i
                    break

            if source_sig_idx == -1:
                log.append(
                    f"ERROR: Source function '{source_func}' not found in '{source_path}'. Skipping create."
                )
                return log

            source_body_range = get_function_block(source_lines, source_func)
            end_idx = source_body_range[1] if source_body_range else source_sig_idx

            # Get the entire function block from the source
            new_func_block = source_lines[source_sig_idx : end_idx + 1]

            # Rename the function in the signature line to match the target
            if source_func != target_func:
                pat = re.compile(rf"(^\s*func\s+){re.escape(source_func)}(\s*\(.*?\):)")
                new_func_block[0] = pat.sub(
                    rf"\1{target_func}\2", new_func_block[0], count=1
                )

            # Append the new function to the end of the file
            new_lines = target_lines + ["\n"] + new_func_block
            atomic_write_with_backup(work_path, "".join(new_lines))
            log.append(
                f"SUCCESS: Created new function '{target_func}' in '{target_res}'."
            )
            return log

        # --- Original logic for replace/prefix/postfix, with fixes ---
        target_sig_line_index = -1
        for i, ln in enumerate(target_lines):
            if re.match(rf"^\s*func\s+{re.escape(target_func)}\s*\(.*?\):", ln):
                target_sig_line_index = i
                break

        # This check is only an error for non-create modes
        if target_sig_line_index == -1:
            log.append(
                f"ERROR: Target function '{target_func}' not found in '{target_res}'. Skipping."
            )
            return log

        target_body_range = get_function_block(target_lines, target_func)
        source_body_range = get_function_block(source_lines, source_func)
        new_lines = []

        if effective_mode == "replace":
            source_sig_idx = -1
            for i, ln in enumerate(source_lines):
                if re.match(rf"^\s*func\s+{re.escape(source_func)}\s*\(.*?\):", ln):
                    source_sig_idx = i
                    break

            if source_sig_idx == -1:
                log.append(
                    f"ERROR: Source function '{source_func}' not found for replace. Skipping."
                )
                return log

            start_idx = target_sig_line_index
            end_idx = target_body_range[1] if target_body_range else start_idx

            source_end_idx = (
                source_body_range[1] if source_body_range else source_sig_idx
            )
            patch_content = source_lines[source_sig_idx : source_end_idx + 1]

            # Rename if necessary
            if source_func != target_func:
                pat = re.compile(rf"(^\s*func\s+){re.escape(source_func)}(\s*\(.*?\):)")
                patch_content[0] = pat.sub(
                    rf"\1{target_func}\2", patch_content[0], count=1
                )

            new_lines = (
                target_lines[:start_idx] + patch_content + target_lines[end_idx + 1 :]
            )

        else:  # prefix or postfix
            if not target_body_range:
                log.append(
                    f"ERROR: Original function body for '{target_func}' is empty. Cannot wrap."
                )
                return log
            if not source_body_range:
                log.append(
                    f"ERROR: Patch function body for '{source_func}' is empty. Cannot wrap."
                )
                return log

            original_body_lines = target_lines[
                target_body_range[0] : target_body_range[1] + 1
            ]
            patch_body_lines = source_lines[
                source_body_range[0] : source_body_range[1] + 1
            ]
            body_indent = (
                _leading_whitespace(target_lines[target_sig_line_index]) + "    "
            )
            wrapper_body = []
            wrapper_body.append(
                f"{body_indent}#--- START {effective_mode.upper()} PATCH: {Path(source_path).name}::{source_func} ---\n"
            )

            if effective_mode == "prefix":
                wrapper_body.extend(patch_body_lines)
                wrapper_body.append(f"{body_indent}#--- ORIGINAL FUNCTION BODY ---\n")
                wrapper_body.extend(original_body_lines)
            else:  # postfix
                wrapper_body.append(f"{body_indent}#--- ORIGINAL FUNCTION BODY ---\n")
                wrapper_body.extend(original_body_lines)
                wrapper_body.append(f"{body_indent}#--- POSTFIX PATCH CODE ---\n")
                wrapper_body.extend(patch_body_lines)

            wrapper_body.append(
                f"{body_indent}#--- END {effective_mode.upper()} PATCH ---\n"
            )

            start_idx = target_sig_line_index + 1
            end_idx = target_body_range[1]
            new_lines = (
                target_lines[:start_idx] + wrapper_body + target_lines[end_idx + 1 :]
            )

        # Ensure file is always written for replace/prefix/postfix ---
        if new_lines:
            ensure_within(work_root, work_path)
            atomic_write_with_backup(work_path, "".join(new_lines))
            log.append(
                f"SUCCESS: Function '{target_func}' patched with {effective_mode.upper()} in '{target_res}'."
            )

        return log

    except FileNotFoundError as e:
        log.append(f"ERROR: File not found during FunctionPatch: {e}")
        return log
    except OSError as e:
        log.append(f"ERROR: I/O error during FunctionPatch: {e}")
        return log
    except Exception as e:
        log.append(f"FATAL ERROR during FunctionPatch: {e}")
        return log


def patch_file_replace(
    original_root: str, work_root: str, target_res: str, source_path: str
) -> List[str]:
    """Replaces the target file entirely with the source file."""
    log = []

    try:
        relative_path = _res_to_path(target_res)
        work_path = os.path.join(work_root, relative_path)

        # Security check: ensure target path is within the working directory root.
        ensure_within(work_root, work_path)

        if not os.path.exists(source_path):
            log.append(f"ERROR: Source file not found: {source_path}. Skipping.")
            return log

        # If a working copy already exists and differs from the replacement,
        # offer hunk-level conflict resolution via a modal HunkViewer.
        try:
            if os.path.exists(work_path):
                # read both files as text for diffing (best-effort; binary will be handled by bytes compare below)
                try:
                    with open(work_path, "r", encoding="utf-8", errors="ignore") as f:
                        orig_text = f.read()
                    with open(source_path, "r", encoding="utf-8", errors="ignore") as f:
                        new_text = f.read()
                except Exception:
                    # fallback: compare as bytes; if equal then no conflict, else skip interactive resolve
                    try:
                        if (
                            open(work_path, "rb").read()
                            != open(source_path, "rb").read()
                        ):
                            log.append(
                                f"CONFLICT: binary files differ: {work_path}; skipping interactive resolution in headless mode."
                            )
                            return log
                        # identical bytes -> nothing to do
                        return log
                    except Exception:
                        log.append(
                            f"ERROR: failed comparing files for conflict resolution: {work_path}"
                        )
                        return log

                # if texts are identical, nothing to do
                if orig_text == new_text:
                    return log

                # We have a textual diff. If UI is available show HunkViewer,
                # otherwise in headless/CI mode automatically apply the replacement
                # (create .bak and overwrite) so non-interactive workflows succeed.
                merged_text = None
                try:
                    parent = None
                    if "tk" in globals() and getattr(tk, "_default_root", None):
                        parent = tk._default_root

                    if parent is None:
                        # headless: apply replacement automatically (with backup)
                        try:
                            safe_atomic_copy_with_bak(source_path, work_path)
                            log.append(
                                f"SUCCESS: Replaced {work_path} with {source_path} (headless auto-apply)"
                            )
                            return log
                        except Exception as e:
                            log.append(
                                f"ERROR: failed to auto-apply replacement in headless mode: {e}"
                            )
                            return log

                    # interactive path: show hunk viewer
                    hv = HunkViewer(parent, orig_text, new_text)
                    merged_text = hv.show_modal()
                except Exception as e:
                    log.append(f"ERROR: failed to open conflict resolution UI: {e}")
                    return log

                if merged_text is None:
                    # user cancelled resolution
                    log.append(
                        f"CONFLICT: user cancelled resolution for {work_path}; skipping."
                    )
                    return log

                # write merged text to temporary file and atomically replace
                tmp_merge = work_path + ".gmos_merged"
                try:
                    with open(tmp_merge, "w", encoding="utf-8") as tf:
                        tf.write(merged_text)
                    safe_atomic_copy_with_bak(tmp_merge, work_path)
                    try:
                        os.remove(tmp_merge)
                    except Exception:
                        pass
                    log.append(f"SUCCESS: Applied merged changes to {work_path}")
                    return log
                except Exception as e:
                    log.append(f"ERROR: failed to apply merged result: {e}")
                    try:
                        os.remove(tmp_merge)
                    except Exception:
                        pass
                    return log

        except Exception as e:
            # Non-fatal: record and skip this replacement
            log.append(f"ERROR: conflict resolution pre-check failed: {e}")
            return log

        # No conflict or resolved above: perform atomic replacement
        try:
            safe_atomic_copy_with_bak(source_path, work_path)
            log.append(f"SUCCESS: Copied {source_path} -> {work_path}")
        except Exception as e:
            # friendly error in the per-mod log and stop processing this replacement
            log.append(f"ERROR: failed to copy replacement file: {e}")

    except Exception as e:
        log.append(f"FATAL ERROR during FileReplace: {e}")
    return log


def run_patcher(
    original_root: str, work_root: str, patch_plan: List[Tuple[str, str, Tuple]]
) -> List[str]:
    """
    Execute the normalized patch_plan.
    This is an idempotent process. It first cleans the target files from the
    working directory to ensure a fresh patch application every time.
    """
    log: List[str] = []

    # 1. Identify all unique files that will be patched.
    files_to_patch = set()
    for _, _, details in patch_plan:
        try:
            # All patch types have the target resource path as the first element in details.
            target_res = details[0]
            files_to_patch.add(_res_to_path(target_res))
        except (IndexError, TypeError):
            continue  # Skip malformed instructions

    # 2. Delete the existing patched files and their backups to ensure a clean start.
    if files_to_patch:
        log.append("--- Preparing clean slate for patch process ---")
        for rel_path in files_to_patch:
            work_path = Path(work_root) / rel_path
            bak_path = work_path.with_suffix(work_path.suffix + ".bak")

            if work_path.exists():
                try:
                    work_path.unlink()
                    log.append(f"Removed existing patched file: {rel_path}")
                except OSError as e:
                    log.append(f"WARNING: Could not remove {rel_path}: {e}")
            if bak_path.exists():
                try:
                    bak_path.unlink()
                except OSError as e:
                    logger.debug("failed to remove backup file %s: %s", bak_path, e)

    applied_files = set()
    applied_ops = []

    # Split variable ops and others (preserve plan order for others)
    var_ops: List[Tuple[str, str, Tuple]] = []
    other_ops: List[Tuple[str, str, Tuple]] = []
    for instr in patch_plan:
        if instr[1] == "VariablePatch":
            var_ops.append(instr)
        else:
            other_ops.append(instr)

    # Execute non-variable operations first
    for mod_name, op, details in other_ops:
        log.append(f"--- Applying {op} from {mod_name} ---")
        try:
            if op == "FileReplace":
                target_res, source_path = details
                log.extend(
                    patch_file_replace(
                        original_root, work_root, target_res, source_path
                    )
                )
                applied_files.add(_res_to_path(target_res))
                applied_ops.append(
                    {
                        "mod": mod_name,
                        "op": op,
                        "target": target_res,
                        "source": source_path,
                    }
                )
            elif op == "FunctionPatch":
                try:
                    t_res, t_func, s_path, s_func, mode = details
                except ValueError:
                    t_res, t_func, s_path, s_func = details
                    mode = None  # Fallback for older format if needed

                try:
                    log.extend(
                        patch_function(
                            original_root,
                            work_root,
                            t_res,
                            t_func,
                            s_path,
                            s_func,
                            mode=mode,
                        )
                    )
                    applied_files.add(_res_to_path(t_res))
                    applied_ops.append(
                        {
                            "mod": mod_name,
                            "op": op,
                            "target": f"{t_res}::{t_func}",
                            "source": f"{s_path}::{s_func}",
                            "mode": mode,
                        }
                    )
                except Exception as e:
                    log.append(
                        f"FATAL ERROR while processing FunctionPatch for {mod_name}: {e}"
                    )
                    applied_ops.append(
                        {"mod": mod_name, "op": op, "status": "error", "notes": str(e)}
                    )
            else:
                log.append(
                    f"WARNING: Unknown non-variable operation '{op}' from {mod_name}. Skipped."
                )
                applied_ops.append({"mod": mod_name, "op": op, "status": "skipped"})
        except Exception as e:
            log.append(f"FATAL ERROR while processing {op} for {mod_name}: {e}")
            applied_ops.append(
                {"mod": mod_name, "op": op, "status": "error", "notes": str(e)}
            )

    # Group variable ops by (target_res, target_var)
    by_target: Dict[Tuple[str, str], List[Tuple[str, str, str, str]]] = defaultdict(
        list
    )
    for mod_name, op, details in var_ops:
        try:
            t_res, t_var, s_path, s_var, mode = details
        except ValueError:
            log.append(
                f"ERROR: Malformed VariablePatch details from {mod_name}: {details}"
            )
            continue
        by_target[(t_res, t_var)].append((mod_name, s_path, s_var, mode))

    # Apply per-target: replace -> create -> add
    for (t_res, t_var), ops in by_target.items():
        log.append(f"=== Variable target {t_res}::{t_var} ===")
        # REPLACE (keep order)
        for mod_name, s_path, s_var, mode in ops:
            if mode == "replace":
                log.append(f"--- Applying Variable REPLACE from {mod_name} ---")
                lines = patch_variable(
                    original_root,
                    work_root,
                    t_res,
                    t_var,
                    s_path,
                    s_var,
                    mode="replace",
                )
                log.extend(lines)
                applied_ops.append(
                    {
                        "mod": mod_name,
                        "op": "VariablePatch",
                        "mode": "replace",
                        "target": f"{t_res}::{t_var}",
                        "source": f"{s_path}::{s_var}",
                    }
                )
                applied_files.add(_res_to_path(t_res))
        # CREATE
        for mod_name, s_path, s_var, mode in ops:
            if mode in ("create", "dataadd"):
                log.append(f"--- Applying Variable CREATE from {mod_name} ---")
                lines = patch_variable(
                    original_root, work_root, t_res, t_var, s_path, s_var, mode="create"
                )
                log.extend(lines)
                applied_ops.append(
                    {
                        "mod": mod_name,
                        "op": "VariablePatch",
                        "mode": "create",
                        "target": f"{t_res}::{t_var}",
                        "source": f"{s_path}::{s_var}",
                    }
                )
                applied_files.add(_res_to_path(t_res))
        # ADD
        for mod_name, s_path, s_var, mode in ops:
            if mode == "add":
                log.append(f"--- Applying Variable ADD from {mod_name} ---")
                lines = patch_variable(
                    original_root, work_root, t_res, t_var, s_path, s_var, mode="add"
                )
                log.extend(lines)
                applied_ops.append(
                    {
                        "mod": mod_name,
                        "op": "VariablePatch",
                        "mode": "add",
                        "target": f"{t_res}::{t_var}",
                        "source": f"{s_path}::{s_var}",
                    }
                )
                applied_files.add(_res_to_path(t_res))

    # write human-readable patch.log (best-effort)
    try:
        log_path = os.path.join(work_root, "patch.log")
        ensure_within(work_root, log_path)  # Safety check
        log_content = time.strftime("%Y-%m-%d %H:%M:%S") + " - Patch run\n"
        log_content += "\n".join(log) + "\n"
        log_content += "--- end run ---\n"
        atomic_replace(
            log_path, log_content
        )  # Use replace instead of append for clean log
    except Exception as e:
        logger.debug("ignored exception: %s", e)
    # runtime manifest (structured)
    try:
        manifest_path = os.path.join(work_root, "runtime_manifest.json")
        ensure_within(work_root, manifest_path)  # Safety check
        manifest = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "original_root": original_root,
            "work_root": work_root,
            "applied_ops": applied_ops,
            "modified_files": sorted(list(applied_files)),
        }
        atomic_replace(manifest_path, json.dumps(manifest, indent=2))
        log.append(f"INFO: runtime_manifest written: {manifest_path}")
        for rel in sorted(list(applied_files)):
            log.append(f"MODIFIED: {rel}")
    except Exception as e:
        log.append(f"WARNING: Failed to write runtime_manifest.json: {e}")

    return log


# ---------------------- Conflict Detection ----------------------


def analyze_mods_for_conflicts(
    mod_configs: List[Dict[str, Any]],
) -> Dict[str, List[Tuple[str, str, Any]]]:
    """
    Build conflict map keyed by canonical target keys:
      - Variable::<res>::<var>
      - Function::<res>::<func>
      - FileReplace::<res>
    Rules (summary):
      - Variable: multiple 'replace' -> hard conflict
      - Variable: create + replace -> conflict
      - Variable: multiple 'add' -> flagged
      - Function: >1 full replace -> conflict
      - Function: replace + wrappers -> conflict
      - FileReplace: multi-touch -> conflict
    Returns dict {target_key: [(mod_name, operation, details), ...]} for targets that need user attention.
    """
    targets: Dict[str, List[Tuple[str, str, Any]]] = {}
    all_instructions: List[Tuple[str, str, Tuple]] = []

    for mod in mod_configs:
        try:
            all_instructions.extend(generate_patch_plan(mod["Path"], mod))
        except Exception as e:
            # skip malformed mod during conflict analysis; preserve trace for audits
            logger.debug(
                "Skipping malformed mod during conflict analysis: %s (%s)",
                mod.get("Name", mod.get("Path")),
                e,
            )
            continue

    for mod_name, op, details in all_instructions:
        key = None
        if op == "FileReplace":
            t_res = details[0]
            key = f"FileReplace::{t_res}"
        elif op == "VariablePatch":
            t_res, t_var = details[0], details[1]
            key = f"Variable::{t_res}::{t_var}"
        elif op == "FunctionPatch":
            t_res, t_func = details[0], details[1]
            key = f"Function::{t_res}::{t_func}"
        else:
            # conservative fallback
            try:
                t_res = details[0]
                key = f"Other::{t_res}"
            except Exception as e:
                logger.debug("Conflict-key generation failed for %s: %s", t_res, e)
                continue

        targets.setdefault(key, []).append((mod_name, op, details))

    conflicts: Dict[str, List[Tuple[str, str, Any]]] = {}
    for key, instrs in targets.items():
        if len(instrs) <= 1:
            continue

        if key.startswith("Variable::"):
            replace_count = sum(
                1
                for _, _, d in instrs
                if isinstance(d, tuple) and len(d) >= 5 and d[4] == "replace"
            )
            create_count = sum(
                1
                for _, _, d in instrs
                if isinstance(d, tuple)
                and len(d) >= 5
                and d[4] in ("create", "dataadd")
            )
            add_count = sum(
                1
                for _, _, d in instrs
                if isinstance(d, tuple) and len(d) >= 5 and d[4] == "add"
            )

            if replace_count > 1:
                conflicts[key] = instrs
                continue
            if create_count > 0 and replace_count > 0:
                conflicts[key] = instrs
                continue
            if add_count > 1 or (add_count >= 1 and replace_count >= 1):
                conflicts[key] = instrs
                continue

        elif key.startswith("Function::"):
            # count full replacements (source func not prefixed with prefix_/postfix_)
            repls = 0
            for _, _, d in instrs:
                try:
                    sfunc = d[3]
                    if not sfunc.startswith(("prefix_", "postfix_")):
                        repls += 1
                except Exception:
                    repls += 1
            if repls > 1:
                conflicts[key] = instrs
            elif repls == 1 and len(instrs) > 1:
                conflicts[key] = instrs

        else:
            # file replaces and other multi-touch edits are conflicts
            conflicts[key] = instrs

    return conflicts


# ---------------------- Conflict Resolution Dialog ----------------------


class ResolveDialog(simpledialog.Dialog):
    """
    Conflict resolution dialog.
    Shows each conflicting target with the list of mods touching it.
    Allows reordering the overall mod list (drag/drop or Move Up/Down)
    and quick actions: Open Mod Folder, Toggle Enable.
    The dialog returns the new mod order via resolve_callback(new_mod_configs).
    """

    def __init__(self, parent, conflicts, mod_configs, resolve_callback):
        self.conflicts = conflicts
        self.mod_configs = mod_configs
        self.resolve_callback = resolve_callback
        # keep a live name->config map for quick operations
        self.mod_map = {m["Name"]: m for m in mod_configs}
        self.resolved_order = [m["Name"] for m in mod_configs]
        super().__init__(parent, title="Resolve Mod Conflicts")

    def body(self, master):
        tk.Label(
            master,
            text="Conflicts detected. Later mods win. Review and reorder or disable mods.",
            font=("Inter", 10, "bold"),
        ).pack(pady=6)

        # Scrollable conflicts area
        conf_frame = ttk.Frame(master)
        conf_frame.pack(fill="both", expand=False, padx=6)

        canvas = tk.Canvas(conf_frame, height=180)
        vsb = ttk.Scrollbar(conf_frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # list each conflict with mods and modes
        for target_key, instructions in self.conflicts.items():
            # human friendly heading
            parts = target_key.split("::")
            heading = parts[0] + " on " + (parts[2] if len(parts) > 2 else parts[1])
            hdr = ttk.Label(inner, text=heading, font=("Inter", 9, "bold"))
            hdr.pack(anchor="w", pady=(8, 2))

            # mods involved - show mod name + op + mode (if any)
            mods_frame = ttk.Frame(inner)
            mods_frame.pack(fill="x", padx=6)
            lb = tk.Listbox(
                mods_frame, height=min(6, len(instructions)), exportselection=False
            )
            lb.pack(side="left", fill="x", expand=True)
            # attach a small frame with quick action buttons
            btnf = ttk.Frame(mods_frame)
            btnf.pack(side="right", fill="y", padx=4)
            ttk.Button(
                btnf,
                text="Open Folder",
                command=lambda lb_ref=lb: self._open_selected_mod_folder(lb_ref),
            ).pack(fill="x", pady=2)
            ttk.Button(
                btnf,
                text="Toggle Enable",
                command=lambda lb_ref=lb: self._toggle_selected_mod(lb_ref),
            ).pack(fill="x", pady=2)
            ttk.Button(
                btnf,
                text="Select in Main List",
                command=lambda lb_ref=lb: self._select_in_main_list(lb_ref),
            ).pack(fill="x", pady=2)

            # populate listbox with readable entries and store metadata via listbox index -> tuple
            for instr in instructions:
                mod_name = instr[0]
                op = instr[1]
                details = instr[2]
                mode = ""
                try:
                    # variable detail path: (t_res, t_var, s_path, s_var, mode)
                    if op == "VariablePatch" and len(details) >= 5:
                        mode = details[4]
                except Exception:
                    mode = ""
                display = f"{mod_name}  [{op}{(':' + mode) if mode else ''}]"
                lb.insert(tk.END, display)
            # store a reference on the listbox so handlers can find which conflict this was
            lb._target_key = target_key

        # Reorder area for entire mod list
        ttk.Label(
            master, text="Reorder mods (last wins):", font=("Inter", 10, "bold")
        ).pack(pady=(8, 4))
        self.list_frame = ttk.Frame(master)
        self.list_frame.pack(fill="both", padx=6)

        self.list_box = tk.Listbox(self.list_frame, height=10, exportselection=False)
        self.list_box.pack(side="left", fill="both", expand=True)
        for name in self.resolved_order:
            label = name
            # mark disabled/invalid
            mod = self.mod_map.get(name)
            if mod and not mod.get("Valid", True):
                label += " [INVALID]"
            if mod and not mod.get("Enabled", True):
                label += " [DISABLED]"
            self.list_box.insert(tk.END, label)

        reorder_buttons = ttk.Frame(self.list_frame)
        reorder_buttons.pack(side="right", fill="y", padx=6)
        ttk.Button(reorder_buttons, text="Move Up", command=self.move_up).pack(pady=6)
        ttk.Button(reorder_buttons, text="Move Down", command=self.move_down).pack(
            pady=6
        )
        ttk.Button(reorder_buttons, text="Reset Order", command=self.reset_order).pack(
            pady=6
        )

        # drag support
        self.list_box.bind("<Button-1>", self.on_list_click)
        self.list_box.bind("<B1-Motion>", self.on_drag_motion)
        self.drag_index = None

        return self.list_box

    # listbox drag helpers
    def on_list_click(self, event):
        self.drag_index = self.list_box.nearest(event.y)

    def on_drag_motion(self, event):
        if self.drag_index is None:
            return
        new_index = self.list_box.nearest(event.y)
        if new_index != self.drag_index:
            val = self.list_box.get(self.drag_index)
            self.list_box.delete(self.drag_index)
            self.list_box.insert(new_index, val)
            self.drag_index = new_index

    def move_up(self):
        sel = self.list_box.curselection()
        if not sel:
            return
        i = sel[0]
        if i == 0:
            return
        val = self.list_box.get(i)
        self.list_box.delete(i)
        self.list_box.insert(i - 1, val)
        self.list_box.selection_set(i - 1)

    def move_down(self):
        sel = self.list_box.curselection()
        if not sel:
            return
        i = sel[0]
        if i >= self.list_box.size() - 1:
            return
        val = self.list_box.get(i)
        self.list_box.delete(i)
        self.list_box.insert(i + 1, val)
        self.list_box.selection_set(i + 1)

    def reset_order(self):
        self.list_box.delete(0, tk.END)
        for name in [m["Name"] for m in self.mod_configs]:
            label = name
            mod = self.mod_map.get(name)
            if mod and not mod.get("Valid", True):
                label += " [INVALID]"
            if mod and not mod.get("Enabled", True):
                label += " [DISABLED]"
            self.list_box.insert(tk.END, label)

    # quick-action helpers that call back into parent App
    def _open_selected_mod_folder(self, listbox):
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        display = listbox.get(idx)
        mod_name = display.split()[0]
        mod = self.mod_map.get(mod_name)
        if mod and "Path" in mod:
            try:
                webbrowser.open(mod["Path"])
            except Exception as e:
                logger.debug("ignored exception: %s", e)

    def _toggle_selected_mod(self, listbox):
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        display = listbox.get(idx)
        mod_name = display.split()[0]
        mod = self.mod_map.get(mod_name)
        if not mod:
            return
        # toggle enabled state
        mod["Enabled"] = not mod.get("Enabled", True)

        # Update entry text in this conflict listbox
        try:
            # rebuild readable label for this conflict list entry (keep op/mode info minimal)
            label = f"{mod_name}  [{'DISABLED' if not mod['Enabled'] else 'ENABLED'}]"
            listbox.delete(idx)
            listbox.insert(idx, label)
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        # Update main app state in-place without closing the dialog
        try:
            parent_app = self.master  # the App instance
            for m in parent_app.mod_configs:
                if m.get("Name") == mod_name:
                    m["Enabled"] = mod["Enabled"]
                    break
            parent_app.update_patch_instructions()
            parent_app.update_conflict_status()
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        # keep dialog open so user can toggle multiple mods
        self.master.focus_force()

    def _select_in_main_list(self, listbox):
        sel = listbox.curselection()
        if not sel:
            return
        display = listbox.get(sel[0])
        mod_name = display.split()[0]
        # ask parent to highlight the mod in its main list
        try:
            self.master.focus_force()
            self.resolve_callback_select = mod_name
            self.apply()  # will close dialog and let parent handle selection
        except Exception as e:
            logger.debug("ignored exception: %s", e)

    def buttonbox(self):
        box = ttk.Frame(self)
        ttk.Button(box, text="Resolve & Re-Patch", width=15, command=self.ok).pack(
            side=tk.LEFT, padx=5, pady=5
        )
        ttk.Button(box, text="Cancel", width=10, command=self.cancel).pack(
            side=tk.LEFT, padx=5, pady=5
        )
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack()

    def apply(self):
        # Build new order from listbox entries (strip suffix markers)
        new_order = []
        for i in range(self.list_box.size()):
            label = self.list_box.get(i)
            name = label.split()[0]
            new_order.append(name)
        # Map back to configs
        mod_map = {m["Name"]: m for m in self.mod_configs}
        new_mod_configs = [mod_map[n] for n in new_order if n in mod_map]
        # call parent callback
        try:
            self.resolve_callback(new_mod_configs)
        except Exception as e:
            logger.debug("ignored exception: %s", e)


# ---------------------- Tooltip ----------------------
class _ToolTip:
    """Simple tooltip for a widget."""

    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.id = None
        self.tw = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._cancel)
        widget.bind("<ButtonPress>", self._cancel)

    def _schedule(self, _ev=None):
        self._cancel()
        self.id = self.widget.after(self.delay, self._show)

    def _cancel(self, _ev=None):
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None
        if self.tw:
            try:
                self.tw.destroy()
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            self.tw = None

    def _show(self):
        if self.tw:
            return
        x, y, cx, cy = (
            self.widget.bbox("insert") if hasattr(self.widget, "bbox") else (0, 0, 0, 0)
        )
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 20
        self.tw = tk.Toplevel(self.widget)
        self.tw.wm_overrideredirect(True)
        self.tw.wm_geometry(f"+{x}+{y}")
        lbl = ttk.Label(
            self.tw, text=self.text, relief="solid", borderwidth=1, padding=(6, 3)
        )
        lbl.pack()


# ---------------------- Main Application GUI ----------------------


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Godot Mod Overhaul System (GMOS)")
        self.geometry("1250x1000")
        # Apply a simple theme for the "START GAME" button accent
        style = ttk.Style(self)
        style.configure(
            "Accent.TButton",
            foreground="green",
            background="black",
            font=("Arial", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "dark green")],
            foreground=[("active", "white")],
        )
        self.config = DEFAULTS.copy()
        cfg = ensure_config()
        if cfg:
            self.config.update(cfg)
        self.mod_configs = []  # Stores parsed mod info
        self.instructions = (
            []
        )  # The final, ordered list of patches (mod_name, op, details)
        self.patch_preview = []  # Cache for the last simulated patch log
        self.load_config()
        self.setup_ui()
        self.load_mods()  # Initial load

    def load_config(self):
        """Load configuration into self.config (backwards-compatible)."""
        try:
            # prefer platform config location
            cfg = load_config(get_config_path())
            # fallback to legacy ./config.json for users who already used that
            if not cfg and os.path.exists("config.json"):
                try:
                    with open("config.json", "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception as e:
                    logger.debug("legacy config.json read failed: %s", e)
            if cfg:
                self.config.update(cfg)
        except Exception as e:
            logger.debug("Error loading config: %s", e)

    def save_config(self):
        try:
            save_data = {}
            # Save only keys that we expose as UI vars
            for k, sv in getattr(self, "vars", {}).items():
                save_data[k] = sv.get()
            # Use atomic_replace for safer writing
            atomic_replace("config.json", json.dumps(save_data, indent=4))
        except Exception as e:
            print(f"Error saving config: {e}")

    def setup_ui(self):
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        main_paned = ttk.Panedwindow(self, orient=tk.VERTICAL)
        main_paned.pack(fill="both", expand=True)

        # --- Top Frame: Config and Mod List ---
        top_frame = ttk.Frame(main_paned, padding="10")
        main_paned.add(top_frame, weight=1)

        # 1. Configuration Section (Grid)
        config_frame = ttk.LabelFrame(top_frame, text="Configuration", padding="10")
        config_frame.pack(fill="x", pady=(0, 10))

        self.vars = {}
        row = 0
        for key, default in DEFAULTS.items():
            if key == "mos_module":
                continue

            tk.Label(config_frame, text=key.replace("_", " ").title() + ":").grid(
                row=row, column=0, sticky="w", padx=5, pady=2
            )

            var = tk.StringVar(value=safe_norm(self.config.get(key, default)))
            self.vars[key] = var

            entry = ttk.Entry(config_frame, textvariable=var)
            entry.grid(row=row, column=1, sticky="ew", padx=5, pady=2)

            if key.endswith("_dir") or key == "launch_override":
                # Add browse button for directory/file paths
                command = (
                    self.browse_directory if key.endswith("_dir") else self.browse_file
                )
                ttk.Button(
                    config_frame,
                    text="Browse",
                    command=lambda k=key, v=var, cmd=command: cmd(v),
                ).grid(row=row, column=2, padx=5, pady=2)
            else:
                ttk.Label(config_frame, text="").grid(row=row, column=2, padx=5, pady=2)

            row += 1

        config_frame.grid_columnconfigure(1, weight=1)

        # 2. Mod List Section
        mod_list_frame = ttk.LabelFrame(
            top_frame,
            text="Loaded Mods (Order determines Patch Priority - Last Wins)",
            padding="10",
        )
        mod_list_frame.pack(fill="both", expand=True)

        list_controls_frame = ttk.Frame(mod_list_frame)
        list_controls_frame.pack(fill="x", pady=5)

        ttk.Button(
            list_controls_frame, text="Refresh Mods", command=self.load_mods
        ).pack(side="left", padx=5)
        self.conflict_label = tk.Label(
            list_controls_frame, text="No Conflicts Detected", fg="green"
        )
        self.conflict_label.pack(side="left", padx=10)

        mod_list_controls = ttk.Frame(mod_list_frame)
        mod_list_controls.pack(fill="x", pady=5)
        ttk.Button(
            mod_list_controls,
            text="Move Up",
            command=lambda: self.move_selected_mod(-1),
        ).pack(side="left", padx=5)
        ttk.Button(
            mod_list_controls,
            text="Move Down",
            command=lambda: self.move_selected_mod(1),
        ).pack(side="left", padx=5)
        ttk.Button(
            mod_list_controls, text="Toggle Enable", command=self.toggle_selected_mod
        ).pack(side="left", padx=5)
        btn_export = ttk.Button(
            mod_list_controls, text="📤", width=3, command=self.export_mod_order
        )
        btn_export.pack(side="left", padx=5)
        _ToolTip(btn_export, "Export current mod order to a JSON file")
        btn_import = ttk.Button(
            mod_list_controls, text="📥", width=3, command=self.import_mod_order
        )
        btn_import.pack(side="left", padx=5)
        _ToolTip(btn_import, "Import mod order from a JSON file")
        ttk.Button(
            mod_list_controls,
            text="Resolve Conflicts",
            command=self.open_resolve_dialog,
        ).pack(side="right", padx=5)
        ttk.Button(
            mod_list_controls,
            text="Rollback Working Dir",
            command=self.rollback_working_dir,
        ).pack(side="right", padx=5)
        ttk.Button(
            mod_list_controls,
            text="Open Working Dir",
            command=lambda: webbrowser.open(
                safe_norm(self.vars["work_root_dir"].get())
            ),
        ).pack(side="right", padx=5)
        ttk.Button(
            mod_list_controls,
            text="View Runtime Manifest",
            command=self.view_runtime_manifest,
        ).pack(side="right", padx=5)
        self.mod_list_box = tk.Listbox(mod_list_frame, height=10, exportselection=False)
        self.mod_list_box.pack(fill="both", expand=True)

        # Drag and drop implementation for reordering
        self.mod_list_box.bind("<Button-1>", self.on_mod_list_click)
        self.mod_list_box.bind("<B1-Motion>", self.on_drag_motion)
        self.drag_index = None

        # bind selection changes and double-click toggle
        self.mod_list_box.bind("<<ListboxSelect>>", self._on_mod_selection_change)
        self.mod_list_box.bind("<Double-1>", self._on_mod_double_click)
        # Floating arrow buttons that follow the selected row
        self._arrow_frame = ttk.Frame(self.mod_list_box.master, relief="flat")
        self._btn_up = ttk.Button(
            self._arrow_frame,
            text="▲",
            width=2,
            command=lambda: self.move_selected_mod(-1),
        )
        self._btn_down = ttk.Button(
            self._arrow_frame,
            text="▼",
            width=2,
            command=lambda: self.move_selected_mod(1),
        )
        self._btn_up.pack(side="top", padx=2, pady=0)
        self._btn_down.pack(side="top", padx=2, pady=0)
        self._arrow_frame.place_forget()  # hide initially
        # Create the ModInfoPane inspector to the right of the mod list.
        # Use top_frame as the parent so the pane sits alongside the mod list.
        try:
            # Create the ModInfoPane but keep it hidden initially.
            # UI can reveal it with the toggle button added below.
            if not getattr(self, "mod_info", None):
                try:
                    self.mod_info = ModInfoPane(top_frame)
                    # do NOT pack it now; keep hidden until user opens it
                    self.mod_info_visible = False
                except Exception:
                    # headless/tests may not allow widget creation
                    self.mod_info = None
                    self.mod_info_visible = False
        except Exception:
            self.mod_info = None
            self.mod_info_visible = False

        # Add a small toggle button to show/hide the mod info pane.
        try:
            if not getattr(self, "mod_info_toggle_btn", None):

                def _toggle():
                    try:
                        if getattr(self, "mod_info_visible", False):
                            try:
                                self.mod_info.pack_forget()
                            except Exception:
                                pass
                            self.mod_info_visible = False
                            try:
                                self.mod_info_toggle_btn.config(text="Show Mod Info")
                            except Exception:
                                pass
                        else:
                            try:
                                # pack to right so it sits beside the list
                                self.mod_info.pack(side="right", fill="y", padx=(6, 0))
                            except Exception:
                                pass
                            self.mod_info_visible = True
                            try:
                                self.mod_info_toggle_btn.config(text="Hide Mod Info")
                            except Exception:
                                pass
                    except Exception:
                        pass

                self.mod_info_toggle_btn = tk.Button(
                    top_frame, text="Show Mod Info", command=_toggle
                )
                # place the toggle button near the mod list controls; non-invasive pack
                try:
                    self.mod_info_toggle_btn.pack(side="right", padx=(6, 0))
                except Exception:
                    # ignore layout failures in atypical UI setups
                    pass
        except Exception:
            pass

        # 3. Action Buttons
        action_frame = ttk.Frame(top_frame, padding="5")
        action_frame.pack(fill="x", pady=10)
        ttk.Button(
            action_frame,
            text="Apply Patch to Working Dir",
            command=self.run_patcher_action,
        ).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(
            action_frame,
            text="Simulate & Diff Patches",
            command=self.simulate_and_diff_action,
        ).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(action_frame, text="Save Diff", command=self.save_diff_to_file).pack(
            side="left", padx=5
        )
        ttk.Button(
            action_frame,
            text="START GAME",
            command=self.start_game_action,
            style="Accent.TButton",
        ).pack(side="right", padx=10)

        # --- Bottom Frame: Log and Diff ---
        bottom_frame = ttk.Frame(main_paned, padding="10")
        main_paned.add(bottom_frame, weight=1)

        # Log Pane
        self.log_notebook = ttk.Notebook(bottom_frame)
        self.log_notebook.pack(fill="both", expand=True)
        log_tab = ttk.Frame(self.log_notebook)
        self.log_notebook.add(log_tab, text="Patch Log")
        self.log_txt = scrolledtext.ScrolledText(log_tab, wrap=tk.WORD, height=15)
        self.log_txt.pack(fill="both", expand=True)
        self.append_log("Application loaded.")

        # Diff Pane
        diff_tab = ttk.Frame(self.log_notebook)
        self.log_notebook.add(diff_tab, text="Diff Preview")
        self.diff_txt = scrolledtext.ScrolledText(diff_tab, wrap=tk.WORD, height=15)
        self.diff_txt.pack(fill="both", expand=True)

    # --- GUI Handlers ---

    def on_mod_list_click(self, event):
        self.drag_index = self.mod_list_box.nearest(event.y)

    def on_drag_motion(self, event):
        if self.drag_index is not None:
            new_index = self.mod_list_box.nearest(event.y)
            if new_index != self.drag_index:

                # Update internal config order
                mod_config_to_move = self.mod_configs.pop(self.drag_index)
                self.mod_configs.insert(new_index, mod_config_to_move)

                # Update listbox display
                ordered_mods, deps_errors = apply_dependency_resolution(
                    self.mod_configs
                )
                rebuild_mod_listbox_from_configs(
                    self.mod_list_box, ordered_mods, _get_mod_name_from_config
                )
                self.drag_index = new_index
                self.update_patch_instructions()

    def _on_mod_selection_change(self, _ev=None):
        sel = self.mod_list_box.curselection()
        if not sel:
            self._arrow_frame.place_forget()
            # clear inspector when nothing is selected
            try:
                if getattr(self, "mod_info", None):
                    self.mod_info.update_for_config(None)
            except Exception:
                pass
            return
        idx = sel[0]
        # get bbox of the selected item (y offset relative to listbox)
        try:
            bbox = self.mod_list_box.bbox(idx)
            if not bbox:
                self._arrow_frame.place_forget()
                return
            x, y, w, h = bbox
            # place arrow_frame to the right of the listbox row (adjust offsets)
            list_x = self.mod_list_box.winfo_x()
            list_y = self.mod_list_box.winfo_y()
            place_x = list_x + self.mod_list_box.winfo_width() - 40
            place_y = list_y + y + 2
            # disable up/down when at edges
            if idx == 0:
                self._btn_up.state(["disabled"])
            else:
                self._btn_up.state(["!disabled"])
            if idx >= self.mod_list_box.size() - 1:
                self._btn_down.state(["disabled"])
            else:
                self._btn_down.state(["!disabled"])
            self._arrow_frame.place(x=place_x, y=place_y)
        except Exception:
            self._arrow_frame.place_forget()
            try:
                if getattr(self, "mod_info", None):
                    self.mod_info.update_for_config(None)
            except Exception:
                pass
            return

        # Update the ModInfoPane with the selected mod config (if available)
        try:
            cfg = self.mod_configs[idx] if 0 <= idx < len(self.mod_configs) else None
            if getattr(self, "mod_info", None):
                self.mod_info.update_for_config(cfg)
        except Exception:
            try:
                if getattr(self, "mod_info", None):
                    self.mod_info.update_for_config(None)
            except Exception:
                pass

    def _on_mod_double_click(self, event=None):
        sel = self.mod_list_box.curselection()
        if not sel:
            return
        idx = sel[0]
        mod = self.mod_configs[idx]
        mod["Enabled"] = not mod.get("Enabled", True)
        ordered_mods, deps_errors = apply_dependency_resolution(self.mod_configs)
        rebuild_mod_listbox_from_configs(
            self.mod_list_box, ordered_mods, _get_mod_name_from_config
        )
        self.update_patch_instructions()
        self.update_conflict_status()
        self.append_log(
            f"Mod '{mod['Name']}' {'enabled' if mod['Enabled'] else 'disabled'} via double-click."
        )

    def browse_directory(self, var):
        directory = filedialog.askdirectory()
        if directory:
            var.set(safe_norm(directory))

    def browse_file(self, var):
        file_path = filedialog.askopenfilename()
        if file_path:
            var.set(safe_norm(file_path))

    def append_log(self, message: str):
        timestamp = time.strftime("[%H:%M:%S]")
        self.log_txt.insert(tk.END, f"{timestamp} {message}\n")
        self.log_txt.see(tk.END)
        self.update_idletasks()  # Ensure immediate display

    def load_mods(self, mod_configs_override: Optional[List[Dict[str, Any]]] = None):
        """Loads and parses mod configurations, updating the internal list and GUI.
        Now performs manifest validation and marks invalid mods with .Valid/.Errors.
        """
        self.append_log("Loading mods...")

        if mod_configs_override is not None:
            self.mod_configs = mod_configs_override
        else:
            self.mod_configs = []
            mods_dir = safe_norm(self.vars["mods_dir"].get())
            if not os.path.isdir(mods_dir):
                self.append_log(f"Warning: Mods directory not found: {mods_dir}")
                self.mod_list_box.delete(0, tk.END)
                self.update_conflict_status()
                return

            for item in os.listdir(mods_dir):
                mod_path = os.path.join(mods_dir, item)
                if os.path.isdir(mod_path):
                    config = parse_mod_config(mod_path)
                    if config:
                        config["Path"] = mod_path  # Store the physical path
                        config["Enabled"] = config.get("Enabled", True)
                        # Validate immediately and store status
                        valid, err = validate_mod_config(config)
                        config["Valid"] = bool(valid)
                        if not valid:
                            config["Errors"] = err
                            self.append_log(
                                f"ERROR: Mod '{config['Name']}' invalid: {err}"
                            )
                        else:
                            config["Errors"] = None
                            self.append_log(f"Found mod: {config['Name']}")
                        self.mod_configs.append(config)

            # Sort by directory name initially
            self.mod_configs.sort(key=lambda m: Path(m["Path"]).name)
            # Resolve declared dependencies and reorder mods accordingly.
            # apply_dependency_resolution returns (ordered_list, {mod_name: [errors]})
            try:
                ordered, dep_errors = apply_dependency_resolution(self.mod_configs)
                if ordered:
                    self.mod_configs = ordered

                # annotate per-mod dependency errors into config for UI feedback
                for name, errs in (dep_errors or {}).items():
                    for m in self.mod_configs:
                        # prefer explicit Metadata Name if present, else fallback to folder name
                        mod_name = m.get("Name") or _get_mod_name_from_config(m)
                        if mod_name == name:
                            m.setdefault("Errors", []).extend(errs)
                            m["Valid"] = False
            except Exception as e:
                # Non-fatal; surface to debug log and continue with name-sorted order
                logger.debug("Dependency resolution failed: %s", e)

            # Rebuild the mod listbox to reflect resolved order and error feedback.
            # If your UI uses a different list widget, replace the call with the appropriate updater.
            try:
                rebuild_mod_listbox_from_configs(
                    self.mod_list_box, self.mod_configs, _get_mod_name_from_config
                )
            except Exception:
                # keep load resilient if UI widget API differs
                logger.debug(
                    "Failed to rebuild mod listbox with dependency annotations."
                )
        # Rebuild instructions from only valid mods
        self.update_patch_instructions()

        # Show a single consolidated error dialog if there are invalid mods
        invalid_count = sum(1 for mod in self.mod_configs if not mod.get("Valid", True))
        if invalid_count:
            msg_lines = []
            for mod in self.mod_configs:
                if not mod.get("Valid", True):
                    msg_lines.append(f"{mod['Name']}: {mod.get('Errors')}")
            messagebox.showerror(
                "Invalid Mods Detected",
                "Some mods failed validation and were skipped:\n\n"
                + "\n\n".join(msg_lines),
            )

        self.update_conflict_status()
        self.append_log(
            f"Loaded {len(self.mod_configs)} mods ({invalid_count} invalid)."
        )
        # Update ModInfoPane to reflect current selection (or clear it)
        try:
            if getattr(self, "mod_info", None):
                sel = self.mod_list_box.curselection()
                if sel:
                    idx = int(sel[0])
                    cfg = (
                        self.mod_configs[idx]
                        if 0 <= idx < len(self.mod_configs)
                        else None
                    )
                    self.mod_info.update_for_config(cfg)
                else:
                    self.mod_info.update_for_config(None)
        except Exception:
            # non-fatal; continue
            pass

        self._arrow_frame.place_forget()

    def rollback_working_dir(self):
        """Preview and selectively restore *.bak files in work_root or remove work_root.
        Debug-hardened: logs key steps, catches errors creating the preview window,
        forces window to top and reports problems via messagebox and log.
        """
        try:
            self.append_log("Rollback: invoked")
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        work_root = (
            safe_norm(self.vars["work_root_dir"].get())
            if "work_root_dir" in self.vars
            else None
        )
        if not work_root or not os.path.isdir(work_root):
            messagebox.showinfo("Rollback", f"No working directory found: {work_root}")
            try:
                self.append_log(f"Rollback: no work_root or missing dir: {work_root}")
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            return

        # Gather bak files (relative)
        bak_list = []
        try:
            for root, dirs, files in os.walk(work_root):
                for fn in files:
                    if fn.endswith(".bak"):
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, work_root)
                        bak_list.append(rel)
        except Exception as e:
            messagebox.showerror("Rollback Error", f"Failed scanning work_root: {e}")
            try:
                self.append_log(f"Rollback error scanning work_root: {e}")
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            return

        try:
            self.append_log(f"Rollback: found {len(bak_list)} .bak files")
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        if not bak_list:
            resp = messagebox.askyesno(
                "Rollback", "No .bak files found. Remove entire working directory?"
            )
            if resp:
                try:
                    shutil.rmtree(work_root)
                    messagebox.showinfo("Rollback", "Working directory removed.")
                    self.append_log(f"Rollback: removed working directory {work_root}")
                except Exception as e:
                    messagebox.showerror("Rollback Error", f"Remove failed: {e}")
                    self.append_log(f"Rollback error (remove): {e}")
            return

        # Create preview window robustly
        try:
            parent = self
            preview = tk.Toplevel(parent)
            preview.title("Rollback — Preview .bak files")
            preview.geometry("700x400")
            preview.transient(parent)
            preview.lift()
            preview.deiconify()
            try:
                preview.attributes("-topmost", True)
                preview.after(200, lambda: preview.attributes("-topmost", False))
            except Exception as e:
                logger.debug("ignored exception: %s", e)
        except Exception as e:
            # If Toplevel creation fails we must show the error and log it
            messagebox.showerror("Rollback Error", f"Cannot create preview window: {e}")
            try:
                self.append_log(f"Rollback error creating preview window: {e}")
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            return

        lbl = tk.Label(
            preview,
            text="Select .bak files to restore (checked) or choose Remove Working Directory.",
        )
        lbl.pack(anchor="w", padx=8, pady=(8, 0))

        # scrolling checkbox list
        frm = tk.Frame(preview)
        frm.pack(fill="both", expand=True, padx=8, pady=8)
        canvas = tk.Canvas(frm)
        sb = tk.Scrollbar(frm, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas)
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        vars_map = {}
        for rel in sorted(bak_list):
            v = tk.BooleanVar(value=True)
            cb = tk.Checkbutton(inner, text=rel, variable=v, anchor="w", justify="left")
            cb.pack(fill="x", anchor="w")
            vars_map[rel] = v

        btn_frame = tk.Frame(preview)
        btn_frame.pack(fill="x", padx=8, pady=8)

        def _restore_selected():
            selected = [r for r, var in vars_map.items() if var.get()]
            if not selected:
                messagebox.showinfo("Rollback", "No files selected to restore.")
                return
            confirm = messagebox.askyesno(
                "Confirm Restore", f"Restore {len(selected)} files from .bak?"
            )
            if not confirm:
                return
            restored = 0
            errors = []
            for rel in selected:
                bak = os.path.join(work_root, rel)
                orig = os.path.join(work_root, rel[:-4])
                try:
                    # safety: ensure target within work_root
                    if not os.path.commonpath([work_root, bak]).startswith(
                        os.path.normpath(work_root)
                    ):
                        raise RuntimeError("path outside work_root")
                    atomic_write_copy(bak, orig)
                    restored += 1
                    self.append_log(
                        f"Rollback: restored {rel} -> {os.path.relpath(orig, work_root)}"
                    )
                except Exception as e:
                    errors.append(f"{rel}: {e}")
                    self.append_log(f"Rollback error restoring {rel}: {e}")
            message = f"Restored {restored} files."
            if errors:
                message += f" {len(errors)} errors occurred. See log."
            messagebox.showinfo("Rollback", message)
            preview.destroy()

        def _remove_work_root():
            confirm = messagebox.askyesno(
                "Confirm Remove", f"Remove entire working directory: {work_root}?"
            )
            if not confirm:
                return
            try:
                shutil.rmtree(work_root)
                self.append_log(f"Rollback: removed working directory {work_root}")
                messagebox.showinfo("Rollback", "Working directory removed.")
            except Exception as e:
                messagebox.showerror("Rollback Error", f"Remove failed: {e}")
                self.append_log(f"Rollback error (remove): {e}")
            preview.destroy()

        btn_restore = tk.Button(
            btn_frame, text="Restore Selected", command=_restore_selected
        )
        btn_restore.pack(side="left", padx=6)
        btn_remove = tk.Button(
            btn_frame, text="Remove Working Directory", command=_remove_work_root
        )
        btn_remove.pack(side="left", padx=6)
        btn_cancel = tk.Button(btn_frame, text="Cancel", command=preview.destroy)
        btn_cancel.pack(side="right", padx=6)

        # final trace entry
        try:
            self.append_log("Rollback: preview window shown")
        except Exception as e:
            logger.debug("ignored exception: %s", e)

    def create_support_bundle(self):
        """Create a support zip containing logs and runtime_manifest from work_root (if present)."""
        try:
            work_root = safe_norm(self.vars["work_root_dir"].get())
        except Exception:
            work_root = None

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

                # include runtime_manifest from work_root if exists
                if work_root:
                    candidate = os.path.join(work_root, "runtime_manifest.json")
                    if os.path.exists(candidate):
                        zf.write(
                            candidate,
                            os.path.join("work_root", "runtime_manifest.json"),
                        )

                # include patch.log if present in work_root or ROOT_DIR
                for candidate in [
                    os.path.join(work_root or "", "patch.log"),
                    os.path.join(ROOT_DIR, "patch.log"),
                ]:
                    if candidate and os.path.exists(candidate):
                        zf.write(
                            candidate,
                            os.path.join("work_root", os.path.basename(candidate)),
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
                logger.debug("ignored exception: %s", e)

    def export_mod_order(self):
        """Export the current mod order (names and enabled flags) to a JSON file."""
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
        data = []
        for m in self.mod_configs:
            data.append(
                {
                    "Name": m.get("Name"),
                    "Path": m.get("Path"),
                    "Enabled": bool(m.get("Enabled", True)),
                }
            )
        try:
            atomic_replace(save_path, json.dumps(data, indent=2))
            messagebox.showinfo(
                "Export Complete", f"Mod order exported to:\n{save_path}"
            )
            self.append_log(f"Exported mod order to {save_path}")
        except Exception as e:
            messagebox.showerror("Export Error", f"Failed to export: {e}")
            self.append_log(f"Export mod order failed: {e}")

    def import_mod_order(self):
        """Import a mod order JSON and re-order/enable/disable mods to match it (best-effort)."""
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")], title="Import mod order"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Import Error", f"Failed to open file: {e}")
            return

        # Build name->index map for existing mods
        name_map = {m["Name"]: m for m in self.mod_configs}
        new_order = []
        for entry in data:
            name = entry.get("Name")
            if name in name_map:
                # update enabled flag if present
                if "Enabled" in entry:
                    name_map[name]["Enabled"] = bool(entry["Enabled"])
                new_order.append(name_map[name])
        # Append any mods not in the import at the end, preserving their current order
        for m in self.mod_configs:
            if m["Name"] not in {x["Name"] for x in data}:
                new_order.append(m)

        self.load_mods(mod_configs_override=new_order)
        messagebox.showinfo("Import Complete", "Mod order imported and applied.")
        self.append_log(f"Imported mod order from {path}")

    def update_patch_instructions(self):
        """
        Generates the single, combined patch plan based on current mod order.
        Skips invalid mods and logs per-mod validation errors.
        """
        self.instructions = []
        skipped = []
        for mod_config in self.mod_configs:
            if not mod_config.get("Valid", True):
                skipped.append(mod_config["Name"])
                continue
            if not mod_config.get("Enabled", True):
                skipped.append(mod_config["Name"] + " (disabled)")
                continue
            try:
                plan = generate_patch_plan(mod_config["Path"], mod_config)
                # Keep the plan in mod order
                self.instructions.extend(plan)
            except Exception as e:
                # Mark mod invalid and record error
                mod_config["Valid"] = False
                mod_config["Errors"] = str(e)
                skipped.append(mod_config["Name"])
                self.append_log(
                    f"ERROR: Failed to generate patch plan for '{mod_config['Name']}': {e}"
                )
        self.append_log(
            f"Generated {len(self.instructions)} patch instructions. Skipped mods: {', '.join(skipped) if skipped else 'none'}."
        )

    def update_conflict_status(self):
        """Checks for conflicts and updates the GUI label."""
        conflicts = analyze_mods_for_conflicts(self.mod_configs)
        if conflicts:
            count = len(conflicts)
            self.conflict_label.config(
                text=f"{count} Conflict{'s' if count > 1 else ''} Detected! (Click to Resolve)",
                fg="red",
            )
            self.conflict_label.bind("<Button-1>", lambda e: self.open_resolve_dialog())
        else:
            self.conflict_label.config(text="No Conflicts Detected", fg="green")
            self.conflict_label.unbind("<Button-1>")

    def open_resolve_dialog(self):
        """Opens the conflict resolution dialog."""
        conflicts = analyze_mods_for_conflicts(self.mod_configs)
        if not conflicts:
            messagebox.showinfo(
                "No Conflicts",
                "No critical conflicts were found. You can reorder mods using Move Up/Down.",
            )
            return

        ResolveDialog(self, conflicts, self.mod_configs, self.resolve_dialog_callback)

    def resolve_dialog_callback(self, new_mod_configs):
        """Called when the ResolveDialog closes with 'OK'."""
        self.load_mods(mod_configs_override=new_mod_configs)
        messagebox.showinfo(
            "Resolution Complete", "Mod order updated. Patch instructions regenerated."
        )

    def move_selected_mod(self, direction: int):
        """Moves the selected mod up (-1) or down (1) in the list."""
        try:
            selection = self.mod_list_box.curselection()
            if not selection:
                return
            index = selection[0]
            new_index = index + direction

            if 0 <= new_index < self.mod_list_box.size():
                # Update internal config order
                mod_config_to_move = self.mod_configs.pop(index)
                self.mod_configs.insert(new_index, mod_config_to_move)

                ordered_mods, deps_errors = apply_dependency_resolution(
                    self.mod_configs
                )
                rebuild_mod_listbox_from_configs(
                    self.mod_list_box, ordered_mods, _get_mod_name_from_config
                )

                self.update_patch_instructions()
                self.update_conflict_status()
        except Exception as e:
            self.append_log(f"Error reordering mod: {e}")

    def open_mod_folder(self, mod_name: str):
        """Open the mod folder in file explorer for convenience."""
        mod = next((m for m in self.mod_configs if m["Name"] == mod_name), None)
        if not mod or "Path" not in mod:
            self.append_log(f"Open folder failed: mod not found: {mod_name}")
            return
        try:
            webbrowser.open(mod["Path"])
        except Exception as e:
            self.append_log(f"Failed to open mod folder {mod_name}: {e}")

    def select_mod_in_main_list(self, mod_name: str):
        """Select and focus a mod by name in the main mod list box."""
        for idx, m in enumerate(self.mod_configs):
            if m.get("Name") == mod_name:
                try:
                    self.mod_list_box.selection_clear(0, tk.END)
                    self.mod_list_box.selection_set(idx)
                    self.mod_list_box.see(idx)
                    self.mod_list_box.focus_set()
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                return
        self.append_log(f"Select failed. Mod not found: {mod_name}")

    def toggle_selected_mod(self):
        """Enable/disable the currently selected mod. Disabled mods are skipped and greyed out."""
        try:
            sel = self.mod_list_box.curselection()
            if not sel:
                return
            idx = sel[0]
            mod = self.mod_configs[idx]
            # default to True
            currently = bool(mod.get("Enabled", True))
            mod["Enabled"] = not currently
            ordered_mods, deps_errors = apply_dependency_resolution(self.mod_configs)
            rebuild_mod_listbox_from_configs(
                self.mod_list_box, ordered_mods, _get_mod_name_from_config
            )
            self.append_log(
                f"Mod '{mod.get('Name')}' {'enabled' if mod['Enabled'] else 'disabled'}."
            )
            # regenerate instructions and conflicts
            self.update_patch_instructions()
            self.update_conflict_status()
        except Exception as e:
            self.append_log(f"Error toggling mod: {e}")

    def toggle_mod_enabled_by_name(self, mod_name: str):
        """Toggle Enabled flag for a mod by name and refresh lists."""
        for idx, m in enumerate(self.mod_configs):
            if m["Name"] == mod_name:
                m["Enabled"] = not m.get("Enabled", True)
                self.append_log(
                    f"Mod '{mod_name}' {'enabled' if m['Enabled'] else 'disabled'} (toggle)."
                )
                self.load_mods(mod_configs_override=self.mod_configs)
                return
        self.append_log(f"Toggle failed. Mod not found: {mod_name}")

    def run_patcher_action(self):
        """Runs the patcher core and applies patches to the working directory."""
        self.append_log("--- Starting Patch Application ---")

        # 1. Validate paths
        original_root = safe_norm(self.vars["original_game_dir"].get())
        work_root = safe_norm(self.vars["work_root_dir"].get())

        if not os.path.isdir(original_root):
            messagebox.showerror(
                "Error", f"Original game directory not found: {original_root}"
            )
            return

        if not self.instructions:
            messagebox.showwarning(
                "Warning", "No mod instructions loaded. Load mods first."
            )
            return

        # 2. Prepare working directory
        try:
            Path(work_root).mkdir(parents=True, exist_ok=True)
            self.append_log(f"Working directory ensured: {work_root}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to prepare working directory: {e}")
            return

        # 3. Execute Patcher Core with Lazy Copy logic
        self.append_log("Starting lazy patching process...")
        patch_log = run_patcher(original_root, work_root, self.instructions)

        for line in patch_log:
            self.append_log(f"PATCH: {line}")

        messagebox.showinfo(
            "Patch Complete",
            "Patching is complete. The working directory now contains only modified files.",
        )

    def start_game_action(self):
        """Launches the game executable from the working directory to ensure all modded files are used."""

        original_root = safe_norm(self.vars["original_game_dir"].get())
        work_root = safe_norm(self.vars["work_root_dir"].get())
        executable_name = self.vars["game_executable"].get()
        launch_override = self.vars["launch_override"].get()

        if launch_override:
            # If an override is provided, we assume the user knows what they're doing.
            game_exe_path = safe_norm(launch_override)
        else:
            # 1. Define paths for the executable in both original and work directories.
            original_exe_path = os.path.join(original_root, executable_name)
            work_exe_path = os.path.join(work_root, executable_name)

            # 2. Ensure the executable exists in the working directory, copying it if necessary.
            if not os.path.exists(work_exe_path):
                if not os.path.exists(original_exe_path):
                    messagebox.showerror(
                        "Launch Error",
                        f"Game executable not found in original directory: {original_exe_path}",
                    )
                    return
                self.append_log("Copying game executable to working directory...")
                try:
                    atomic_write_copy(original_exe_path, work_exe_path)
                    # Also copy the PCK file, which is essential for Godot games.
                    pck_name = Path(executable_name).with_suffix(".pck").name
                    original_pck_path = os.path.join(original_root, pck_name)
                    work_pck_path = os.path.join(work_root, pck_name)
                    if os.path.exists(original_pck_path):
                        atomic_write_copy(original_pck_path, work_pck_path)
                except Exception as e:
                    messagebox.showerror(
                        "Launch Error",
                        f"Failed to copy executable to working directory: {e}",
                    )
                    return

            game_exe_path = work_exe_path

        if not os.path.exists(game_exe_path):
            messagebox.showerror(
                "Launch Error", f"Game executable not found at: {game_exe_path}"
            )
            return

        # Godot's --path argument should point to the directory containing project.godot
        # When running from work_root, this is work_root itself.
        command = [game_exe_path, "--path", work_root]

        self.append_log("--- Attempting to Launch Game ---")
        self.append_log(f"Executable: {game_exe_path}")
        self.append_log(f"Working Directory & Resource Path: {work_root}")

        try:
            # Ensure command is a list and safely locate the executable.
            if isinstance(command, str):
                # split a command string into argv safely
                command_list = shlex.split(command)
            else:
                # assume it's an iterable of argv elements
                command_list = list(command)

            if not command_list:
                raise RuntimeError("Empty launch command.")

            # Try to resolve executable path safely. Prefer an exact path or look on PATH.
            exe = command_list[0]
            exe_path = exe
            if not os.path.isabs(exe):
                exe_path = shutil.which(exe, path=os.environ.get("PATH"))
            if exe_path is None:
                raise RuntimeError(f"Cannot locate executable: {exe}")

            # Launch without a shell. Close file descriptors and drop IO to avoid leaking handles.
            _safe_spawn(
                [exe_path] + command_list[1:],
                cwd=work_root,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _safe_spawn(command, cwd=work_root)
            self.append_log("SUCCESS: Game launched from the modded directory.")
        except Exception as e:
            messagebox.showerror("Launch Error", f"Failed to launch game:\n{e}")
            self.append_log(f"FATAL ERROR during game launch: {e}")

    def simulate_and_diff_action(self):
        """
        Simulate the patch in a temp dir and produce diffs for ALL modified files.
        Each file header lists which mod(s) referenced that file target.
        """
        if not self.instructions:
            messagebox.showwarning("Warning", "No mod instructions loaded.")
            return

        original_root = safe_norm(self.vars["original_game_dir"].get())
        if not os.path.isdir(original_root):
            messagebox.showerror(
                "Error", f"Original game directory not found: {original_root}"
            )
            return

        self.append_log("--- Starting Patch Simulation & Diff (full) ---")
        self.log_notebook.select(1)  # show diff tab
        self.diff_txt.delete("1.0", tk.END)

        touched_by: Dict[str, set] = defaultdict(set)
        for entry in self.instructions:
            try:
                # instructions expected as (mod_name, op, details)
                mod_name = entry[0]
                op = entry[1]
                details = entry[2] if len(entry) > 2 else None
                if op in ("FileReplace", "VariablePatch", "FunctionPatch"):
                    tr = details[0] if details else None
                else:
                    tr = details[0] if details else None
                if tr:
                    touched_by[_res_to_path(tr)].add(mod_name)
            except Exception as e:
                logger.debug(
                    "Failed to register touch for %s by %s: %s", tr, mod_name, e
                )
                continue

        # Run simulation
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_work_root = os.path.join(temp_dir, "sim_work")
            Path(temp_work_root).mkdir(parents=True, exist_ok=True)
            sim_log = run_patcher(original_root, temp_work_root, self.instructions)

            # Prefer runtime_manifest.json for modified file list (more reliable).
            patched_rel_paths = []
            manifest_path = os.path.join(temp_work_root, "runtime_manifest.json")
            try:
                if os.path.exists(manifest_path):
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        manifest = json.load(mf)
                        patched_rel_paths = manifest.get("modified_files", []) or []
                        self.append_log(
                            f"Used runtime_manifest.json for diff (found {len(patched_rel_paths)} files)."
                        )
                else:
                    # Fallback: regex-parse sim_log (legacy behavior)
                    for line in sim_log:
                        m = re.search(r"Copied\s+([^\s]+)\s+to", line)
                        if m:
                            patched_rel_paths.append(m.group(1))
                            continue
                        m2 = re.search(r"Used existing\s+([^\s]+)\s+in", line)
                        if m2:
                            patched_rel_paths.append(m2.group(1))
                    if patched_rel_paths:
                        self.append_log(
                            f"Fallback: parsed sim_log for {len(patched_rel_paths)} files."
                        )
            except Exception as e:
                self.append_log(
                    f"Warning: failed to read runtime_manifest.json or parse sim_log: {e}"
                )
                if not patched_rel_paths:
                    for line in sim_log:
                        m = re.search(r"Copied\s+([^\s]+)\s+to", line)
                        if m:
                            patched_rel_paths.append(m.group(1))
                            continue
                        m2 = re.search(r"Used existing\s+([^\s]+)\s+in", line)
                        if m2:
                            patched_rel_paths.append(m2.group(1))

            if not patched_rel_paths:
                self.diff_txt.insert(
                    tk.END, "No files were modified during the simulation."
                )
                return

            # Deduplicate while preserving order
            seen = set()
            dedup_paths = []
            for p in patched_rel_paths:
                if p not in seen:
                    seen.add(p)
                    dedup_paths.append(p)

            # Generate diffs for each patched file and annotate header with mods
            # and accumulate into combined_diff for artifact export.
            combined_parts = []
            for rel in dedup_paths:
                orig_path = os.path.join(original_root, rel)
                patched_path = os.path.join(temp_work_root, rel)
                header = f"\n===== File: {rel} =====\n"
                mods = touched_by.get(rel, set())
                mods_list = sorted(mods)
                header += f"Mods touching this file: {', '.join(mods_list) if mods_list else 'unknown'}\n\n"
                self.diff_txt.insert(tk.END, header)
                combined_parts.append(header)

                try:
                    with open(orig_path, "r", encoding="utf-8", errors="ignore") as f:
                        orig_lines = f.readlines()
                except Exception:
                    orig_lines = []

                try:
                    with open(
                        patched_path, "r", encoding="utf-8", errors="ignore"
                    ) as f:
                        patched_lines = f.readlines()
                except Exception:
                    patched_lines = []

                diff_iter = difflib.unified_diff(
                    orig_lines,
                    patched_lines,
                    fromfile=f"original/{rel}",
                    tofile=f"patched/{rel}",
                    lineterm="",
                )
                diff_text = "\n".join(diff_iter)
                if not diff_text:
                    # Heuristic binary detection: check for NUL in first 4KiB
                    is_binary = False
                    try:

                        def _sample_has_nul(p):
                            if not os.path.exists(p):
                                return False
                            with open(p, "rb") as b:
                                s = b.read(4096)
                                return b"\0" in s

                        if _sample_has_nul(orig_path) or _sample_has_nul(patched_path):
                            is_binary = True
                    except Exception:
                        is_binary = False

                    if is_binary:
                        diff_text = "(BINARY FILE — no textual diff available.)"
                    else:
                        diff_text = "(No textual diff; files identical or only formatting changes.)"
                self.diff_txt.insert(tk.END, diff_text + "\n")
                combined_parts.append(diff_text + "\n")

            # jump to top of diff tab
            self.log_notebook.select(1)
            # persist combined diff into dryrun artifact for easier bug reports
            try:
                combined = "\n".join(combined_parts)
                # ensure we pass the combined diff to artifact writer
                try:
                    self._save_dryrun_artifact(
                        sim_log, temp_work_root, original_root, combined_diff=combined
                    )
                except TypeError:
                    # backward compatibility if older signature exists
                    self._save_dryrun_artifact(sim_log, temp_work_root, original_root)
            except Exception:
                logger.exception("Failed to save combined diff into dryrun artifact")

    def _save_dryrun_artifact(
        self, sim_log, temp_work_root, original_root, combined_diff: str = None
    ):
        """Persist sim_log and runtime_manifest.json to logs/dryrun_TIMESTAMP/ and zip it."""
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dry_dir = os.path.join(LOG_DIR, f"dryrun_{ts}")
        os.makedirs(dry_dir, exist_ok=True)

        # write sim_log
        try:
            with open(os.path.join(dry_dir, "sim_log.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(sim_log))
        except Exception:
            logger.exception("Failed writing sim_log text")

        # copy manifest if present
        manifest_src = os.path.join(temp_work_root, "runtime_manifest.json")
        if os.path.exists(manifest_src):
            try:
                dest_manifest = os.path.join(dry_dir, "runtime_manifest.json")
                atomic_write_copy(manifest_src, dest_manifest)
            except Exception:
                logger.exception("Failed copying runtime_manifest.json")

        # save combined diff if provided
        if combined_diff:
            try:
                combined_path = os.path.join(dry_dir, "combined_diff.patch")
                atomic_write_bytes(combined_path, combined_diff.encode("utf-8"))
            except Exception:
                logger.exception("Failed writing combined_diff.patch")

        # write metadata
        meta = {
            "timestamp_utc": ts,
            "original_root": original_root,
            "temp_work_root": temp_work_root,
        }
        try:
            with open(os.path.join(dry_dir, "meta.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            logger.exception("Failed writing dryrun meta.json")

        # create zipped snapshot for easy attachment
        try:
            bundle_path = os.path.join(LOG_DIR, f"dryrun_bundle_{ts}.zip")
            with zipfile.ZipFile(
                bundle_path, "w", compression=zipfile.ZIP_DEFLATED
            ) as zf:
                for root, _, files in os.walk(dry_dir):
                    for fn in files:
                        full = os.path.join(root, fn)
                        arc = os.path.relpath(full, dry_dir)
                        zf.write(full, arc)
            logger.info("Dry-run artifact written: %s", bundle_path)
        except Exception:
            logger.exception("Failed creating dryrun bundle")

    def save_diff_to_file(self):
        """Prompt user and save the current Diff Preview content to a .patch file."""
        try:
            content = self.diff_txt.get("1.0", tk.END)
        except Exception:
            tk.messagebox.showerror("Save Diff", "No diff content available.")
            return

        if not content.strip():
            tk.messagebox.showinfo("Save Diff", "No diff content to save.")
            return

        try:
            default_dir = os.path.join(os.path.expanduser("~"), "Documents")
            os.makedirs(default_dir, exist_ok=True)
            ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            default_name = f"gmos_diff_{ts}.patch"
            path = filedialog.asksaveasfilename(
                defaultextension=".patch",
                initialdir=default_dir,
                initialfile=default_name,
                filetypes=[("Patch", "*.patch"), ("Text", "*.txt")],
                title="Save diff",
            )
            if not path:
                return
            # atomic write
            atomic_write_bytes(path, content.encode("utf-8"))
            self.append_log(f"Saved diff to {path}")
            messagebox.showinfo("Save Diff", f"Diff saved to:\n{path}")
            logger.info("User exported diff: %s", path)
        except Exception as e:
            logger.exception("save_diff_to_file failed: %s", e)
            try:
                messagebox.showerror("Save Diff Error", f"Failed to save diff: {e}")
            except Exception as e:
                logger.debug("ignored exception: %s", e)

    def view_runtime_manifest(self):
        """Open runtime_manifest.json from work_root in system viewer or show an error."""
        work_root = safe_norm(self.vars["work_root_dir"].get())
        manifest_path = os.path.join(work_root, "runtime_manifest.json")
        if not os.path.exists(manifest_path):
            messagebox.showinfo(
                "Runtime Manifest", f"No runtime_manifest.json found in {work_root}"
            )
            return
        try:
            webbrowser.open(manifest_path)
            self.append_log(f"Opened runtime_manifest: {manifest_path}")
        except Exception as e:
            messagebox.showerror("Runtime Manifest", f"Failed to open manifest: {e}")
            self.append_log(f"Open manifest failed: {e}")

    def on_close(self):
        self.save_config()
        self.destroy()


def main_entry():
    """
    If CLI args present run headless CLI. Otherwise start GUI.
    This allows onefile/--windowed builds to launch the GUI on double-click,
    while preserving CLI usage for console builds.
    """
    # single-instance guard: try to acquire lock before proceeding.
    # retry a few times with small randomized backoff to close the tiny race window
    got_lock = False
    max_attempts = 10
    for attempt in range(max_attempts):
        try:
            got_lock = acquire_app_lock()
        except Exception:
            got_lock = False
        if got_lock:
            break
        # small randomized sleep to avoid synchronized retries across processes
        time.sleep(0.03 + (secrets.randbelow(1000) / 1000.0) * 0.12)

    if not got_lock:
        # If this is a CLI invocation, print and exit nonzero. Otherwise show dialog.
        if len(sys.argv) > 1:
            print(
                "Another GMOS instance is already running. Exiting.",
                file=sys.stderr,
            )
            return 2
        else:
            try:
                from tkinter import messagebox

                messagebox.showerror(
                    "Already Running",
                    "Another GMOS instance is already running. Close it and try again.",
                )
            except Exception:
                print(
                    "Another GMOS instance is already running. Exiting.",
                    file=sys.stderr,
                )
            return 2

    # If any extra args provided, treat as CLI invocation.
    if len(sys.argv) > 1:
        return _cli_main()

    # No args -> GUI mode
    try:
        if "__app_id" not in globals():
            globals()["__app_id"] = "default_app_id"
        app = App()
        # wire automatic per-workroot locking for GUI.
        try:
            wire_workroot_locking(app)
        except Exception:
            logger.exception("Failed wiring workroot locking")
        app.mainloop()
        return 0
    except Exception as e:
        logger.exception("GUI startup failed: %s", e)
        # If GUI fails, surface a messagebox when possible
        try:
            messagebox.showerror(
                "Startup Error",
                f"Failed to start GUI. See log: {os.path.join(LOG_DIR, 'gmos.log')}\n\n{e}",
            )
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main_entry())
