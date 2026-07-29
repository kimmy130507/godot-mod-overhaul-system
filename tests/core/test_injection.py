# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
import os
from typing import Any, Generator

import pytest

from gmos.core.injection import SandboxInjector

# Check for pyfakefs
try:
    import pyfakefs.fake_filesystem_unittest  # noqa: F401 # type: ignore[reportUnusedImport]

    _pyfakefs_available = True
except ImportError:
    _pyfakefs_available = False


@pytest.fixture
def injector_env(fs: Any) -> Generator[str, None, None]:
    """
    Sets up a fake filesystem game directory.
    Returns the game_dir path string.
    """
    game_dir = "/game/Project"
    fs.create_dir(game_dir)
    yield game_dir


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_inject_binary_fallback(fs: Any, injector_env: str) -> None:
    """Verifies that the SandboxInjector correctly falls back to 'override.cfg'."""
    game_dir = injector_env

    # Simulate a binary project environment (Create file BEFORE init)
    fs.create_file(f"{game_dir}/project.binary")

    # Instantiate AFTER file creation so detection works
    injector = SandboxInjector(game_dir)

    # Test Detection Logic
    assert injector._using_override is True  # type: ignore
    assert injector._target_file == "override.cfg"  # type: ignore
    assert not injector.is_injected()

    # Test Injection
    success = injector.inject()
    assert success is True

    # Verify Configuration Write
    override_path = f"{game_dir}/override.cfg"
    assert os.path.exists(override_path)

    with open(override_path, "r") as f:
        content = f.read()

    assert "[autoload]" in content
    assert 'GMOS_Sandbox="*res://gmos_sandbox.tscn"' in content


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_inject_standard_text_project(fs: Any, injector_env: str) -> None:
    """Regression Test: Standard project.godot injection."""
    game_dir = injector_env

    proj_content = '[application]\nconfig/name="MyGame"\n\n[autoload]\nExistingGlobal="*res://global.gd"\n'
    fs.create_file(f"{game_dir}/project.godot", contents=proj_content)

    # Instantiate AFTER file creation
    injector = SandboxInjector(game_dir)

    assert injector._using_override is False  # type: ignore
    assert injector._target_file == "project.godot"  # type: ignore
    assert not injector.is_injected()

    # Inject
    assert injector.inject() is True
    assert injector.is_injected()

    with open(f"{game_dir}/project.godot", "r") as f:
        content = f.read()

    assert "[autoload]" in content
    assert 'GMOS_Sandbox="*res://gmos_sandbox.tscn"' in content
    assert 'config/name="MyGame"' in content


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_injector_creates_autoload_section(fs: Any, injector_env: str) -> None:
    """Verify injector works even if [autoload] section is missing entirely."""
    game_dir = injector_env

    proj_content = '[application]\nconfig/name="Minimal"'
    proj_path = os.path.join(game_dir, "project.godot")
    fs.create_file(proj_path, contents=proj_content)

    injector = SandboxInjector(game_dir)
    injector.inject()

    with open(proj_path, "r") as f:
        new_content = f.read()

    assert "[autoload]" in new_content
    assert 'GMOS_Sandbox="*res://gmos_sandbox.tscn"' in new_content


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_injection_idempotency(fs: Any, injector_env: str) -> None:
    """Verifies that injecting twice doesn't duplicate entries or error out."""
    game_dir = injector_env
    fs.create_file(f"{game_dir}/project.godot")

    injector = SandboxInjector(game_dir)

    # First injection
    assert injector.inject() is True

    # Second injection should detect existing autoload
    assert injector.inject() is False

    with open(f"{game_dir}/project.godot", "r") as f:
        content = f.read()

    assert content.count("GMOS_Sandbox") == 1


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_remove_sandbox(fs: Any, injector_env: str) -> None:
    """Verifies correct removal/cleanup of the sandbox entry."""
    game_dir = injector_env
    fs.create_file(f"{game_dir}/project.godot")

    injector = SandboxInjector(game_dir)
    injector.inject()

    assert injector.is_injected() is True

    # Remove
    success = injector.remove()
    assert success is True
    assert injector.is_injected() is False

    with open(f"{game_dir}/project.godot", "r") as f:
        content = f.read()

    assert "GMOS_Sandbox" not in content
