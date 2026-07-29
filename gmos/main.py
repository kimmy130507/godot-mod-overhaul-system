# GMOS - Godot Mod Overhaul System
# Copyright (C) 2025-2026 Kim
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
from gmos.core.protocol import register_url_handler, send_link_to_existing_instance
from gmos.io import atomic_write_copy
from gmos.utils import LOG_DIR, logger


def _acquire_lock_or_report() -> bool:
    """
    Attempts to acquire the single-instance application lock.

    Returns True if the lock was successfully acquired (primary instance).
    Returns False if another instance is already running.
    """
    try:
        from gmos.io.locking import acquire_app_lock
    except Exception:
        acquire_app_lock = cast(Any, None)

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
        time.sleep(0.05 + (secrets.randbelow(100) / 1000.0))
    return got_lock


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

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_work_root = os.path.join(temp_dir, "sim_work")
        Path(temp_work_root).mkdir(parents=True, exist_ok=True)
        sim_log = run_patcher(temp_work_root, instructions)

        # Save dryrun artifact
        bundle = save_dryrun_artifact(
            sim_log,
            temp_work_root,
            game_dir,
            out_dir=LOG_DIR,
            combined_diff=None,  # No diff generated in headless mode
        )
        return bundle


class HeadlessArgs(argparse.Namespace):
    """Typed arguments for _cli_main."""

    game_dir: str
    instructions: Optional[str]
    out: Optional[str]


def _cli_main(argv: Optional[List[str]] = None) -> int:
    """
    Handles headless dry-run operations.
    Parses CLI arguments specifically for the headless dry-run mode and saves
    the generated support bundle artifact.
    """
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
    args: HeadlessArgs = p.parse_args(argv, namespace=HeadlessArgs())

    instr: List[Any] = []
    if args.instructions:
        try:
            instr = _load_instructions_from_json(args.instructions)
        except Exception as e:
            logger.exception("Failed loading instructions file: %s", e)
            print(f"Error: failed to load instructions: {e}", file=sys.stderr)
            return 2

    try:
        bundle = headless_dryrun(args.game_dir, instr)
        if args.out and bundle:
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


def main(argv: Optional[List[str]] = None) -> int:
    """
    Primary application entrypoint.

    Execution Flow:
    1. Validates protocol registration (`--register-protocol`).
    2. Parses incoming protocol links (`nxm://...`).
    3. Acquires singleton lock (forwards payload to existing instance via IPC if locked).
    4. Delegates to CLI headless mode or initializes the UI subsystem.
    """
    argv = argv if argv is not None else sys.argv[1:]
    # Check if we are being run just to register the protocol (Admin/UAC)
    if "--register-protocol" in argv:
        try:
            idx = argv.index("--register-protocol")
            proto = argv[idx + 1] if idx + 1 < len(argv) else "nxm"
            register_url_handler(proto)
            return 0
        except Exception as e:
            print(f"Registration failed: {e}", file=sys.stderr)
            return 1

    # Check if we were launched with a Protocol Link (nxm://...)
    pending_link: Optional[str] = None
    for arg in argv:
        if "://" in arg:
            pending_link = arg
            break
    utils.set_windows_appid("com.kim.gmos")

    # Check singleton lock and handle IPC
    got_lock = _acquire_lock_or_report()
    if not got_lock:
        # Secondary instance: Send link or focus command to primary
        payload = pending_link if pending_link else "FOCUS"
        send_link_to_existing_instance(payload)
        # Exit silently
        return 0

    # CLI mode
    if argv and not pending_link:
        return _cli_main(argv)

    # GUI Mode
    try:
        from gmos.ui.app import App

        app = App()
        if pending_link:
            app.after(500, lambda: app.handle_protocol_link(pending_link))
        app.mainloop()

        return 0
    except Exception as e:
        logger.exception("GUI startup failed: %s", e)
        try:
            from tkinter import messagebox

            messagebox.showerror("Startup Error", f"Failed to start GUI.\n\n{e}")
        except Exception:
            pass
        return 1
