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
import shutil
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, cast

from gmos import utils
from gmos.state import policy, profiles
from gmos.ui.widgets import AutoScrollbar, NameInputDialog, ToolTip, UIModConfig

if TYPE_CHECKING:
    from gmos.ui.app import App


class ProfileManagerDialog(tk.Toplevel):
    """Master-Detail view for managing mod profiles."""

    def __init__(self, parent: tk.Misc, app: "App"):
        super().__init__(parent)
        self.app = app
        self.title("Profile Manager")
        utils.load_and_apply_app_icon_to_toplevel(self)
        utils.setup_child_window(self, parent, width=850, height=500, modal=True)
        self.bind("<<ThemeChanged>>", lambda e: utils.apply_window_theme(self))
        self.profiles_dir = os.path.join(self.app.vars["game_dir"].get(), "profiles")
        os.makedirs(self.profiles_dir, exist_ok=True)

        # State Variables
        self.vars = {
            "isolate_data": tk.BooleanVar(value=False),
        }
        self.current_profile_file: Optional[str] = None
        self.var_profile_name = tk.StringVar()

        def _dummy_trace(*args: Any) -> None:
            pass

        self.var_profile_name.trace_add("write", _dummy_trace)
        # Load Icons
        self._load_icons()

        self._setup_ui()
        self._refresh_list()

    def _load_icons(self) -> None:
        self.ico_plus = utils.load_icon("plus.png", size=(16, 16))
        self.ico_import = utils.load_icon("file-down.png", size=(16, 16))
        self.ico_export = utils.load_icon("file-up.png", size=(16, 16))
        self.ico_copy = utils.load_icon("copy.png", size=(16, 16))
        self.ico_trash = utils.load_icon("trash-2.png", size=(16, 16))
        self.ico_play = utils.load_icon(
            "play.png", size=(16, 16), force_color_variant="light"
        )

    def _setup_ui(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        left_frame = ttk.Frame(paned, width=240)
        cast(Any, paned).add(left_frame, weight=1)

        toolbar_left = ttk.Frame(left_frame)
        toolbar_left.pack(fill="x", pady=(0, 5))

        try:
            btn_create = ttk.Button(
                toolbar_left,
                text="Create",
                image=self.ico_plus or "",
                compound="left",
                command=lambda: NameInputDialog(
                    cast(tk.Misc, self),
                    title="New Profile",
                    prompt="Enter Profile Name:",
                    callback=self._create_new_profile,
                ),
                style="primary.TButton",
            )
        except Exception:
            btn_create = ttk.Button(
                toolbar_left,
                text="Create",
                image=self.ico_plus or "",
                compound="left",
                command=lambda: NameInputDialog(
                    cast(tk.Misc, self),
                    title="New Profile",
                    prompt="Enter Profile Name:",
                    callback=self._create_new_profile,
                ),
            )
        btn_create.pack(side="left", fill="x", expand=True, padx=(0, 2))

        btn_import = ttk.Button(
            toolbar_left,
            image=self.ico_import or "",
            command=self._import_profile,
            width=3,
        )
        btn_import.pack(side="left", padx=(2, 0))
        ToolTip(btn_import, "Import Profile from JSON")

        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("active_mods", "last_used"),
            show="tree headings",
            selectmode="browse",
        )

        self.tree.column("#0", width=150)
        self.tree.heading("#0", text="Profile Name", anchor="w")
        self.tree.column("active_mods", width=80, anchor="center")
        self.tree.heading("active_mods", text="Active Mods")
        self.tree.column("last_used", width=120, anchor="e")
        self.tree.heading("last_used", text="Last Used")

        vsb = AutoScrollbar(
            tree_frame, orient="vertical", command=cast(Any, self.tree).yview
        )
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda e: self._load_selected())

        self.right_frame = ttk.Frame(paned, padding=(15, 0, 0, 0))
        cast(Any, paned).add(self.right_frame, weight=3)

        header_frame = ttk.Frame(self.right_frame)
        header_frame.pack(fill="x", pady=(0, 15))

        # 1. Action Column (Right)
        action_col = ttk.Frame(header_frame)
        action_col.pack(side="right", anchor="n")

        # 2. Header Content (Left)
        self.header_container = ttk.Frame(header_frame)
        self.header_container.pack(side="left", fill="both", expand=True, anchor="n")

        # Editable Name Label
        self.lbl_header_name = ttk.Label(
            self.header_container,
            textvariable=self.var_profile_name,
            font=("Segoe UI", 16, "bold"),
            cursor="hand2",
        )
        self.lbl_header_name.pack(fill="x", anchor="w")
        self.lbl_header_name.bind("<Button-1>", self._enable_header_edit)

        # Editable Name Entry (Hidden by default)
        self.ent_header_name = ttk.Entry(
            self.header_container,
            textvariable=self.var_profile_name,
            font=("Segoe UI", 16, "bold"),
        )
        self.ent_header_name.bind("<Return>", self._finish_header_edit)
        self.ent_header_name.bind("<FocusOut>", self._finish_header_edit)

        self.btn_export = ttk.Button(
            action_col,
            image=self.ico_export or "",
            command=self._export_selected,
            style="Link.TButton",
            width=3,
        )
        self.btn_export.pack(side="left", padx=2)
        ToolTip(self.btn_export, "Export to JSON")

        self.btn_copy = ttk.Button(
            action_col,
            image=self.ico_copy or "",
            command=self._copy_profile,
            style="Link.TButton",
            width=3,
        )
        self.btn_copy.pack(side="left", padx=2)
        ToolTip(self.btn_copy, "Duplicate Profile")

        self.btn_delete = ttk.Button(
            action_col,
            image=self.ico_trash or "",
            command=self._delete_profile,
            style="Link.TButton",
            width=3,
        )
        self.btn_delete.pack(side="left", padx=2)
        ToolTip(self.btn_delete, "Delete (Cannot be undone)")

        ttk.Separator(self.right_frame, orient="horizontal").pack(
            fill="x", pady=(0, 15)
        )

        self.content_area = ttk.Frame(self.right_frame)
        self.content_area.pack(fill="both", expand=True)

        lbl_iso = ttk.Label(
            self.content_area, text="Configuration", font=("Segoe UI", 10, "bold")
        )
        lbl_iso.pack(anchor="w", pady=(0, 5))

        self.chk_isolate = ttk.Checkbutton(
            self.content_area,
            text="Isolate Profile Data (Saves & Configs)",
            variable=self.vars["isolate_data"],
            command=self._save_changes_to_selected,
        )
        self.chk_isolate.pack(fill="x", pady=2, padx=10)
        ToolTip(
            self.chk_isolate,
            "Redirects save games and game configurations to a profile-specific folder.\nKeeps your profile data safely isolated.",
        )

        ttk.Label(
            self.content_area, text="Data Management", font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(20, 5))

        btn_transfer = ttk.Button(
            self.content_area,
            text="Import Saves from another Profile...",
            command=self._transfer_saves,
        )
        btn_transfer.pack(anchor="w", padx=10)

        footer_frame = ttk.Frame(self.right_frame)
        footer_frame.pack(side="bottom", fill="x", pady=10)

        try:
            self.btn_load = ttk.Button(
                footer_frame,
                text="Load Profile",
                image=self.ico_play or "",
                compound="left",
                command=self._load_selected,
                style="success.TButton",
            )
        except Exception:
            self.btn_load = ttk.Button(
                footer_frame,
                text="Load Profile",
                image=self.ico_play or "",
                compound="left",
                command=self._load_selected,
            )
        self.btn_load.pack(fill="x", ipady=5)

        self._toggle_detail_view(False)

    def _toggle_detail_view(self, enabled: bool) -> None:
        # Recurse and disable/enable
        for child in self.right_frame.winfo_children():
            if isinstance(child, ttk.Widget):
                try:
                    child.state(["!disabled"] if enabled else ["disabled"])
                except Exception:
                    pass

        # Explicitly handle buttons
        s = "normal" if enabled else "disabled"
        btn_list: List[Any] = [
            self.btn_export,
            self.btn_copy,
            self.btn_delete,
            self.btn_load,
            self.chk_isolate,
        ]
        for btn in btn_list:
            btn.configure(state=s)

        if not enabled:
            self.var_profile_name.set("Select a Profile")
            self.lbl_header_name.configure(cursor="arrow")
            self.lbl_header_name.unbind("<Button-1>")
            self.vars["isolate_data"].set(False)
        else:
            self.lbl_header_name.configure(cursor="hand2")
            self.lbl_header_name.bind("<Button-1>", self._enable_header_edit)

    def _on_select(self, event: Optional["tk.Event[Any]"] = None) -> None:
        """Populate Detail View based on selection."""
        sel = self.tree.selection()
        if not sel:
            self._toggle_detail_view(False)
            self.current_profile_file = None
            return

        self.current_profile_file = sel[0]
        filename = sel[0]
        display_name = filename.replace(".json", "")

        # Enable UI
        self._toggle_detail_view(True)
        self.var_profile_name.set(display_name)

        # Reset edit state just in case
        self.ent_header_name.pack_forget()
        self.lbl_header_name.pack(fill="x", anchor="w")

        # Load Settings
        path = os.path.join(self.profiles_dir, filename)
        try:
            data = profiles.load_profile_from_disk(path)

            iso = data.get("isolation", {"isolate_data": False})
            self.vars["isolate_data"].set(bool(iso.get("isolate_data", False)))
            # Update Load Button Text with Mod Count context
            mod_count = len(data.get("mods", []))
            self.btn_load.configure(text=f"Load Profile ({mod_count} mods)")

        except Exception:
            self.vars["isolate_data"].set(False)

    def _save_changes_to_selected(self) -> None:
        """Writes checkbox state back to disk immediately."""
        if not self.current_profile_file:
            return
        path = os.path.join(self.profiles_dir, self.current_profile_file)
        try:
            data = profiles.load_profile_from_disk(path)
            data["isolation"] = {
                "isolate_data": self.vars["isolate_data"].get(),
            }
            profiles.save_profile_to_disk(data, path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to update settings: {e}")

    def _enable_header_edit(self, _event: Optional["tk.Event[Any]"] = None) -> None:
        if not self.current_profile_file:
            return
        self.lbl_header_name.pack_forget()
        self.ent_header_name.pack(fill="x")
        self.ent_header_name.focus_set()

    def _finish_header_edit(self, _event: Optional["tk.Event[Any]"] = None) -> None:
        self.ent_header_name.pack_forget()
        self.lbl_header_name.pack(fill="x", anchor="w")

        if not self.current_profile_file:
            return

        old_name = self.current_profile_file.replace(".json", "")
        new_name = self.var_profile_name.get().strip()

        if not new_name or new_name == old_name:
            self.var_profile_name.set(old_name)  # Revert
            return

        new_filename = f"{utils.sanitize_filename(new_name)}.json"
        old_path = os.path.join(self.profiles_dir, self.current_profile_file)
        new_path = os.path.join(self.profiles_dir, new_filename)

        if os.path.exists(new_path):
            messagebox.showerror("Error", f"Profile '{new_name}' already exists.")
            self.var_profile_name.set(old_name)
            return

        try:
            os.rename(old_path, new_path)
            self._refresh_list()
            if self.tree.exists(new_filename):
                self.tree.selection_set(new_filename)
        except Exception as e:
            messagebox.showerror("Rename Error", str(e))
            self.var_profile_name.set(old_name)

    def _refresh_list(self) -> None:
        # Preserve selection if possible
        selected_id = self.tree.selection()[0] if self.tree.selection() else None

        self.tree.delete(*self.tree.get_children())
        if not os.path.exists(self.profiles_dir):
            return

        for f in os.listdir(self.profiles_dir):
            if f.endswith(".json"):
                name = f.replace(".json", "")
                path = os.path.join(self.profiles_dir, f)
                try:
                    data = profiles.load_profile_from_disk(path)
                    active_count = sum(
                        1 for m in data.get("mods", []) if m.get("enabled")
                    )
                    last_used = data.get("last_used_utc") or data.get(
                        "timestamp_utc", ""
                    )
                    if last_used:
                        try:
                            dt = datetime.datetime.strptime(
                                last_used, "%Y-%m-%dT%H:%M:%SZ"
                            )
                            last_used_str = dt.strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            last_used_str = last_used
                    else:
                        last_used_str = "Never"
                    self.tree.insert(
                        "",
                        "end",
                        iid=f,
                        text=name,
                        values=(active_count, last_used_str),
                    )
                except Exception:
                    self.tree.insert("", "end", iid=f, text=name, values=("?", "Error"))

        if selected_id and self.tree.exists(selected_id):
            self.tree.selection_set(selected_id)
        elif self.tree.get_children():
            # Select first by default for better UX
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)

    # --- Actions ---

    def _create_new_profile(self, name: str) -> None:
        filename = f"{utils.sanitize_filename(name)}.json"
        path = os.path.join(self.profiles_dir, filename)

        if os.path.exists(path):
            if not messagebox.askyesno(
                "Overwrite", f"Profile '{name}' exists. Overwrite?"
            ):
                return

        # Snapshot current state
        current_mods = cast(Sequence[Dict[str, Any]], self.app.mod_configs)
        data = profiles.create_profile_data(
            list(current_mods), self.app.cfg, description=f"Profile: {name}"
        )

        try:
            profiles.save_profile_to_disk(data, path)
            self._refresh_list()
            # Auto-select the new profile
            if self.tree.exists(filename):
                self.tree.selection_set(filename)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _copy_profile(self) -> None:
        if not self.current_profile_file:
            return
        src_path = os.path.join(self.profiles_dir, self.current_profile_file)
        old_name = self.current_profile_file.replace(".json", "")

        def _do_copy(new_name: str) -> None:
            try:
                data = profiles.load_profile_from_disk(src_path)
                data["description"] = f"Copy of {old_name}"
                new_filename = f"{utils.sanitize_filename(new_name)}.json"
                profiles.save_profile_to_disk(
                    data, os.path.join(self.profiles_dir, new_filename)
                )
                self._refresh_list()
                if self.tree.exists(new_filename):
                    self.tree.selection_set(new_filename)
            except Exception as e:
                messagebox.showerror("Copy Error", str(e))

        NameInputDialog(
            cast(tk.Misc, self),
            title="Copy Profile",
            prompt="Enter Profile Name:",
            callback=_do_copy,
            default_name=f"{old_name} - Copy",
        )

    def _delete_profile(self) -> None:
        if not self.current_profile_file:
            return
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Are you sure you want to delete '{self.current_profile_file}'?\nThis cannot be undone.",
        ):
            return

        path = os.path.join(self.profiles_dir, self.current_profile_file)
        try:
            os.remove(path)
            self._refresh_list()
            self.current_profile_file = None
            self._toggle_detail_view(False)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _load_selected(self) -> None:
        if not self.current_profile_file:
            return

        path = os.path.join(self.profiles_dir, self.current_profile_file)
        try:
            profile = profiles.load_profile_from_disk(path)

            current_mods = cast(List[Dict[str, Any]], self.app.mod_configs)
            profile_mods = profile.get("mods", [])
            current_mod_names = {m.get("Name") for m in current_mods}
            if profile_mods:
                missing_mods = [
                    m["name"]
                    for m in profile_mods
                    if m["name"] not in current_mod_names
                ]
                if len(missing_mods) == len(profile_mods):
                    messagebox.showerror(
                        "Cannot Load Profile",
                        f"None of the {len(profile_mods)} mods in this profile are installed.\n\nMissing mods:\n"
                        + "\n".join(missing_mods[:10])
                        + ("\n..." if len(missing_mods) > 10 else ""),
                    )
                    return

            profile["last_used_utc"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            profiles.save_profile_to_disk(profile, path)
            self._refresh_list()
            new_list_raw, warnings = profiles.apply_profile_to_configs(
                profile, current_mods
            )
            self.app.mod_configs = cast(List[UIModConfig], new_list_raw)

            gd = utils.safe_norm(self.app.vars["game_dir"].get())
            if gd:
                policy.save_load_order(
                    cast(List[Dict[str, Any]], self.app.mod_configs), game_dir=gd
                )

            self.app.refresh_ui_after_load(save_policy=False)
            # Save Active Profile Context
            self.app.cfg["active_profile"] = self.current_profile_file.replace(
                ".json", ""
            )
            self.app.save_config()
            name = self.current_profile_file.replace(".json", "")
            iso = profile.get("isolation", {})
            msg = f"Loaded Profile: {name}"

            details: List[str] = []
            if iso.get("isolate_data"):
                details.append("Using Profile Data Isolation")

            if details:
                msg += "\n(" + ", ".join(details) + ")"

            self.app.show_toast(msg)

            if warnings:
                warn_list = warnings
                warn_msg = "\n".join(warn_list[:5])
                if len(warn_list) > 5:
                    warn_msg += f"\n...and {len(warn_list)-5} more."
                messagebox.showwarning("Version Warnings", warn_msg)

            self.destroy()

        except Exception as e:
            messagebox.showerror("Load Error", str(e))

    def _import_profile(self) -> None:
        src = filedialog.askopenfilename(filetypes=[("GMOS Profile", "*.json")])
        if src:
            try:
                data = profiles.load_profile_from_disk(src)
                name = os.path.basename(src)
                dest = os.path.join(self.profiles_dir, name)

                if os.path.exists(dest):
                    if not messagebox.askyesno(
                        "Overwrite", f"Profile '{name}' exists. Overwrite?"
                    ):
                        return

                profiles.save_profile_to_disk(data, dest)
                self._refresh_list()
                self.app.show_toast("Profile imported")
            except Exception as e:
                messagebox.showerror("Import Error", str(e))

    def _export_selected(self) -> None:
        if not self.current_profile_file:
            return
        src = os.path.join(self.profiles_dir, self.current_profile_file)
        dest = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("GMOS Profile", "*.json")],
            initialfile=self.current_profile_file,
        )
        if dest:
            try:
                data = profiles.load_profile_from_disk(src)
                profiles.save_profile_to_disk(data, dest)
                self.app.show_toast("Profile exported")
            except Exception as e:
                messagebox.showerror("Export Error", str(e))

    def _transfer_saves(self) -> None:
        if not self.current_profile_file:
            return
        name = self.current_profile_file.replace(".json", "")
        src_dir = filedialog.askdirectory(
            title=f"Select Source User Data Folder to Import into '{name}'", parent=self
        )
        if not src_dir:
            return

        dest_dir = os.path.join(self.profiles_dir, name, "userdata")
        if os.path.abspath(src_dir) == os.path.abspath(dest_dir):
            messagebox.showerror(
                "Error", "Source and destination are the same.", parent=self
            )
            return

        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
            messagebox.showinfo(
                "Success",
                f"Successfully imported user data into profile '{name}'.",
                parent=self,
            )
        except Exception as e:
            messagebox.showerror(
                "Import Error", f"Failed to import data: {e}", parent=self
            )
