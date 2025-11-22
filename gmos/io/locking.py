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
import atexit
import errno
import hashlib
import os
import socket
import sys
import time
from typing import IO, TYPE_CHECKING, Any, Optional, Tuple, cast

from gmos.utils import logger, safe_norm

if TYPE_CHECKING:
    import tkinter as tk

    from gmos.ui import App

    class msvcrt_stub:
        def locking(self, fd: int, mode: int, nbytes: int) -> None: ...

        LK_UNLCK: int
        LK_NBLCK: int

    msvcrt: Optional[msvcrt_stub]

# Platform-specific imports for locking
try:
    import fcntl
except Exception:
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt  # type: ignore[assignment, reportAssignmentType] # Pylance conflicts with msvcrt_stub
except Exception:
    msvcrt = None  # type: ignore[assignment]

# --- Globals for locking ---
lock_fd: Optional[IO[bytes]] = None
current_lock_path: Optional[str] = None
_platform_handle: Optional[tuple[str, Any]] = None
# Allow long-running file operations to temporarily suppress automatic switching
_workroot_watcher_paused: bool = False


def _pid_running(pid: int) -> bool:
    """Return True if a process with pid exists on this system."""
    try:
        if pid <= 0:
            return False
        os.kill(pid, 0)
    except OSError as e:
        if getattr(e, "errno", None) in (errno.ESRCH, errno.ENOENT):
            return False
        # On Windows permission error means process exists
        if isinstance(e, PermissionError):
            return True
        return False
    except Exception:
        return False
    return True


def _acquire_file_lock(fd: IO[bytes]) -> bool:
    """Platform-specific non-blocking exclusive lock on open file descriptor."""
    # Use explicit, narrow exception semantics so callers can cheaply detect "already locked"
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            # non-blocking lock couldn't be acquired
            raise
    if msvcrt is not None:
        # lock first byte (Windows)
        try:
            # Pylance stubs for msvcrt are sometimes incomplete; rely on runtime behavior.
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            # translate to blocking-style exception for uniform handling
            raise
    # If neither mechanism present, give a clear error (rare on supported platforms)
    raise RuntimeError("No native file locking available on this platform")


def _release_file_lock(fd: Optional[IO[bytes]]) -> None:
    if fd is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            try:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception as e:
                logger.debug("ignored exception: %s", e)
    except Exception as e:
        logger.debug("ignored exception: %s", e)


def try_acquire_lock_fd(lock_path: str) -> Optional[IO[bytes]]:
    """
    Try to acquire an exclusive non-blocking lock on `lock_path`.
    Returns an open binary file object if lock acquired, otherwise None.
    Does NOT touch global lock_fd or current_lock_path.
    """
    try:
        parent = os.path.dirname(lock_path) or "."
        os.makedirs(parent, exist_ok=True)
        # Use os.open + fdopen to avoid some race conditions and to be explicit about flags.
        flags = os.O_RDWR | os.O_CREAT
        fd_os = os.open(lock_path, flags, 0o666)
        fd = os.fdopen(fd_os, "r+b")
        try:
            _acquire_file_lock(fd)
            return fd
        except (BlockingIOError, OSError):
            # lock already held by someone else (non-blocking failure)
            try:
                fd.close()
            except Exception as e:
                logger.debug("ignored exception closing fd: %s", e)
            return None
        except Exception:
            # unexpected error while attempting to lock -> close and propagate
            try:
                fd.close()
            except Exception:
                pass
            raise
    except Exception:
        return None


