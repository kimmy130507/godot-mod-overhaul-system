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
"""Delegate python -m gmos to gmos.main:main."""

import sys
import warnings
from pathlib import Path
from typing import Callable, List, Optional

try:
    from gmos.io import sweep_orphan_gmos_temps
except ImportError:
    # If this is run before gmos.io is fully available, handle gracefully
    sweep_orphan_gmos_temps = None  # type: ignore[assignment]
    warnings.warn(
        "Could not import sweep_orphan_gmos_temps for cleanup.",
        RuntimeWarning,
        stacklevel=2,
    )


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    # Startup Cleanup Call (Placed before main package import)
    # This is the ideal placement: as early as possible in the execution flow.
    if sweep_orphan_gmos_temps is not None:
        try:
            # Define the critical directories to scan for orphaned files
            work_dirs = [str(Path.cwd()), str(Path.home() / ".gmos")]

            # Run the cleanup. Use the robust age threshold (5 minutes default)
            removed_count = sweep_orphan_gmos_temps(work_dirs, age_threshold=300.0)

            if removed_count > 0:
                print(f"GMOS: Cleaned up {removed_count} orphaned temporary files.")

        except Exception as e:
            print(f"GMOS Cleanup Failed: {e}", file=sys.stderr)
    # ----------------------------------------------------------- #

    try:
        package_main: Callable[[Optional[List[str]]], int]
        from gmos.main import main as package_main
    except Exception as e:
        print(f"gmos: failed to import entrypoint: {e}", file=sys.stderr)
        return 2

    try:
        rc = package_main(argv)
        return int(rc)
    except SystemExit:
        raise
    except Exception as e:
        try:
            from gmos.utils import logger

            logger.exception("Uncaught exception in gmos.__main__: %s", e)
        except Exception:
            warnings.warn(
                f"logger unavailable while reporting startup error: {e}",
                RuntimeWarning,
                stacklevel=2,
            )
        print(f"Error launching gmos: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
