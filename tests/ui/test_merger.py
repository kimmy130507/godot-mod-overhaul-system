# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import tkinter as tk
from typing import Any, cast
from unittest.mock import MagicMock, mock_open, patch

import pytest

from gmos.ui.merger import MergeStudio
from gmos.utils import ModConfig

# --- Fixtures ---

# Detect display availability for CI/Headless environments safely
tk_available = False
try:
    import tkinter

    try:
        _root_check = tkinter.Tk()
        _root_check.destroy()
        tk_available = True
    except tkinter.TclError:
        pass
except ImportError:
    pass


@pytest.fixture(autouse=True)
def mock_ttk_bootstrap() -> Any:
    """Prevent ttkbootstrap from making Tcl calls during tests."""
    with (
        patch("ttkbootstrap.style.Bootstyle.update_ttk_widget_style", return_value=""),
        patch("ttkbootstrap.style.Style.theme_use"),
        patch("ttkbootstrap.style.StyleBuilderTTK.scale_size", return_value=1),
        patch("ttkbootstrap.style.Style.get_instance") as mock_get_inst,
    ):

        # Ensure the global style instance returns True for style checks
        mock_get_inst.return_value.style_exists_in_theme.return_value = True
        yield


@pytest.fixture
def tk_root() -> Any:
    """Provides a hidden Tk root for GUI tests."""
    if not tk_available:
        pytest.skip("Headless environment: cannot initialize Tkinter")
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


@pytest.fixture
def mock_app(tk_root: tk.Tk) -> MagicMock:
    """Mocks the main App object with required attributes."""
    app = MagicMock()
    app.vars = {"game_dir": tk.StringVar(value="/fake/game")}
    app.mod_configs = [{"Name": "Mod A"}, {"Name": "Mod B"}]
    # Ensure load_mods doesn't actually run
    app.load_mods = MagicMock()
    return app


# --- Mock Data ---

MOCK_CONFLICTS = {
    "Variable::res://player.gd::speed": [
        (
            "Mod A",
            "VariablePatch",
            ("res://player.gd", "speed", "/mods/A/p.gd", "speed", "replace"),
        ),
        (
            "Mod B",
            "VariablePatch",
            ("res://player.gd", "speed", "/mods/B/p.gd", "speed", "replace"),
        ),
    ]
}

MOCK_FILE_CONTENT = """extends Node
var speed = 10
func _ready():
    pass
"""

# --- Tests ---


def test_merge_studio_init_population(tk_root: tk.Tk, mock_app: MagicMock) -> None:
    """Verify that Merge Studio initializes and populates the conflict tree."""

    with (
        patch(
            "gmos.core.patcher.analyze_mods_for_conflicts", return_value=MOCK_CONFLICTS
        ),
        patch("gmos.state.policy.load_file_rules", return_value={}),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=MOCK_FILE_CONTENT)),
    ):

        studio = MergeStudio(cast(tk.Widget, tk_root), mock_app)

        # Verify Tree Population
        children = studio.tree.get_children()
        assert len(children) > 0, "Tree should have items"

        # We expect a file node for 'player.gd'
        item_id = children[0]
        item_text = studio.tree.item(item_id, "text")
        assert "player.gd" in item_text

        # Expand and check for children (the specific conflict)
        sub_children = studio.tree.get_children(item_id)
        assert len(sub_children) == 1
        conflict_text = studio.tree.item(sub_children[0], "text")
        assert "Variable" in conflict_text
        assert "speed" in conflict_text