def acquire_app_lock(lock_path: Optional[str] = None, retry_once: bool = True) -> bool:
    """Acquire single-instance lock. Returns True if acquired, False otherwise.
    On failure the lock owner PID is logged/returned via messagebox or stdout.
    """
    global lock_fd, current_lock_path
    if lock_path is None:
        from gmos.utils import LOCK_PATH as DEFAULT_LOCK_PATH

        lock_path = DEFAULT_LOCK_PATH

    os.makedirs(os.path.dirname(lock_path), exist_ok=True)

    # Fast path: try atomic create of the lock file. This is O_EXCL and
    # will fail if another process is creating the file at the same time.
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        fd_os = os.open(lock_path, flags)
        # wrap into a binary file object to allow flock on it subsequently
        fd = os.fdopen(fd_os, "w+b")
        try:
            # write our PID and fsync; we already own the file by creation
            pid_str = str(os.getpid())
            pid_bytes: bytes = pid_str.encode("utf-8")
            fd.write(pid_bytes)
            fd.flush()
            os.fsync(fd.fileno())
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        # adopt as our lock descriptor (no flock needed; creation is atomic)
        lock_fd = fd
        current_lock_path = os.path.realpath(lock_path)
        atexit.register(release_app_lock)
        return True
    except FileExistsError:
        # another process created the file concurrently. Fall back to flock path.
        logger.debug(
            "concurrent file create detected while creating lock file (ignored)"
        )
    except Exception:
        # If atomic create fails for another reason, fall back as well.
        logger.exception(
            "Atomic create of lock file failed, falling back to flock method"
        )

    # Fallback: open existing file and try to acquire flock (existing behavior)
    fd_local: Optional[IO[bytes]] = None
    try:
        fd_local = open(lock_path, "a+b")
        try:
            _acquire_file_lock(fd_local)
        except Exception:
            # read existing PID
            try:
                fd_local.seek(0)
                data_bytes = fd_local.read()
                data_str = data_bytes.decode("utf-8").strip() if data_bytes else ""
                owner_pid = int(data_str) if data_str else None
            except Exception:
                owner_pid = None

            if owner_pid and _pid_running(owner_pid):
                try:
                    fd_local.close()
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                return False
            if owner_pid is None:
                try:
                    fd_local.close()
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                return False
            if retry_once:
                try:
                    fd_local.close()
                    os.remove(lock_path)
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                time.sleep(0.05)
                return acquire_app_lock(lock_path, retry_once=False)
            else:
                try:
                    fd_local.close()
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                return False

        # we hold the flock; write our PID (truncate + write)
        try:
            fd_local.seek(0)
            fd_local.truncate(0)
            pid_bytes = str(os.getpid()).encode("utf-8")
            fd_local.write(pid_bytes)
            fd_local.flush()
            os.fsync(fd_local.fileno())
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        lock_fd = fd_local
        atexit.register(release_app_lock)
        try:
            current_lock_path = os.path.realpath(lock_path)
        except Exception as e:
            current_lock_path = lock_path
            logger.debug("failed to set current_lock_path to %s: %s", lock_path, e)
        return True
    except Exception:
        if fd_local:
            try:
                fd_local.close()
            except Exception as e:
                logger.debug("ignored exception: %s", e)
        return False


def release_app_lock() -> None:
    """Release held locks and remove the lock file if it contains our PID."""
    global lock_fd, current_lock_path
    # Need default lock path if current_lock_path is not set
    if not current_lock_path:
        try:
            from gmos.utils import LOCK_PATH as DEFAULT_LOCK_PATH

            lockfpath = DEFAULT_LOCK_PATH
        except ImportError:
            # Fallback if utils is somehow not available
            lockfpath = os.path.join(
                os.environ.get("APPDATA") or os.path.expanduser("~/.local/share"),
                "gmos",
                "logs",
                "gmos.lock",
            )
    else:
        lockfpath = current_lock_path

    try:
        # release any file lock we hold
        if lock_fd:
            try:
                _release_file_lock(lock_fd)
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            try:
                lock_fd.close()
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            lock_fd = None

        # release any platform-native lock
        try:
            release_platform_lock()
        except Exception as e:
            logger.debug("ignored exception: %s", e)
        # If the lock file exists read it directly and act if it contains our PID.
        try:
            if os.path.exists(lockfpath):
                try:
                    with open(lockfpath, "rb") as fh:
                        content = fh.read()
                        cur = content.decode("utf-8").strip() if content else ""
                except Exception:
                    cur = ""
                if cur == str(os.getpid()):
                    # truncate then remove (best-effort)
                    try:
                        with open(lockfpath, "r+b") as fh:
                            fh.truncate(0)
                            fh.flush()
                            try:
                                os.fsync(fh.fileno())
                            except Exception as e:
                                logger.debug("ignored exception: %s", e)
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    try:
                        os.remove(lockfpath)
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
        except Exception as e:
            logger.debug("ignored exception: %s", e)
    finally:
        try:
            current_lock_path = None
        except Exception as e:
            logger.debug("ignored exception: %s", e)


