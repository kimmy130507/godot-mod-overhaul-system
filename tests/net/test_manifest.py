# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
from unittest.mock import MagicMock

from gmos.net.manifest import (
    LobbyManifest,
    ManifestEntry,
    compute_dir_hash,
    generate_manifest,
)


def test_lobby_manifest_json_serialization() -> None:
    """Verify serialization and deserialization of the P2P LobbyManifest."""
    mods = [
        ManifestEntry(
            mod_id="mod1",
            name="Mod One",
            version="1.0",
            provider="Local",
            archive_hash="hash123",
            download_url="http://dl",
            file_size=1024,
        )
    ]
    manifest = LobbyManifest("TestHost", "1.0.0", 123456789.0, mods)

    json_str = manifest.to_json()
    assert "TestHost" in json_str
    assert "hash123" in json_str

    loaded = LobbyManifest.from_json(json_str)
    assert loaded.host_name == "TestHost"
    assert len(loaded.mods) == 1
    assert loaded.mods[0].mod_id == "mod1"
    assert loaded.mods[0].file_size == 1024


def test_compute_dir_hash(tmp_path: Path) -> None:
    """Verify stable hashing of directories and cache hit logic."""
    file1 = tmp_path / "file1.txt"
    file2 = tmp_path / "file2.txt"
    file1.write_text("hello", encoding="utf-8")
    file2.write_text("world", encoding="utf-8")

    hash1 = compute_dir_hash(str(tmp_path))

    # Cache hit check
    hash1_cached = compute_dir_hash(str(tmp_path))
    assert hash1 == hash1_cached

    # Modify file to invalidate cache
    file1.write_text("hello modified", encoding="utf-8")
    # Change the modified time slightly to ensure the OS registers it for the test
    os.utime(
        str(file1), (os.path.getatime(str(file1)), os.path.getmtime(str(file1)) + 1)
    )

    hash2 = compute_dir_hash(str(tmp_path))
    assert hash1 != hash2


def test_generate_manifest() -> None:
    """Verify generate_manifest extracts mod data from a mocked Session."""
    mock_session = MagicMock()

    mock_mod = MagicMock()
    mock_mod.is_enabled = True
    mock_mod.path = "/fake/path/ModABC"
    mock_mod.config = {"Sections": {"ModInfo": {"Name": "TestMod", "Version": "2.0"}}}

    mock_session.mods = [mock_mod]
    mock_session.refresh_mods.return_value = []

    manifest = generate_manifest(mock_session, "MyHost")

    assert manifest.host_name == "MyHost"
    assert len(manifest.mods) == 1
    assert manifest.mods[0].name == "TestMod"
    assert manifest.mods[0].version == "2.0"
