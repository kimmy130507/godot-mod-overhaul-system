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
import concurrent.futures
import ctypes
import ctypes.wintypes
import errno
import json as _json
import os
import random
import shutil
import stat
import sys
import threading
import time
import traceback
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable, Generator, List, Optional, Tuple, Union, cast

from gmos.utils import (
    check_write_permission,
    fast_tempfile,
    handle_permission_error,
    logger,
    retry_on_permission,
)

# Registry for path-based locks
_path_locks: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)
_path_locks_mutex = threading.Lock()


def _get_path_lock(path: str) -> threading.RLock:
    """Return a unique RLock for the path."""
    ap = os.path.abspath(path)
    with _path_locks_mutex:
        lock = _path_locks.get(ap)
        if lock is None:
            lock = threading.RLock()
            _path_locks[ap] = lock
        return lock


@contextmanager
def path_lock(path: str) -> Generator[None, None, None]:
    """Acquires the in-process, per-path lock."""
    lock = _get_path_lock(path)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def _temp_file_context(
    parent_dir: str, prefix: str = ".gmos_tmp_"
) -> Generator[Path, None, None]:
    """Context manager for temporary files."""
    fd, tmp_path_str = fast_tempfile(parent_dir, prefix=prefix)
    os.close(fd)  # Close descriptor immediately
    tmp_p = Path(tmp_path_str)
    try:
        yield tmp_p
    finally:
        # Aggressively remove temp file
        if tmp_p.exists():
            try:
                retry_on_permission(
                    lambda: safe_remove(str(tmp_p)), parent=None, path=str(tmp_p)
                )
            except Exception:
                try:
                    logger.debug("cleanup failed for %s", tmp_p)
                except Exception:
                    pass


# Concurrency Infrastructure
_io_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
_io_executor_lock = threading.Lock()


def get_io_executor(max_workers: int = 8) -> concurrent.futures.ThreadPoolExecutor:
    """Get or create the global ThreadPoolExecutor."""
    global _io_executor
    if _io_executor is None:
        with _io_executor_lock:
            if _io_executor is None:
                _io_executor = concurrent.futures.ThreadPoolExecutor(
                    max_workers=max_workers, thread_name_prefix="gmos_io_worker"
                )
    return _io_executor


def shutdown_io_executor(wait: bool = True) -> None:
    """Cleanly shutdown the I/O executor."""
    global _io_executor
    with _io_executor_lock:
        if _io_executor:
            _io_executor.shutdown(wait=wait)
            _io_executor = None


def sweep_orphan_gmos_temps(paths: List[str], *, age_threshold: float = 300.0) -> int:
    """
    Removes orphaned .gmos_tmp_* files older than `age_threshold` seconds.

    :param paths: A list of directory paths (str) to scan for orphaned files.
    :param age_threshold: Minimum age (in seconds) for a file to be considered orphaned.
    :return: The number of files successfully removed.
    """
    removed = 0
    now = time.time()

    # Pre-calculate the time boundary for efficiency
    age_boundary = now - float(age_threshold)

    for base in paths:
        try:
            if not os.path.isdir(base):
                continue

            with path_lock(os.path.abspath(base)):
                with os.scandir(base) as scanner:
                    for entry in scanner:
                        if not entry.name.startswith(".gmos_tmp_"):
                            continue

                        try:
                            if entry.stat().st_mtime < age_boundary:
                                full_path = entry.path
                                retry_on_permission(
                                    partial(os.unlink, full_path),
                                    parent=None,
                                    path=full_path,
                                )
                                removed += 1
                        except FileNotFoundError:
                            pass
                        except Exception as e:
                            logger.debug(
                                "sweep: failed to remove orphan %s: %s", entry.path, e
                            )

        except Exception as e:
            # Log failure to scan the directory but continue to the next one
            logger.debug("sweep: scanning %s failed: %s", base, e)

    return removed