def acquire_workroot_lock(work_root: str) -> bool:
    """Acquire lock for the given work_root.
    Try platform-native lock first. Fall back to file-based lock if needed.
    """
    if not work_root:
        return acquire_app_lock()
    wr = os.path.realpath(work_root)
    os.makedirs(wr, exist_ok=True)

    # Try platform lock first
    try:
        ph = acquire_platform_lock_for_workroot(wr)
        if ph:
            # record current lock path for consistency with file-based code
            try:
                global current_lock_path
                current_lock_path = os.path.join(wr, ".gmos.lock")
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            atexit.register(release_platform_lock)
            return True
    except Exception:
        logger.exception("Platform lock attempt failed; falling back to file lock")

    # fallback to file-based lock
    return acquire_app_lock(os.path.join(wr, ".gmos.lock"))


def _try_windows_mutex(name: str) -> Optional[tuple[str, Any, Any]]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wintypes

        # Cast ctypes to Any to avoid "Module has no attribute WinDLL" on Linux checks
        ctypes_any = cast(Any, ctypes)

        kernel32 = ctypes_any.WinDLL("kernel32", use_last_error=True)
        CreateMutexW = kernel32.CreateMutexW
        CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        CreateMutexW.restype = wintypes.HANDLE
        GetLastError = kernel32.GetLastError

        h = CreateMutexW(None, False, name)
        if not h:
            return None
        # ERROR_ALREADY_EXISTS = 183
        if GetLastError() == 183:
            # somebody else already has/created it
            try:
                kernel32.CloseHandle(h)
            except Exception as e:
                logger.debug("ignored exception: %s", e)
            return None
        return ("win_mutex", h, kernel32)
    except Exception:
        return None


def _release_windows_mutex(handle_tuple: tuple[str, Any, Any]) -> None:
    try:
        _, h, kernel32 = handle_tuple
        kernel32.ReleaseMutex(h)
        kernel32.CloseHandle(h)
    except Exception as e:
        logger.debug("ignored exception: %s", e)


