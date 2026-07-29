# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GMOS SDK Test Suite
# Verifies the "Bridge" functionality: Workspace Init -> Change Detection -> Patch Generation

import os
from typing import Any
from unittest.mock import patch

import pytest

from gmos.core.sdk import GodotBridge

try:
    import pyfakefs.fake_filesystem_unittest  # noqa: F401 # type: ignore[reportUnusedImport]

    _pyfakefs_available = True
except ImportError:
    _pyfakefs_available = False


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_smart_diff_variable_patch(fs: Any) -> None:
    """
    Feature: If a script only changes a variable value,
    generate [VariablePatch] instead of [FileReplace].
    """
    # Use fs fixture for path creation
    tmp_path = "/smart_diff_work"
    fs.create_dir(tmp_path)

    workspace = os.path.join(tmp_path, "ws")
    os.makedirs(workspace)
    output = os.path.join(tmp_path, "out")
    os.makedirs(output)

    # 1. Create a fake 'player.gd' in workspace with NEW value
    script_path = os.path.join(workspace, "player.gd")
    with open(script_path, "w") as f:
        f.write("extends Node\nvar speed = 200\n")

    # 2. Mock PCK content (OLD value)
    vanilla_content = b"extends Node\nvar speed = 100\n"

    bridge = GodotBridge(tmp_path, workspace)

    # Ensure dummy pck exists for PCKReader check
    pck_path = os.path.join(tmp_path, "dummy.pck")
    with open(pck_path, "wb") as f:
        f.write(b"GDPC" + b"\x00" * 500)
    bridge.pck_path = pck_path

    # Mock PCKReader to return vanilla content
    with patch("gmos.core.sdk.PCKReader") as MockPCKReader:
        MockPCKReader.return_value.__enter__.return_value.read_file.return_value = (
            vanilla_content
        )
        # Force scan_for_changes to detect this specific file
        with patch.object(
            bridge, "scan_for_changes", return_value={"res://player.gd": "patched"}
        ):
            draft = bridge.build_patch_draft("SpeedMod")
            manifest_path = bridge.commit_mod_patch(
                output, "SpeedMod", "Tester", "1.0.0", "Test Mod", draft
            )

    # 3. Verify the generated manifest
    with open(manifest_path, "r") as f:
        content = f.read()

    # Should contain VariablePatch section
    assert "[VariablePatch]" in content
    # Should NOT contain FileReplace
    assert "[FileReplace]" not in content
    assert "res://player.gd::speed" in content
