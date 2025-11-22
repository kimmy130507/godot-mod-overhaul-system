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
import datetime
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
import zipfile
from collections import defaultdict
from functools import partial
from pathlib import Path
from tkinter import (
    Button,
    Label,
    Toplevel,
    filedialog,
    messagebox,
    scrolledtext,
    simpledialog,
    ttk,
)
from tkinter.ttk import Progressbar
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Union, cast

try:
    from typing import NotRequired
except ImportError:
    from typing_extensions import NotRequired
try:
    import ttkbootstrap as ttkb
except ImportError:
    ttkb = None
from gmos import utils
from gmos.core import security
from gmos.core.patcher import (
    Hunk,
    analyze_mods_for_conflicts,
    apply_dependency_resolution,
    apply_hunks,
    generate_patch_plan,
    generate_unified_diff,
    parse_unified_diff_hunks,
    run_patcher,
    save_dryrun_artifact,
)
from gmos.core.sdk import GodotBridge
from gmos.core.session import GmosSession
from gmos.io import (
    atomic_write_bytes,
    atomic_write_copy,
    cache,
    get_io_executor,
    safe_read_bytes,
)
from gmos.state import policy, profiles
from gmos.state.config import DEFAULTS, get_config_path, load_config, write_config
from gmos.utils import _get_mod_name_from_config  # type: ignore [reportPrivateUsage]
from gmos.utils import _safe_spawn  # type: ignore [reportPrivateUsage]
from gmos.utils import (
    LOG_DIR,
    ROOT_DIR,
    ModConfig,
    handle_permission_error,
    logger,
    safe_norm,
)


class UIModConfig(ModConfig):
    """Extends ModConfig with UI-specific state keys."""

    Enabled: NotRequired[bool]
    Valid: NotRequired[bool]
    Errors: NotRequired[Optional[List[str]]]
    _deps_errors: NotRequired[List[str]]  # type: ignore[misc]
    _deps_tooltip: NotRequired[str]
    _security_risks: NotRequired[List[security.SecurityRisk]]
    Path: NotRequired[str]  # type: ignore[misc]


def res_to_path(res_path: str) -> str:
    """Converts 'res://path/to/file' to 'path/to/file'."""
    if res_path.startswith("res://"):
        return res_path[6:].lstrip("/")
    return res_path.lstrip("/")


class ProgressDialog(Toplevel):
    def __init__(self, parent: tk.Misc, title: str = "Progress"):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        self.transient(cast("tk.Tk", parent))
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
        self.geometry("+200+200")

    def start(self) -> None:
        self.pb.start(50)

    def stop(self) -> None:
        try:
            self.pb.stop()  # type: ignore[no-untyped-call]
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
        """Close dialog safely."""
        try:
            self.stop()
            self.destroy()
        except Exception:
            pass


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

    def __init__(self, parent: tk.Misc | None, orig_text: str, new_text: str):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        self.parent = parent
        self.orig_text = orig_text
        self.new_text = new_text
        if parent:
            # transient requires a Tk/Toplevel-like master
            self.transient(cast("tk.Tk", parent))
        self.title("Resolve conflicts - Hunk viewer")
        self.resizable(True, True)
        self.result: Optional[str] = None

        # generate diff and hunks (Hunk is a TypedDict defined in gmos.patcher)
        self.diff_text: str = generate_unified_diff(
            orig_text, new_text, fromfile="original", tofile="replacement"
        )
        # parse_unified_diff_hunks returns List[Hunk]
        self.hunks: List[Hunk] = parse_unified_diff_hunks(self.diff_text)
        self.hunk_vars: List[tk.IntVar] = [tk.IntVar(value=1) for _ in self.hunks]

        # layout: left pane with hunk list + checkboxes, right pane preview
        left = tk.Frame(self)
        left.pack(side="left", fill="y", padx=6, pady=6)
        tk.Label(left, text="Hunks (check to apply)").pack(anchor="w")
        list_frame = tk.Frame(left)
        list_frame.pack(fill="y", expand=True)
        # create a scrollable frame for many hunks
        canvas = tk.Canvas(list_frame, width=320, height=300)
        vs = tk.Scrollbar(
            list_frame,
            orient="vertical",
            command=canvas.yview,  # type: ignore[reportUnknownArgumentType]
        )
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
        canvas.configure(scrollregion=canvas.bbox("all"))

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
            v.trace_add("write", lambda *_: self._update_preview())  # type: ignore[arg-type]

        # initial preview
        self._update_preview()  # type: ignore

        # modal behavior
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _selected_indices(self) -> List[int]:
        return [i for i, v in enumerate(self.hunk_vars) if v.get()]

    def _update_preview(self) -> None:
        try:
            sel = self._selected_indices()
            # apply_hunks now accepts Sequence[Hunk], so call directly.
            merged = apply_hunks(self.orig_text, self.hunks, sel)
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", merged)
            self.preview.configure(state="disabled")
        except Exception as e:
            logger.debug(
                "HunkViewer preview content update failed: %s", e, exc_info=True
            )
            pass

    def _on_apply(self) -> None:
        self.result = apply_hunks(
            self.orig_text,
            self.hunks,
            self._selected_indices(),
        )
        self.destroy()

    def _on_apply_all(self) -> None:
        self.result = apply_hunks(
            self.orig_text,
            self.hunks,
            list(range(len(self.hunks))),
        )
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def show_modal(self, *, headless_auto_accept: bool = False) -> Optional[str]:
        """
        Show modal UI and return merged_text or None if cancelled.
        headless_auto_accept: when True, or when no GUI available, compute and
        return merged result without user interaction.
        """

        # Quick headless detection and early accept.
        try:
            if headless_auto_accept:
                return self._compute_merged_text_and_close()

            # No Tk root -> headless
            if not getattr(tk, "_default_root", None):
                logger.debug("HunkViewer: no tkinter root; auto-accepting hunks")
                return self._compute_merged_text_and_close()

            # On Unix, if DISPLAY is not set, treat as headless
            if sys.platform != "win32" and not os.environ.get("DISPLAY"):
                logger.debug("HunkViewer: no DISPLAY; auto-accepting hunks")
                return self._compute_merged_text_and_close()
        except Exception as e:
            # detection failed; log and continue to UI path
            try:
                logger.debug(
                    "HunkViewer headless detection failed, falling back to UI: %s", e
                )
            except Exception:  # nosec B110
                pass

        # Existing UI path. Keep behaviour unchanged.
        # Wait for user to close the modal and return previously-stored self.result
        self.wait_window(self)
        return getattr(self, "result", None)

    def _compute_merged_text_and_close(self) -> str:
        """
        Compute merged text deterministically by accepting all hunks.
        Return merged text string.
        """
        try:
            orig = getattr(self, "orig_text", None)
            new = getattr(self, "new_text", None)
            if orig is None or new is None:
                # fallback: return new if available, else orig
                return new or orig or ""
            # apply_hunks should return the merged string when all hunks accepted.
            merged = apply_hunks(
                orig,
                self.hunks,
                list(range(len(self.hunks))),
            )  # Apply all hunks
            return merged
        except Exception as e:
            # fallback conservative behaviour: prefer the replacement file (new)
            try:
                logger.debug("HunkViewer _compute_merged_text_and_close failed: %s", e)
            except Exception:  # nosec B110
                pass
            return getattr(self, "new_text", "") or getattr(self, "orig_text", "")


