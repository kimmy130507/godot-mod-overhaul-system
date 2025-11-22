# SPDX-FileCopyrightText: 2025 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pytest test bootstrap for GMOS tests.

- Runs a one-time sweep of orphaned .gmos_tmp_* files in the pytest base temp
  directory and the repository working directory (configurable).
- Ensures the global replace shim is disabled during tests to avoid interactive
  permission flows.
- Provides a small fixture `gmos_paths` (list[str]) for tests that want the
  canonical work dirs used by the sweep.
"""

import os
from pathlib import Path
from typing import Dict, Generator, List

import pytest


# Prefer importing the sweep util lazily so importing tests doesn't require full app import.
def _maybe_sweep_orphans(paths: List[str], *, age_threshold: float = 0.5) -> None:
    try:
        # Import locally to avoid side-effects at import-time of the test runner.
        from gmos.io import sweep_orphan_gmos_temps

        sweep_orphan_gmos_temps(paths, age_threshold=age_threshold)
    except Exception:
        # Best-effort: don't fail test session startup if sweep isn't available.
        pass


@pytest.fixture(scope="session", autouse=True)
def session_sweep_orphans(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    """
    Autouse session fixture: sweep orphan .gmos_tmp_* files before tests run.

    Uses the pytest temp base directory and current working directory to remove
    stale artifacts that could affect tests. Uses a small age_threshold (0.5s)
    so freshly-created temps from the current run are not removed.
    """
    base_temp = tmp_path_factory.getbasetemp()
    repo_cwd = Path.cwd()

    # Ensure the global replace shim is disabled in tests
    os.environ.setdefault("GMOS_APPLY_REPLACE_SHIM", "0")

    paths = [str(base_temp), str(repo_cwd)]
    _maybe_sweep_orphans(paths, age_threshold=0.5)

    # yield to run tests; no cleanup necessary at session end
    yield


@pytest.fixture
def gmos_paths(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> Dict[str, str]:
    """
    Convenience fixture returning canonical work directories used by tests.
    - tmp_path: per-test temporary dir (pytest-provided)
    """
    return {
        "test_tmp": str(tmp_path),
        "session_tmp_base": str(tmp_path_factory.getbasetemp()),
        "cwd": str(Path.cwd()),
    }
