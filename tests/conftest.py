# SPDX-FileCopyrightText: 2025-2026 Kim
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Pytest test bootstrap for GMOS tests.
"""

import os
import sys
from pathlib import Path
from typing import Callable, Dict, Generator, List, Optional, Union, cast
from unittest.mock import MagicMock

import pytest

if sys.platform != "win32":
    sys.modules["winreg"] = MagicMock()
from gmos.utils import ModConfig


# --- Global Mocking for UI Dependencies ---
@pytest.fixture(scope="function", autouse=True)
def mock_ui_dependencies() -> Generator[None, None, None]:
    """
    Globally mocks heavy UI libraries to prevent import-side effects
    (like font loading or widget patching) during testing.
    """
    from unittest.mock import patch

    try:
        with (
            patch(
                "ttkbootstrap.style.Bootstyle.update_ttk_widget_style", return_value=""
            ),
            patch("ttkbootstrap.style.Style.theme_use", return_value="darkly"),
            patch("ttkbootstrap.style.StyleBuilderTTK.scale_size", return_value=1),
            patch("ttkbootstrap.style.Style.get_instance") as mock_get_inst,
            patch("gmos.utils.load_icon", return_value=None),
            patch("gmos.utils.extract_icon_from_exe", return_value=None),
            patch("gmos.ui.widgets.ImageCache.get_thumbnail", return_value=None),
        ):
            mock_get_inst.return_value.style_exists_in_theme.return_value = True
            yield
    except ImportError:
        yield


@pytest.fixture
def write_file() -> Callable[[Path, str], Path]:
    """Fixture that returns a helper function to write text to a path."""

    def _write(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def create_mod_config(
    write_file: Callable[[Path, str], Path],
) -> Callable[..., ModConfig]:
    """Helper to create a valid mod directory and config structure."""

    def _create(root: Path, name: str, deps: Optional[List[str]] = None) -> ModConfig:
        mod_dir = root / name
        mod_dir.mkdir(parents=True, exist_ok=True)

        # Build the mod.mos content
        lines = [
            "[ModInfo]",
            f"Name={name}",
            "Version=1.0",
        ]

        # Prepare sections dict for the return object
        sections: Dict[str, Union[List[str], Dict[str, str]]] = {}

        if deps:
            lines.append("[Dependencies]")
            dep_line = f"requires = {', '.join(deps)}"
            lines.append(dep_line)
            sections["Dependencies"] = [dep_line]

        write_file(mod_dir / "mod.mos", "\n".join(lines))

        return cast(ModConfig, {"Path": str(mod_dir), "Sections": sections})

    return _create


@pytest.fixture
def game_project(tmp_path: Path, write_file: Callable[[Path, str], Path]) -> Path:
    """Creates a standard fake game environment."""
    game_dir = tmp_path / "game"
    game_dir.mkdir()

    scripts_dir = game_dir / "scripts"
    scripts_dir.mkdir()

    player_gd = scripts_dir / "player.gd"
    write_file(player_gd, "extends Node\nfunc _ready():\n\tpass")

    return game_dir


def _maybe_sweep_orphans(paths: List[str], *, age_threshold: float = 0.5) -> None:
    try:
        from gmos.io import sweep_orphan_gmos_temps

        sweep_orphan_gmos_temps(paths, age_threshold=age_threshold)
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def session_sweep_orphans(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    """Autouse session fixture: sweep orphan .gmos_tmp_* files before tests run."""
    base_temp = tmp_path_factory.getbasetemp()
    repo_cwd = Path.cwd()

    os.environ.setdefault("GMOS_APPLY_REPLACE_SHIM", "0")

    paths = [str(base_temp), str(repo_cwd)]
    _maybe_sweep_orphans(paths, age_threshold=0.5)
    yield


@pytest.fixture
def gmos_paths(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> Dict[str, str]:
    return {
        "test_tmp": str(tmp_path),
        "session_tmp_base": str(tmp_path_factory.getbasetemp()),
        "cwd": str(Path.cwd()),
    }
