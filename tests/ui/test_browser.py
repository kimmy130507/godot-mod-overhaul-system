# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import tkinter as tk
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from gmos.ui.browser import DownloadsView

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


@patch("gmos.ui.browser.AutoScrollbar")
def test_downloads_view_task_management(mock_scroll: MagicMock, tk_root: Any) -> None:
    """Verify that tasks can be added, updated, and removed dynamically from the Downloads list."""

    mock_app = MagicMock()
    with (
        patch("ttkbootstrap.Style"),
        patch("tkinter.ttk.Style.lookup", return_value="#333333"),
    ):
        view = DownloadsView(cast(tk.Widget, tk_root), mock_app)

        # Add a task
        view.add_task("task_123", "Example Mod", "Brotato")

        assert "task_123" in view._tasks  # type: ignore[reportPrivateUsage]
        assert view._task_values["task_123"][0] == "Example Mod"  # type: ignore[reportPrivateUsage]

        # Update State
        view.update_task_state("task_123", "downloading", "Downloading...")
        assert view._task_values["task_123"][2] == "Downloading"  # type: ignore[reportPrivateUsage]

        # Remove Task
        view.remove_task("task_123")
        assert "task_123" not in view._tasks  # type: ignore[reportPrivateUsage]
        assert "task_123" not in view._task_values  # type: ignore[reportPrivateUsage]
        view.destroy()
