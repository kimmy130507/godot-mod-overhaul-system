# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import tkinter as tk
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gmos.ui.app import App

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


@patch("gmos.ui.app.load_global_config")
@patch("gmos.ui.app.GmosSession")
@patch("gmos.ui.app.DashboardView")
def test_app_initialization(
    mock_dash: MagicMock, mock_session: MagicMock, mock_cfg: MagicMock, tk_root: Any
) -> None:
    """Verify that the main application window initializes correctly without rendering errors."""
    mock_cfg.return_value.theme_preference = "darkly"
    mock_cfg.return_value.default_instance_id = None
    mock_cfg.return_value.legal_accepted = True

    with patch("ttkbootstrap.Style") as mock_style:
        mock_style.return_value.colors.bg = "#333333"
        app = App(config_path=None)

        # Verify baseline variables
        assert "game_dir" in app.vars
        assert app.vars["game_dir"].get() == ""
        assert app.title() == "Godot Mod Overhaul System (GMOS)"

        app.destroy()
