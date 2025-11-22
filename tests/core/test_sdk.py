# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GMOS SDK Test Suite
# Verifies the "Bridge" functionality: Workspace Init -> Change Detection -> Patch Generation

import os
import struct
from typing import Any

import pytest

# Import the new SDK module
from gmos.core.sdk import GodotBridge
from gmos.io import pck

# Check for pyfakefs
try:
    import pyfakefs.fake_filesystem_unittest  # noqa: F401 # type: ignore[reportUnusedImport]

    _pyfakefs_available = True
except ImportError:
    _pyfakefs_available = False


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_sdk_workflow_detects_changes(fs: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    End-to-End test of the SDK logic using a fake filesystem.
    1. Setup a fake game dir with a PCK.
    2. Initialize workspace (extract).
    3. Modify a file in the workspace.
    4. Generate a mod patch and verify the manifest.
    """
    # --- Setup ---
    game_dir = "/game"
    workspace = "/workspace"
    output_mod = "/output_mod"

    fs.create_dir(game_dir)
    fs.create_dir(workspace)
    fs.create_dir(output_mod)

    # Create a fake PCK in game_dir
    pck_path = os.path.join(game_dir, "data.pck")
    # Write a minimal valid PCK header to pass the 'read_pck_header' check if it's called not-mocked
    # Magic: 0x43504447 ('GDPC'), Version 2, Major 0, Minor 0, Patch 0
    # Flags: 0, FileBase: 0, Reserved: 16*4 bytes, Count: 0
    header_bytes = (
        struct.pack("<I", 0x43504447)
        + struct.pack("<I", 2)
        + struct.pack("<I", 0)
        + struct.pack("<I", 0)
        + struct.pack("<I", 0)
        + struct.pack("<I", 0)
        + struct.pack("<Q", 0)
        + (b"\x00" * (16 * 4))
        + struct.pack("<I", 0)
    )
    fs.create_file(pck_path, contents=header_bytes)

    # Mock the extractor to "extract" a vanilla file
    def mock_extract(pck_file: str, out_dir: str) -> int:
        # Create the 'vanilla' file in workspace
        # This simulates extracting res://player.gd
        os.makedirs(os.path.join(out_dir, "scripts"), exist_ok=True)
        with open(os.path.join(out_dir, "scripts", "player.gd"), "w") as f:
            f.write("var speed = 100")
        return 1

    monkeypatch.setattr(pck, "extract_pck", mock_extract)

    # We also need to mock read_pck_header because scan_for_changes uses it
    # to know what files SHOULD exist and their original checksums.
    from gmos.io.pck import PCKFileEntry, PCKHeader

    def mock_read_header(path: str) -> PCKHeader:
        # Return a header describing our fake vanilla file
        # MD5 of "var speed = 100"
        import hashlib

        content = b"var speed = 100"
        md5 = hashlib.md5(content).digest()  # nosec B324

        entry = PCKFileEntry(
            path="res://scripts/player.gd",
            offset=0,
            size=len(content),
            md5=md5,
            flags=0,
        )
        # Pass required fields to dataclass constructor
        return PCKHeader(0, 0, 0, 0, 0, 0, 0, [entry])

    monkeypatch.setattr(pck, "read_pck_header", mock_read_header)

    # --- Execution ---
    bridge = GodotBridge(game_dir, workspace)

    # 1. Init Workspace
    bridge.init_workspace()

    # Verify vanilla file exists
    player_script = os.path.join(workspace, "scripts", "player.gd")
    assert os.path.exists(player_script)

    # 2. Simulate User Edit (Modify file)
    with open(player_script, "w") as f:
        f.write("var speed = 200")  # Changed!

    # 3. Generate Patch
    manifest = bridge.generate_mod_patch(output_mod, "SuperSpeed", "Tester")

    # --- Validation ---
    assert os.path.exists(manifest)

    with open(manifest, "r") as f:
        content = f.read()

    # Check Manifest Content
    assert "Name = SuperSpeed" in content
    # Check FileReplace instruction
    # res://scripts/player.gd = scripts/player.gd
    assert "res://scripts/player.gd" in content
    assert "scripts/player.gd" in content
    assert "[FileReplace]" in content

    # Verify the modified file was copied to output
    exported_script = os.path.join(output_mod, "scripts", "player.gd")
    assert os.path.exists(exported_script)
    with open(exported_script, "r") as f:
        assert f.read() == "var speed = 200"