def _probe_no_share_open(path: str) -> Tuple[bool, Optional[int]]:
    """
    Probes if a file is exclusively locked on Windows.
    """
    if os.name != "nt":
        return True, None

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_NONE = 0x00000000
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80

    CreateFileW = cast(Any, ctypes).windll.kernel32.CreateFileW
    CreateFileW.argtypes = [
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.HANDLE,
    ]
    CreateFileW.restype = ctypes.wintypes.HANDLE

    INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value

    h = CreateFileW(
        path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_NONE,
        None,
        OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if h == INVALID_HANDLE_VALUE or h is None:
        err = cast(Any, ctypes).GetLastError()
        return False, int(err)
    else:
        # close handle
        cast(Any, ctypes).windll.kernel32.CloseHandle(h)
        return True, None


def _movefileex_replace(src: str, dst: str) -> bool:
    """Windows: MoveFileExW with REPLACE_EXISTING | WRITE_THROUGH."""
    if os.name != "nt":
        return False
    try:
        MOVEFILE_REPLACE_EXISTING = 0x1
        MOVEFILE_WRITE_THROUGH = 0x8
        flags = MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
        MoveFileExW = cast(Any, ctypes).windll.kernel32.MoveFileExW
        MoveFileExW.argtypes = [
            ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.LPCWSTR,
            ctypes.wintypes.DWORD,
        ]
        MoveFileExW.restype = ctypes.wintypes.BOOL
        res = MoveFileExW(str(src), str(dst), flags)
        return bool(res)
    except Exception:
        return False


def _dump_internal_diagnostics(tag: str, target_path: str) -> None:
    """Dump thread stacks and process open files to log."""
    try:
        logger.error("INTERNAL DIAGNOSTIC DUMP (%s) for %s", tag, target_path)

        # timestamp
        logger.error("Diagnostic timestamp: %s", time.strftime("%Y-%m-%dT%H:%M:%S"))

        # thread stacks
        frames = sys._current_frames()  # type: ignore[reportPrivateUsage, unused-ignore]
        for tid, frame in frames.items():
            try:
                # find thread name
                name = None
                for th in threading.enumerate():
                    if th.ident == tid:
                        name = th.name
                        break
                stack = "".join(traceback.format_stack(frame))
                logger.error("Thread id=%s name=%s stack:\n%s", tid, name, stack)
            except Exception:
                logger.exception("Failed to log stack for thread %s", tid)

        # list open files using psutil if available
        try:
            import psutil

            p = psutil.Process()
            try:
                files = p.open_files()
                if files:
                    logger.error("Process open_files() for pid=%s:", p.pid)
                    for f in files:
                        logger.error(" - %s (fd=%s)", f.path, getattr(f, "fd", "?"))
                else:
                    logger.error("Process open_files() returned no files.")
            except Exception:
                logger.exception("psutil.Process.open_files() failed")
        except Exception:
            # fallback: try to list fds on POSIX, or just log message on Windows
            try:
                if hasattr(os, "listdir"):
                    # On POSIX, list /proc/self/fd if available
                    fd_dir = "/proc/self/fd"
                    if os.path.isdir(fd_dir):
                        fds = os.listdir(fd_dir)
                        logger.error(
                            "Open file descriptors in /proc/self/fd: %s", ", ".join(fds)
                        )
                    else:
                        logger.error(
                            "psutil not installed and /proc/self/fd not available"
                        )
                else:
                    logger.error(
                        "psutil not installed; cannot list open files on this platform"
                    )
            except Exception:
                logger.exception("Fallback file descriptor listing failed")
    except Exception:
        # do not let diagnostic itself crash the process
        try:
            logger.exception("internal diagnostic dump failed for %s", target_path)
        except Exception:
            pass


def replace_with_retries(
    src_temp: str, dst: str, max_attempts: int = 4, base_delay: float = 0.02
) -> None:
    """
    Retries replacement on WinError 5/32/EACCES/EBUSY.
    """
    last_exc: Optional[Exception] = None
    dst_p = Path(dst)

    with path_lock(dst):
        for attempt in range(1, max_attempts + 1):
            try:
                os.replace(src_temp, dst)
                return
            except OSError as e:
                last_exc = e
                winerr = getattr(e, "winerror", None)
                errn = getattr(e, "errno", None)

                # Retry-eligible errors
                if winerr in (5, 32) or errn in (errno.EACCES, errno.EBUSY):
                    try:
                        if dst_p.exists():
                            st_mode = dst_p.stat().st_mode
                            os.chmod(dst, st_mode | stat.S_IWRITE)
                    except Exception:
                        pass

                    # Exponential backoff
                    if attempt < max_attempts:
                        sleep_time = min(
                            0.5,
                            base_delay
                            * (2 ** (attempt - 1))
                            * (0.8 + random.random() * 0.4),
                        )
                        time.sleep(sleep_time)
                        continue

                break

    # Windows fallback: MoveFileExW
    if os.name == "nt":
        try:
            if _movefileex_replace(src_temp, dst):
                return
        except Exception:
            pass

    # Fallback
    src_p = Path(src_temp)
    try:
        shutil.copy2(src_temp, dst)
    except Exception as copy_exc:
        try:
            if src_p.exists():
                try:
                    os.chmod(src_temp, 0o600)
                except Exception:
                    pass
                try:
                    os.remove(src_temp)
                except Exception:
                    pass
        except Exception:
            pass
        if last_exc:
            raise last_exc from copy_exc
        else:
            raise copy_exc

    # Cleanup
    try:
        if src_p.exists():
            try:
                os.remove(src_temp)
            except Exception:
                try:
                    os.chmod(src_temp, 0o600)
                    os.remove(src_temp)
                except Exception:
                    pass
    except Exception:
        pass


class AtomicFile:
    """
    Robust atomic file operations (Write-Aside -> Replace).
    """

    def __init__(self, path: str):
        self.path = Path(path).resolve()

    def write(
        self,
        content: Union[str, bytes],
        backup: bool = False,
        mode: Optional[int] = 0o666,
        encoding: str = "utf-8",
    ) -> None:
        """Atomically writes content to the file."""
        path_str = str(self.path)
        parent_dir = self.path.parent

        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
            ok, err = check_write_permission(path_str)
            if not ok:
                if not self.path.exists():
                    ok_p, err_p = check_write_permission(str(parent_dir))
                    if not ok_p:
                        raise RuntimeError(
                            err_p or f"No write permission for directory '{parent_dir}'"
                        )
                else:
                    raise RuntimeError(
                        err or f"No write permission for file '{self.path}'"
                    )
        except Exception as e:
            handle_permission_error(e, path_str)
            raise

        if backup and self.path.exists():
            bak_path = self.path.with_name(self.path.name + ".bak")
            if not bak_path.exists():
                try:
                    current_bytes = safe_read_bytes(path_str)
                    AtomicFile(str(bak_path)).write(
                        current_bytes, backup=False, mode=None
                    )
                except Exception as e:
                    logger.warning(f"Failed to create backup for {self.path}: {e}")

        with _temp_file_context(str(parent_dir), prefix=".atomic_") as tmp_path:
            tmp_str = str(tmp_path)

            if isinstance(content, str):
                with open(tmp_str, "w", encoding=encoding) as f:
                    f.write(content)
            else:
                with open(tmp_str, "wb") as f:
                    f.write(content)

            if mode is not None:
                try:
                    retry_on_permission(
                        lambda: safe_chmod(tmp_str, mode), parent=None, path=tmp_str
                    )
                except Exception:
                    pass  # Chmod failure is non-critical on some FS

            retry_on_permission(
                lambda: replace_with_retries(tmp_str, path_str),
                parent=None,
                path=path_str,
            )


@dataclass
class ReplaceDiagnostics:
    """Result object for background replace tasks."""

    src: str
    dst: str
    attempts_allowed: int
    attempts_made: int = 0
    errors: List[Tuple[int, str]] = field(default_factory=lambda: [])
    success: bool = False
    canceled: bool = False
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    last_exception: Optional[Exception] = None
    thread_name: Optional[str] = None


def start_replace_task(
    src: str,
    dst: str,
    *,
    done_cb: Callable[[ReplaceDiagnostics], None],
    progress_cb: Optional[Callable[[float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    attempts: int = 6,
    base_delay: float = 0.03,
    max_sleep: float = 0.5,
    poll_interval: float = 0.08,
) -> tuple[ReplaceDiagnostics, threading.Thread]:
    """
    Start a background replace operation.
    """
    diag = ReplaceDiagnostics(src=src, dst=dst, attempts_allowed=attempts)
    dst_p = Path(dst)

    def _worker() -> None:
        diag.start_time = time.time()
        diag.thread_name = threading.current_thread().name
        last_exc: Optional[Exception] = None
        for i in range(attempts):
            diag.attempts_made = i + 1
            if cancel_event and cancel_event.is_set():
                diag.canceled = True
                break
            try:
                os.replace(src, dst)
                diag.success = True
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                # record a concise repr for diagnostics
                try:
                    diag.errors.append((i, f"{type(e).__name__}: {e}"))
                except Exception:
                    pass
                winerr = getattr(e, "winerror", None)
                errn = getattr(e, "errno", None)
                # transient conditions we retry on
                if winerr in (32, 5) or errn in (
                    errno.EACCES,
                    errno.EBUSY,
                    errno.EPERM,
                ):
                    # call progress callback: fraction of attempts consumed
                    if progress_cb:
                        try:
                            progress_cb(min(0.95, (i + 1) / float(attempts)))
                        except Exception:
                            pass
                    sleep = min(base_delay * (1.5**i), max_sleep)
                    # small cooperative sleep so GUI can remain responsive if caller polls
                    time.sleep(sleep)
                    continue
                else:
                    diag.last_exception = e
                    break

        diag.end_time = time.time()
        if last_exc:
            diag.last_exception = last_exc
        # Final progress update
        if progress_cb:
            try:
                progress_cb(1.0 if diag.success else 0.0)
            except Exception:
                pass
        try:
            done_cb(diag)
        except Exception:
            logger.exception("start_replace_task: done_cb raised")

    thr = threading.Thread(
        target=_worker, daemon=True, name=f"gmos-replace-{dst_p.name}"
    )
    thr.start()
    return diag, thr


def safe_atomic_write(dst_path: str, data: bytes, *, mode: int = 0o666) -> bool:
    """Atomically write bytes to dst_path with permission checks."""
    try:
        AtomicFile(dst_path).write(data, mode=mode)
        return True
    except Exception as e:
        logger.debug(f"safe_atomic_write failed: {e}")
        raise


def safe_write_text(path: str, text: str, encoding: str = "utf-8") -> bool:
    """Atomically write text to path."""
    try:
        AtomicFile(path).write(text, mode=0o666, encoding=encoding)
        return True
    except Exception as e:
        logger.debug(f"safe_write_text failed: {e}")
        raise


def safe_copy2(src: str, dst: str) -> Any:
    """shutil.copy2 wrapper with permission checks."""
    try:
        ok, err = check_write_permission(dst)
        if not ok:
            raise RuntimeError(err or f"No write permission to '{dst}'")
        return shutil.copy2(src, dst)
    except Exception as e:
        handle_permission_error(e, dst)
        raise


def safe_remove(path: str) -> None:
    """os.remove wrapper with permission checks and retry logic."""
    WIN_ERROR_FILE_IN_USE = 32
    MAX_RETRIES = 6
    RETRY_DELAY = 0.1  # seconds

    path_p = Path(path)
    try:
        ok, err = check_write_permission(path)
        if not ok and path_p.exists():
            raise RuntimeError(err or f"No write permission to remove '{path}'")

        if path_p.exists():
            with path_lock(path):
                for i in range(MAX_RETRIES):
                    try:
                        return os.remove(path)
                    except OSError as e:
                        winerr = getattr(e, "winerror", None)
                        errn = getattr(e, "errno", None)
                        # Diagnostic: log immediate context (attempt, thread, exception, stack)
                        try:
                            logger.debug(
                                "safe_remove: OSError on attempt %d/%d path=%s exc=%r thread=%s",
                                i + 1,
                                MAX_RETRIES,
                                path,
                                e,
                                threading.current_thread().name,
                            )
                            st = "".join(traceback.format_stack(limit=8))
                            logger.debug("safe_remove stack (sample):\n%s", st)
                        except Exception:
                            pass

                        # Attempt to detect an external exclusive lock (Windows only).
                        try:
                            ok_probe, probe_err = _probe_no_share_open(path)
                            if not ok_probe:
                                if os.getpid() == os.getpid():
                                    _dump_internal_diagnostics(
                                        "self-handle-detected", path
                                    )
                                logger.debug(
                                    "safe_remove: probe reports path locked (err=%s) for %s; backing off before retry",
                                    probe_err,
                                    path,
                                )
                                # longer sleep to give external process time to release handle
                                time.sleep(0.15 * (1 + random.random() * 0.5))
                                if i < MAX_RETRIES - 1:
                                    continue
                                logger.error(
                                    "Failed to remove file after retries: %s", path
                                )
                                raise
                        except Exception:
                            # probe failed — continue with existing remedial attempts
                            logger.debug(
                                "safe_remove: probe failed for %s, continuing remedial steps",
                                path,
                            )

                        # Handle Windows file-in-use (32) or access-denied (5) and POSIX EACCES/EBUSY.
                        if winerr in (WIN_ERROR_FILE_IN_USE, 5) or errn in (
                            errno.EACCES,
                            errno.EBUSY,
                        ):
                            # Best-effort: try to clear read-only / restrictive bits on the file
                            try:
                                st_mode = path_p.stat().st_mode
                                os.chmod(path, st_mode | stat.S_IWRITE)
                            except Exception:
                                pass

                        # Retry after a short delay (exponential-ish backoff)
                        if i < MAX_RETRIES - 1:
                            logger.debug(
                                "safe_remove: transient remove error (%s) on %s, retrying (%d/%d)",
                                e,
                                path,
                                i + 1,
                                MAX_RETRIES,
                            )
                            time.sleep(RETRY_DELAY * (1.3**i))
                            continue
                        else:
                            logger.error(
                                "Failed to remove file after retries: %s", path
                            )
                            raise
                    else:
                        # Not a transient error we handle; re-raise immediately
                        raise

    except FileNotFoundError:
        logger.debug("cleanup no-op; file already removed")
        return
    except Exception as e:
        handle_permission_error(e, path)
        raise


def safe_chmod(path: str, mode: int) -> None:
    """Wrapper around os.chmod with permission checks."""
    try:
        ok, err = check_write_permission(path)
        if not ok:
            raise RuntimeError(err or f"No write permission to chmod '{path}'")
        return os.chmod(path, mode)
    except Exception as e:
        handle_permission_error(e, path)
        raise


def safe_read_bytes(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except Exception as e:
        logger.debug("safe_read_bytes failed for %s: %s", path, e)
        raise


def safe_read_text(path: str, encoding: str = "utf-8") -> str:
    try:
        return Path(path).read_text(encoding=encoding)
    except Exception as e:
        logger.debug("safe_read_text failed for %s: %s", path, e)
        raise


def safe_read_lines(path: str, encoding: str = "utf-8") -> List[str]:
    try:
        with Path(path).open("r", encoding=encoding) as f:
            return f.readlines()
    except Exception as e:
        logger.debug("safe_read_lines failed for %s: %s", path, e)
        raise


def safe_read_json(path: str, **json_kwargs: Any) -> Any:
    try:
        with Path(path).open("r", encoding=json_kwargs.pop("encoding", "utf-8")) as f:
            return _json.load(f, **json_kwargs)
    except Exception as e:
        logger.debug("safe_read_json failed for %s: %s", path, e)
        raise


def _on_rm_error(func: Callable[[str], None], path: str, exc: BaseException) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def safe_rmtree(path: str) -> None:
    try:
        ok, err = check_write_permission(path)
        if not ok and os.path.exists(path):  # os.path.exists is fine for a dir
            raise RuntimeError(err or f"No permission to remove tree '{path}'")
        rmtree_func = cast(Any, shutil.rmtree)
        if sys.version_info >= (3, 12):
            rmtree_func(path, onexc=_on_rm_error)
        else:
            rmtree_func(path, onerror=_on_rm_error)
    except Exception as e:
        handle_permission_error(e, path)
        raise


def atomic_write_copy(src_path: str, dst_path: str) -> None:
    """
    Atomically copy src_path -> dst_path by copying to a temp file in the destination
    directory then os.replace. Preserves mode where possible.
    """
    src_p = Path(src_path).resolve()
    dst_p = Path(dst_path)
    ddir_p = dst_p.parent
    ddir_p.mkdir(parents=True, exist_ok=True)

    with _temp_file_context(str(ddir_p), prefix=".atomic-") as tmp_p:
        shutil.copy2(src_p, tmp_p)
        replace_with_retries(str(tmp_p), str(dst_p))


def create_symlink(src: str, dst: str) -> bool:
    """
    Creates a symbolic link at 'dst' pointing to 'src'.
    Handles Windows privilege checks and platform differences.
    Returns True if successful, False if failed (fallback required).
    """
    src_p = Path(src).resolve()
    dst_p = Path(dst)

    dst_p.parent.mkdir(parents=True, exist_ok=True)

    try:
        if dst_p.exists() or dst_p.is_symlink():
            if dst_p.is_dir() and not dst_p.is_symlink():
                # Safety: Don't delete real directories via this generic function
                return False
            safe_remove(str(dst_p))

        os.symlink(src_p, dst_p)
        return True

    except OSError as e:
        # Windows: OSError [WinError 1314] A required privilege is not held by the client
        if os.name == "nt" and getattr(e, "winerror", 0) == 1314:
            logger.debug(
                "Symlink creation failed due to privileges. Enable Developer Mode or Run as Admin."
            )
            return False
        logger.warning(f"Symlink failed for {dst} -> {src}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected symlink error: {e}")
        return False


class SymlinkManager:
    """
    Manages the deployment of the Virtual File System.
    Tracks which files are symlinked vs hard-copied (fallback).
    """

    def __init__(self, game_dir: str):
        self.game_dir = Path(game_dir).resolve()

    def deploy(self, rel_path: str, source_path: str) -> bool:
        """
        Deploys a file to the game directory using a symlink.
        Falls back to Hard Copy if symlinking fails.
        """
        dest = self.game_dir / rel_path

        # SAFETY: Create backup if target exists and isn't already a symlink (vanilla file)
        if dest.exists() and not dest.is_symlink():
            bak = dest.with_name(dest.name + ".bak")
            if not bak.exists():
                try:
                    os.replace(dest, bak)
                except OSError:
                    # If replace fails (perms), try copy-delete
                    atomic_write_copy(str(dest), str(bak))
                    safe_remove(str(dest))

        if create_symlink(str(source_path), str(dest)):
            return True

        # Fallback: Hard Copy (The "Old Way")
        logger.info(f"Fallback: Hard copying {rel_path}")
        atomic_write_copy(str(source_path), str(dest))
        return False


def atomic_replace(target_path: str, text: str) -> None:
    """Legacy wrapper for atomic text write (no backup)."""
    AtomicFile(target_path).write(text, backup=False)


def atomic_copy_with_single_bak(src: str, dst: str) -> None:
    src_p = Path(src).resolve()
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    bak = dst_p.with_name(dst_p.name + ".bak")

    if dst_p.exists() and not bak.exists():
        atomic_write_copy(str(dst_p), str(bak))
    atomic_write_copy(str(src_p), str(dst_p))


def atomic_write_bytes(dst_path: str, bdata: bytes, *, mode: int = 0o644) -> None:
    """Legacy wrapper for atomic byte write."""
    AtomicFile(dst_path).write(bdata, mode=mode, backup=False)


def atomic_write_with_backup(target_path: str, new_text: str) -> None:
    """Legacy wrapper for atomic text write with backup."""
    AtomicFile(target_path).write(new_text, backup=True)


def safe_atomic_copy_with_bak(src: str, dst: str, *args: Any, **kwargs: Any) -> Any:
    """
    Safe wrapper that verifies write permissions, then delegates to the
    specialized atomic_copy_with_single_bak.
    Falls back to a conservative atomic copy if the import is unavailable.
    """
    try:
        parent = os.path.dirname(dst) or "."
        ok, err = check_write_permission(parent)
        if not ok:
            raise RuntimeError(err or "no write permission")
        try:
            return atomic_copy_with_single_bak(src, dst, *args, **kwargs)
        except ImportError:
            tmp = dst + ".tmp.fallback"  # Use a more unique name
            bufsize = 1024 * 1024  # 1 MiB

            try:
                with open(tmp, "wb") as fw, open(src, "rb") as fr:
                    shutil.copyfileobj(fr, fw, length=bufsize)

                replace_with_retries(tmp, dst)

            finally:
                if os.path.exists(tmp):
                    try:
                        retry_on_permission(
                            lambda: safe_remove(tmp), parent=None, path=tmp
                        )
                    except Exception:
                        try:
                            logger.debug("cleanup failed for %s", tmp)
                        except Exception:
                            pass

            return ["SUCCESS"]
    except Exception as e:
        handle_permission_error(e, dst)
        raise
