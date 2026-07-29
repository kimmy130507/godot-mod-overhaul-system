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
Command Line Interface for GMOS.

Provides direct terminal access to mod management, patching,
and P2P syncing independently of the graphical interface.
"""

import argparse
import logging
import sys
import time
from typing import Any, Dict, List, Optional, cast

from gmos.core.session import GmosSession
from gmos.net.p2p import P2PClient, P2PHost
from gmos.state import policy
from gmos.utils import logger


class HeadlessConflictDelegate:
    """
    Handles conflicts in headless mode.
    Default strategy: 'theirs' (New/Modded content overwrites Old/Vanilla).
    """

    def __init__(self, strategy: str = "overwrite"):
        self.strategy = strategy

    def resolve(self, file_path: str, orig_text: str, new_text: str) -> Optional[str]:
        if self.strategy == "fail":
            logger.error(f"Conflict detected in {file_path}. Aborting (strategy=fail).")
            return None
        # Automatically accept the new version (Last Mod Wins) for 'overwrite'
        return new_text


def setup_logging(verbose: bool) -> None:
    """
    Configures terminal logging for CLI operations.

    :param verbose: Enables DEBUG level logging when True.
    """
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter("%(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)


def _refresh_mods(session: GmosSession) -> None:
    """Consumes the mod refresh generator."""
    for _ in session.refresh_mods():
        pass


def cmd_list(session: GmosSession, args: argparse.Namespace) -> None:
    """List all installed mods and their status."""
    print(f"Game Directory: {session.game_dir}")
    print(f"{'State':<10} {'Name':<40} {'Version':<15}")
    print("-" * 65)
    _refresh_mods(session)

    for mod in session.mods:
        status = "[x]" if mod.is_enabled else "[ ]"

        config: Dict[str, Any] = cast(Dict[str, Any], mod.config or {})

        sections = cast(Dict[str, Any], config.get("Sections", {}))
        mod_info = cast(Dict[str, Any], sections.get("ModInfo", {}))
        version = str(mod_info.get("Version", "?.?.?"))

        name = str(config.get("Name", mod.path))
        print(f"{status:<10} {name:<40} {version:<15}")


def cmd_patch(session: GmosSession, args: argparse.Namespace) -> None:
    """Run the patcher."""
    print("Starting patch process...")
    delegate = HeadlessConflictDelegate(strategy=str(args.conflict))

    try:
        _refresh_mods(session)

        for msg in session.apply_changes(conflict_delegate=delegate):
            print(msg)
        print("Patching complete.")
    except Exception:
        logger.exception("Patch failed")
        sys.exit(1)


def cmd_restore(session: GmosSession, args: argparse.Namespace) -> None:
    """Restore game to vanilla state."""
    print("Restoring vanilla files...")
    # TODO: Expose explicit revert functionality in Session
    pass


def cmd_toggle(session: GmosSession, args: argparse.Namespace) -> None:
    """Enable or Disable a mod."""
    target = str(args.mod_name)
    found = False

    _refresh_mods(session)

    for mod in session.mods:
        config: Dict[str, Any] = cast(Dict[str, Any], mod.config or {})
        name = str(config.get("Name", ""))

        if name == target:
            mod.is_enabled = args.action == "enable"
            print(f"Mod '{name}' set to {args.action}d.")
            found = True
            break

    if found:
        configs: List[Dict[str, Any]] = [
            cast(Dict[str, Any], m.config) for m in session.mods if m.config
        ]
        policy.save_load_order(configs, game_dir=session.game_dir)
    else:
        print(f"Mod '{target}' not found.")
        sys.exit(1)


def cmd_p2p_host(session: GmosSession, args: argparse.Namespace) -> None:
    """Start a P2P host lobby."""
    host = P2PHost(session)
    host.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        host.stop()


def cmd_p2p_join(session: GmosSession, args: argparse.Namespace) -> None:
    """Join a P2P mod lobby."""
    client = P2PClient(session)
    client.connect(str(args.ip))


def main() -> None:
    """
    CLI Entrypoint.

    Parses terminal arguments and delegates to specific command handlers.
    """
    parser = argparse.ArgumentParser(description="GMOS Command Line Interface")
    parser.add_argument(
        "--game-dir", required=True, help="Path to Godot game directory"
    )
    parser.add_argument("--mods-dir", required=True, help="Path to mods directory")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List installed mods").set_defaults(
        func=cmd_list
    )

    p_patch = subparsers.add_parser("patch", help="Apply mods to the game")
    p_patch.set_defaults(func=cmd_patch)
    p_patch.add_argument(
        "--conflict",
        choices=["overwrite", "fail"],
        default="overwrite",
        help="Conflict resolution strategy",
    )
    subparsers.add_parser("restore", help="Restore game to vanilla state").set_defaults(
        func=cmd_restore
    )
    p_toggle = subparsers.add_parser("mod", help="Manage mod state")
    p_toggle.set_defaults(func=cmd_toggle)
    p_toggle.add_argument("action", choices=["enable", "disable"])
    p_toggle.add_argument("mod_name", help="Exact name of the mod")

    p_p2p = subparsers.add_parser("p2p", help="Peer-to-Peer Syncing")
    p2p_subs = p_p2p.add_subparsers(dest="p2p_command", required=True)
    p2p_subs.add_parser("host", help="Host a mod lobby").set_defaults(func=cmd_p2p_host)
    p_join = p2p_subs.add_parser("join", help="Join a mod lobby")
    p_join.set_defaults(func=cmd_p2p_join)
    p_join.add_argument("ip", help="Host IP Address")

    args = parser.parse_args()

    setup_logging(args.verbose)

    session = GmosSession(game_dir=args.game_dir, mods_dir=args.mods_dir)

    # Dispatch directly to the registered handler function
    if hasattr(args, "func"):
        args.func(session, args)


if __name__ == "__main__":
    main()