def test_inline_resolution_logic(tk_root: tk.Tk, mock_app: MagicMock) -> None:
    """Verify the logic of the inline candidate selection & Apply."""

    with (
        patch(
            "gmos.core.patcher.analyze_mods_for_conflicts", return_value=MOCK_CONFLICTS
        ),
        patch("gmos.state.policy.load_file_rules", return_value={}),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=MOCK_FILE_CONTENT)),
    ):
        studio = MergeStudio(cast(tk.Widget, tk_root), mock_app)
        studio._load_file("player.gd")  # type: ignore[reportPrivateUsage]

        # Setup mock candidates directly since file reading is bypassed
        conflict_key = "Variable::res://player.gd::speed"
        studio.active_zones["zone_test"] = conflict_key
        studio.editor.tag_add("zone_test", "1.0", "2.0")

        # Trigger open resolution panel
        studio._open_resolution_modal(conflict_key)  # type: ignore[reportPrivateUsage]

        # 1. Test Default Selection
        assert studio.cand_list.get(0) == "Vanilla"  # type: ignore[reportPrivateUsage]

        # 2. Simulate selecting "Mod A"
        studio.active_candidates["Mod A"] = "var speed = 100"
        idx = list(cast(tuple[str, ...], studio.cand_list.get(0, "end"))).index("Mod A")  # type: ignore[reportPrivateUsage]
        studio.cand_list.selection_clear(0, "end")
        studio.cand_list.selection_set(idx)
        studio._on_candidate_select(None)  # type: ignore[reportPrivateUsage]

        content = str(studio.cand_right.get("1.0", "end-1c"))
        assert "var speed = 100" in content

        # 3. Test Apply
        studio._apply_zone()  # type: ignore[reportPrivateUsage]
        assert studio.resolutions[conflict_key]["winner"] == "Mod A"
        assert studio.resolutions[conflict_key]["code"] == "var speed = 100"


def test_save_unified_patch(tk_root: tk.Tk, mock_app: MagicMock) -> None:
    """Verify that _save_all writes the correct files to disk."""

    with (
        patch(
            "gmos.core.patcher.analyze_mods_for_conflicts", return_value=MOCK_CONFLICTS
        ),
        patch("gmos.state.policy.load_file_rules", return_value={}),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", mock_open(read_data=MOCK_FILE_CONTENT)) as m_open,
    ):

        studio = MergeStudio(cast(tk.Widget, tk_root), mock_app)

        # Simulate a resolved custom patch
        conflict_key = "Variable::res://player.gd::speed"
        studio.resolutions[conflict_key] = {
            "winner": "Custom Patch",
            "code": "var speed = 9999",
        }

        # Mocking os.makedirs to avoid FS errors
        with patch("os.makedirs"):
            studio._save_all()  # type: ignore[reportPrivateUsage]

        # Verify file writes
        # We expect writes for:
        # 1. The patched player.gd
        # 2. The mod.mos manifest

        handle = m_open()
        writes = [call.args[0] for call in handle.write.call_args_list]

        # Check manifest content
        assert any("[ModInfo]" in w for w in writes)
        assert any('Name="GMOS_Unified_Patch"' in w for w in writes)

        # Check patched file content
        # The logic patches the file. Original was "var speed = 10", patch is 9999.
        assert any("var speed = 9999" in w for w in writes)

        # Verify App Reload was called
        mock_app.load_mods.assert_called_once()


def test_conflict_parsing_integration() -> None:
    """
    Test that the conflict keys from patcher match what the Merger expects.
    This ensures core changes don't break the UI.
    """
    from gmos.core.patcher import analyze_mods_for_conflicts

    # Mock mod configs
    mods: list[dict[str, Any]] = [
        {
            "Name": "A",
            "Path": "/a",
            "Sections": {"VariablePatch": ["res://s.gd::v = /a/s.gd ; mode=replace"]},
        },
        {
            "Name": "B",
            "Path": "/b",
            "Sections": {"VariablePatch": ["res://s.gd::v = /b/s.gd ; mode=replace"]},
        },
    ]

    # We need to mock generate_patch_plan since it reads files
    with patch("gmos.core.patcher.generate_patch_plan") as mock_plan:
        # Mod A plan
        plan_a = [
            ("A", "VariablePatch", ("res://s.gd", "v", "/a/s.gd", "v", "replace"))
        ]
        # Mod B plan
        plan_b = [
            ("B", "VariablePatch", ("res://s.gd", "v", "/b/s.gd", "v", "replace"))
        ]

        mock_plan.side_effect = [plan_a, plan_b]

        conflicts = analyze_mods_for_conflicts(cast(list[ModConfig], mods))

        # Verify Key Structure (Type::Res::Name)
        expected_key = "Variable::res://s.gd::v"
        assert expected_key in conflicts
        assert len(conflicts[expected_key]) == 2
