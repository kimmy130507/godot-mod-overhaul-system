# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025 Kim
#
# This file is part of GMOS.
#
# GMOS is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# GMOS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GMOS.  If not, see <https://www.gnu.org/licenses/>.
"""
GMOS Package Bootstrap
Exposes the Five Pillars: core, io, state, ui, utils.
"""
from typing import TYPE_CHECKING, Any

# Statically import the logic pillars (safe for CLI/Headless)
from gmos import core, io, state, utils

# Lazy-import the UI pillar to avoid initializing Tkinter
# unless explicitly requested or accessed.
if TYPE_CHECKING:
    from gmos import ui

__all__ = ["core", "io", "state", "ui", "utils"]


def __getattr__(name: str) -> Any:
    """Lazy import for gmos.ui to preserve CLI startup speed."""
    if name == "ui":
        from gmos import ui

        return ui
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
