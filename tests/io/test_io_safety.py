# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
# GMOS I/O Safety Test Suite
import os
import sys
from typing import Any

import pytest

from gmos.core.patcher import ensure_within

# Import the I/O primitives to test
from gmos.io import atomic_write_with_backup

# Check for pyfakefs
try:
    import pyfakefs.fake_filesystem_unittest  # noqa: F401 # type: ignore[reportUnusedImport]

    _pyfakefs_available = True
except ImportError:
    _pyfakefs_available = False


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_atomic_write_creates_backup(fs: Any) -> None:
    """Verify .bak creation behavior using a fake filesystem."""
    # fs fixture is provided by pytest-pyfakefs if installed

    # Setup
    work_dir = "/game"
    fs.create_dir(work_dir)
    target_file = os.path.join(work_dir, "data.txt")

    # Initial write
    fs.create_file(target_file, contents="Version 1")

    # Perform atomic write
    atomic_write_with_backup(target_file, "Version 2")

    # Verify target updated
    with open(target_file, "r") as f:
        assert f.read() == "Version 2"

    # Verify backup created
    backup_file = target_file + ".bak"
    assert os.path.exists(backup_file)
    with open(backup_file, "r") as f:
        assert f.read() == "Version 1"


@pytest.mark.skipif(
    not _pyfakefs_available or sys.platform == "win32",
    reason="pyfakefs missing OR Windows chmod does not block owner writes",
)
def test_atomic_write_handles_readonly_dir(fs: Any) -> None:
    """Verify graceful failure when directory is not writable."""
    protected_dir = "/protected"
    fs.create_dir(protected_dir)
    target = os.path.join(protected_dir, "file.txt")

    # Remove write permission from parent dir
    os.chmod(protected_dir, 0o444)

    # Should raise PermissionError (or OSError) handled by our retry logic
    # Since retry logic eventually gives up and raises, we expect an exception.
    # We assert it doesn't hang or crash unpredictably.
    with pytest.raises(OSError):
        atomic_write_with_backup(target, "Fail")

    assert not os.path.exists(target)


@pytest.mark.skipif(not _pyfakefs_available, reason="pyfakefs not installed")
def test_ensure_within_safety(fs: Any) -> None:
    """Verify path traversal protection."""
    root = "/game"
    fs.create_dir(root)

    # Valid case
    assert ensure_within(root, "/game/subdir/file.txt") is True

    # Invalid case (traversal)
    # resolve() in pyfakefs works similarly to real fs
    fs.create_dir("/outside")

    with pytest.raises(RuntimeError, match="Path escape"):
        ensure_within(root, "/outside/hacker.txt")

    with pytest.raises(RuntimeError, match="Path escape"):
        ensure_within(root, "/game/../outside/file.txt")
