# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import tkinter as tk
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from gmos.ui.dashboard import DashboardView

tk_available = False
try:
    _root = tk.Tk()
    _root.destroy()
    tk_available = True
except tk.TclError:
    pass


@pytest.fixture
def mock_app(tk_root: Any) -> MagicMock:
    app = MagicMock()
    app.vars = {
        "game_dir": tk.StringVar(master=tk_root),
        "launch_override": tk.StringVar(master=tk_root),
        "game_executable": tk.StringVar(master=tk_root),
    }
    app.cfg = {}
    app.menubar_frame = tk.Frame(tk_root)
    app.style.colors.primary = "#333333"
    app.style.colors.inputbg = "#333333"
    app.style.colors.bg = "#333333"
    app.mod_configs = [
        {"Name": "Mod A", "Enabled": True, "Valid": True},
        {"Name": "Mod B", "Enabled": False, "Valid": True},
        {"Name": "Mod C", "Enabled": True, "Valid": False},
    ]
    app.conflict_cache = {"Mod A": {}}
    return app


@pytest.fixture
def tk_root() -> Any:
    if not tk_available:
        pytest.skip("Headless environment: cannot initialize Tkinter")
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except Exception:
        pass


@patch("gmos.ui.dashboard.AutoScrollbar")
def test_dashboard_filters(
    mock_scroll: MagicMock, mock_app: MagicMock, tk_root: Any
) -> None:
    """Verify the DashboardView correctly filters the mod lists based on active Treeview selection."""
    with (
        patch("ttkbootstrap.Style"),
        patch("tkinter.ttk.Style.lookup", return_value="#333333"),
    ):
        dash = DashboardView(cast(tk.Widget, tk_root), mock_app)

        # Test 'All' filter
        dash.filter_tree.selection_set("all")
        res = dash.filter_mods(mock_app.mod_configs)
        assert len(res) == 3

        # Test 'Enabled' filter
        dash.filter_tree.selection_set("enabled")
        res = dash.filter_mods(mock_app.mod_configs)
        assert len(res) == 2
        assert res[0].get("Name") == "Mod A"
        assert res[1].get("Name") == "Mod C"

        # Test 'Disabled' filter
        dash.filter_tree.selection_set("disabled")
        res = dash.filter_mods(mock_app.mod_configs)
        assert len(res) == 1
        assert res[0].get("Name") == "Mod B"

        # Test Search String filter
        dash.filter_tree.selection_set("all")
        dash.search_var.set("Mod B")
        res = dash.filter_mods(mock_app.mod_configs)
        assert len(res) == 1
        assert res[0].get("Name") == "Mod B"
        dash.destroy()
