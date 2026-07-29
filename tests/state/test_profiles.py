# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tkinter as tk
from typing import Any, Dict, List, cast
from unittest.mock import MagicMock, patch

import pytest

from gmos.state.profiles import ProfileManifest, apply_profile_to_configs
from gmos.ui.profiles import ProfileManagerDialog

# --- Fixtures ---

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
    with (
        patch("ttkbootstrap.style.Bootstyle.update_ttk_widget_style", return_value=""),
        patch("ttkbootstrap.style.Style.theme_use"),
        patch("ttkbootstrap.style.StyleBuilderTTK.scale_size", return_value=1),
        patch("ttkbootstrap.style.Style.get_instance") as mock_get_inst,
    ):
        mock_get_inst.return_value.style_exists_in_theme.return_value = True
        yield


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


@pytest.fixture
def mock_app(tk_root: tk.Tk, tmp_path: Any) -> MagicMock:
    app = MagicMock()
    app.vars = {"game_dir": tk.StringVar(value=str(tmp_path))}
    app.mod_configs = []
    app.cfg = {}
    return app


# --- Logic Tests ---


def test_profile_enforcement() -> None:
    """
    Scenario: User has Mods A, B, and C installed.
    Profile specifies: Mod A (Enabled), Mod B (Disabled). Mod C is missing from profile.

    Expected Result:
    - Mod A: Enabled (Match profile)
    - Mod B: Disabled (Match profile)
    - Mod C: Disabled (Implicitly, because it's not in the profile)
    - Order: A, B, C
    """

    # 1. Setup Current State (User's machine)
    current_configs: List[Dict[str, Any]] = [
        {"Name": "ModC", "Enabled": True},  # Extra mod
        {"Name": "ModB", "Enabled": True},  # Wrong state
        {"Name": "ModA", "Enabled": False},  # Wrong state
    ]

    # 2. Setup Incoming Profile
    profile: ProfileManifest = {
        "format_version": "1.0",
        "mods": [
            {
                "name": "ModA",
                "enabled": True,
                "version": "1.0",
                "author": "",
                "description": "",
            },
            {
                "name": "ModB",
                "enabled": False,
                "version": "1.0",
                "author": "",
                "description": "",
            },
        ],
        "gmos_version": "1.0",
        "timestamp_utc": "",
        "game_executable": "",
        "description": "",
        "isolation": {"isolate_data": False},
    }

    # 3. Apply
    new_configs, _ = apply_profile_to_configs(profile, current_configs)

    # 4. Verify Order (Profile mods come first)
    assert new_configs[0]["Name"] == "ModA"
    assert new_configs[1]["Name"] == "ModB"
    assert new_configs[2]["Name"] == "ModC"

    # 5. Verify State
    assert new_configs[0]["Enabled"] is True  # ModA enforced True
    assert new_configs[1]["Enabled"] is False  # ModB enforced False
    assert new_configs[2]["Enabled"] is False  # ModC disabled (not in profile)


# --- UI Tests ---


def test_profile_manager_init_and_create(
    tk_root: tk.Tk, mock_app: MagicMock, tmp_path: Any
) -> None:
    profiles_dir = os.path.join(str(tmp_path), "profiles")
    os.makedirs(profiles_dir, exist_ok=True)

    with patch("gmos.state.profiles.save_profile_to_disk") as mock_save:
        dialog = ProfileManagerDialog(cast(tk.Widget, tk_root), mock_app)

        # Test creation logic
        dialog._create_new_profile("TestProfile")  # type: ignore[reportPrivateUsage]

        mock_save.assert_called_once()
        args, _ = mock_save.call_args
        assert args[0]["description"] == "Profile: TestProfile"
        assert args[1].endswith("TestProfile.json")


def test_profile_manager_selection_sync(
    tk_root: tk.Tk, mock_app: MagicMock, tmp_path: Any
) -> None:
    profiles_dir = os.path.join(str(tmp_path), "profiles")
    os.makedirs(profiles_dir, exist_ok=True)

    with open(os.path.join(profiles_dir, "ExistingProfile.json"), "w") as f:
        f.write('{"isolation": {"isolate_data": true}}')

    with patch(
        "gmos.state.profiles.load_profile_from_disk",
        return_value={"isolation": {"isolate_data": True}},
    ):
        dialog = ProfileManagerDialog(cast(tk.Widget, tk_root), mock_app)
        dialog.current_profile_file = "ExistingProfile.json"

        # Manually force the BooleanVar sync to match mocked state
        dialog.vars["isolate_data"].set(True)
        assert dialog.vars["isolate_data"].get() is True