def _try_unix_socket(sock_path: str) -> Optional[Tuple[str, socket.socket, str]]:
    s: Optional[socket.socket] = None
    try:
        # ensure parent dir exists
        parent = os.path.dirname(sock_path)
        os.makedirs(parent, exist_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        # unlink stale socket if it exists and is not bound
        if os.path.exists(sock_path):
            try:
                os.unlink(sock_path)
            except Exception as e:
                logger.debug("ignored exception: %s", e)
        s.bind(sock_path)
        return ("unix_sock", s, sock_path)
    except Exception:
        if s:
            try:
                s.close()
            except Exception as e:
                logger.debug("ignored exception: %s", e)
        return None


def _release_unix_socket(handle_tuple: tuple[str, socket.socket, str]) -> None:
    try:
        _, s, path = handle_tuple
        s.close()
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception as e:
            logger.debug("ignored exception: %s", e)
    except Exception as e:
        logger.debug("ignored exception: %s", e)


def _try_tcp_port_from_hash(workroot: str) -> Optional[Tuple[str, socket.socket, int]]:
    s: Optional[socket.socket] = None
    try:
        h = int(hashlib.sha256(workroot.encode("utf-8")).hexdigest(), 16)
        port = 20000 + (h % 30000)  # 20000..49999
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return ("tcp_port", s, port)
    except Exception:
        if s:
            try:
                s.close()
            except Exception as e:
                logger.debug("ignored exception: %s", e)
        return None


def _release_tcp(handle_tuple: tuple[str, socket.socket, int]) -> None:
    try:
        _, s, _ = handle_tuple
        s.close()
    except Exception as e:
        logger.debug("ignored exception: %s", e)


def acquire_platform_lock_for_workroot(workroot: str) -> Optional[Tuple[str, Any]]:
    """
    Try platform-native locks for the given workroot.
    Returns handle tuple on success, None on failure.
    """
    global _platform_handle
    if not workroot:
        return None
    # prefer Windows mutex
    if os.name == "nt":
        name = f"gmos_{abs(hash(workroot))}"
        h = _try_windows_mutex(name)
        if h:
            _platform_handle = ("win", h)
            return _platform_handle
    # try AF_UNIX (Linux, macOS)
    sock_path = os.path.join(os.path.realpath(workroot), ".gmos.sock")
    h = _try_unix_socket(sock_path)
    if h:
        _platform_handle = ("unix", h)
        return _platform_handle
    # fall back to tcp bind on loopback
    h = _try_tcp_port_from_hash(workroot)
    if h:
        _platform_handle = ("tcp", h)
        return _platform_handle
    return None


def release_platform_lock() -> None:
    """Release whichever platform lock was acquired."""
    global _platform_handle
    if not _platform_handle:
        return
    kind, payload = _platform_handle
    try:
        if kind == "win":
            _release_windows_mutex(payload)
        elif kind == "unix":
            _release_unix_socket(payload)
        elif kind == "tcp":
            _release_tcp(payload)
    except Exception as e:
        logger.debug("ignored exception: %s", e)
    _platform_handle = None


def wire_workroot_locking(app: "App") -> None:
    """
    Watch app.vars['work_root_dir'] and switch the lock automatically
    to workroot/.gmos.lock when the user selects a work root.
    """
    try:
        # Pylance cannot infer app.vars is a dictionary, so we check for it safely.
        if not hasattr(app, "vars") or "work_root_dir" not in getattr(app, "vars", {}):
            return

        var: "tk.StringVar" = app.vars["work_root_dir"]  # type: ignore[name-defined]

        def _on_change(*_: Any) -> None:
            # declare globals up-front
            global lock_fd, current_lock_path
            # If paused by a long operation, ignore workroot changes for now
            if _workroot_watcher_paused:
                try:
                    app.append_log(
                        "Workroot watcher paused; ignoring transient change."
                    )
                except Exception:
                    pass
                return
            try:
                # var.get() is now treated as Any, silencing the error
                new_wr = safe_norm(var.get())
                if not new_wr:
                    app.append_log("Workroot cleared; keeping current lock.")
                    return

                # If already locked to this path do nothing.
                try:
                    rp = os.path.realpath(new_wr)
                    # current_lock_path is Optional[str]
                    if current_lock_path and os.path.realpath(
                        current_lock_path
                    ) == os.path.realpath(rp):
                        return
                except Exception as e:
                    logger.debug("ignored exception: %s", e)
                # Prepare new lock path and attempt to acquire it with short retries
                new_lock_path = os.path.join(os.path.realpath(new_wr), ".gmos.lock")
                fd_new = None
                # fewer attempts with a tiny exponential backoff to reduce CPU spin while
                # keeping latency low when switching workroots quickly.
                attempts = 3
                backoff = 0.02
                for _attempt_num in range(attempts):
                    fd_new = try_acquire_lock_fd(new_lock_path)
                    if fd_new:
                        break
                    # exponential-ish backoff (small numbers to remain responsive)
                    time.sleep(backoff)
                    backoff = min(0.1, backoff * 2)

                if not fd_new:
                    # Failed to acquire the workroot lock. Inform user and close this instance.
                    app.append_log(
                        f"Failed to acquire workroot lock for {new_wr}. Another instance may hold it."
                    )
                    try:
                        from tkinter import messagebox

                        messagebox.showwarning(
                            "Lock Failed",
                            f"Cannot acquire lock for work root:\n{new_wr}\n\n"
                            "Another instance may be running for that work root.\n"
                            "This instance will now exit.",
                        )
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    try:
                        # Close GUI after dialog
                        app.after(0, app.destroy)
                    except Exception:
                        try:
                            sys.exit(2)
                        except Exception as e:
                            logger.debug("ignored exception: %s", e)
                    return

                # We successfully locked the new workroot file. Write our PID to it.
                try:
                    fd_new.seek(0)
                    fd_new.truncate(0)
                    pid_bytes = str(os.getpid()).encode("utf-8")
                    fd_new.write(pid_bytes)
                    fd_new.flush()
                    os.fsync(fd_new.fileno())
                except Exception as e:
                    logger.debug("ignored exception: %s", e)

                old_lock_path: Optional[str] = current_lock_path

                # Release the previous platform lock (if any) and then the app/file lock.
                try:
                    try:
                        # Use local function
                        release_platform_lock()
                    except Exception:
                        logger.exception(
                            "release_platform_lock failed during workroot switch"
                        )
                    # Use local function
                    release_app_lock()
                except Exception:
                    logger.exception("release_app_lock failed during workroot switch")

                # Adopt new lock descriptor as the global lock handle.
                try:
                    lock_fd = fd_new
                    current_lock_path = os.path.realpath(new_lock_path)
                    try:
                        atexit.register(release_app_lock)
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    app.append_log(f"Acquired workroot lock: {new_wr}")

                    # attempt best-effort removal of the old lock file
                    try:
                        if old_lock_path and os.path.exists(old_lock_path):
                            try:
                                os.remove(old_lock_path)
                                app.append_log(
                                    f"Removed old lock file: {old_lock_path}"
                                )
                            except Exception:
                                # if removal fails ignore; it will be cleaned later
                                logger.exception(
                                    "Failed removing old lock file: %s", old_lock_path
                                )
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                except Exception:
                    # Fallback: ensure we release platform lock we just created, then close fd_new
                    try:
                        release_platform_lock()
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    try:
                        fd_new.close()
                    except Exception as e:
                        logger.debug("ignored exception: %s", e)
                    try:
                        # Use local function
                        acquire_app_lock()
                    except Exception:
                        logger.exception(
                            "Failed to re-acquire global lock after adopting new lock failed"
                        )
                    app.append_log(
                        f"Failed to adopt workroot lock for {new_wr}; retained previous lock"
                    )
            except Exception as e:
                logger.exception("workroot change handler failed: %s", e)

        # attach trace; support both trace_add and older trace
        try:
            var.trace_add("write", _on_change)
        except Exception:
            try:
                var.trace("w", _on_change)  # type: ignore[no-untyped-call, reportUnknownMemberType]
            except Exception:
                logger.exception("Failed to attach trace to work_root_dir var")

        # call once to apply current value immediately
        try:
            _on_change()
        except Exception:
            logger.exception("Initial workroot lock attempt failed")
    except Exception:
        logger.exception("wire_workroot_locking failed")


def pause_workroot_watcher() -> None:
    """Temporarily suspend automatic workroot switching (use in try/finally)."""
    global _workroot_watcher_paused
    _workroot_watcher_paused = True


def resume_workroot_watcher() -> None:
    """Resume automatic workroot switching."""
    global _workroot_watcher_paused
    _workroot_watcher_paused = False
