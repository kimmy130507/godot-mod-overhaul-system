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
import difflib
import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple, cast

from gmos import utils
from gmos.core import patcher
from gmos.io import atomic_replace
from gmos.state import policy
from gmos.ui.widgets import AutoScrollbar, Toast, res_to_path
from gmos.utils import ModConfig, detect_icon_theme

if TYPE_CHECKING:
    from gmos.ui.app import App
THEME_COLORS = {
    "light": {
        "bg": "#ffffff",
        "conflict_bg": "#ffe0e0",
        "resolved_bg": "#e6ffec",
        "diff_add": "#e6ffec",
        "diff_rem": "#ffebe9",
        "line_num": "#808080",
    },
    "dark": {
        "bg": "#1e1e1e",
        "conflict_bg": "#4d1f1f",
        "resolved_bg": "#1f4d26",
        "diff_add": "#1e3a24",
        "diff_rem": "#4a1e1e",
        "line_num": "#808080",
    },
}


class MergeStudio(tk.Toplevel):
    def __init__(self, parent: tk.Widget, app: "App"):
        super().__init__(parent)
        self.app = app
        self.title("Merge Studio")
        utils.load_and_apply_app_icon_to_toplevel(self)
        utils.setup_child_window(self, parent, width=1200, height=800, modal=True)

        self.game_dir = app.vars["game_dir"].get()
        self.file_rules = policy.load_file_rules(game_dir=self.game_dir)
        self.custom_patches: Dict[str, str] = {}  # Key: conflict_key, Value: code
        self.has_unsaved_changes = False

        # UI State
        self.current_file_path: Optional[str] = None
        self.line_map: Dict[str, Tuple[int, int]] = {}  # id -> (start, end)
        self.conflict_data: Dict[str, Any] = {}
        self.active_zones: Dict[str, str] = {}  # zone_tag -> conflict_key
        self.selected_conflict_key: Optional[str] = None
        self.active_candidates: Dict[str, str] = {}
        self.base_text: str = ""
        self.current_candidate_name: Optional[str] = None
        self.resolutions: Dict[str, Dict[str, str]] = (
            {}
        )  # c_key -> {"winner": name, "code": text}
        self.custom_edits: Dict[str, str] = {}  # Preserves unapplied custom typing
        self.tree_nodes: Dict[str, str] = {}  # c_key -> item_id
        self.file_nodes: Dict[str, str] = {}  # f_path -> item_id
        self.active_mod_names: Set[str] = set()
        self.ico_conflict = utils.load_icon("triangle-alert.png", size=(16, 16))
        self.ico_resolved = utils.load_icon("check.png", size=(16, 16))
        self._init_data()
        self._setup_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close_attempt)
        self.bind("<<ThemeChanged>>", self._apply_theme_colors)
        self.after(100, self._apply_theme_colors)

    def _on_close_attempt(self) -> None:
        if getattr(self, "has_unsaved_changes", False):
            if not messagebox.askyesno(
                "Unsaved Changes",
                "You have unsaved changes that haven't been patched.\nAre you sure you want to close Merge Studio?",
                parent=self,
            ):
                return
        self.destroy()

    def _init_data(self) -> None:
        # 1. Analyze Conflicts
        active_mods = [
            m
            for m in self.app.mod_configs
            if m.get("Enabled", True) and m.get("Valid", True)
        ]
        self.active_mod_names = {str(m.get("Name")) for m in active_mods}
        raw_conflicts = patcher.analyze_mods_for_conflicts(
            cast(List[ModConfig], active_mods)
        )

        # 2. Structure Data
        self.files_map: Dict[str, List[str]] = {}  # file_path -> [conflict_keys]
        self.conflict_db: Dict[str, Any] = {}  # key -> {candidates: {}, type: str}

        for key, instructions in raw_conflicts.items():
            # Key format: Type::ResPath::Name
            parts = key.split("::")
            c_type = parts[0]
            res_path = parts[1]
            name = parts[2] if len(parts) > 2 else ""

            # Use shared utility instead of private method
            f_path = res_to_path(res_path).replace("\\", "/")

            if f_path not in self.files_map:
                self.files_map[f_path] = []

            self.files_map[f_path].append(key)

            # Prepare candidates logic
            candidates: Dict[str, Any] = {}
            for mod_name, op, details in instructions:
                candidates[mod_name] = {"op": op, "details": details}

            self.conflict_db[key] = {
                "type": c_type,
                "name": name,
                "res": res_path,
                "candidates_meta": candidates,
            }

    def _setup_ui(self) -> None:
        # Toolbar
        toolbar = ttk.Frame(self, padding=5)
        toolbar.pack(fill="x")

        self.btn_save = ttk.Button(
            toolbar,
            text="Generate Patch",
            style="success.TButton",
            command=self._save_all,
        )
        self.btn_save.pack(side="right", padx=5)

        # Main Split
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, padx=5, pady=5)
        top_paned = ttk.PanedWindow(paned, orient="horizontal")
        cast(Any, paned).add(top_paned, weight=2)
        # Left: File Tree
        left_frame = ttk.Frame(top_paned, width=300)
        cast(Any, top_paned).add(left_frame, weight=1)

        self.tree = ttk.Treeview(left_frame, show="tree", selectmode="browse")
        tree_vsb = AutoScrollbar(
            left_frame, orient="vertical", command=cast(Any, self.tree).yview
        )
        self.tree.configure(yscrollcommand=tree_vsb.set)
        tree_vsb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_file_select)
        self.tree.tag_configure("gray", foreground="gray")
        self.tree.tag_configure("resolved_child", foreground="#4caf50")
        # Populate Tree
        if not self.files_map:
            self.tree.insert("", "end", text="No conflicts detected.", tags=("gray",))
        else:

            def _is_file_resolved(f_path: str) -> bool:
                for c_key in self.files_map[f_path]:
                    if c_key in self.resolutions:
                        continue
                    winner = self.file_rules.get(f_path)
                    if winner and winner in self.active_mod_names:
                        continue
                    return False
                return True

            sorted_files = sorted(
                self.files_map.keys(), key=lambda p: (_is_file_resolved(p), p)
            )
            for f_path in sorted_files:
                display_name = os.path.basename(f_path)

                # Check if all conflicts in this file have a rule
                all_resolved = True
                for c_key in self.files_map[f_path]:
                    has_active_rule = False
                    if c_key in self.resolutions:
                        has_active_rule = True
                    else:
                        winner = self.file_rules.get(f_path)
                        if winner and winner in self.active_mod_names:
                            has_active_rule = True
                    if not has_active_rule:
                        all_resolved = False
                        break

                icon = self.ico_resolved if all_resolved else self.ico_conflict

                node = self.tree.insert(
                    "",
                    "end",
                    text=f" {display_name}",
                    image=icon or "",
                    values=(f_path,),
                )
                self.file_nodes[f_path] = node
                # Add children (functions/vars)
                for c_key in self.files_map[f_path]:
                    data = self.conflict_db[c_key]
                    c_name = data["name"] or "Whole File"
                    winner_name = ""
                    is_resolved = False
                    if c_key in self.resolutions:
                        winner_name = self.resolutions[c_key]["winner"]
                        is_resolved = True
                    elif f_path in self.file_rules:
                        winner_name = self.file_rules[f_path]
                        if winner_name in self.active_mod_names:
                            is_resolved = True
                        else:
                            winner_name = ""

                    text_display = f" {data['type']}: {c_name}"
                    if winner_name:
                        text_display += f" ({winner_name})"

                    child_icon = self.ico_resolved if is_resolved else ""
                    child_node = self.tree.insert(
                        node,
                        "end",
                        text=text_display,
                        image=child_icon or "",
                        values=(c_key, "child"),
                    )
                    if is_resolved:
                        self.tree.item(child_node, tags=("resolved_child",))
                    self.tree_nodes[c_key] = child_node

        # Right: Editor
        editor_container = ttk.Frame(top_paned)
        cast(Any, top_paned).add(editor_container, weight=3)
        is_dark = detect_icon_theme() == "light"
        colors = THEME_COLORS["dark"] if is_dark else THEME_COLORS["light"]

        self.editor = tk.Text(
            editor_container,
            wrap="none",
            font=("Consolas", 10),
            state="disabled",
            bg=colors["bg"],
        )
        vsb = AutoScrollbar(
            editor_container, orient="vertical", command=cast(Any, self.editor).yview
        )
        hsb = AutoScrollbar(
            editor_container, orient="horizontal", command=cast(Any, self.editor).xview
        )
        self.editor.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self.editor.pack(fill="both", expand=True)

        # Bottom: Inline Resolution Panel
        self.res_panel = ttk.Frame(paned)
        cast(Any, paned).add(self.res_panel, weight=1)
        res_top_bar = ttk.Frame(self.res_panel)
        res_top_bar.pack(side="top", fill="x", pady=(0, 5))
        ttk.Label(
            res_top_bar, text="Resolution Candidates", font=("Segoe UI", 9, "bold")
        ).pack(side="left", anchor="w", padx=5)

        self.btn_apply_zone = ttk.Button(
            res_top_bar,
            text="Apply Selected",
            state="disabled",
            command=self._apply_zone,
            style="success.TButton",
        )
        self.btn_apply_zone.pack(side="right", padx=5)

        res_body = ttk.Frame(self.res_panel)
        res_body.pack(side="top", fill="both", expand=True)

        cand_list_frame = ttk.Frame(res_body)
        cand_list_frame.pack(side="left", fill="y", padx=5, pady=5)
        self.cand_list = tk.Listbox(
            cand_list_frame,
            exportselection=False,
            width=25,
            bg=colors["bg"],
            fg=utils.get_dynamic_text_color(colors["bg"]),
        )
        cand_list_vsb = AutoScrollbar(
            cand_list_frame, orient="vertical", command=cast(Any, self.cand_list).yview
        )
        self.cand_list.configure(yscrollcommand=cand_list_vsb.set)
        cand_list_vsb.pack(side="right", fill="y")
        self.cand_list.pack(side="left", fill="both", expand=True)
        self.cand_list.bind("<<ListboxSelect>>", self._on_candidate_select)
        cand_editor_frame = ttk.Frame(res_body)
        cand_editor_frame.pack(
            side="left", fill="both", expand=True, pady=5, padx=(0, 5)
        )
        self.cand_left_frame = ttk.Frame(cand_editor_frame)
        self.cand_left_frame.pack(side="left", fill="both", expand=True, padx=(0, 2))
        self.cand_right_frame = ttk.Frame(cand_editor_frame)
        self.cand_right_frame.pack(side="left", fill="both", expand=True, padx=(2, 0))

        self.cand_left = tk.Text(
            self.cand_left_frame,
            wrap="none",
            font=("Consolas", 10),
            state="disabled",
            bg=colors["bg"],
            width=1,
        )
        self.cand_right = tk.Text(
            self.cand_right_frame,
            wrap="none",
            font=("Consolas", 10),
            state="disabled",
            bg=colors["bg"],
            width=1,
        )

        cand_vsb = AutoScrollbar(self.cand_right_frame, orient="vertical")

        def _sync_y(*args: Any) -> None:
            cast(Any, self.cand_left).yview(*args)
            cast(Any, self.cand_right).yview(*args)

        def _sync_set(*args: Any) -> None:
            cand_vsb.set(*args)
            self.cand_left.yview_moveto(args[0])
            self.cand_right.yview_moveto(args[0])

        cand_vsb.config(command=_sync_y)
        self.cand_left.configure(yscrollcommand=_sync_set)
        self.cand_right.configure(yscrollcommand=_sync_set)

        left_hsb = AutoScrollbar(
            self.cand_left_frame,
            orient="horizontal",
            command=cast(Any, self.cand_left).xview,
        )
        self.cand_left.configure(xscrollcommand=left_hsb.set)
        left_hsb.pack(side="bottom", fill="x")
        self.cand_left.pack(side="top", fill="both", expand=True)

        cand_vsb.pack(side="right", fill="y")
        right_hsb = AutoScrollbar(
            self.cand_right_frame,
            orient="horizontal",
            command=cast(Any, self.cand_right).xview,
        )
        self.cand_right.configure(xscrollcommand=right_hsb.set)
        right_hsb.pack(side="bottom", fill="x")
        self.cand_right.pack(side="top", fill="both", expand=True)

        # Tags
        is_dark = detect_icon_theme() == "light"
        colors = THEME_COLORS["dark"] if is_dark else THEME_COLORS["light"]

        self.editor.tag_config(
            "conflict_zone",
            background=colors["conflict_bg"],
            foreground=utils.get_dynamic_text_color(colors["conflict_bg"]),
        )
        self.editor.tag_config(
            "resolved_zone",
            background=colors["resolved_bg"],
            foreground=utils.get_dynamic_text_color(colors["resolved_bg"]),
        )
        for ed in (self.cand_left, self.cand_right):
            ed.tag_config("add", background=colors["diff_add"])
            ed.tag_config("rem", background=colors["diff_rem"])
            ed.tag_config("hatch", background=colors["line_num"], bgstipple="gray50")
            ed.tag_config("line_num", foreground=colors["line_num"])
        self.editor.tag_config("clickable", font=("Consolas", 10, "bold underline"))

        self.editor.tag_bind("clickable", "<Button-1>", self._on_zone_click)
        self.editor.tag_bind(
            "clickable", "<Enter>", lambda e: self.editor.config(cursor="hand2")
        )
        self.editor.tag_bind(
            "clickable", "<Leave>", lambda e: self.editor.config(cursor="")
        )

    def _apply_theme_colors(self, event: Optional["tk.Event[Any]"] = None) -> None:
        """Updates only the specific text color based on the current theme variant."""
        text_color = utils.get_dynamic_text_color()
        self.editor.config(fg=text_color, insertbackground=text_color)
        for ed in (self.cand_left, self.cand_right):
            ed.config(fg=text_color, insertbackground=text_color)
        self.cand_list.config(fg=text_color)
        utils.apply_window_theme(self)

    def _get_candidate_text(self, mod_name: str, meta: Dict[str, Any]) -> str:
        """Lazily fetches text for a mod candidate."""
        try:
            op = meta["op"]
            details = meta["details"]
            src_path = ""
            if op == "FileReplace":
                src_path = details[1]
            elif len(details) > 2:
                src_path = details[2]

            if src_path and os.path.exists(src_path):
                with open(src_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                if op == "FunctionPatch":
                    s_func = details[3]
                    rng = patcher.get_function_block(lines, s_func)
                    if rng:
                        return "".join(lines[rng[0] : rng[1] + 1])
                elif op == "VariablePatch":
                    s_var = details[3]
                    rng = patcher.get_var_block(lines, s_var)
                    if rng:
                        return "".join(lines[rng[0] : rng[1] + 1])

                return "".join(lines)
            return "# Source file missing"
        except Exception as e:
            return f"# Error reading source: {e}"

    def _on_file_select(self, event: "tk.Event[Any]") -> None:
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.item(sel[0])
        values = item["values"]

        if not values:
            return

        if len(values) > 1 and values[1] == "child":
            # Clicked a specific conflict node -> Scroll to it
            c_key = values[0]
            self._highlight_conflict(c_key)
        else:
            # Clicked a file node -> Load file
            f_path = values[0]
            self._load_file(f_path)

    def _load_file(self, f_path: str) -> None:
        self._save_current_custom_edit()
        self.current_file_path = f_path
        full_path = os.path.normpath(os.path.join(self.game_dir, f_path))

        content = ""
        if os.path.exists(full_path):
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        else:
            content = "# Original file not found (or inside PCK)"

        self.editor.config(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", content)

        # Process Conflicts for this file
        self.line_map.clear()
        self.active_zones.clear()

        conflict_keys = self.files_map.get(f_path, [])
        lines = content.splitlines(keepends=True)

        regions: List[Tuple[int, int, str, str, str]] = []

        for key in conflict_keys:
            data = self.conflict_db[key]
            name = data["name"]
            c_type = data["type"]

            rng = None
            if c_type == "Function":
                rng = patcher.get_function_block(lines, name)
            elif c_type == "Variable":
                rng = patcher.get_var_block(lines, name)
            elif c_type == "FileReplace":
                rng = (0, max(0, len(lines) - 1))
            if rng:
                regions.append((rng[0], rng[1], key, name, c_type))

        # Apply Tags
        for start, end, key, _name, c_type in regions:
            # Check if resolved in custom session or policy
            is_resolved = key in self.resolutions or (
                self.current_file_path in self.file_rules
                and self.file_rules[self.current_file_path] in self.active_mod_names
            )

            tag_name = f"zone_{key}"
            self.active_zones[tag_name] = key

            start_idx = f"{start+1}.0"
            end_idx = f"{end+2}.0"

            if c_type != "FileReplace":
                zone_tag = "resolved_zone" if is_resolved else "conflict_zone"
                self.editor.tag_add(zone_tag, start_idx, end_idx)
            self.editor.tag_add("clickable", start_idx, end_idx)
            self.editor.tag_add(tag_name, start_idx, end_idx)
        # Auto-select if there is only one conflict zone
        if len(regions) == 1:
            self._open_resolution_modal(regions[0][2])
        self.editor.config(state="disabled")

    def _highlight_conflict(self, c_key: str) -> None:
        for tag, key in self.active_zones.items():
            if key == c_key:
                ranges = self.editor.tag_ranges(tag)
                if ranges:
                    self.editor.see(ranges[0])
                break

    def _on_zone_click(self, event: "tk.Event[Any]") -> None:
        index = self.editor.index(f"@{event.x},{event.y}")
        tags = self.editor.tag_names(index)
        c_keys: List[str] = []
        for t in tags:
            if t in self.active_zones:
                c_keys.append(str(self.active_zones[t]))

        if not c_keys:
            return

        if len(c_keys) == 1:
            self._open_resolution_modal(c_keys[0])
        else:
            menu = tk.Menu(self, tearoff=0)

            def make_cmd(k: str) -> Callable[[], None]:
                return lambda: self._open_resolution_modal(k)

            for key in c_keys:
                data = self.conflict_db[key]
                c_name = str(data["name"]) or "Whole File"
                label = f"{data['type']}: {c_name}"
                menu.add_command(label=label, command=make_cmd(key))
            menu.post(event.x_root, event.y_root)

    def _open_resolution_modal(self, c_key: str) -> None:
        self._save_current_custom_edit()
        self.selected_conflict_key = c_key
        data = self.conflict_db[c_key]

        tag_name = [t for t, k in self.active_zones.items() if k == c_key][0]
        ranges = self.editor.tag_ranges(tag_name)
        self.base_text = str(self.editor.get(ranges[0], ranges[1]))

        self.active_candidates = {"Vanilla": self.base_text}

        meta_dict = cast(Dict[str, Any], data["candidates_meta"])

        for mod_name, meta in meta_dict.items():
            if mod_name == "GMOS_Unified_Patch":
                continue
            self.active_candidates[mod_name] = self._get_candidate_text(mod_name, meta)
        self.active_candidates["Custom Patch"] = str(
            self.custom_edits.get(c_key, self.base_text)
        )

        self.cand_list.delete(0, "end")
        for name in (
            ["Vanilla"]
            + [
                k
                for k in self.active_candidates.keys()
                if k not in ("Vanilla", "Custom Patch")
            ]
            + ["Custom Patch"]
        ):
            self.cand_list.insert("end", name)

        self.cand_list.selection_set(0)
        self.current_candidate_name = "Vanilla"
        self._on_candidate_select(None)

    def _save_current_custom_edit(self) -> None:
        if self.selected_conflict_key and self.current_candidate_name == "Custom Patch":
            custom_content = self.cand_right.get("1.0", "end-1c")
            self.active_candidates["Custom Patch"] = custom_content
            self.custom_edits[self.selected_conflict_key] = custom_content

    def _on_candidate_select(self, event: Optional["tk.Event[Any]"] = None) -> None:
        self._save_current_custom_edit()
        sel = cast(Tuple[int, ...], cast(Any, self.cand_list).curselection())

        if not sel:
            return
        name = str(cast(Any, self.cand_list).get(int(sel[0])))
        self.current_candidate_name = name
        self.btn_apply_zone.config(state="disabled" if name == "Vanilla" else "normal")
        content = self.active_candidates.get(name, "")

        self.cand_left.config(state="normal")
        self.cand_right.config(state="normal")
        self.cand_left.delete("1.0", "end")
        self.cand_right.delete("1.0", "end")

        base_lines = self.base_text.splitlines()
        cand_lines = content.splitlines()
        self.cand_left_frame.pack_forget()
        self.cand_right_frame.pack_forget()

        if name not in ("Custom Patch", "Vanilla"):
            self.cand_left_frame.pack(
                side="left", fill="both", expand=True, padx=(0, 2)
            )
        self.cand_right_frame.pack(side="left", fill="both", expand=True, padx=(2, 0))

        def insert_side(ed: tk.Text, line: str, tag: str, ln: int = -1) -> None:
            prefix = f"{ln:4} " if ln > 0 else "     "
            ed.insert("end", prefix, ("line_num", tag) if tag else ("line_num",))
            ed.insert("end", line + "\n", (tag,) if tag else ())

        if name == "Custom Patch":
            left_idx = 0
            for line in base_lines:
                left_idx += 1
                insert_side(self.cand_left, line, "", left_idx)
            self.cand_right.insert("1.0", content)
        elif name == "Vanilla":
            left_idx = 0
            for line in base_lines:
                left_idx += 1
                insert_side(self.cand_left, line, "", left_idx)
                insert_side(self.cand_right, line, "", left_idx)
        else:
            matcher = difflib.SequenceMatcher(None, base_lines, cand_lines)
            left_idx = 0
            right_idx = 0
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    for line in base_lines[i1:i2]:
                        left_idx += 1
                        right_idx += 1
                        insert_side(self.cand_left, line, "", left_idx)
                        insert_side(self.cand_right, line, "", right_idx)
                elif tag == "replace":
                    diff = (i2 - i1) - (j2 - j1)
                    for line in base_lines[i1:i2]:
                        left_idx += 1
                        insert_side(self.cand_left, line, "rem", left_idx)
                    for line in cand_lines[j1:j2]:
                        right_idx += 1
                        insert_side(self.cand_right, line, "add", right_idx)
                    if diff > 0:
                        for _ in range(diff):
                            insert_side(self.cand_right, "", "hatch")
                    elif diff < 0:
                        for _ in range(-diff):
                            insert_side(self.cand_left, "", "hatch")
                elif tag == "delete":
                    for line in base_lines[i1:i2]:
                        left_idx += 1
                        insert_side(self.cand_left, line, "rem", left_idx)
                        insert_side(self.cand_right, "", "hatch")
                elif tag == "insert":
                    for line in cand_lines[j1:j2]:
                        right_idx += 1
                        insert_side(self.cand_right, line, "add", right_idx)
                        insert_side(self.cand_left, "", "hatch")

        self.cand_left.config(state="disabled")
        self.cand_right.config(state="normal" if name == "Custom Patch" else "disabled")

    def _apply_zone(self) -> None:
        if not self.selected_conflict_key:
            return
        self._save_current_custom_edit()
        sel = cast(Tuple[int, ...], cast(Any, self.cand_list).curselection())
        if not sel:
            return
        winner_name = str(cast(Any, self.cand_list).get(int(sel[0])))
        c_key = self.selected_conflict_key

        if winner_name == "Custom Patch":
            content = self.cand_right.get("1.0", "end-1c")
            if not content.strip():
                messagebox.showwarning(
                    "Empty Patch", "Custom Patch is empty.", parent=self
                )
                return
        elif winner_name == "Vanilla":
            content = self.base_text
        else:
            content = self.active_candidates[winner_name]
            self.resolutions[c_key] = {"winner": winner_name, "code": content}

        tag_name = [t for t, k in self.active_zones.items() if k == c_key][0]
        ranges = self.editor.tag_ranges(tag_name)
        self.editor.config(state="normal")
        self.editor.delete(ranges[0], ranges[1])
        self.editor.insert(ranges[0], content)
        new_end_idx = self.editor.index(f"{ranges[0]} + {len(content)} chars")
        self.editor.tag_add("resolved_zone", ranges[0], new_end_idx)
        self.editor.tag_add("clickable", ranges[0], new_end_idx)
        self.editor.tag_add(tag_name, ranges[0], new_end_idx)
        self.editor.config(state="disabled")
        node = self.tree_nodes[c_key]
        data = self.conflict_db[c_key]
        c_name = data["name"] or "Whole File"
        self.tree.item(
            node,
            text=f" {data['type']}: {c_name} ({winner_name})",
            image=self.ico_resolved or "",
        )
        self.tree.item(node, tags=("resolved_child",))
        if self.current_file_path:
            f_path = self.current_file_path
            all_resolved = True
            for k in self.files_map[f_path]:
                has_active_rule = False
                if k in self.resolutions:
                    has_active_rule = True
                else:
                    winner = self.file_rules.get(f_path)
                    if winner and winner in self.active_mod_names:
                        has_active_rule = True
                if not has_active_rule:
                    all_resolved = False
                    break
            p_node = self.file_nodes.get(f_path)
            if p_node:
                icon = self.ico_resolved if all_resolved else self.ico_conflict
                self.tree.item(p_node, image=icon or "")
                if all_resolved:
                    self.tree.move(p_node, "", "end")
        self.cand_list.delete(0, "end")
        self.cand_left.config(state="normal")
        self.cand_right.config(state="normal")
        self.cand_left.delete("1.0", "end")
        self.cand_right.delete("1.0", "end")
        self.cand_left.config(state="disabled")
        self.cand_right.config(state="disabled")
        self.btn_apply_zone.config(state="disabled")
        self.selected_conflict_key = None
        self.has_unsaved_changes = True

        # Notify user of unsaved changes waiting to be patched
        self.btn_save.config(text="* Generate Patch (Unsaved)", style="warning.TButton")

    def _save_all(self) -> None:
        """
        Generates a 'GMOS_Unified_Patch' mod containing all resolved custom patches
        and updates the policy to favor it.
        """
        if not self.resolutions:
            messagebox.showinfo("Merge Studio", "No changes to apply.")
            return

        patch_mod_dir = os.path.join(self.game_dir, "mods", "GMOS_Unified_Patch")
        os.makedirs(patch_mod_dir, exist_ok=True)

        patches_by_file: Dict[str, List[Tuple[str, str]]] = {}

        for key, res_dict in self.resolutions.items():
            data = self.conflict_db[key]
            res = data["res"]
            f_path = res_to_path(res).replace("\\", "/")
            if f_path not in patches_by_file:
                patches_by_file[f_path] = []
            patches_by_file[f_path].append((key, res_dict["code"]))

        for f_path, changes in patches_by_file.items():
            full_orig = os.path.normpath(os.path.join(self.game_dir, f_path))
            if not os.path.exists(full_orig):
                continue

            with open(full_orig, "r", encoding="utf-8") as f:
                content = f.read()

            lines = content.splitlines(keepends=True)
            replacements: List[Tuple[int, int, str]] = []

            for key, new_code in changes:
                data = self.conflict_db[key]
                name = data["name"]
                c_type = data["type"]

                rng = None
                if c_type == "Function":
                    rng = patcher.get_function_block(lines, name)
                elif c_type == "Variable":
                    rng = patcher.get_var_block(lines, name)
                elif c_type == "FileReplace":
                    rng = (0, max(0, len(lines) - 1))
                if rng:
                    replacements.append((rng[0], rng[1], new_code))

            replacements.sort(key=lambda x: x[0], reverse=True)

            for start, end, code in replacements:
                new_block = code.splitlines(keepends=True)
                if not new_block:
                    new_block = ["\n"]
                lines[start : end + 1] = new_block

            dest = os.path.normpath(os.path.join(patch_mod_dir, f_path))
            utils.ensure_parent_dir(dest)
            atomic_replace(dest, "".join(lines))

        mos_lines = [
            "[ModInfo]",
            'Name="GMOS_Unified_Patch"',
            'Description="Auto-generated merged conflicts"',
            'Version="1.0"',
            'Author="GMOS"',
            "",
            "[FileReplace]",
        ]
        for f_path in patches_by_file:
            res_path = "res://" + f_path.replace(os.sep, "/")
            mos_lines.append(f"{res_path}={f_path.replace(os.sep, '/')}")

        mos_dest = os.path.join(patch_mod_dir, "mod.mos")
        atomic_replace(mos_dest, "\n".join(mos_lines) + "\n")

        new_rules = self.file_rules.copy()
        for f_path in patches_by_file:
            new_rules[f_path] = "GMOS_Unified_Patch"

        policy.save_policy(
            cast(List[Dict[str, Any]], self.app.mod_configs), new_rules, self.game_dir
        )

        self.app.load_mods()
        Toast(cast(tk.Widget, self), "Unified Patch Created & Applied!", kind="success")
        self.destroy()
