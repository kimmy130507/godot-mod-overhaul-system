# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tkinter as tk
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from gmos.ui.profiles import ProfileManagerDialog

tk_available = False
try:
    _root = tk.Tk()
    _root.destroy()
    tk_available = True
except tk.TclError:
    pass


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


def test_profile_manager_initialization(tk_root: Any) -> None:
    """Verify ProfileManagerDialog UI instantiation and directory creation."""

    mock_app = MagicMock()
    mock_app.vars = {"game_dir": tk.StringVar(master=tk_root, value="/fake/game/dir")}

    with (
        patch("os.makedirs") as mock_makedirs,
        patch("os.listdir", return_value=[]),
        patch("ttkbootstrap.Style"),
        patch("tkinter.ttk.Style.lookup", return_value="#333333"),
    ):

        dlg = ProfileManagerDialog(cast(tk.Widget, tk_root), mock_app)

        # Verify logic setup
        expected_path = os.path.join("/fake/game/dir", "profiles")
        mock_makedirs.assert_called_with(expected_path, exist_ok=True)
        assert dlg.vars["isolate_data"].get() is False
        assert dlg.title() == "Profile Manager"
        dlg.destroy()
