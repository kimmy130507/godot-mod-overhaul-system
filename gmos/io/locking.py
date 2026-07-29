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
Locking Subsystem (Global Singleton Rewrite)

Enforces a "Single Instance" policy via a global lock file in the temp directory.
"""

import atexit
import os
import sys
import tempfile
from typing import IO, Any, Optional

from gmos.utils import logger

# Platform-specific locking mechanisms
msvcrt: Optional[Any] = None
fcntl: Optional[Any] = None
if sys.platform == "win32":
    try:
        import msvcrt as _msvcrt

        msvcrt = _msvcrt
    except ImportError:
        pass
else:
    try:
        import fcntl as _fcntl

        fcntl = _fcntl
    except ImportError:
        pass


# Fixed lock file in temp directory ensures cross-instance visibility.
LOCK_FILENAME = "gmos_singleton.lock"
LOCK_FILE_PATH = os.path.join(tempfile.gettempdir(), LOCK_FILENAME)


class LockManager:
    """
    Manages the global application lock.
    """

    def __init__(self) -> None:
        self._lock_fd: Optional[IO[bytes]] = None
        self._lock_path = LOCK_FILE_PATH
        atexit.register(self.release)

    @property
    def current_path(self) -> str:
        return self._lock_path

    def acquire(
        self, path: Optional[str] = None, graceful_restart: bool = False
    ) -> bool:
        """
        Attempts to acquire the global singleton lock.

        Args:
            path: Ignored (kept for legacy compatibility).
            graceful_restart: Ignored (legacy compatibility).

        Returns:
            True if this instance successfully acquired the lock.
            False if another instance is already running.
        """
        if self._lock_fd:
            return True
        fd = None
        try:
            mode = "r+b" if os.path.exists(self._lock_path) else "w+b"
            fd = open(self._lock_path, mode)

            if sys.platform == "win32":
                if not msvcrt:
                    logger.error("msvcrt module missing on Windows")
                    return True
                # Lock 1 byte at start of file. LK_NBLCK raises IOError if fail.
                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                if not fcntl:
                    logger.error("fcntl module missing on Unix")
                    return True
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            fd.seek(0)
            fd.truncate()
            fd.write(str(os.getpid()).encode("utf-8"))
            fd.flush()
            os.fsync(fd.fileno())

            self._lock_fd = fd
            logger.info(f"Global singleton lock acquired: {self._lock_path}")
            return True

        except (IOError, OSError, PermissionError):
            logger.debug("Another GMOS instance is running (Lock held).")
            if fd:
                try:
                    fd.close()
                except Exception:
                    pass
            return False
        except Exception as e:
            logger.exception(f"Unexpected error acquiring lock: {e}")
            return False

    def release(self, keep_new_acquired: bool = False) -> None:
        """Releases the global lock."""
        if self._lock_fd:
            try:
                if sys.platform == "win32" and msvcrt:
                    self._lock_fd.seek(0)
                    msvcrt.locking(self._lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
                elif sys.platform != "win32" and fcntl:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_UN)

                self._lock_fd.close()
                logger.info("Global lock released.")
            except Exception as e:
                logger.error(f"Error releasing lock: {e}")
            finally:
                self._lock_fd = None
                # Do NOT remove file to prevent race conditions during close.

    # --- Legacy Internal Stubs ---
    def pause_watcher(self) -> None:
        """No-op."""

    def resume_watcher(self) -> None:
        pass


# ==============================================================================
# SINGLETON INSTANCE & LEGACY COMPATIBILITY API
# ==============================================================================
# The following functions maintain the API signature expected by main.py and ui/app.py
# but redirect logic to the new Global Singleton manager.

manager = LockManager()


def acquire_app_lock(lock_path: Optional[str] = None, retry_once: bool = True) -> bool:
    """
    Main entry point for locking.
    Redirects to global singleton acquisition regardless of 'lock_path'.
    """
    return manager.acquire()


def release_app_lock() -> None:
    manager.release()


def pause_game_dir_watcher() -> None:
    """No-op."""
    pass


def resume_game_dir_watcher() -> None:
    """No-op."""
    pass


def wire_game_dir_locking(app: Any) -> None:
    """No-op: Singleton mode uses global lock."""
    logger.debug("Singleton Mode Active.")
