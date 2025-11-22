# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
from unittest.mock import patch

from gmos.core.sdk import GodotBridge


def test_smart_diff_variable_patch(tmp_path: Path) -> None:
    """
    Feature: If a script only changes a variable value,
    generate [VariablePatch] instead of [FileReplace].
    """
    workspace = tmp_path / "ws"
    workspace.mkdir()
    output = tmp_path / "out"
    output.mkdir()

    # 1. Create a fake 'player.gd' in workspace
    # The 'new' version has speed = 200
    script_path = workspace / "player.gd"
    script_path.write_text("extends Node\nvar speed = 200\n", encoding="utf-8")

    # 2. Mock the PCK reader to return the 'vanilla' version
    # The 'old' version has speed = 100
    vanilla_content = b"extends Node\nvar speed = 100\n"

    bridge = GodotBridge(str(tmp_path), str(workspace))
    # Mock pck_path existence so it proceeds
    bridge.pck_path = "dummy.pck"

    with patch("gmos.io.pck.get_file_content", return_value=vanilla_content):
        # We also mock scan_for_changes to simply return our file
        with patch.object(bridge, "scan_for_changes", return_value=["res://player.gd"]):

            manifest_path = bridge.generate_mod_patch(str(output), "SpeedMod", "Tester")

    # 3. Verify the generated manifest
    with open(manifest_path, "r") as f:
        content = f.read()

    # Should contain VariablePatch section
    assert "[VariablePatch]" in content
    # Should NOT contain FileReplace for this file (it was handled smartly)
    assert "[FileReplace]" not in content
    # Check syntax: res://player.gd::speed = ...
    assert "res://player.gd::speed" in content
