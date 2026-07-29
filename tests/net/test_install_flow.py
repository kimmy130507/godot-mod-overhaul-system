# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_app_env() -> Generator[Any, None, None]:
    """Mocks the App environment, bypassing GUI initialization."""
    # Patch App.__init__ to return None, skipping all UI/Thread setup.
    # This gives us a blank instance we can populate manually.
    with patch("gmos.ui.app.App.__init__", return_value=None):
        from gmos.ui.app import App

        # Initialize hollow App
        app = App()

        # Manually inject dependencies required by _on_download_progress
        app.browser_view = MagicMock()
        app.load_mods = MagicMock()  # type: ignore[method-assign]
        app.active_tasks = set()
        app._dl_stats = {}  # type: ignore[reportPrivateUsage]
        app.download_status_var = MagicMock()  # Mock the StringVar used in _update
        app.dashboard = None
        app.log_view = None
        # Bypass 'after' to execute the callback immediately
        # (Tkinter's after signature: ms, func, *args)
        app.after = lambda ms, func: func()  # type: ignore

        yield app


def test_install_clears_download_and_refreshes(mock_app_env: MagicMock) -> None:
    """
    Scenario: A mod finishes installing.
    Expected: The task is removed from UI and the mod list is refreshed.
    """
    app = mock_app_env
    task_id = "task_123"
    app.active_tasks.add(task_id)

    # Simulate "Installed" Signal from Session
    # This triggers the internal _update function via the mocked app.after
    app._on_download_progress(task_id, 100, 100, "Installed 📂", "Test Mod")

    # Verification
    app.load_mods.assert_called_once()
    app.browser_view.remove_task.assert_called_with(task_id)
