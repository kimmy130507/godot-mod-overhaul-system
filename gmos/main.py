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
Primary package entrypoint and CLI/GUI launcher.
Handles CLI vs. GUI, application lock, and contains all CLI-specific logic.
"""

import argparse
import json
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional, cast

from gmos import utils
from gmos.core.patcher import run_patcher, save_dryrun_artifact
from gmos.io import atomic_write_copy
from gmos.utils import LOG_DIR, logger


def _acquire_lock_or_report() -> bool:
    try:
        from gmos.io.locking import acquire_app_lock
    except Exception:
        acquire_app_lock = None  # type: ignore[assignment]

    got_lock = False
    max_attempts = 10
    for _ in range(max_attempts):
        try:
            if acquire_app_lock is None:
                got_lock = True
            else:
                # call with no args; locking impl may accept defaults
                got_lock = acquire_app_lock()
        except Exception:
            got_lock = False

        if got_lock:
            break
        time.sleep(0.03 + (secrets.randbelow(1000) / 1000.0) * 0.12)
    return got_lock


# --- CLI Logic ---


def _load_instructions_from_json(path: str) -> List[Any]:
    with open(path, "r", encoding="utf-8") as f:
        return cast(List[Any], json.load(f))


def headless_dryrun(game_dir: str, instructions: List[Any]) -> Optional[str]:
    """
    Run run_patcher in a temp workspace and persist dry-run artifact.
    instructions should be a Python list (as run_patcher expects).
    Returns path to created bundle or None.
    """
    if not os.path.isdir(game_dir):
        raise FileNotFoundError(f"Game directory not found: {game_dir}")

    # Create a temporary simulate work dir and run patcher
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_work_root = os.path.join(temp_dir, "sim_work")
        Path(temp_work_root).mkdir(parents=True, exist_ok=True)
        # Suppress Pylance error due to un-typed `patch_plan` argument in gmos.patcher
        sim_log = run_patcher(temp_work_root, instructions)

        # Save dryrun artifact to LOG_DIR
        # Call the consolidated function from patcher
        bundle = save_dryrun_artifact(
            sim_log,
            temp_work_root,
            game_dir,
            out_dir=LOG_DIR,
            combined_diff=None,  # No diff generated in headless mode
        )
        return bundle


# Custom Namespace for type hints on parsed arguments
class HeadlessArgs(argparse.Namespace):
    """Type-hinted container for _cli_main's arguments."""

    game_dir: str
    instructions: Optional[str]
    out: Optional[str]


def _cli_main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="GMOS headless dry-run")
    p.add_argument("--game-dir", "-g", required=True, help="Game directory to patch")
    p.add_argument(
        "--instructions", "-i", required=False, help="JSON file with instructions list"
    )
    p.add_argument(
        "--out",
        "-O",
        required=False,
        help="Optional output path for created support bundle (.zip)",
    )
    # Use custom namespace to provide type hints for args members
    args: HeadlessArgs = p.parse_args(argv, namespace=HeadlessArgs())

    instr: List[Any] = []
    if args.instructions:
        try:
            # Use local function
            instr = _load_instructions_from_json(args.instructions)
        except Exception as e:
            logger.exception("Failed loading instructions file: %s", e)
            print(f"Error: failed to load instructions: {e}", file=sys.stderr)
            return 2

    try:
        bundle = headless_dryrun(args.game_dir, instr)
        if args.out and bundle:
            # copy to requested path
            try:
                atomic_write_copy(bundle, args.out)
                print(args.out)
            except Exception as e:
                logger.exception("Failed copying bundle to out path: %s", e)
                print(
                    f"Error: failed copying bundle to {args.out}: {e}", file=sys.stderr
                )
                return 3
        else:
            print(bundle or "")
        return 0
    except Exception as e:
        logger.exception("Headless dry-run failed: %s", e)
        print(f"Error: {e}", file=sys.stderr)
        return 1