# ----------------------------
# Mod Info Pane (Tkinter)
# ----------------------------
class ModInfoPane(tk.Frame):
    """
    Right-side inspector panel showing selected mod metadata and dependency errors.
    """

    def __init__(self, master: tk.Misc, width: int = 360, **kwargs: Any):
        super().__init__(master, width=width, **kwargs)
        self.columnconfigure(1, weight=1)
        self._widgets: Dict[str, tk.Widget] = {}

        def _label(row: int, text: str, bold: bool = False) -> tk.Label:
            lbl = tk.Label(self, text=text, anchor="w", justify="left")
            if bold:
                lbl.configure(font=("TkDefaultFont", 9, "bold"))
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
        desc_widget = tk.Text(self, height=6, wrap="word")
        self._widgets["desc"] = tk.Text(self, height=6, wrap="word")
        self._widgets["desc"].grid(
            row=3, column=0, columnspan=2, sticky="nsew", padx=6, pady=6
        )
        desc_widget.configure(state="disabled")
        # Dependencies
        _label(4, "Dependencies:", bold=True)
        self._widgets["deps"] = tk.Label(
            self, text="", anchor="w", justify="left", fg="black", wraplength=260
        )
        self._widgets["deps"].grid(row=4, column=1, sticky="nw", padx=6, pady=2)

        _label(5, "Dependency Errors:", bold=True)
        self._widgets["errors"] = tk.Label(
            self, text="", anchor="w", justify="left", fg="red", wraplength=260
        )
        # Content spans both columns on the NEXT row
        self._widgets["errors"].grid(
            row=6, column=0, columnspan=2, sticky="nw", padx=6, pady=(0, 6)
        )

        self._widgets["security"] = tk.Label(
            self, text="", anchor="w", justify="left", fg="orange", wraplength=260
        )
        self._widgets["security"].grid(
            row=7, column=0, columnspan=2, sticky="nw", padx=6, pady=(0, 6)
        )

        btnf = tk.Frame(self)
        btnf.grid(row=8, column=0, columnspan=2, sticky="ew", padx=6, pady=6)

        self._widgets["open_folder_btn"] = tk.Button(
            btnf, text="Open Mod Folder", command=self._open_mod_folder
        )
        self._widgets["open_folder_btn"].pack(side="left")
        self._widgets["toggle_enable_btn"] = tk.Button(
            btnf, text="Enable", command=self._toggle_enable
        )
        self._widgets["toggle_enable_btn"].pack(side="left", padx=6)

        # internal state
        self._current_cfg: Optional[UIModConfig] = None

    def _get_cfg_path(self) -> str | os.PathLike[str]:
        cfg = self._current_cfg
        if not cfg:
            return ""
        return cfg.get("Path", "")

    def _open_mod_folder(self) -> None:
        path = self._get_cfg_path()
        if not path:
            return
        try:
            p = os.fspath(path)
        except TypeError:
            return

        abs_path = os.path.abspath(p)
        if not os.path.exists(abs_path):
            return

        if sys.platform.startswith("win"):
            opener = "explorer"
        elif sys.platform == "darwin":
            opener = "open"
        else:
            opener = "xdg-open"

        exe_path = shutil.which(opener)
        if exe_path is None:
            return

        subprocess.Popen([exe_path, abs_path])

    def _toggle_enable(self) -> None:
        cfg = self._current_cfg
        if not cfg:
            return

        # 1. Update the config object
        cur = cfg.get("Enabled", True)
        new_state = not bool(cur)
        cfg["Enabled"] = new_state

        # 2. Update local UI immediately
        self.update_for_config(cfg)

        # 3. Force the Main App to refresh the listbox and patch instructions
        try:
            # Get the root App instance
            app = cast(Any, self.winfo_toplevel())

            # Ensure we have the method before calling
            if hasattr(app, "load_mods") and hasattr(app, "mod_configs"):
                # Reloading with the existing list forces a re-sort/re-paint
                app.load_mods(mod_configs_override=app.mod_configs)

                # Also regenerate instructions since enabled state changed
                if hasattr(app, "update_patch_instructions"):
                    app.update_patch_instructions()

                if hasattr(app, "update_conflict_status"):
                    app.update_conflict_status()

        except Exception as e:
            logger.debug("Failed to refresh main app from inspector: %s", e)

    def _safe_set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text or "")
        widget.configure(state="disabled")

    def update_for_config(self, cfg: Optional[UIModConfig]) -> None:
        self._current_cfg = cfg
        if not cfg:
            cast(tk.Label, self._widgets["name"]).configure(text="")
            cast(tk.Label, self._widgets["version"]).configure(text="")
            cast(tk.Label, self._widgets["author"]).configure(text="")
            if isinstance(self._widgets["desc"], tk.Text):
                self._safe_set_text(self._widgets["desc"], "")
            cast(tk.Label, self._widgets["deps"]).configure(text="(none)")
            cast(tk.Label, self._widgets["errors"]).configure(text="")
            cast(tk.Label, self._widgets["security"]).configure(text="")

            if isinstance(self._widgets["open_folder_btn"], tk.Button):
                self._widgets["open_folder_btn"].configure(state="disabled")
            if isinstance(self._widgets["toggle_enable_btn"], tk.Button):
                self._widgets["toggle_enable_btn"].configure(
                    text="Enable", state="disabled"
                )
            return

        if isinstance(self._widgets["open_folder_btn"], tk.Button):
            self._widgets["open_folder_btn"].configure(state="normal")
        if isinstance(self._widgets["toggle_enable_btn"], tk.Button):
            self._widgets["toggle_enable_btn"].configure(state="normal")

        name = cfg.get("Name") or _get_mod_name_from_config(cfg)
        version = ""
        author = ""
        description = ""
        sections: Dict[str, Any] = cfg.get("Sections") or {}
        for sec_k, lines in sections.items():
            if sec_k.lower() == "metadata":
                for line in lines:
                    try:
                        k, v = [p.strip() for p in line.split("=", 1)]
                    except ValueError:
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

        cast(tk.Label, self._widgets["name"]).configure(text=name or "")
        cast(tk.Label, self._widgets["version"]).configure(text=version or "")
        cast(tk.Label, self._widgets["author"]).configure(text=author or "")
        if isinstance(self._widgets["desc"], tk.Text):
            self._safe_set_text(self._widgets["desc"], description or "")

        deps: List[str] = []
        for sec_k, lines in sections.items():
            if sec_k.lower() == "dependencies":
                for line in lines:
                    try:
                        _, val = [p.strip() for p in line.split("=", 1)]
                    except Exception:
                        val = line.strip()
                    for part in (p.strip() for p in val.split(",") if p.strip()):
                        deps.append(part)
        cast(tk.Label, self._widgets["deps"]).configure(
            text=", ".join(deps) or "(none)"
        )

        errlist = cfg.get("_deps_errors") or cfg.get("Errors") or []
        if errlist:
            cast(tk.Label, self._widgets["errors"]).configure(text="\n".join(errlist))
        else:
            cast(tk.Label, self._widgets["errors"]).configure(text="")

        risks = cfg.get("_security_risks", [])
        if risks:
            high_risks = sum(1 for r in risks if r.severity == "HIGH")
            med_risks = len(risks) - high_risks
            msg = f"⚠️ Found {len(risks)} potential risks:\n"
            msg += f"{high_risks} HIGH, {med_risks} MEDIUM\nCheck files manually."
            cast(tk.Label, self._widgets["security"]).configure(
                text=msg, fg="red" if high_risks > 0 else "orange"
            )
        else:
            cast(tk.Label, self._widgets["security"]).configure(
                text="✅ Clean Scan", fg="green"
            )

        is_enabled = cfg.get("Enabled", True)
        if isinstance(self._widgets["toggle_enable_btn"], tk.Button):
            self._widgets["toggle_enable_btn"].configure(
                text="Disable" if is_enabled else "Enable"
            )


# -------------------------
# Permission error dialog
# -------------------------
class PermissionErrorDialog(tk.Toplevel):
    """
    Modal dialog offering Retry / Choose folder / Abort when permission errors occur.
    Usage:
        dlg = PermissionErrorDialog(parent, path, exc)
        choice = dlg.show()  # returns 'retry' | 'choose' | 'abort'
    """

    def __init__(
        self,
        parent: Optional[tk.Widget],
        path: str | os.PathLike[str],
        exc: Exception,
    ):
        super().__init__(parent or getattr(tk, "_default_root", None))
        utils.load_and_apply_app_icon_to_toplevel(self)
        if parent:
            self.transient(cast("tk.Tk", parent))
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

        # center and focus
        self.update_idletasks()
        try:
            self.grab_set()
            self.focus_force()
        except Exception:
            pass

    def _on_retry(self) -> None:
        self._choice = "retry"
        self.destroy()

    def _on_choose(self) -> None:
        # show folder chooser relative to this dialog
        try:
            newdir = filedialog.askdirectory(parent=self)
            if newdir:
                self._choice = ("choose", newdir)
                self.destroy()
            else:
                # user cancelled chooser: stay in dialog
                return
        except Exception:
            # fallback: treat as abort
            self._choice = "abort"
            self.destroy()

    def _on_abort(self) -> None:
        self._choice = "abort"
        self.destroy()

    def show(self) -> Any:
        # run modal loop
        try:
            self.wait_window(self)
        except Exception:
            # fallback if wait_window fails in some envs
            pass
        return self._choice


