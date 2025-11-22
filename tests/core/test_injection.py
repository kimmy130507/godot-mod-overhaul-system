# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GMOS Test Suite: Runtime Injection
# Verifies that the SandboxInjector correctly modifies project.godot

import os
from typing import Any

import pytest

from gmos.core.injection import SandboxInjector

# Check for pyfakefs
try:
    import pyfakefs.fake_filesystem_unittest  # noqa: F401 # type: ignore[reportUnusedImport]

    _pyfakefs_available = True
except ImportError:
    _pyfakefs_available = False


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_sandbox_injection(fs: Any) -> None:
    game_dir = "/game"
    fs.create_dir(game_dir)

    # 1. Create dummy project.godot
    proj_content = """
[application]
config/name="Test Game"

[autoload]
ExistingGlobal="*res://global.gd"
"""
    proj_path = os.path.join(game_dir, "project.godot")
    fs.create_file(proj_path, contents=proj_content)

    injector = SandboxInjector(game_dir)

    # 2. Verify initially not injected
    assert not injector.is_injected()

    # 3. Perform Injection
    assert injector.inject() is True

    # 4. Verify Injection State
    assert injector.is_injected()

    # 5. Verify File Content
    with open(proj_path, "r") as f:
        new_content = f.read()

    # Should contain the sandbox entry
    assert 'GMOS_Sandbox="*res://gmos_sandbox.tscn"' in new_content
    # Should preserve existing entry
    assert 'ExistingGlobal="*res://global.gd"' in new_content


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_injector_creates_autoload_section(fs: Any) -> None:
    """Verify injector works even if [autoload] section is missing."""
    game_dir = "/game_minimal"
    fs.create_dir(game_dir)

    proj_content = '[application]\nconfig/name="Minimal"'
    proj_path = os.path.join(game_dir, "project.godot")
    fs.create_file(proj_path, contents=proj_content)

    injector = SandboxInjector(game_dir)
    injector.inject()

    with open(proj_path, "r") as f:
        new_content = f.read()

    assert "[autoload]" in new_content
    assert 'GMOS_Sandbox="*res://gmos_sandbox.tscn"' in new_content
