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
Locking Subsystem

Implements a robust Process Locking mechanism to prevent instance conflicts.
Follows architectural directives for Concurrency Control:
1. Smart Grace Period (Livelock Prevention): Handles OS cleanup lag during restarts.
2. Consistent Handover: Atomically transitions from Global Lock to Game Directory Lock.
3. Transactional State: Encapsulates lock state in a manager class.
"""

import atexit
import errno
import os
import time
from typing import IO, TYPE_CHECKING, Any, Optional

from gmos.utils import logger, safe_norm

if TYPE_CHECKING:
    import tkinter as tk

    from gmos.ui import App

    class msvcrt_stub:
        def locking(self, fd: int, mode: int, nbytes: int) -> None: ...

        LK_UNLCK: int
        LK_NBLCK: int

    msvcrt: Optional[msvcrt_stub]

# Platform-specific imports
try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt  # type: ignore[assignment]
except ImportError:
    msvcrt = None  # type: ignore[assignment]


def _pid_running(pid: int) -> bool:
    """Check if a process with the given PID is currently running."""
    try:
        if pid <= 0:
            return False
        os.kill(pid, 0)  # Signal 0 checks existence without killing
    except OSError as e:
        # ESRCH: No such process (it's gone)
        if getattr(e, "errno", None) == errno.ESRCH:
            return False
        # EPERM: Process exists but we can't signal it (it's running)
        if getattr(e, "errno", None) == errno.EPERM:
            return True
        return False
    except Exception:
        return False
    return True


class LockManager:
    """
    Manages the lifecycle of the application's single-instance lock.
    Handles the transition (handover) from Global Lock to Game-Specific Lock.
    """

    def __init__(self) -> None:
        self._lock_fd: Optional[IO[bytes]] = None
        self._lock_path: Optional[str] = None
        self._paused: bool = False
        atexit.register(self.release)

    @property
    def current_path(self) -> Optional[str]:
        return self._lock_path

    def pause_watcher(self) -> None:
        """Temporarily prevent automatic lock switching."""
        self._paused = True

    def resume_watcher(self) -> None:
        """Resume automatic lock switching."""
        self._paused = False

    def _low_level_lock(self, fd: IO[bytes]) -> bool:
        """Apply OS-level non-blocking lock to file descriptor."""
        if msvcrt:
            try:
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        elif fcntl:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (IOError, OSError):
                return False
        return True  # No locking mechanism available, assume success (risky but rare)

    def _low_level_unlock(self, fd: IO[bytes]) -> None:
        """Release OS-level lock."""
        try:
            if msvcrt:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass

    def acquire(self, path: str, graceful_restart: bool = True) -> bool:
        """
        Attempt to acquire the lock at `path`.

        Args:
            path: The file path to lock.
            graceful_restart: If True, waits 0.5s if a stale lock is detected (Livelock Prevention).

        Returns:
            True if acquired, False if occupied.
        """
        path = os.path.abspath(path)

        # 1. Idempotency Check
        if self._lock_fd and self._lock_path == path:
            return True

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        # 2. Check for existing owner (Smart Grace Period)
        if os.path.exists(path):
            owner_pid = self._read_owner_pid(path)
            if owner_pid and _pid_running(owner_pid):
                if graceful_restart:
                    logger.info(
                        "Lock held by PID %s. Waiting 0.5s for cleanup...", owner_pid
                    )
                    time.sleep(0.5)
                    # Recursively retry once with grace=False
                    return self.acquire(path, graceful_restart=False)
                else:
                    logger.warning("Lock acquisition failed. Active PID: %s", owner_pid)
                    return False

        # 3. Attempt Acquisition
        try:
            # Open in r+b to allow reading/writing without truncating immediately
            # If file doesn't exist, 'w+b' ensures creation.
            mode = "r+b" if os.path.exists(path) else "w+b"
            fd = open(path, mode)

            if not self._low_level_lock(fd):
                fd.close()
                return False

            # 4. We have the lock. Update State.
            # Truncate and write our PID (Transactional Commit)
            fd.seek(0)
            fd.truncate()
            fd.write(str(os.getpid()).encode("utf-8"))
            fd.flush()
            os.fsync(fd.fileno())

            # 5. Release old lock if we held one (Handover)
            self.release(keep_new_acquired=True)

            self._lock_fd = fd
            self._lock_path = path
            logger.info("Lock acquired: %s", path)
            return True

        except Exception as e:
            logger.error("Error acquiring lock %s: %s", path, e)
            return False

    def release(self, keep_new_acquired: bool = False) -> None:
        """
        Release the current lock.
        Args:
            keep_new_acquired: If True, this is part of a handover, so don't run atexit cleanup yet.
        """
        if self._lock_fd:
            try:
                self._low_level_unlock(self._lock_fd)
                self._lock_fd.close()

                # Cleanup file if we owned it
                if self._lock_path and os.path.exists(self._lock_path):
                    # Check if it still contains our PID before deleting
                    # (Prevent deleting a lock file if another process just stole it - unlikely with OS locks but safer)
                    if self._read_owner_pid(self._lock_path) == os.getpid():
                        try:
                            os.remove(self._lock_path)
                        except OSError:
                            pass
            except Exception as e:
                logger.debug("Error releasing lock: %s", e)

            self._lock_fd = None
            self._lock_path = None

    def _read_owner_pid(self, path: str) -> Optional[int]:
        try:
            with open(path, "rb") as f:
                content = f.read().strip()
                if content:
                    return int(content)
        except (ValueError, OSError):
            pass
        return None


# Singleton Instance
manager = LockManager()

# --- Legacy/Compatibility API ---
# These functions route to the manager to maintain backward compatibility with existing calls.


def acquire_app_lock(lock_path: Optional[str] = None, retry_once: bool = True) -> bool:
    if lock_path is None:
        from gmos.utils import LOCK_PATH as DEFAULT_LOCK_PATH

        lock_path = DEFAULT_LOCK_PATH
    return manager.acquire(lock_path, graceful_restart=retry_once)


def release_app_lock() -> None:
    manager.release()


def pause_game_dir_watcher() -> None:
    """Temporarily suspend automatic game_dir switching (use in try/finally)."""
    manager.pause_watcher()


def resume_game_dir_watcher() -> None:
    """Resume automatic game_dir switching."""
    manager.resume_watcher()


# --- UI Integration ---


def wire_game_dir_locking(app: "App") -> None:
    """
    Watch app.vars['game_dir'] and switch the lock automatically.

    Refactored to:
    1. Listen to 'game_dir' (Current UI variable).
    2. Use the LockManager for atomic handover.
    """
    try:
        # Check safely for the variable map
        if not hasattr(app, "vars"):
            return

        target_var_name = "game_dir"

        if target_var_name not in app.vars:
            logger.debug(
                "wire_game_dir_locking: '%s' not found in app.vars", target_var_name
            )
            return

        var: "tk.StringVar" = app.vars[target_var_name]

        def _on_change(*_: Any) -> None:
            if manager._paused:  # type: ignore [reportPrivateUsage]
                return

            new_dir = safe_norm(var.get())
            if not new_dir or not os.path.isdir(new_dir):
                return

            # Construct the game-specific lock path
            # Strategy: <GameDir>/.gmos.lock
            new_lock_path = os.path.join(new_dir, ".gmos.lock")

            # Idempotency check handled inside manager.acquire
            success = manager.acquire(new_lock_path)

            if not success:
                # Critical Failure: Lock Acquisition Failed.
                # ACTION: LOCK REJECTION (Force Revert)
                # We must prevent the UI from pointing to a directory we do not own.

                app.append_log(f"LOCK REJECTED: Could not acquire lock for {new_dir}")

                from tkinter import messagebox

                messagebox.showerror(
                    "Lock Rejected",
                    f"Cannot switch to:\n{new_dir}\n\n"
                    "Another GMOS instance is already managing this game.\n"
                    "The setting will be reverted.",
                )

                # 1. Determine the safe fallback path (where we currently hold the lock)
                # If we have a current lock path, revert to its directory.
                # If we have no lock (startup), revert to empty string.
                safe_fallback = ""
                if manager.current_path:
                    # manager.current_path is full file path (e.g. .../.gmos.lock)
                    # We need the directory part.
                    safe_fallback = os.path.dirname(manager.current_path)

                # 2. Pause the watcher to prevent infinite recursion
                # (changing the var triggers _on_change again!)
                manager.pause_watcher()
                try:
                    var.set(safe_fallback)

                    # Force-reset dependent paths to defaults to match the fallback state.
                    # This prevents the UI from holding "stale" paths from the failed attempt.
                    if "mods_dir" in app.vars:
                        # If falling back to a game, assume default 'mods' folder.
                        # If falling back to empty, reset to relative default.
                        fallback_mods = (
                            os.path.join(safe_fallback, "mods")
                            if safe_fallback
                            else "mods"
                        )
                        app.vars["mods_dir"].set(fallback_mods)

                    if "game_executable" in app.vars:
                        # Reset to basic default
                        app.vars["game_executable"].set("game.exe")

                    if "launch_override" in app.vars:
                        app.vars["launch_override"].set("")

                    app.append_log(
                        f"Reverted configuration to safe state: '{safe_fallback}'"
                    )
                finally:
                    manager.resume_watcher()

        # Attach trace
        try:
            var.trace_add("write", _on_change)
        except AttributeError:
            # Fallback for older python/tk versions
            var.trace("w", _on_change)  # type: ignore

        # Trigger once to transition from Global -> Initial Game Dir (if set)
        if var.get():
            _on_change()

    except Exception as e:
        logger.exception("Failed to wire game_dir locking: %s", e)