# ---------------------- Conflict Resolution Dialog ----------------------
class ResolveDialog(simpledialog.Dialog):
    """
    Conflict resolution dialog.
    Shows each conflicting target with the list of mods touching it.
    Allows reordering the overall mod list (drag/drop or Move Up/Down)
    and quick actions: Open Mod Folder, Toggle Enable.
    The dialog returns the new mod order via resolve_callback(new_mod_configs).
    """

    if TYPE_CHECKING:
        master: "App"  # type: ignore [reportIncompatibleVariableOverride]

    def __init__(
        self,
        parent: tk.Widget,
        conflicts: Dict[str, Any],
        mod_configs: List[UIModConfig],
        resolve_callback: Callable[[List[UIModConfig]], None],
    ):
        utils.load_and_apply_app_icon_to_toplevel(self)
        self.conflicts = conflicts
        self.mod_configs = mod_configs
        self.resolve_callback = resolve_callback
        # keep a live name->config map for quick operations
        self.mod_map: Dict[str, UIModConfig] = {
            m["Name"]: m for m in mod_configs if "Name" in m
        }
        self.file_rules = policy.load_file_rules()
        self.resolved_order: List[str] = [m["Name"] for m in mod_configs if "Name" in m]
        self.resolve_callback_select: str | None = None
        self.drag_index: int | None = None
        self.list_box: tk.Listbox
        super().__init__(parent, title="Resolve Mod Conflicts")

    def body(self, master: tk.Widget) -> tk.Listbox:
        tk.Label(
            master,
            text="Conflicts detected. Later mods win. Review and reorder or disable mods.",
            font=("Inter", 10, "bold"),
        ).pack(pady=6)

        # Scrollable conflicts area
        conf_frame = ttk.Frame(master)
        conf_frame.pack(fill="both", expand=False, padx=6)

        canvas = tk.Canvas(conf_frame, height=180)
        vsb = ttk.Scrollbar(
            conf_frame,
            orient="vertical",
            command=canvas.yview,  # type: ignore[reportUnknownArgumentType]
        )
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
            # target_key format: Type::res://path::var
            t_parts = target_key.split("::")
            # Reconstruct res path for policy lookup
            res_path = t_parts[1] if len(t_parts) > 1 else ""
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
                text="Set as Winner",
                command=lambda lb_ref=lb, rp=res_path: self._set_winner(lb_ref, rp),  # type: ignore[misc]
            ).pack(fill="x", pady=2)
            ttk.Button(
                btnf,
                text="Open Folder",
                command=lambda lb_ref=lb: self._open_selected_mod_folder(lb_ref),  # type: ignore[misc]
            ).pack(fill="x", pady=2)
            ttk.Button(
                btnf,
                text="Toggle Enable",
                command=lambda lb_ref=lb: self._toggle_selected_mod(lb_ref),  # type: ignore[misc]
            ).pack(fill="x", pady=2)
            ttk.Button(
                btnf,
                text="Select in Main List",
                command=lambda lb_ref=lb: self._select_in_main_list(lb_ref),  # type: ignore[misc]
            ).pack(fill="x", pady=2)
            # Check if a policy rule exists
            norm_res = res_to_path(res_path)
            current_winner = self.file_rules.get(norm_res)
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
                if current_winner == mod_name:
                    display += " [WINNER]"
                lb.insert(tk.END, display)
                if current_winner == mod_name:
                    lb.itemconfig(tk.END, bg="#d0f0c0")  # type: ignore[reportUnknownMemberType] # Light green highlight

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

        return self.list_box

    # listbox drag helpers
    def on_list_click(self, event: "tk.Event[Any]") -> None:
        self.drag_index = int(cast(str, self.list_box.nearest(event.y)))  # type: ignore[no-untyped-call, reportUnknownMemberType]

    def on_drag_motion(self, event: "tk.Event[Any]") -> None:
        if self.drag_index is None:
            return
        new_index = int(cast(str, self.list_box.nearest(event.y)))  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if new_index != self.drag_index:
            val = cast(str, self.list_box.get(self.drag_index))  # type: ignore[reportUnknownMemberType]
            self.list_box.delete(self.drag_index)
            self.list_box.insert(new_index, val)
            self.drag_index = new_index

    def move_up(self) -> None:
        sel = cast(tuple[str, ...], self.list_box.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if not sel:
            return
        i = int(sel[0])
        if i == 0:
            return
        val = cast(str, self.list_box.get(i))  # type: ignore[reportUnknownMemberType]
        self.list_box.delete(i)
        self.list_box.insert(i - 1, val)
        self.list_box.selection_set(i - 1)

    def move_down(self) -> None:
        sel = cast(tuple[str, ...], self.list_box.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if not sel:
            return
        i = int(sel[0])
        if i >= self.list_box.size() - 1:
            return
        val = cast(str, self.list_box.get(i))  # type: ignore[reportUnknownMemberType]
        self.list_box.delete(i)
        self.list_box.insert(i + 1, val)
        self.list_box.selection_set(i + 1)

    def reset_order(self) -> None:
        self.list_box.delete(0, tk.END)
        for name in [m["Name"] for m in self.mod_configs if "Name" in m]:
            label = name
            mod = self.mod_map.get(name)
            if mod and not mod.get("Valid", True):
                label += " [INVALID]"
            if mod and not mod.get("Enabled", True):
                label += " [DISABLED]"
            self.list_box.insert(tk.END, label)

    def _set_winner(self, listbox: tk.Listbox, res_path: str) -> None:
        """Sets the selected mod as the authoritative winner for this file."""
        sel = cast(tuple[str, ...], listbox.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if not sel:
            return
        idx = int(sel[0])
        display = cast(str, listbox.get(idx))  # type: ignore[reportUnknownMemberType]
        mod_name = display.split()[0]

        # Normalize path logic must match patcher._res_to_path exactly or be compatible
        # ui.res_to_path strips "res://" and leading slash.
        norm_path = res_to_path(res_path)

        self.file_rules[norm_path] = mod_name
        policy.save_policy(
            cast(List[Dict[str, Any]], self.mod_configs), self.file_rules
        )

        messagebox.showinfo(
            "Policy Updated",
            f"'{mod_name}' set as winner for:\n{norm_path}\n\nRestart dialog to see changes.",
        )

    def _open_selected_mod_folder(self, listbox: tk.Listbox) -> None:
        sel = cast(tuple[str, ...], listbox.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if not sel:
            return
        idx = int(sel[0])
        display = cast(str, listbox.get(idx))  # type: ignore[reportUnknownMemberType]
        mod_name = display.split()[0]
        mod = self.mod_map.get(mod_name)
        if mod and "Path" in mod:
            try:
                webbrowser.open(mod["Path"])
            except Exception as e:
                logger.debug(
                    "Failed to open mod folder from ResolveDialog: %s", e, exc_info=True
                )

    def _toggle_selected_mod(self, listbox: tk.Listbox) -> None:
        sel = cast(tuple[str, ...], listbox.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if not sel:
            return
        idx = int(sel[0])
        display = cast(str, listbox.get(idx))  # type: ignore[reportUnknownMemberType]
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
            logger.debug(
                "Failed to update conflict listbox entry after mod toggle: %s",
                e,
                exc_info=True,
            )
        # Update main app state in-place without closing the dialog
        try:
            parent_app = self.master
            for m in parent_app.mod_configs:
                if m.get("Name") == mod_name:
                    m["Enabled"] = mod["Enabled"]
                    break
            parent_app.update_patch_instructions()
            parent_app.update_conflict_status()
        except Exception as e:
            logger.debug(
                "Failed to update parent app state after mod toggle: %s",
                e,
                exc_info=True,
            )
        # keep dialog open so user can toggle multiple mods
        self.master.focus_force()

    def _select_in_main_list(self, listbox: tk.Listbox) -> None:
        sel = cast(tuple[str, ...], listbox.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if not sel:
            return
        display = cast(str, listbox.get(int(sel[0])))  # type: ignore[reportUnknownMemberType]
        mod_name = display.split()[0]
        # ask parent to highlight the mod in its main list
        try:
            self.master.focus_force()
            self.resolve_callback_select = mod_name
            self.apply()  # type: ignore[no-untyped-call] # will close dialog and let parent handle selection
        except Exception as e:
            logger.debug(
                "Failed to focus/select mod in main list from ResolveDialog: %s",
                e,
                exc_info=True,
            )

    def buttonbox(self) -> None:
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

    def apply(self) -> None:
        # Build new order from listbox entries (strip suffix markers)
        new_order: List[str] = []
        for i in range(self.list_box.size()):
            label = cast(str, self.list_box.get(i))  # type: ignore[reportUnknownMemberType]
            name = label.split()[0]
            new_order.append(name)
        # Map back to configs
        mod_map: Dict[str, UIModConfig] = {
            m["Name"]: m for m in self.mod_configs if "Name" in m
        }
        new_mod_configs: List[UIModConfig] = [
            mod_map[n] for n in new_order if n in mod_map
        ]
        # call parent callback
        try:
            self.resolve_callback(new_mod_configs)
        except Exception as e:
            logger.debug(
                "Tooltip window destruction failed during cancel: %s", e, exc_info=True
            )


# ---------------------- Legal Disclaimer ----------------------
class LegalDisclaimerDialog(tk.Toplevel):
    """
    First-run legal acknowledgment dialog.
    """

    def __init__(self, parent: tk.Widget):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        self.title("Legal Notice — Read Before Using GMOS")
        self.geometry("600x400")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)

        # Center
        self.update_idletasks()
        # Robust geometry calculation
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        x = px + (pw // 2) - 300
        y = py + ph // 2 - 200
        self.geometry(f"+{x}+{y}")

        self.grab_set()

        # Content
        frame = ttk.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        lbl = ttk.Label(
            frame, text="GMOS modifies your game files.", font=("Segoe UI", 12, "bold")
        )
        lbl.pack(pady=(0, 10), anchor="w")

        body_text = (
            "GMOS modifies your game files and may change game behavior. "
            "Some games prohibit modifications in their EULA or Terms of Service. "
            "Using this tool may cause account or access restrictions imposed by game publishers.\n\n"
            'By checking "I understand" you acknowledge responsibility for verifying the target '
            "game's policy and accept that the GMOS authors are not liable for any consequences."
        )

        # Use Text widget for wrapping
        txt = tk.Text(
            frame,
            wrap="word",
            height=8,
            font=("Segoe UI", 10),
            bg="#f0f0f0",
            relief="flat",
        )
        txt.insert("1.0", body_text)
        txt.config(state="disabled")
        txt.pack(fill="x", pady=10)

        # Controls
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

        ttk.Button(btn_frame, text="Show LEGAL.md", command=self.show_legal_file).pack(
            side="left"
        )

        self.cont_btn = ttk.Button(
            btn_frame, text="Continue", command=self.on_accept, state="disabled"
        )
        self.cont_btn.pack(side="right", padx=5)

        ttk.Button(btn_frame, text="Cancel", command=self.on_cancel).pack(side="right")

        self.result = False

    def toggle_continue(self) -> None:
        if self.accepted_var.get():
            self.cont_btn.config(state="normal")
        else:
            self.cont_btn.config(state="disabled")

    def show_legal_file(self) -> None:
        # Try to open LEGAL.md, fallback to messagebox
        possible_paths = ["LEGAL.md", "LICENSE", "README.md"]
        found = False
        for p in possible_paths:
            if os.path.exists(p):
                try:
                    webbrowser.open(p)
                    found = True
                    break
                except Exception:
                    pass
        if not found:
            messagebox.showinfo(
                "Legal Info",
                "LEGAL.md not found.\nGMOS is provided AS-IS under the GNU GPLv3.",
            )

    def on_accept(self) -> None:
        self.result = True
        self.destroy()

    def on_cancel(self) -> None:
        self.result = False
        self.destroy()


# ---------------------- Toast Notification ----------------------
class Toast(tk.Toplevel):
    """
    Non-blocking transient notification overlay.
    """

    def __init__(
        self, parent: tk.Widget, message: str, duration: int = 3000, kind: str = "info"
    ):
        super().__init__(parent)
        self.overrideredirect(True)

        # Theme colors
        bg = "#333333"
        fg = "#ffffff"
        if kind == "error":
            bg = "#d9534f"
        elif kind == "success":
            bg = "#5cb85c"

        self.configure(bg=bg)
        lbl = tk.Label(
            self, text=message, bg=bg, fg=fg, padx=20, pady=10, font=("Segoe UI", 10)
        )
        lbl.pack()

        # Center-bottom placement relative to parent
        self.update_idletasks()
        pw = parent.winfo_width() if parent.winfo_width() > 1 else 1000
        ph = parent.winfo_height() if parent.winfo_height() > 1 else 800
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()

        w = self.winfo_width()
        h = self.winfo_height()

        x = px + (pw // 2) - (w // 2)
        y = py + ph - h - 50

        self.geometry(f"+{x}+{y}")
        self.lift()  # type: ignore[reportUnknownMemberType]
        self.after(duration, self.destroy)


# ---------------------- Developer Tools Dialog ----------------------
class DeveloperToolsDialog(tk.Toplevel):
    """
    UI for Modder SDK Bridge and Runtime Sandbox.
    """

    def __init__(self, parent: "App"):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        self.title("GMOS Developer Tools")
        self.geometry("600x450")
        self.resizable(False, False)
        self.parent = parent
        self.game_dir = parent.vars["game_dir"].get()

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)

        # --- Tab 1: SDK / Bridge ---
        bridge_tab = ttk.Frame(tabs, padding=10)
        tabs.add(bridge_tab, text="Modder Bridge (SDK)")

        ttk.Label(bridge_tab, text="Workspace Path:", font=("", 9, "bold")).pack(
            anchor="w"
        )
        self.ws_var = tk.StringVar(value=os.path.join(self.game_dir, "gmos_workspace"))
        ws_frm = ttk.Frame(bridge_tab)
        ws_frm.pack(fill="x", pady=(0, 10))
        ttk.Entry(ws_frm, textvariable=self.ws_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(ws_frm, text="Browse", command=self._browse_ws).pack(
            side="left", padx=4
        )

        ttk.Label(bridge_tab, text="Actions:", font=("", 9, "bold")).pack(anchor="w")

        ttk.Button(
            bridge_tab,
            text="1. Initialize Workspace (Extract Game)",
            command=self._init_workspace,
        ).pack(fill="x", pady=2)

        ttk.Button(
            bridge_tab, text="2. Launch Godot Editor", command=self._launch_editor
        ).pack(fill="x", pady=2)

        ttk.Separator(bridge_tab, orient="horizontal").pack(fill="x", pady=10)

        ttk.Label(bridge_tab, text="Build Mod:", font=("", 9, "bold")).pack(anchor="w")
        self.mod_name_var = tk.StringVar(value="MyNewMod")

        nm_frm = ttk.Frame(bridge_tab)
        nm_frm.pack(fill="x", pady=2)
        ttk.Label(nm_frm, text="Name:").pack(side="left")
        ttk.Entry(nm_frm, textvariable=self.mod_name_var).pack(
            side="left", fill="x", expand=True, padx=4
        )

        ttk.Button(
            bridge_tab, text="3. Compile Mod (Diff & Export)", command=self._compile_mod
        ).pack(fill="x", pady=5)

        # --- Tab 2: Sandbox ---
        sb_tab = ttk.Frame(tabs, padding=10)
        tabs.add(sb_tab, text="Runtime Sandbox")

        self.sb_status_lbl = ttk.Label(sb_tab, text="Checking status...")
        self.sb_status_lbl.pack(pady=10)
        self.sb_btn = ttk.Button(sb_tab, text="Action", command=self._toggle_sandbox)
        self.sb_btn.pack(pady=5)
        self._refresh_sandbox_ui()

    def _browse_ws(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.ws_var.set(d)

    def _init_workspace(self) -> None:
        if not messagebox.askyesno(
            "Confirm", "Extracting the game will take time. Continue?"
        ):
            return
        try:
            bridge = GodotBridge(self.game_dir, self.ws_var.get())
            count = bridge.init_workspace()
            messagebox.showinfo("Success", f"Extracted {count} files to workspace.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _launch_editor(self) -> None:
        # Try to find godot in path or ask user
        exe = shutil.which("godot") or shutil.which("godot4")
        if not exe:
            exe = filedialog.askopenfilename(title="Locate Godot Editor Executable")
        if not exe:
            return
        try:
            bridge = GodotBridge(self.game_dir, self.ws_var.get())
            bridge.launch_editor(exe)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _compile_mod(self) -> None:
        out_dir = filedialog.askdirectory(title="Select Output Folder for Mod")
        if not out_dir:
            return
        try:
            bridge = GodotBridge(self.game_dir, self.ws_var.get())
            path = bridge.generate_mod_patch(out_dir, self.mod_name_var.get(), "User")
            if path:
                messagebox.showinfo("Success", f"Mod generated at:\n{path}")
            else:
                messagebox.showwarning(
                    "No Changes", "No modified files detected in workspace."
                )
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _refresh_sandbox_ui(self) -> None:
        # Force update idletasks to ensure file ops settle
        self.update_idletasks()
        is_injected = self.parent.session.check_sandbox_status()

        if is_injected:
            self.sb_status_lbl.config(
                text="✅ Sandbox is INSTALLED (Active)", foreground="green"
            )
            self.sb_btn.config(text="Uninstall Sandbox")
        else:
            self.sb_status_lbl.config(
                text="❌ Sandbox is NOT INSTALLED", foreground="red"
            )
            self.sb_btn.config(text="Install Sandbox")

    def _toggle_sandbox(self) -> None:
        try:
            new_state = self.parent.session.toggle_sandbox()
            if new_state:
                messagebox.showinfo("Success", "Sandbox injected into project.godot.")
            else:
                messagebox.showinfo("Success", "Sandbox removed from project.godot.")
            self._refresh_sandbox_ui()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to toggle sandbox: {e}")


# ---------------------- Tooltip ----------------------
class _ToolTip:
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

    def _schedule(self, _ev: Any | None = None) -> None:
        self._cancel()
        self.id = self.widget.after(self.delay, self._show)

    def _cancel(self, _ev: Any | None = None) -> None:
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None
        if self.tw:
            try:
                self.tw.destroy()
            except Exception as e:
                # Tooltip window destruction sometimes fails non-critically
                logger.debug("Tooltip window destruction failed: %s", e, exc_info=True)
            self.tw = None

    def _show(self) -> None:
        if self.tw:
            return
        bbox_coords = (
            self.widget.bbox("insert")  # type: ignore[call-overload, reportArgumentType]
            if hasattr(self.widget, "bbox")
            else None
        )
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


def rebuild_mod_listbox_from_configs(
    listbox: tk.Listbox,
    mod_configs: List[UIModConfig],
    name_getter: Callable[[ModConfig], str] = _get_mod_name_from_config,
) -> None:
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
    except Exception as e:
        logger.debug("Failed to clear listbox: %s", e, exc_info=True)
        return

    for cfg in mod_configs:
        name = name_getter(cfg)
        label = name
        is_invalid = bool(cfg.get("_deps_errors") or cfg.get("Errors"))
        if is_invalid:
            label = f"{label} [INVALID]"
        # detect disabled flag conventionally stored as Enabled or enabled
        enabled = cfg.get("Enabled")
        if enabled is None:
            # try lowercase key in sections metadata if present
            enabled = cast(Optional[bool], cfg.get("enabled", True))

        idx = listbox.size()
        # Mark risky mods
        risks = cfg.get("_security_risks", [])
        if risks:
            label = f"⚠️ {label}"
        listbox.insert(idx, label)
        try:
            if not enabled:
                listbox.itemconfig(idx, fg="gray")  # type: ignore[reportUnknownMemberType]
            elif is_invalid:
                listbox.itemconfig(idx, fg="red")  # type: ignore[reportUnknownMemberType]
        except Exception as e:
            logger.debug(
                "Ignoring itemconfig error (unsupported listbox): %s", e, exc_info=True
            )
            pass

        # attach tooltip text in cfg for UI to consume if desired
        if is_invalid:
            cfg["_deps_tooltip"] = "\n".join(
                cfg.get("_deps_errors", []) or cfg.get("Errors") or []
            )
        else:
            cfg.pop("_deps_tooltip", None)


# ---------------------- Main Application GUI ----------------------
class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Godot Mod Overhaul System (GMOS)")
        self.geometry("1500x1000")
        # --- UI Modernization ---
        self.style = None
        if ttkb:
            # Apply Bootstrap Theme (Dark Mode)
            self.style = ttkb.Style(theme="darkly")
            # Ensure window background matches theme
            style_any = cast(Any, self.style)
            self.configure(bg=style_any.colors.bg)
        else:
            # Fallback Standard Theme
            self.style = ttk.Style(self)
            self.style.configure(
                "Accent.TButton",
                foreground="green",
                background="black",
                font=("Arial", 10, "bold"),
            )
            self.style.map(
                "Accent.TButton",
                background=[("active", "dark green")],
                foreground=[("active", "white")],
            )
        utils.load_and_apply_app_icon(self)
        self.cfg: Dict[str, Any] = DEFAULTS.copy()
        self.mod_configs: List[UIModConfig] = []  # Stores parsed mod info
        self.instructions: List[Any] = (
            []
        )  # The final, ordered list of patches (mod_name, op, details)
        self.session = GmosSession(game_dir="", mods_dir="")
        self.patch_preview: List[str] = []  # Cache for the last simulated patch log
        self.vars: Dict[str, tk.StringVar] = {}
        self.drag_index: int | None = None
        self.mod_info: Optional[ModInfoPane] = None
        self.mod_info_visible = False
        self.dev_tools_window: Optional[Toplevel] = None
        self.mod_info_toggle_btn: Optional[tk.Button] = None
        self.mod_list_box: tk.Listbox
        self.log_notebook: ttk.Notebook
        self.load_config()
        self._acquire_singleton_lock()
        self._is_busy = False  # Track if a critical task is running
        self.setup_ui()
        # Show Legal Check
        if not self.cfg.get("legal_accepted", False):
            self.show_legal_check()
        self.load_mods()  # Initial load

    def _acquire_singleton_lock(self) -> None:
        """Ensures only one instance is running using utils.LOCK_PATH."""
        utils.ensure_log_dir_exists()
        try:
            # Keep the file handle open in self._lock_file to maintain the lock
            self._lock_file = open(utils.LOCK_PATH, "a")
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.lockf(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            messagebox.showerror("GMOS Running", "Another instance is already running.")
            sys.exit(0)

    def load_config(self) -> None:
        """Load configuration into self.configure (backwards-compatible)."""
        try:
            # prefer platform config location
            cfg = load_config(get_config_path())
            # fallback to legacy ./config.json for users who already used that
            if not cfg and os.path.exists("config.json"):
                try:
                    with open("config.json", "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception as e:
                    logger.debug(
                        "Failed to read legacy config.json file: %s", e, exc_info=True
                    )
            if cfg:
                self.cfg.update(cfg)
        except Exception as e:
            logger.debug(
                "Outer exception during full config load: %s", e, exc_info=True
            )

    def save_config(self) -> None:
        try:
            # Update the main config dictionary with current UI variables
            # This preserves hidden keys like 'legal_accepted'
            for k, sv in getattr(self, "vars", {}).items():
                self.cfg[k] = sv.get()
            write_config(self.cfg)
        except Exception as e:
            print(f"Error saving config: {e}")

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
            self.cfg["legal_accepted"] = True
            self.save_config()

    def setup_ui(self) -> None:
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        main_paned = ttk.Panedwindow(self, orient=tk.VERTICAL)
        main_paned.pack(fill="both", expand=True)

        # --- Top Frame: Config and Mod List ---
        top_frame = ttk.Frame(main_paned, padding="10")
        main_paned.add(top_frame, weight=3)  # type: ignore [reportUnknownMemberType]

        # 1. Configuration Section (Grid)
        config_frame = ttk.LabelFrame(top_frame, text="Configuration", padding="10")
        config_frame.pack(fill="x", pady=(0, 10))
        self.vars = {}
        row = 0
        # Explicitly list fields to show, ignoring technical keys
        fields = [
            ("Game Directory", "game_dir", True),
            ("Mods Directory", "mods_dir", True),
            ("Game Executable", "game_executable", False),
            ("Launch Override", "launch_override", False),
        ]

        for label, key, is_dir in fields:
            tk.Label(config_frame, text=label + ":").grid(
                row=row, column=0, sticky="w", padx=5, pady=2
            )
            val = safe_norm(self.cfg.get(key, DEFAULTS.get(key, "")))
            var = tk.StringVar(value=val)
            self.vars[key] = var

            entry = ttk.Entry(config_frame, textvariable=var)
            entry.grid(row=row, column=1, sticky="ew", padx=5, pady=2)

            cmd = self.browse_directory if is_dir else self.browse_file
            ttk.Button(config_frame, text="Browse", command=partial(cmd, var)).grid(
                row=row, column=2, padx=5, pady=2
            )
            row += 1

        # Force PCK Option
        self.force_pck_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            config_frame,
            text="Force PCK Patching (Advanced)",
            variable=self.force_pck_var,
        ).grid(row=row, column=1, sticky="w", padx=5, pady=2)
        config_frame.grid_columnconfigure(1, weight=1)

        # Create a frame to hold the mod list and the info pane side-by-side
        middle_frame = ttk.Frame(top_frame)
        middle_frame.pack(fill="both", expand=True, pady=5)

        config_frame.grid_columnconfigure(1, weight=1)

        # 2. Mod List Section
        mod_list_frame = ttk.LabelFrame(
            middle_frame,
            text="Loaded Mods (Order determines Patch Priority - Last Wins)",
            padding="10",
        )
        mod_list_frame.pack(side="left", fill="both", expand=True)

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
            text="Revert Game Files",
            command=self.rollback_working_dir,
        ).pack(side="right", padx=5)
        ttk.Button(
            mod_list_controls,
            text="Open Game Dir",
            command=lambda: webbrowser.open(safe_norm(self.vars["game_dir"].get())),
        ).pack(side="right", padx=5)
        ttk.Button(
            mod_list_controls,
            text="View Runtime Manifest",
            command=self.view_runtime_manifest,
        ).pack(side="right", padx=5)
        ttk.Button(
            mod_list_controls,
            text="🛠️Dev Tools",
            command=self.open_developer_tools,
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
                    self.mod_info = ModInfoPane(middle_frame)
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

                # place the toggle button near the mod list controls; non-invasive pack
                try:
                    if self.mod_info_toggle_btn:
                        self.mod_info_toggle_btn.pack(side="right", padx=(6, 0))
                except Exception as e:
                    logger.debug(
                        "Ignoring layout failure in atypical UI: %s", e, exc_info=True
                    )
                    pass
        except Exception as e:
            logger.exception(
                "Outer exception in mod info packing logic: %s", e, exc_info=True
            )
            pass

        # 3. Action Buttons
        action_frame = ttk.Frame(top_frame, padding="5")
        action_frame.pack(fill="x", pady=10)

        # --- Pack right-aligned buttons first (from right to left) ---

        # Add the toggle button for the mod info pane
        # (We moved this code down and changed its parent to action_frame)
        try:
            if not getattr(self, "mod_info_toggle_btn", None):

                def _toggle() -> None:  # NOTE: _toggle function is defined here
                    try:
                        if getattr(self, "mod_info_visible", False):
                            # HIDING
                            try:
                                if self.mod_info:
                                    self.mod_info.pack_forget()
                            except Exception as e:
                                logger.debug(
                                    "mod_info.pack_forget() unsupported or failed (ignored): %s",
                                    e,
                                )
                                pass
                            self.mod_info_visible = False
                            if hasattr(self, "_arrow_frame"):
                                self._arrow_frame.place_forget()
                            try:
                                if self.mod_info_toggle_btn:
                                    self.mod_info_toggle_btn.configure(
                                        text="Show Mod Info"
                                    )
                            except Exception as e:
                                logger.debug(
                                    "Ignored error configuring mod info button: %s",
                                    e,
                                    exc_info=True,
                                )
                                pass
                        else:
                            # SHOWING
                            try:
                                # pack to right so it sits beside the list
                                if self.mod_info:
                                    self.mod_info.pack(
                                        side="right", fill="y", padx=(6, 0)
                                    )
                                    sel = cast(tuple[str, ...], self.mod_list_box.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
                                    if sel:
                                        idx = int(sel[0])
                                        if 0 <= idx < len(self.mod_configs):
                                            self.mod_info.update_for_config(
                                                self.mod_configs[idx]
                                            )
                            except Exception as e:
                                logger.debug(
                                    "Error packing mod info widget: %s",
                                    e,
                                    exc_info=True,
                                )
                                pass
                            self.mod_info_visible = True
                            try:
                                if self.mod_info_toggle_btn:
                                    self.mod_info_toggle_btn.configure(
                                        text="Hide Mod Info"
                                    )
                            except Exception as e:
                                logger.debug(
                                    "Error configuring 'Hide Mod Info' text: %s",
                                    e,
                                    exc_info=True,
                                )
                                pass
                    except Exception as e:
                        logger.exception(
                            "Outer exception in mod info toggle logic: %s",
                            e,
                            exc_info=True,
                        )
                        pass

                self.mod_info_toggle_btn = tk.Button(
                    action_frame,
                    text="Show Mod Info",
                    command=_toggle,  # Parent is action_frame
                )
                self.mod_info_toggle_btn.pack(
                    side="right", padx=(6, 0)
                )  # Pack in action_frame
        except Exception as e:
            logger.exception(
                "Outer exception in mod info packing logic: %s", e, exc_info=True
            )
            pass
        start_btn_kwargs: Dict[str, Any] = {
            "text": "START GAME",
            "command": self.start_game_action,
        }
        if ttkb:
            # Use Bootstrap success style (Green)
            start_btn_kwargs["bootstyle"] = "success"  # type: ignore
            start_btn_kwargs["width"] = 20
        else:
            start_btn_kwargs["style"] = "Accent.TButton"
        self.start_game_btn = ttk.Button(action_frame, **start_btn_kwargs).pack(
            side="right", padx=10
        )

        # --- Pack left-aligned buttons last (from left to right) ---
        self.patch_btn = ttk.Button(
            action_frame,
            text="Apply Mods to Game",
            command=self.run_patcher_action,
        )
        self.patch_btn.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(
            action_frame,
            text="Simulate & Diff Patches",
            command=self.simulate_and_diff_action,
        ).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(
            action_frame,
            text="Clear Cache",
            command=self.clear_cache_action,
        ).pack(side="left", padx=5)
        ttk.Button(action_frame, text="Save Diff", command=self.save_diff_to_file).pack(
            side="left", padx=5
        )

        # --- Bottom Frame: Log and Diff ---
        bottom_frame = ttk.Frame(main_paned, padding="10")
        main_paned.add(bottom_frame, weight=1)  # type: ignore [reportUnknownMemberType]

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
        self.diff_txt = scrolledtext.ScrolledText(diff_tab, wrap=tk.NONE, height=15)
        self.diff_txt.pack(fill="both", expand=True)

    # --- GUI Handlers ---

    def on_mod_list_click(self, event: "tk.Event[Any]") -> None:
        self.drag_index = int(cast(str, self.mod_list_box.nearest(event.y)))  # type: ignore[no-untyped-call, reportUnknownMemberType]

    def on_drag_motion(self, event: "tk.Event[Any]") -> None:
        if self.drag_index is not None:
            new_index = int(cast(str, self.mod_list_box.nearest(event.y)))  # type: ignore[no-untyped-call, reportUnknownMemberType]
            if new_index != self.drag_index:

                # Update internal config order
                mod_config_to_move = self.mod_configs.pop(self.drag_index)
                self.mod_configs.insert(new_index, mod_config_to_move)

                # Update listbox display
                ordered_mods, _ = apply_dependency_resolution(
                    cast(List[ModConfig], self.mod_configs)
                )
                rebuild_mod_listbox_from_configs(
                    self.mod_list_box,
                    cast(List[UIModConfig], ordered_mods),
                    _get_mod_name_from_config,
                )
                self.drag_index = new_index
                self.update_patch_instructions()

    def _on_mod_selection_change(self, _ev: Any | None = None) -> None:
        sel = cast(tuple[str, ...], self.mod_list_box.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if not sel:
            self._arrow_frame.place_forget()
            # clear inspector when nothing is selected
            try:
                if getattr(self, "mod_info", None):
                    if self.mod_info:
                        self.mod_info.update_for_config(None)
            except Exception as e:
                logger.debug(
                    "Error updating mod info on config load: %s", e, exc_info=True
                )
                pass
            return
        idx = int(sel[0])
        # get bbox of the selected item (y offset relative to listbox)
        try:
            bbox = self.mod_list_box.bbox(idx)
            if not bbox:
                self._arrow_frame.place_forget()
                return
            _, y, _, _ = bbox
            # place arrow_frame to the right of the listbox row (adjust offsets)
            list_x = self.mod_list_box.winfo_x()
            list_y = self.mod_list_box.winfo_y()
            place_x = list_x + self.mod_list_box.winfo_width() - 40
            place_y = list_y + y + 2
            # disable up/down when at edges
            if idx == 0:
                self._btn_up.state(["disabled"])  # type: ignore [reportUnknownMemberType]
            else:
                self._btn_up.state(["!disabled"])  # type: ignore [reportUnknownMemberType]
            if idx >= self.mod_list_box.size() - 1:
                self._btn_down.state(["disabled"])  # type: ignore [reportUnknownMemberType]
            else:
                self._btn_down.state(["!disabled"])  # type: ignore [reportUnknownMemberType]
            self._arrow_frame.place(x=place_x, y=place_y)
        except Exception:
            self._arrow_frame.place_forget()
            try:
                if getattr(self, "mod_info", None):
                    if self.mod_info:
                        self.mod_info.update_for_config(None)
            except Exception as e:
                logger.debug(
                    "Error updating mod info on mod selection: %s", e, exc_info=True
                )
                pass
            return

        # Update the ModInfoPane with the selected mod config (if available)
        try:
            cfg = self.mod_configs[idx] if 0 <= idx < len(self.mod_configs) else None
            if getattr(self, "mod_info", None):
                if self.mod_info:
                    self.mod_info.update_for_config(cfg)
        except Exception as e:
            logger.debug(
                "Error updating mod info on active mod change: %s", e, exc_info=True
            )
            try:
                if getattr(self, "mod_info", None):
                    if self.mod_info:
                        self.mod_info.update_for_config(None)
            except Exception:
                logger.debug("Non-fatal error updating mod info: %s", e, exc_info=True)
                pass

    def _on_mod_double_click(self, event: Any | None = None) -> None:
        sel = cast(tuple[str, ...], self.mod_list_box.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
        if not sel:
            return
        idx = int(sel[0])
        mod = self.mod_configs[idx]
        mod["Enabled"] = not mod.get("Enabled", True)
        ordered_mods, _ = apply_dependency_resolution(
            cast(List[ModConfig], self.mod_configs)
        )
        rebuild_mod_listbox_from_configs(
            self.mod_list_box,
            cast(List[UIModConfig], ordered_mods),
            _get_mod_name_from_config,
        )
        self.update_patch_instructions()
        self.update_conflict_status()
        self.append_log(
            f"Mod '{mod.get('Name', 'Unknown')}' {'enabled' if mod['Enabled'] else 'disabled'} via double-click."
        )

    def browse_directory(self, var: tk.StringVar) -> None:
        directory = filedialog.askdirectory()
        if directory:
            var.set(safe_norm(directory))

    def browse_file(self, var: tk.StringVar) -> None:
        file_path = filedialog.askopenfilename()
        if file_path:
            var.set(safe_norm(file_path))

    def append_log(self, message: str) -> None:
        timestamp = time.strftime("[%H:%M:%S]")
        self.log_txt.insert(tk.END, f"{timestamp} {message}\n")
        self.log_txt.see(tk.END)
        self.update_idletasks()  # Ensure immediate display

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
            self._refresh_ui_after_load(save_policy=True)
            return

        # 1. Set busy cursor and force UI update
        self.configure(cursor="watch")
        self.update_idletasks()

        # 2. Update session paths
        self.session.game_dir = safe_norm(self.vars["game_dir"].get())
        self.session.mods_dir = safe_norm(self.vars["mods_dir"].get())

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
            # Ensure crucial keys are synchronized
            cfg["Path"] = rmod.path
            cfg["Enabled"] = rmod.is_enabled
            cfg["Valid"] = rmod.is_valid
            cfg["Errors"] = rmod.errors if rmod.errors else None
            cfg["_security_risks"] = rmod.security_risks
            self.mod_configs.append(cfg)

        self.configure(cursor="")
        self._refresh_ui_after_load(save_policy=True)

    def _refresh_ui_after_load(self, save_policy: bool = False) -> None:
        """Updates UI elements based on current self.mod_configs."""
        try:
            # Rebuild instructions from only valid mods
            self.update_patch_instructions()
            # Save the potentially updated list (e.g. new mods added)
            if save_policy:
                try:
                    policy.save_load_order(cast(List[Dict[str, Any]], self.mod_configs))
                except Exception:
                    pass

            # Rebuild listbox
            rebuild_mod_listbox_from_configs(
                self.mod_list_box, self.mod_configs, _get_mod_name_from_config
            )
            # Show a single consolidated error dialog if there are invalid mods
            invalid_count = sum(
                1 for mod in self.mod_configs if not mod.get("Valid", True)
            )
            if invalid_count:
                msg_lines: List[str] = []
                for mod in self.mod_configs:
                    if not mod.get("Valid", True):
                        errors: List[str] = mod.get("Errors") or ["Unknown Error"]
                        error_str = "\n    - ".join(map(str, errors))
                        msg_lines.append(
                            f"[{mod.get('Name', 'Unknown')}]\n    - {error_str}"
                        )
                messagebox.showerror(
                    "Invalid Mods Detected",
                    "Some mods failed validation and were skipped:\n\n"
                    + "\n".join(msg_lines),
                )

            self.update_conflict_status()
            self.append_log(
                f"Loaded {len(self.mod_configs)} mods ({invalid_count} invalid)."
            )

            # Update ModInfoPane to reflect current selection (or clear it)
            try:
                if getattr(self, "mod_info", None) and self.mod_info:
                    sel = cast(tuple[str, ...], self.mod_list_box.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
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
                pass

            self._arrow_frame.place_forget()
        except Exception as e:
            logger.error("Error refreshing UI: %s", e)

    def update_ui_after_rollback(self, message: str) -> None:
        """
        Updates the UI elements after a successful rollback operation.
        Calls rebuild_mod_listbox_from_configs to refresh the mod list
        to reflect the (now clean) state of the work directory.
        :param message: The success message from the rollback operation.
        """
        try:
            # Call the global rebuild function
            rebuild_mod_listbox_from_configs(
                self.mod_list_box, self.mod_configs, _get_mod_name_from_config
            )

            # Update main status and application title
            # Use conflict_label as status_label is not defined
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

    def rollback_working_dir(self) -> None:
        """Preview and selectively restore *.bak files in work_root or remove work_root.
        Debug-hardened: logs key steps, catches errors creating the preview window,
        forces window to top and reports problems via messagebox and log.
        """
        try:
            self.append_log("Rollback: invoked")
        except Exception as e:
            logger.debug(
                "Failed to append initial 'Rollback: invoked' log message: %s",
                e,
                exc_info=True,
            )
        work_root = self.vars.get("game_dir", tk.StringVar()).get()
        if not work_root or not os.path.isdir(work_root):
            messagebox.showinfo("Rollback", f"No working directory found: {work_root}")
            try:
                self.append_log(f"Rollback: no work_root or missing dir: {work_root}")
            except Exception as e:
                logger.debug(
                    "Failed to append 'Rollback: no work_root' log message:f %s",
                    e,
                    exc_info=True,
                )
            return

        # Helper function for consistent working directory removal logic
        def _remove_work_root(
            self: "App", preview_window: Optional[tk.Toplevel] = None
        ) -> None:
            # Check if the work_root is the root directory or too close to it for safety
            if (
                work_root == os.path.expanduser("~")
                or work_root == os.getcwd()
                or len(Path(work_root).parts) < 3
            ):
                # legacy code
                messagebox.showerror(
                    "Security Check Failed",
                    f"Refusing to remove working directory because it is too close to a critical system path: {work_root}",
                )
                self.append_log(
                    "Rollback: SECURITY REFUSED to remove path close to root."
                )
                return

            confirm = messagebox.askyesno(
                "Confirm Remove",
                f"Are you sure you want to permanently remove the entire working directory:\n\n{work_root}?",
            )
            if confirm:
                try:
                    # Explicitly destroy the preview window if it exists before removal
                    if preview_window and preview_window.winfo_exists():
                        preview_window.destroy()

                    shutil.rmtree(work_root)
                    messagebox.showinfo("Rollback", "Working directory removed.")
                    self.append_log(f"Rollback: removed working directory {work_root}")
                    # In a real app, you would clean up UI/state here
                    if hasattr(self, "update_ui_after_rollback"):
                        self.update_ui_after_rollback("Working directory removed.")
                except Exception as e:
                    messagebox.showerror("Rollback Error", f"Remove failed: {e}")
                    self.append_log(f"Rollback error (remove): {e}")

        # Gather bak files (relative)
        bak_list: List[str] = []
        try:
            for root, _, files in os.walk(work_root):
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
                logger.debug(
                    "Failed to append log after work_root scan failure: %s",
                    e,
                    exc_info=True,
                )
            return

        try:
            self.append_log(f"Rollback: found {len(bak_list)} .bak files")
        except Exception as e:
            logger.debug(
                "Failed to append log message for bak file count: %s", e, exc_info=True
            )

        # If no .bak files, prompt for directory removal and return
        if not bak_list:
            resp = messagebox.askyesno(
                "Rollback",
                f"No .bak files found in {work_root}. Remove entire working directory?",
            )
            if resp:
                _remove_work_root(self)
            return

        # Create preview window robustly
        preview: Optional[tk.Toplevel] = None
        try:
            parent = self
            preview = tk.Toplevel(parent)
            preview.title("Rollback — Preview .bak files")
            preview.geometry("700x400")
            preview.transient(parent)
            preview.lift()  # type: ignore [reportUnknownMemberType]
            preview.deiconify()
            try:
                # Force to top briefly, then release
                preview.attributes("-topmost", True)  # type: ignore [reportUnknownMemberType]
                preview.after(
                    200,
                    lambda: preview.attributes("-topmost", False),  # type: ignore [reportUnknownMemberType]
                )
            except Exception as e:
                logger.debug(
                    "Ignored error setting rollback preview window topmost: %s",
                    e,
                    exc_info=True,
                )
        except Exception as e:
            # If Toplevel creation fails we must show the error and log it
            messagebox.showerror("Rollback Error", f"Cannot create preview window: {e}")
            try:
                self.append_log(f"Rollback error creating preview window: {e}")
            except Exception as e:
                logger.debug(
                    "Failed to append log after preview window creation failure: %s",
                    e,
                    exc_info=True,
                )
            return

        lbl = tk.Label(
            preview,
            text="Select .bak files to restore (checked) or clean all backups (Dangerous).",
        )
        lbl.pack(anchor="w", padx=8, pady=(8, 0))

        # scrolling checkbox list setup
        frm = tk.Frame(preview)
        frm.pack(fill="both", expand=True, padx=8, pady=8)
        canvas = tk.Canvas(frm)
        sb = tk.Scrollbar(
            frm,
            orient="vertical",
            command=canvas.yview,  # type: ignore[reportUnknownArgumentType]
        )
        inner = tk.Frame(canvas)
        # Bind inner frame size to canvas scroll region
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        vars_map: Dict[str, tk.BooleanVar] = {}
        for rel in sorted(bak_list):
            v = tk.BooleanVar(value=True)
            cb = tk.Checkbutton(inner, text=rel, variable=v, anchor="w", justify="left")
            cb.pack(fill="x", anchor="w")
            vars_map[rel] = v

        btn_frame = tk.Frame(preview)
        btn_frame.pack(fill="x", padx=8, pady=8)

        def _restore_selected() -> None:
            selected: List[str] = [r for r, var in vars_map.items() if var.get()]
            if not selected:
                messagebox.showinfo("Rollback", "No files selected to restore.")
                return
            confirm = messagebox.askyesno(
                "Confirm Restore", f"Restore {len(selected)} files from .bak?"
            )
            if not confirm:
                return
            restored = 0
            errors: List[str] = []
            for rel in selected:
                bak = os.path.join(work_root, rel)
                orig = os.path.join(work_root, rel[:-4])
                try:
                    # safety: ensure target within work_root
                    # This check is vital against path traversal exploits
                    if not os.path.commonpath([work_root, orig]).startswith(
                        os.path.normpath(work_root)
                    ):
                        raise RuntimeError("path outside work_root detected")

                    atomic_write_copy(bak, orig)
                    restored += 1
                    self.append_log(
                        f"Rollback: restored {rel} -> {os.path.relpath(orig, work_root)}"
                    )
                except Exception as e:
                    errors.append(f"{rel}: {e}")
                    self.append_log(f"Rollback error restoring {rel}: {e}")

            # Show summary message and destroy preview
            message = f"Restored {restored} files."
            if errors:
                message += f" {len(errors)} errors occurred. See log."
            messagebox.showinfo("Rollback", message)
            if preview:
                preview.destroy()
            if hasattr(self, "update_ui_after_rollback"):
                self.update_ui_after_rollback(message)

        # --- Button creation and packing ---
        btn_restore = tk.Button(
            btn_frame,
            text=f"Restore Selected ({len(bak_list)})",
            command=_restore_selected,
            bg="#4CAF50",
            fg="white",  # Green for restoration
            relief="raised",
        )
        btn_restore.pack(side="left", padx=(0, 4), expand=True, fill="x")

        btn_remove = tk.Button(
            btn_frame,
            text="Clean All Backups (Dangerous)",
            command=lambda: _remove_work_root(
                self, preview
            ),  # Pass preview to helper for destruction
            bg="#F44336",
            fg="white",  # Red for destructive action
            relief="raised",
        )
        btn_remove.pack(side="right", padx=(4, 0), expand=True, fill="x")

        # Make the window modal and wait for it to close
        if preview:
            preview.grab_set()
            parent.wait_window(preview)

        btn_cancel = tk.Button(
            btn_frame,
            text="Cancel",
            command=preview.destroy if preview else lambda: None,
        )
        btn_cancel.pack(side="right", padx=6)

        # final trace entry
        try:
            self.append_log("Rollback: preview window shown")
        except Exception as e:
            logger.debug(
                "Failed to append log message after rollback preview window creation: %s",
                e,
                exc_info=True,
            )

    def create_support_bundle(self) -> None:
        """Create a support zip containing logs and runtime_manifest from work_root (if present)."""
        try:
            work_root = safe_norm(self.vars["game_dir"].get())
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
                policy.save_load_order(cast(List[Dict[str, Any]], self.mod_configs))
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
                plan = generate_patch_plan(mod_path, mod_config)
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

    def update_conflict_status(self) -> None:
        """Checks for conflicts and updates the GUI label."""
        conflicts = analyze_mods_for_conflicts(cast(List[ModConfig], self.mod_configs))
        if conflicts:
            count = len(conflicts)
            self.conflict_label.configure(
                text=f"{count} Conflict{'s' if count > 1 else ''} Detected! (Click to Resolve)",
                fg="red",
            )
            self.conflict_label.bind("<Button-1>", lambda e: self.open_resolve_dialog())
        else:
            self.conflict_label.configure(text="No Conflicts Detected", fg="green")
            self.conflict_label.unbind("<Button-1>")

    def open_resolve_dialog(self) -> None:
        """Opens the conflict resolution dialog."""
        conflicts = analyze_mods_for_conflicts(cast(List[ModConfig], self.mod_configs))
        if not conflicts:
            messagebox.showinfo(
                "No Conflicts",
                "No critical conflicts were found. You can reorder mods using Move Up/Down.",
            )
            return

        ResolveDialog(
            cast(tk.Widget, self),
            conflicts,
            self.mod_configs,
            self.resolve_dialog_callback,
        )

    def resolve_dialog_callback(self, new_mod_configs: List[UIModConfig]) -> None:
        """Called when the ResolveDialog closes with 'OK'."""
        self.load_mods(mod_configs_override=new_mod_configs)
        messagebox.showinfo(
            "Resolution Complete", "Mod order updated. Patch instructions regenerated."
        )
        # Save the resolution to policy
        policy.save_load_order(cast(List[Dict[str, Any]], self.mod_configs))

    def move_selected_mod(self, direction: int) -> None:
        """Moves the selected mod up (-1) or down (1) in the list."""
        try:
            selection = cast(tuple[str, ...], self.mod_list_box.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
            if not selection:
                return
            index = int(selection[0])
            new_index = index + direction

            if 0 <= new_index < self.mod_list_box.size():
                # Update internal config order
                mod_config_to_move = self.mod_configs.pop(index)
                self.mod_configs.insert(new_index, mod_config_to_move)

                ordered_mods, _ = apply_dependency_resolution(
                    cast(List[ModConfig], self.mod_configs)
                )
                rebuild_mod_listbox_from_configs(
                    self.mod_list_box,
                    cast(List[UIModConfig], ordered_mods),
                    _get_mod_name_from_config,
                )

                self.update_patch_instructions()
                self.update_conflict_status()
                # Auto-save policy on move
                policy.save_load_order(cast(List[Dict[str, Any]], self.mod_configs))
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

    def select_mod_in_main_list(self, mod_name: str) -> None:
        """Select and focus a mod by name in the main mod list box."""
        for idx, m in enumerate(self.mod_configs):
            if m.get("Name") == mod_name:
                try:
                    self.mod_list_box.selection_clear(0, tk.END)
                    self.mod_list_box.selection_set(idx)
                    self.mod_list_box.see(idx)
                    self.mod_list_box.focus_set()
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
        """Enable/disable the currently selected mod. Disabled mods are skipped and greyed out."""
        try:
            sel = cast(tuple[str, ...], self.mod_list_box.curselection())  # type: ignore[no-untyped-call, reportUnknownMemberType]
            if not sel:
                return
            idx = int(sel[0])
            mod = self.mod_configs[idx]
            # default to True
            currently = bool(mod.get("Enabled", True))
            mod["Enabled"] = not currently
            ordered_mods, _ = apply_dependency_resolution(
                cast(List[ModConfig], self.mod_configs)
            )
            rebuild_mod_listbox_from_configs(
                self.mod_list_box,
                cast(List[UIModConfig], ordered_mods),
                _get_mod_name_from_config,
            )
            self.append_log(
                f"Mod '{mod.get('Name')}' {'enabled' if mod['Enabled'] else 'disabled'}."
            )
            # regenerate instructions and conflicts
            self.update_patch_instructions()
            self.update_conflict_status()
            # Auto-save policy on toggle
            policy.save_load_order(cast(List[Dict[str, Any]], self.mod_configs))
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

    def run_patcher_action(self) -> None:
        """
        Orchestrates the patching process via the Session (v2.0 Architecture).
        """
        if not self.mod_configs:
            messagebox.showwarning("No Mods", "No mods are loaded to patch.")
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
            # self.session.apply_changes yields log strings.
            # We pass 'self' as the conflict_delegate because App implements resolve().
            for msg in self.session.apply_changes(conflict_delegate=self):
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

    def resolve(self, file_path: str, orig_text: str, new_text: str) -> Optional[str]:
        """
        Thread-safe implementation of ConflictDelegate.resolve.
        Called by the background patcher thread; executes UI on main thread and waits.
        """
        result_holder: Dict[str, Optional[str]] = {"val": None}
        done_event = threading.Event()

        def _ui_ask() -> None:
            try:
                # HunkViewer must be imported from gmos.ui (usually available in scope)
                hv = HunkViewer(self, orig_text, new_text)
                result_holder["val"] = hv.show_modal()
            except Exception as e:
                logger.error("Failed to open HunkViewer: %s", e)
            finally:
                done_event.set()

        # Schedule the dialog on the main thread
        self.after(0, _ui_ask)

        # Block the worker thread here until the user closes the dialog
        done_event.wait()

        return result_holder["val"]

    def start_game_action(self) -> None:
        """Launches the game executable from the working directory to ensure all modded files are used."""

        # 1. Set busy cursor and force UI update while we prepare paths
        self.configure(cursor="watch")
        self.update_idletasks()

        game_dir = safe_norm(self.vars.get("game_dir", tk.StringVar()).get())
        executable_name = self.vars["game_executable"].get()
        launch_override = self.vars["launch_override"].get()
        game_exe_path: str | None = None

        try:
            if launch_override:
                # If an override is provided, use it directly
                game_exe_path = safe_norm(launch_override)
                if not os.path.exists(game_exe_path):
                    raise FileNotFoundError(
                        f"Launch override executable not found: {game_exe_path}"
                    )
            else:
                # 2. Define and validate paths
                # Force absolute path.
                # This solves the issue where os.path.join(".", "game.exe") results in ".\game.exe",
                # which shutil.which() fails to find because "." is not in the system PATH.
                game_exe_path = os.path.abspath(os.path.join(game_dir, executable_name))

            # 3. Determine launch command (Godot-specific arguments included for context)
            command: List[str] = [game_exe_path, "--path", game_dir]

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
            _safe_spawn(
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
        Simulate the patch in a temp dir and produce diffs for ALL modified files.
        Each file header lists which mod(s) referenced that file target.
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
        self.append_log("--- Starting Patch Simulation & Diff (full) ---")
        self.log_notebook.select(1)  # type: ignore [reportUnknownMemberType]
        self.diff_txt.delete("1.0", tk.END)
        # Offload to background
        get_io_executor().submit(self._simulate_worker, game_dir)

    def _simulate_worker(self, game_dir: str) -> None:
        """Background worker for simulation and diff generation."""
        try:
            # 1. Pre-analysis: Identify which mods touch which relative paths
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

            # 2. Run simulation in a temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_work_root = os.path.join(temp_dir, "sim_work")
                Path(temp_work_root).mkdir(parents=True, exist_ok=True)
                sim_log = run_patcher(temp_work_root, self.instructions)

                # 3. Determine which files were actually modified (patched_rel_paths)
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

                    if not patched_rel_paths:
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
                            self.after(
                                0,
                                lambda: self.append_log(
                                    f"Fallback: parsed sim_log for {len(patched_rel_paths)} files."
                                ),
                            )

                except Exception as sim_exc:
                    logger.exception("Failed to read manifest or parse sim_log.")
                    err_msg = f"Warning: failed to read runtime_manifest.json or parse sim_log: {str(sim_exc)}"
                    self.after(0, lambda: self.append_log(err_msg))
                    if not patched_rel_paths:
                        self.after(
                            0,
                            lambda: self.diff_txt.insert(
                                tk.END, "No files were modified during the simulation."
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

                # 4. Generate diffs
                combined_parts: List[str] = []
                for rel in dedup_paths:
                    orig_path = os.path.join(
                        game_dir, rel
                    )  # For diffing, we assume game_dir is vanilla-ish?
                    # Issue: In the single folder model, game_dir might be dirty.
                    # Simulation runs in empty temp dir, so reading orig_path from game_dir
                    # might compare against already-patched files if revert failed.
                    # However, since this is simulation, we can't revert the real game dir.
                    # We have to trust that the user understands 'Diff' shows change relative to CURRENT disk state.
                    patched_path = os.path.join(temp_work_root, rel)

                    # Create file header with mods info
                    header = f"\n===== File: {rel} =====\n"
                    mods = touched_by.get(rel, set())
                    mods_list = sorted(mods)
                    header += f"Mods touching this file: {', '.join(mods_list) if mods_list else 'unknown'}\n\n"
                    self.diff_txt.insert(tk.END, header)
                    combined_parts.append(header)

                    # Read files safely (ignoring encoding errors for diff)
                    try:
                        with open(
                            orig_path, "r", encoding="utf-8", errors="ignore"
                        ) as f:
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

                    # Generate unified diff
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

                    # Incremental UI update
                    def _update_diff_ui(text: str) -> None:
                        self.diff_txt.insert(tk.END, text + "\n")

                    self.after(0, _update_diff_ui, diff_text)
                    combined_parts.append(diff_text + "\n")

                # 5. Persist combined diff into dryrun artifact
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
                    self.after(0, lambda: self.append_log("Dryrun artifact saved."))
                except Exception:
                    logger.exception(
                        "Failed to save combined diff into dryrun artifact"
                    )

            # Jump to top of diff tab after completion
            self.after(0, lambda: self.log_notebook.select(1))  # type: ignore [reportUnknownMemberType]

        except Exception as e:
            # 6. Handle critical errors during the simulation/diff process
            logger.exception("Critical error during simulate_and_diff_action: %s", e)
            err_str = str(e)

            def _show_err() -> None:
                messagebox.showerror(
                    "Simulation Error",
                    f"An unexpected error occurred during the patch simulation:\n\n{err_str}\n\nPlease check the log file for details.",
                )

            self.after(0, _show_err)

        finally:
            # 7. Always reset the cursor
            self.after(0, lambda: self.configure(cursor=""))

    def save_diff_to_file(self) -> None:
        """Prompt user and save the current Diff Preview content to a .patch file."""
        try:
            content = self.diff_txt.get("1.0", tk.END)
        except Exception:
            messagebox.showerror("Save Diff", "No diff content available.")
            return

        if not content.strip():
            messagebox.showinfo("Save Diff", "No diff content to save.")
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
                logger.debug(
                    "Failed to show error messagebox after diff save failure: %s",
                    e,
                    exc_info=True,
                )

    def view_runtime_manifest(self) -> None:
        """Open runtime_manifest.json from work_root in system viewer or show an error."""
        work_root = safe_norm(self.vars["game_dir"].get())
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

    def open_developer_tools(self) -> None:
        """Opens the Developer Tools window, ensuring only one instance exists."""
        # Prevent multiple instances
        if self.dev_tools_window is not None and self.dev_tools_window.winfo_exists():
            self.dev_tools_window.lift()  # type: ignore[reportUnknownMemberType]
            self.dev_tools_window.focus_force()
            return

        if not self.vars["game_dir"].get():
            messagebox.showerror("Error", "Please select a game directory first.")
            return

        self.dev_tools_window = DeveloperToolsDialog(self)

    def on_close(self) -> None:
        if getattr(self, "_is_busy", False):
            messagebox.showwarning(
                "Cannot Close",
                "GMOS is currently working (patching/restoring).\nPlease wait until the process finishes to prevent file corruption.",
            )
            return
        self.save_config()
        self.destroy()
        os._exit(0)


###############################################################################
# Progress Bar Dialog (Non-blocking, Updatable)
###############################################################################


class ProgressBarDialog(tk.Toplevel):
    """
    Lightweight non-blocking progress dialog.
    Use .update_progress(percent, message) to update.
    Use .close() when done.
    """

    def __init__(
        self, parent: tk.Misc, title: str = "Working...", max_value: int = 100
    ):
        super().__init__(parent)
        utils.load_and_apply_app_icon_to_toplevel(self)
        self.title(title)
        self.resizable(False, False)
        self.max_value = max_value

        # Window placement
        self.geometry(
            "+{}+{}".format(parent.winfo_rootx() + 80, parent.winfo_rooty() + 80)
        )

        # UI
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

        # Prevent closing mid-operation
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        self.update_idletasks()

    def update_progress(self, value: int, message: Optional[str] = None) -> None:
        """Non-blocking UI update."""
        if message is not None:
            self.label.config(text=message)
        self.pb["value"] = min(value, self.max_value)
        self.update_idletasks()

    def close(self) -> None:
        """Close dialog safely."""
        try:
            self.destroy()
        except Exception:
            pass


###############################################################################
# Replace operation UI integration
###############################################################################


def replace_with_progress(
    parent: tk.Misc,
    src: str,
    dst: str,
    *,
    attempts: int = 6,
    title: str = "Replacing file...",
) -> tuple[Any, threading.Event, Optional[threading.Thread]]:
    """
    Convenience helper: run a robust atomic replace (with retries) in background
    while showing a ProgressBarDialog. Returns a tuple (diag, cancel_event, thread)
    where:
      - diag is the ReplaceDiagnostics object returned immediately by start_replace_task
        (it will be updated by the worker).
      - cancel_event is a threading.Event the caller can set to request cancellation.
      - thread is the worker Thread object (daemon).

    Notes:
      - parent must be a Tk root or any widget for .after scheduling.
      - Errors and finalization are marshalled back into the GUI thread via parent.after.
    """
    # Local import to avoid requiring tkinter at module import time in headless contexts.
    import threading

    try:
        # Prefer the progress dialog defined above
        dlg: Union[ProgressDialog, Any] = ProgressDialog(
            parent, title=title
        )  # Annotated union
    except Exception:
        # If UI unavailable, fallback to a no-op object with similar API
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

    # progress_cb will be called from worker thread; marshal to GUI
    def progress_cb(frac: float) -> None:
        try:
            # convert to 0..100
            v = max(0.0, min(1.0, float(frac)))
        except Exception:
            v = 0.0
        try:
            parent.after(
                0,
                lambda: dlg.set_text(f"Attempting replace... {int(v * 100)}%"),
            )
        except Exception:
            try:
                dlg.set_text(f"Attempting replace... {int(v * 100)}%")
            except Exception:
                pass

    # We must import ReplaceDiagnostics locally to type this callback
    from gmos.io import ReplaceDiagnostics

    def done_cb(diag: ReplaceDiagnostics) -> None:
        def _finish() -> None:
            try:
                # show result briefly then close
                if getattr(diag, "success", False):
                    dlg.set_text("Replace succeeded")
                else:
                    msg = "Replace failed"
                    if getattr(diag, "last_exception", None):
                        msg += f": {type(diag.last_exception).__name__}"
                    dlg.set_text(msg)
                # close after short delay so user can read status
                parent.after(350, dlg.close)
            except Exception:
                try:
                    dlg.close()
                except Exception:
                    pass

        try:
            parent.after(0, _finish)
        except Exception:
            _finish()

    # Start the dialog and background task
    try:
        parent.after(0, dlg.start)
    except Exception:
        try:
            dlg.start()
        except Exception:
            pass

    # This will return a ReplaceDiagnostics-like object immediately.
    try:
        from gmos.io import ReplaceDiagnostics, start_replace_task

        # adapt to the expected signature: start_replace_task(..., done_cb, progress_cb, cancel_event, attempts, base_delay, max_sleep, poll_interval)
        diag, thread = start_replace_task(
            src,
            dst,
            done_cb=done_cb,
            progress_cb=progress_cb,
            cancel_event=cancel_event,
            attempts=attempts,
            # Use conservative defaults consistent with io module's helper
            base_delay=0.03,
            max_sleep=0.5,
            poll_interval=0.08,
        )
        # return diag and the cancel_event so callers can request cancellation
        return diag, cancel_event, thread
    except Exception as e:
        # If start_replace_task is unexpectedly missing or raises at import time,
        # fall back to a simple synchronous replace in a background thread.
        try:
            logger.debug("start_replace_task import failed, falling back: %s", e)
        except Exception:
            pass

        def _sync_worker(diag_obj: ReplaceDiagnostics) -> None:
            from gmos.io import replace_with_retries

            diag_obj.start_time = time.time()

            try:
                replace_with_retries(src, dst)
                # success
                diag_obj.success = True
                diag_obj.end_time = time.time()
                done_cb(diag_obj)
            except Exception as exc:
                diag_obj.last_exception = exc
                diag_obj.end_time = time.time()
                done_cb(diag_obj)

        diag = ReplaceDiagnostics(
            src=src,
            dst=dst,
            attempts_allowed=0,  # Fallback path
        )

        thr = threading.Thread(
            target=_sync_worker, args=(diag,), daemon=True, name="gmos-replace-fallback"
        )
        thr.start()
        diag = type(
            "Diag", (), {"success": None, "last_exception": None, "thread": thr}
        )()
        return diag, cancel_event, thr


###############################################################################
# Helper / Convenience API
###############################################################################


def show_progress(
    parent: tk.Misc, title: str = "Working...", max_value: int = 100
) -> "ProgressBarDialog":
    """
    Returns a ProgressBarDialog instance.
    Caller should manually call .update_progress(...)
    and .close() when finished.
    """
    dlg = ProgressBarDialog(parent, title=title, max_value=max_value)
    return dlg
