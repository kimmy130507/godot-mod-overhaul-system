# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path

import pytest

from gmos.io.cache import detect_godot_version, get_cache_path, purge_cache


def test_detect_godot_version_missing_metadata(tmp_path: Path) -> None:
    """Verify missing metadata returns 0 (Unknown) and cache path falls back safely."""
    proj = tmp_path / "project.godot"
    proj.touch()
    assert detect_godot_version(str(tmp_path)) == 0
    assert get_cache_path(str(tmp_path)) == os.path.join(str(tmp_path), ".import")


def test_detect_godot_version_godot3(tmp_path: Path) -> None:
    """Verify config_version parameter correctly identifies Godot 3."""
    proj = tmp_path / "project.godot"
    proj.write_text("config_version=4\n", encoding="utf-8")
    assert detect_godot_version(str(tmp_path)) == 3
    assert get_cache_path(str(tmp_path)) == os.path.join(str(tmp_path), ".import")


def test_detect_godot_version_godot4(tmp_path: Path) -> None:
    """Verify config_version parameter correctly identifies Godot 4."""
    proj = tmp_path / "project.godot"
    proj.write_text("config_version=5\n", encoding="utf-8")
    assert detect_godot_version(str(tmp_path)) == 4
    assert get_cache_path(str(tmp_path)) == os.path.join(
        str(tmp_path), ".godot", "imported"
    )


def test_detect_godot_version_spaced_formatting(tmp_path: Path) -> None:
    """Verify resilience to whitespace formatting."""
    proj = tmp_path / "project.godot"
    proj.write_text("  config_version = 5  \n", encoding="utf-8")
    assert detect_godot_version(str(tmp_path)) == 4


def test_detect_godot_version_godot4_hidden_dir(tmp_path: Path) -> None:
    """Verify Godot 4 detection via the .godot directory fallback."""
    godot_dir = tmp_path / ".godot"
    godot_dir.mkdir()
    assert detect_godot_version(str(tmp_path)) == 4


def test_detect_godot_version_pck_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify detection via the PCK header for compiled games."""

    def mock_get_pck(game_dir: str) -> str:
        return "dummy.pck"

    monkeypatch.setattr("gmos.io.cache.get_main_pck_path", mock_get_pck)

    class DummyHeader:
        major = 4

    def mock_read_header(pck_path: str) -> DummyHeader:
        return DummyHeader()

    monkeypatch.setattr("gmos.io.cache.read_pck_header", mock_read_header)

    assert detect_godot_version(str(tmp_path)) == 4


def test_purge_cache_safety(tmp_path: Path) -> None:
    """Verify cache purging prevents accidental deletion of exported runtime environments."""

    # Should raise error if project.godot is missing (indicates packaged game)
    with pytest.raises(PermissionError, match="project.godot"):
        purge_cache(str(tmp_path))

    # Add project.godot and test valid purge
    proj = tmp_path / "project.godot"
    proj.touch()

    cache_dir = tmp_path / ".import"
    cache_dir.mkdir()
    (cache_dir / "test.stex").touch()

    count = purge_cache(str(tmp_path))
    assert count == 1
    assert not (cache_dir / "test.stex").exists()
