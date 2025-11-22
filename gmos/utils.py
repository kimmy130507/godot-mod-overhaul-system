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
from __future__ import annotations

import logging
import os
import random
import shlex
import shutil
import subprocess  # nosec B404
import sys
import threading
import time
import tkinter as tk
import types
from logging.handlers import RotatingFileHandler
from subprocess import CompletedProcess
from tkinter import messagebox
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    TypedDict,
    Union,
    cast,
)

# --- Shared Types & Helpers ---


class ModConfig(TypedDict, total=False):
    """Defines the structure of a parsed mod config dictionary."""

    Name: str
    Path: str
    Sections: Dict[str, Union[List[str], Dict[str, str]]]
    _deps_errors: List[str]
    _resolved_order_rank: int


def _get_mod_name_from_config(mod_config: ModConfig) -> str:
    """Determine mod name. Prefer Metadata 'Name' then folder basename."""
    # try metadata section lines like "Name = value"
    sections = mod_config.get("Sections", {}) or {}
    # case-insensitive lookup
    for sec_k in sections.keys():
        if sec_k.lower() == "metadata":
            section_content = sections[sec_k]
            if isinstance(section_content, list):
                for line in section_content:
                    try:
                        k, v = [p.strip() for p in line.split("=", 1)]
                    except ValueError:
                        continue
                    if k.lower() == "name" and v:
                        return v
    # fallback to folder name of the mod path
    path = mod_config.get("Path", "") or ""
    return os.path.basename(path) or path


# Logging: defer filesystem operations/handler creation until runtime.
ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
APPDATA_BASE = os.environ.get("APPDATA") or os.path.expanduser(
    os.path.join("~", ".local", "share")
)
LOG_DIR = os.path.join(APPDATA_BASE, "gmos", "logs")
LOCK_PATH = os.path.join(LOG_DIR, "gmos.lock")

# Module-level logger instance. Handlers are attached by configure_logging().
logger = logging.getLogger("gmos")
logger.setLevel(logging.WARNING)  # WARNING, INFO, DEBUG

_LOGGER_CONFIGURED = False
_ensure_log_dir_lock = threading.Lock()


def ensure_log_dir_exists() -> None:
    """Create LOG_DIR if needed. Thread-safe and best-effort; errors are logged."""
    # Fast-path: if it already exists, avoid acquiring the lock.
    if os.path.isdir(LOG_DIR):
        return
    # Use a process-wide lock to avoid races in multi-threaded startup.
    try:
        with _ensure_log_dir_lock:
            # Re-check after acquiring lock
            if os.path.isdir(LOG_DIR):
                return
            os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        logger.debug("Could not create LOG_DIR=%s", LOG_DIR, exc_info=True)