# --- Main Application Entry ---


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    utils.set_windows_appid("com.kim.gmos")
    got_lock = _acquire_lock_or_report()
    if not got_lock:
        # CLI invocation -> nonzero exit; GUI -> show dialog if available
        if argv:
            print("Another GMOS instance is already running. Exiting.", file=sys.stderr)
            return 2
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "Already Running",
                "Another GMOS instance is already running. Close it and try again.",
            )
        except Exception:
            print("Another GMOS instance is already running. Exiting.", file=sys.stderr)
        return 2

    # CLI mode when args present
    if argv:
        # Call local _cli_main
        return _cli_main(argv)

    # GUI mode (no args)

    try:
        from gmos.io.locking import wire_workroot_locking
        from gmos.state.config import ensure_config, get_config_path, load_config
        from gmos.ui import App

        # Now, create the main App window. This establishes the Tk root,
        # which is required before any setup dialogs run.
        app = App()
        # --- Proactive Setup Check ---
        config_path = get_config_path()
        config = load_config(config_path)

        # Define what "valid" means
        # Check if the value is present and is a path-like string before calling os.path.isdir
        game_dir_val = config.get("game_dir")
        is_valid = config and game_dir_val and os.path.isdir(game_dir_val)

        if not is_valid:
            logger.info("No valid config found. Running SetupWizard.")

            # Disabling the window keeps it mapped/visible, which prevents the
            # Toplevel wizard from inheriting a 'hidden' state on Windows.
            withdraw_fallback = False
            try:
                app.attributes("-disabled", 1)  # type: ignore [reportUnknownMemberType]
            except Exception:
                # Fallback to withdrawing if disabling fails
                app.withdraw()
                withdraw_fallback = True

            config = ensure_config(config_path)
            # Check for success after the wizard runs
            new_game_dir_val = config.get("game_dir")
            setup_was_successful = (
                config and new_game_dir_val and os.path.isdir(new_game_dir_val)
            )

            if setup_was_successful:
                # Success path: Destroy the stale app and create a new one.
                logger.info(
                    "Setup successful. Destroying stale App instance for clean reload."
                )

                # Destroy the stale app instance (including the Tk root)
                try:
                    app.destroy()
                except Exception as e:
                    logger.debug(
                        "Failed to destroy stale app instance (ignored): %s", e
                    )

                # Re-create the main App window. This will force it to load the *new* config.
                app = App()

            else:
                # Failure/Cancel path: Show warning and restore the original (stale) app.
                logger.warning("Setup was cancelled. Exiting.")
                try:
                    from tkinter import messagebox

                    messagebox.showwarning(
                        "Setup Failed",
                        "Configuration setup was cancelled or incomplete. You must configure the game paths manually in the Settings tab to continue.",
                    )
                except Exception:
                    pass
            # Setup is complete, restore the main window state.
            if withdraw_fallback:
                # If we fell back to withdrawing, deiconify it now
                app.deiconify()
            else:
                # Re-enable the window
                try:
                    app.attributes("-disabled", 0)  # type: ignore [reportUnknownMemberType]
                except Exception:
                    pass

        if wire_workroot_locking is not None:
            try:
                wire_workroot_locking(app)
            except Exception as e:
                # best-effort logging
                try:
                    logger.exception("Failed wiring workroot locking: %s", e)
                except Exception:
                    import warnings

                    warnings.warn(
                        f"Failed wiring workroot locking: {e}",
                        RuntimeWarning,
                        stacklevel=2,
                    )

        if hasattr(app, "mainloop"):
            app.mainloop()
        elif hasattr(app, "run"):
            app.run()  # type: ignore [reportUnknownMemberType, reportAttributeAccessIssue]

        return 0
    except Exception as e:
        try:
            logger.exception("GUI startup failed: %s", e)
        except Exception:
            import warnings

            warnings.warn(
                f"Logger unavailable when reporting GUI startup failure: {e}",
                RuntimeWarning,
                stacklevel=2,
            )
        try:
            from tkinter import messagebox

            messagebox.showerror(
                "Startup Error",
                f"Failed to start GUI. See log: {LOG_DIR}/gmos.log\n\n{e}",
            )
        except Exception:
            print(f"Failed to start GUI: {e}", file=sys.stderr)
        return 1
