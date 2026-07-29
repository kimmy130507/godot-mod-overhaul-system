# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from typing import Any, Generator
from unittest.mock import patch

import pytest

from gmos.state.config import load_instance_config_dict, save_instance_config_dict

# --- Helpers & Fixtures ---

# Detect display availability for CI/Headless environments safely
tk_available = False
tk: Any = None
try:
    import tkinter

    tk = tkinter
    try:
        _root_check = tk.Tk()
        _root_check.destroy()
        tk_available = True
    except tk.TclError:
        pass
except ImportError:
    pass


@pytest.fixture
def tk_root() -> Generator[Any, None, None]:
    """
    Provides a hidden Tk root for GUI tests.
    Skips the test if a display is not available.
    """
    if not tk_available:
        pytest.skip("Headless environment: cannot initialize Tkinter")

    root = tk.Tk()
    root.withdraw()  # Hide the window
    yield root
    try:
        root.destroy()
    except Exception:
        pass


# --- Test Instance Config I/O ---
# Note: These test persistence logic, not UI dialogs.
# They are kept here for continuity but ideally belong in tests/state/.


def test_instance_config_io(tmp_path: Path) -> None:
    """Verify we can save and load the per-game instance.json."""
    cfg_dir = tmp_path / "gmos_data"
    cfg_file = cfg_dir / "instance.json"

    data = {
        "game_executable": "godot_bin.exe",
        "game_dir": str(tmp_path / "game"),
        "mods_dir": str(tmp_path / "game" / "mods"),
    }

    save_instance_config_dict(data, str(cfg_file))
    assert cfg_file.exists()

    loaded = load_instance_config_dict(str(cfg_file))
    assert loaded["game_executable"] == "godot_bin.exe"
    assert loaded["game_dir"] == str(tmp_path / "game")


def test_load_defaults_on_missing(tmp_path: Path) -> None:
    """Verify loading a missing config returns safe defaults."""
    cfg_file = tmp_path / "non_existent.json"
    loaded = load_instance_config_dict(str(cfg_file))

    assert "game_dir" in loaded
    assert loaded["game_dir"] == ""
    assert loaded["game_executable"] == "game.exe"


# --- Test Mod Info Pane GUI ---


def test_mod_info_pane_update_for_config(tk_root: Any, tmp_path: Path) -> None:
    """
    Verifies that the Info Pane populates widgets correctly.
    Uses the tk_root fixture to safely manage the GUI lifecycle.
    """
    from gmos.ui.dashboard import ModInfoPane
    from gmos.ui.widgets import UIModConfig

    # Test: Update with valid config
    # Patch style updates and style instance to prevent TclError/AttributeError
    with (
        patch("ttkbootstrap.style.Bootstyle.update_ttk_widget_style", return_value=""),
        patch("ttkbootstrap.style.Style.get_instance") as mock_get_inst,
    ):

        mock_get_inst.return_value.style_exists_in_theme.return_value = True

        pane = ModInfoPane(tk_root)

        cfg: UIModConfig = {
            "Path": str(tmp_path / "my_mod"),
            "Name": "Test Mod",
            "Sections": {
                "ModInfo": {
                    "Name": "Test Mod",
                    "Version": "1.2.3",
                    "Author": "Unit Tester",
                    "Description": "A test mod for the info pane",
                }
            },
            "_deps_errors": ["missing dependency: dep2"],
        }

        # Test: Update with valid config
        pane.update_for_config(cfg)

        # Verification: Check internal widget state using actual attributes
        assert pane.lbl_name.cget("text") == "Test Mod"
        assert "Unit Tester" in pane.lbl_sub.cget("text")

        # Test: Clear selection
        # Note: Current implementation just hides the pane on None, implies no crash.
        pane.update_for_config(None)


# --- Test Permission Dialog Logic (Mocked) ---


def test_retry_on_permission_abort() -> None:
    """
    Ensure the retry logic propagates the exception if the user chooses 'Abort' (or close).
    """
    from gmos.utils import retry_on_permission

    # Patch the Dialog class used by retry_on_permission.
    # autospec=True ensures the mock mimics the real class signature.
    with patch("gmos.ui.widgets.PermissionErrorDialog", autospec=True) as MockDlg:
        # Simulate user clicking "Abort" (show returns "abort")
        mock_instance = MockDlg.return_value
        mock_instance.show.return_value = "abort"

        calls = 0

        def op() -> None:
            nonlocal calls
            calls += 1
            raise PermissionError("denied")

        # Expect the exception to bubble up after 1 attempt
        with pytest.raises(PermissionError):
            retry_on_permission(op, parent=None, path="/fake")

        assert calls == 1
        MockDlg.assert_called()


def test_retry_on_permission_retry_then_succeed() -> None:
    """
    Ensure the logic retries the operation if the user clicks 'Retry'.
    """
    from gmos.utils import retry_on_permission

    with patch("gmos.ui.widgets.PermissionErrorDialog", autospec=True) as MockDlg:
        # Simulate user clicking "Retry"
        mock_instance = MockDlg.return_value
        mock_instance.show.return_value = "retry"

        calls = 0

        def op() -> str:
            nonlocal calls
            calls += 1
            # Fail on first call, succeed on second
            if calls < 2:
                raise PermissionError("denied")
            return "ok"

        res = retry_on_permission(op, parent=None, path="/fake")

        assert res == "ok"
        assert calls == 2  # 1 failure + 1 success


def test_legal_disclaimer_accept(tk_root: Any) -> None:
    """
    Verify the Legal Disclaimer correctly toggles its continue button and sets the result state.
    """
    from gmos.ui.widgets import LegalDisclaimerDialog

    with (
        patch("ttkbootstrap.Style"),
        patch("tkinter.ttk.Style.lookup", return_value="#333333"),
    ):
        dlg = LegalDisclaimerDialog(tk_root)
        dlg.accepted_var.set(True)
        dlg.toggle_continue()
        assert str(dlg.cont_btn.cget("state")) == "normal"
        dlg.on_accept()
        assert dlg.result is True


def test_settings_dialog_init(tk_root: Any) -> None:
    """
    Verify the Settings Dialog initializes and hydrates values from the global config correctly.
    """
    from unittest.mock import MagicMock

    from gmos.ui.settings import SettingsDialog

    mock_app = MagicMock()
    mock_app.global_cfg.nexus_api_key = "test_api_key"
    mock_app.global_cfg.theme_preference = "darkly"
    mock_app.global_cfg.sandbox_enabled = True
    mock_app.style.colors.bg = "#333333"
    mock_app.style.colors.primary = "#333333"
    mock_app.style.colors.inputbg = "#333333"
    with (
        patch("ttkbootstrap.Style"),
        patch("tkinter.ttk.Style.lookup", return_value="#333333"),
    ):
        dlg = SettingsDialog(tk_root, mock_app)
        assert dlg.nexus_key_var.get() == "test_api_key"
        assert dlg.theme_var.get() == "darkly"

        dlg.destroy()


# (HunkViewer test removed - feature deprecated)