def configure_logging(log_dir: Optional[str] = None, level: int = logging.INFO) -> None:
    """Configure file handler and console handler once.
    Call early in main() or UI bootstrap.
    """
    global _LOGGER_CONFIGURED, LOG_DIR
    if _LOGGER_CONFIGURED:
        return
    if log_dir:
        LOG_DIR = log_dir  # type: ignore[reportConstantRedefinition]
    ensure_log_dir_exists()
    try:
        fh_path = os.path.join(LOG_DIR, "gmos.log")
        fh = RotatingFileHandler(
            fh_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
        logger.addHandler(fh)
    except Exception:
        logger.debug(
            "Failed to create file handler for LOG_DIR=%s", LOG_DIR, exc_info=True
        )
    logger.propagate = False
    _LOGGER_CONFIGURED = True  # type: ignore[reportConstantRedefinition]


def get_logger() -> logging.Logger:
    """Return the project logger. Call configure_logging() early to enable file logging."""
    return logger


def ensure_parent_dir(path: str) -> None:
    """Ensure the parent directory for `path` exists (no-op if path has no parent)."""
    try:
        parent = os.path.dirname(os.fspath(path)) or "."
        os.makedirs(parent, exist_ok=True)
    except Exception:
        logger.debug("failed to ensure parent dir for %s", path, exc_info=True)


def path_is_writable(path: str) -> bool:
    """
    Return True if `path` is writable or its parent directory is writable.
    This is a lightweight best-effort check (race conditions are possible).
    """
    try:
        p = os.fspath(path)
        if os.path.exists(p):
            return os.access(p, os.W_OK)
        parent = os.path.dirname(p) or "."
        return os.access(parent, os.W_OK)
    except Exception:
        return False


_app_icon_img: Optional[tk.PhotoImage] = None


def set_windows_appid(appid: str = "com.kim.gmos") -> None:
    """Set Windows AppUserModelID so the taskbar groups and icons behave."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        cast(Any, ctypes.windll).shell32.SetCurrentProcessExplicitAppUserModelID(appid)  # type: ignore[attr-defined]
    except Exception:
        logger.debug("Failed to set AppUserModelID", exc_info=True)


def load_and_apply_app_icon(root: tk.Tk) -> Optional[tk.PhotoImage]:
    """Load platform-appropriate icon and apply to root. Returns PhotoImage or None."""
    global _app_icon_img
    try:
        # Windows: use ICO
        if sys.platform.startswith("win"):
            ico = resource_path(os.path.join("assets", "gmos.ico"))
            if os.path.exists(ico):
                try:
                    root.iconbitmap(ico)  # type: ignore[call-arg, attr-defined]
                except Exception:
                    # alternate signature for some tk versions
                    root.iconbitmap(default=ico)  # type: ignore[call-arg, attr-defined]
                logger.debug("Applied .ico to root: %s", ico)
                return None
            logger.debug("ICO not found at %s", ico)
            return None

        # Non-Windows: use PNG -> PhotoImage -> iconphoto
        png_candidates = [
            resource_path(os.path.join("assets", "icons", "icon-256.png")),
            resource_path(os.path.join("assets", "icon-256.png")),
            resource_path(os.path.join("assets", "icons", "icon-128.png")),
        ]
        for png in png_candidates:
            if os.path.exists(png):
                _app_icon_img = tk.PhotoImage(file=png)
                try:
                    root.tk.call("wm", "iconphoto", root._w, _app_icon_img)  # type: ignore[attr-defined]
                except Exception:
                    try:
                        root.iconphoto(False, _app_icon_img)
                    except Exception:
                        logger.debug("Failed to apply iconphoto to root", exc_info=True)
                logger.debug("Applied .png to root: %s", png)
                return _app_icon_img
        logger.debug("PNG icon not found in candidates.")
        return None
    except Exception:
        logger.debug("Failed to load and apply icon", exc_info=True)
        return None


def load_and_apply_app_icon_to_toplevel(top: tk.Toplevel) -> None:
    """Load platform-appropriate icon and apply to toplevel."""
    try:
        # 1. Windows: Explicitly apply .ico file.
        # Toplevels often don't inherit this from root automatically on Windows.
        if sys.platform.startswith("win"):
            ico = resource_path(os.path.join("assets", "gmos.ico"))
            if os.path.exists(ico):
                try:
                    top.iconbitmap(ico)  # type: ignore[reportUnknownMemberType]
                except Exception:
                    top.iconbitmap(default=ico)  # type: ignore[reportUnknownMemberType]
                return

        # 2. Non-Windows (Linux/Mac): Inherit from master or use cached PNG
        if _app_icon_img:
            # Use the internal Tcl call to force the icon on the window handle (_w)
            # This is more reliable than top.iconphoto for child windows
            try:
                cast(Any, top).tk.call(
                    "wm", "iconphoto", cast(Any, top)._w, _app_icon_img
                )
            except Exception:
                # Fallback standard call
                try:
                    top.iconphoto(False, _app_icon_img)
                except Exception:
                    pass
    except Exception:
        logger.debug("Failed to apply Toplevel icon", exc_info=True)


# --- Uncaught Exception Hook ---
def excepthook(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: Optional[types.TracebackType],
) -> None:
    """Custom exception hook to log unhandled exceptions."""
    logger.error("Unhandled exception: %s", exc, exc_info=(exc_type, exc, tb))

    # Fallback to default excepthook behavior in addition to logging
    sys.__excepthook__(exc_type, exc, tb)


# --- Process/Spawn Helpers ---
def _safe_spawn(
    command: Union[str, Sequence[str]],
    cwd: Optional[str] = None,
    timeout: float = 30.0,
    capture_output: bool = False,
    **popen_kwargs: Any,
) -> Union[subprocess.Popen[Any], Dict[str, Any]]:
    """
    Backwards-compatible safe spawn.
    """
    logger.debug(
        "_safe_spawn: command=%r cwd=%r capture_output=%s", command, cwd, capture_output
    )

    if isinstance(command, str):
        cmd = shlex.split(command)
    else:
        cmd = list(command)

    if not cmd:
        raise RuntimeError("Empty command")

    exe = cmd[0]
    if not os.path.isabs(exe):
        exe_path = shutil.which(exe, path=os.environ.get("PATH"))
    else:
        exe_path = exe if os.path.exists(exe) else None

    if exe_path is None:
        raise RuntimeError(f"Cannot locate executable: {exe}")

    # Platform sane defaults
    if sys.platform.startswith("win"):
        CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        popen_defaults = {"creationflags": CREATE_NO_WINDOW}
    else:
        popen_defaults = {"start_new_session": True}

    # Merge popen kwargs
    merged: Dict[str, Any] = {}
    merged.update(popen_defaults)
    merged.update(popen_kwargs)

    if capture_output:
        # Run to completion and capture output (text mode)
        run_kwargs: Dict[str, Any] = dict(
            cwd=cwd, timeout=timeout, check=False, **merged
        )
        run_kwargs.update({"capture_output": True, "text": True})
        try:
            # Use distinct variable name to avoid type confusion with Popen
            proc_complete = cast(
                CompletedProcess[str],
                subprocess.run([exe_path] + cmd[1:], **run_kwargs),
            )
            return {
                "returncode": proc_complete.returncode,
                "stdout": proc_complete.stdout,
                "stderr": proc_complete.stderr,
            }
        except subprocess.TimeoutExpired as te:
            logger.error("_safe_spawn timeout: %s", te)
            return {"returncode": 124, "stdout": None, "stderr": str(te)}
        except Exception as e:
            logger.exception("_safe_spawn failed (capture): %s", e)
            return {"returncode": 1, "stdout": None, "stderr": str(e)}
    else:
        # Legacy behavior: return Popen so caller can wait/interact
        popen_kwargs_internal: Dict[str, Any] = {
            "cwd": cwd,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        popen_kwargs_internal.update(merged)
        try:
            # Use distinct variable name here as well
            proc_popen = subprocess.Popen([exe_path] + cmd[1:], **popen_kwargs_internal)
            # nosec: B603 - executable validated via shutil.which
            return proc_popen
        except Exception as e:
            logger.exception("_safe_spawn failed (popen): %s", e)
            raise


# --- Permission-Related Logic ---


def check_write_permission(path: str) -> tuple[bool, Optional[str]]:
    """
    Check whether we can write to `path` (file or directory).
    Returns (True, None) when writable. Otherwise returns (False, message).
    """
    try:
        if not path:
            return False, "empty path"
        if os.path.isdir(path):
            parent = path
        else:
            parent = os.path.dirname(path) or "."

        # quick check
        if not os.access(parent, os.W_OK):
            err_msg = f"No write permission to '{parent}'"
            # Diagnostic log: include short stack + thread so we can trace the caller
            try:
                import threading
                import traceback

                logger.debug(
                    "check_write_permission denied: path=%s parent=%s thread=%s reason=%s",
                    path,
                    parent,
                    threading.current_thread().name,
                    err_msg,
                )
                stack = "".join(traceback.format_stack(limit=6))
                logger.debug("check_write_permission stack (sample):\n%s", stack)
            except Exception:
                pass
            return False, err_msg

        fd = None
        try:
            fd, tmp = fast_tempfile(parent, prefix=".gmos_check_")
            os.close(fd)
            # Simplified cleanup: no retry needed for a temp check file.
            # This avoids a cycle back to io.safe_remove.
            try:
                os.remove(tmp)
            except Exception:
                logger.debug("cleanup failed for %s", tmp)
                pass
        except PermissionError as pe:
            err_msg = f"Permission denied writing to '{parent}': {pe}"
            try:
                import threading
                import traceback

                logger.debug(
                    "check_write_permission PermissionError: path=%s parent=%s thread=%s err=%s",
                    path,
                    parent,
                    threading.current_thread().name,
                    pe,
                )
                stack = "".join(traceback.format_stack(limit=6))
                logger.debug("check_write_permission stack (sample):\n%s", stack)
            except Exception:
                pass
            return False, err_msg
        except Exception as e:
            logger.debug("mkstemp/remove best-effort failed: %s", e)
            pass

        return True, None
    except Exception as e:
        return False, f"permission check error: {e}"


def handle_permission_error(
    exc: Exception, path: str, parent: Optional[object] = None
) -> None:
    """
    Friendly handling of permission errors. Logs and optionally shows a GUI messagebox.
    parent: if a Tk parent window is available, a messagebox will be shown.
    """
    msg = f"Permission error while accessing '{path}': {exc}\n\n"
    msg += "Common fixes:\n - choose a different work folder\n - run GMOS as administrator/sudo\n - check folder ACLs / antivirus\n"
    logger.error(msg)
    # show GUI alert if possible
    try:
        if parent is not None:
            messagebox.showerror("Permission Error", msg, parent=cast(Any, parent))
    except Exception:
        # headless: print to debug only
        logger.debug("Permission dialog not shown (headless or no tkinter available).")


def retry_on_permission(
    op: Callable[[], Any],
    parent: Optional[Any] = None,
    path: Optional[str] = None,
    path_updater: Optional[Callable[[str], None]] = None,
    max_attempts: int = 5,
) -> Any:
    """
    Run operation `op()` and on permission-related failure show a dialog that
    lets the user Retry / Choose folder / Abort (GUI) or call handle_permission_error
    (headless). Returns the op() result on success.
    """
    attempts = 0
    last_exc: Optional[Exception] = None
    while True:
        try:
            return op()
        except Exception as e:
            last_exc = e
            attempts += 1

            # Hard limit to avoid infinite loops
            if attempts >= max_attempts:
                try:
                    # Use local handle_permission_error
                    handle_permission_error(e, path or "<unknown>", parent=parent)
                except Exception:
                    # ensure we don't swallow the original error while logging issues
                    try:
                        logger.exception("handle_permission_error failed: %s", e)
                    except Exception:
                        pass
                raise

            # Attempt GUI dialog if available
            try:
                from gmos.ui import PermissionErrorDialog
            except Exception:
                # GUI not available. Delegate to central handler and re-raise.
                try:
                    # Use local handle_permission_error
                    handle_permission_error(e, path or "<unknown>", parent=parent)
                except Exception:
                    try:
                        logger.exception("handle_permission_error failed: %s", e)
                    except Exception:
                        pass
                raise

            # Show the dialog and act on user's choice
            try:
                dialog = PermissionErrorDialog(parent, path or "<unknown>", e)
                choice = dialog.show()
            except Exception as exc:
                # If dialog creation/showing itself fails, fallback to handler and raise.
                try:
                    # Use local handle_permission_error
                    handle_permission_error(e, path or "<unknown>", parent=parent)
                except Exception:
                    try:
                        logger.exception("Permission dialog failed: %s", exc)
                    except Exception:
                        pass
                raise

            # Interpret the user's choice
            if choice == "retry":
                continue

            if isinstance(choice, tuple) and choice[0] == "choose":
                chosen_dir = cast(str, choice[1])
                # Give caller an explicit hook to apply chosen dir into op's context.
                if path_updater:
                    try:
                        path_updater(chosen_dir)
                    except Exception as exc:
                        try:
                            logger.exception("path_updater callback failed: %s", exc)
                        except Exception:
                            pass
                        # Continue loop; let user choose again or abort.
                        continue
                else:
                    # Legacy fallback: attach chosen dir to exception for caller inspection
                    try:
                        cast(Any, last_exc).selected_dir = chosen_dir
                    except Exception:
                        pass
                # retry the op after updating path
                continue

            # abort chosen or unexpected value: re-raise original exception
            raise last_exc from None


# --- Misc Helpers ---


def fast_tempfile(parent: str, prefix: str = ".gmos_tmp_") -> Tuple[int, str]:
    """
    Extremely fast temp file generator for Windows.
    Produces a guaranteed-unique filename without using tempfile.mkstemp().
    """
    for _ in range(12):  # never needed more than 2–3
        name = f"{prefix}{int(time.time()*1000000):x}_{random.getrandbits(32):08x}"
        path = os.path.join(parent, name)
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
            return fd, path
        except FileExistsError:
            continue
    raise RuntimeError("fast_tempfile: could not generate unique name")


def safe_norm(p: str) -> str:
    """Normalize path (expand user, normalize separators)."""
    return os.path.normpath(os.path.expanduser(p)) if p else p


def run_checked(
    cmd: Union[str, Sequence[str]],
    *,
    timeout: Optional[float] = None,
    shell: bool = False,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
    **kwargs: Any,
) -> CompletedProcess[str]:
    """
    Run a command and return CompletedProcess. Raises CalledProcessError on non-zero exit.
    """
    run_kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "check": False,
        "text": True,
        "encoding": "utf-8",
    }
    if timeout is not None:
        run_kwargs["timeout"] = timeout
    if env is not None:
        run_kwargs["env"] = env
    if cwd is not None:
        run_kwargs["cwd"] = cwd
    run_kwargs.update(kwargs)

    # Determine command arguments based on input type and shell flag.
    proc: CompletedProcess[str]

    if isinstance(cmd, str):
        if shell:
            try:
                # Try splitting first to avoid shell if possible (legacy safety attempt)
                # We use a distinct variable name `cmd_list` to avoid redefinition issues
                cmd_list: List[str] = shlex.split(cmd)
                proc = cast(
                    CompletedProcess[str],
                    subprocess.run(cmd_list, shell=False, **run_kwargs),
                )
            except Exception:
                # Caller explicitly requested shell; run in shell as last resort.
                # Here we pass `cmd` (str) directly to run(..., shell=True)
                proc = cast(
                    CompletedProcess[str],
                    subprocess.run(cmd, shell=True, **run_kwargs),  # nosec B602
                )
        else:
            # Not shell, so we MUST split the string
            cmd_list_noshell: List[str] = shlex.split(cmd)
            proc = cast(
                CompletedProcess[str],
                subprocess.run(cmd_list_noshell, shell=False, **run_kwargs),
            )
    else:
        # cmd is already a sequence
        proc = cast(
            CompletedProcess[str], subprocess.run(list(cmd), shell=False, **run_kwargs)
        )

    # Tests expect a CalledProcessError to be raised on non-zero exit.
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, proc.args, output=proc.stdout, stderr=proc.stderr
        )

    return proc


def run_stream(
    cmd: Union[str, Sequence[str]],
    *,
    shell: bool = False,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
    bufsize: int = 1,
) -> Any:
    """
    Run a process and stream its stdout lines as they arrive.

    Yields each stdout line (str, with trailing newline removed).
    Raises CalledProcessError when the process exits with a non-zero code.
    """
    cmd_args: Union[str, Sequence[str]]

    if isinstance(cmd, str) and not shell:
        cmd_args = shlex.split(cmd)
        use_shell = False
    elif isinstance(cmd, str) and shell:
        # User explicitly requested shell=True with a string. Acknowledge risk.
        cmd_args = cmd
        use_shell = True
    else:
        # cmd is already a sequence, no shell needed.
        cmd_args = cmd
        use_shell = False

    proc = subprocess.Popen(
        cmd_args,
        shell=use_shell,  # nosec B602
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=bufsize,
        env=env,
        cwd=cwd,
    )

    # stream lines from stdout
    try:
        assert proc.stdout is not None  # for type checkers
        for raw in iter(proc.stdout.readline, ""):
            # yield without trailing newline
            yield raw.rstrip("\n")
        proc.stdout.close()
        ret = proc.wait()
        if ret != 0:
            # capture stderr for context
            stderr = proc.stderr.read() if proc.stderr is not None else None
            raise subprocess.CalledProcessError(
                ret, proc.args, output=None, stderr=stderr
            )
    finally:
        # ensure child cleaned up
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        except OSError:
            pass
        except Exception as e:
            logger.debug("Error terminating process: %s", e)


def resource_path(rel_path: str) -> str:
    """Resolve an asset path in multiple likely locations."""
    candidates: list[str] = []
    # 1) frozen bundle
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", "."), rel_path))
    # 2) package-local assets (gmos/assets/...)
    pkg_dir = os.path.dirname(__file__)  # .../gmos
    candidates.append(os.path.join(pkg_dir, rel_path))
    # 3) repo root assets (one level up from package)
    repo_root = os.path.abspath(os.path.join(pkg_dir, ".."))
    candidates.append(os.path.join(repo_root, rel_path))
    # 4) cwd
    candidates.append(os.path.abspath(rel_path))

    for p in candidates:
        p = os.path.normpath(p)
        if os.path.exists(p):
            return p
    # if none exist, return normalized first candidate so caller sees expected path
    return os.path.normpath(candidates[0])


__all__ = [
    "logger",
    "LOG_DIR",
    "ROOT_DIR",
    "LOCK_PATH",
    "get_logger",
    "_safe_spawn",
    "check_write_permission",
    "handle_permission_error",
    "retry_on_permission",
    "safe_norm",
    "resource_path",
    "ModConfig",
    "_get_mod_name_from_config",
]
