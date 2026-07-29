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
from __future__ import annotations

import ctypes
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
from ctypes import wintypes
from logging.handlers import RotatingFileHandler
from subprocess import CompletedProcess
from tkinter import messagebox, ttk
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    TypedDict,
    TypeVar,
    Union,
    cast,
)

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = cast(Any, None)
    ImageTk = cast(Any, None)


def extract_icon_from_exe(exe_path: str) -> Optional[Any]:
    """
    Extracts the first icon from a Windows Executable (.exe) as a PIL Image.
    Returns None if on non-Windows, file not found, or PIL missing.
    """
    if not sys.platform.startswith("win") or not Image:
        return None

    try:
        # Windows API Constants & Functions
        windll = cast(Any, ctypes).windll
        shell32 = windll.shell32
        user32 = windll.user32
        gdi32 = windll.gdi32

        # Extract Icon Handle
        large_icons = (wintypes.HICON * 1)()
        small_icons = (wintypes.HICON * 1)()

        count = shell32.ExtractIconExW(exe_path, 0, large_icons, small_icons, 1)
        if count == 0 or not large_icons[0]:
            return None

        hIcon = large_icons[0]

        # Create a Device Context (DC)
        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)

        # Create a 32-bit Bitmap
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = 32
        bmi.biHeight = 32
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0  # BI_RGB

        bits_ptr = ctypes.c_void_p()

        hBitmap = gdi32.CreateDIBSection(
            hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), 0, 0
        )

        # Select object and draw
        old_obj = gdi32.SelectObject(hdc_mem, hBitmap)
        user32.DrawIconEx(hdc_mem, 0, 0, hIcon, 32, 32, 0, 0, 3)  # DI_NORMAL

        # Create buffer copy for PIL
        size = 32 * 32 * 4
        buf = (ctypes.c_char * size).from_address(bits_ptr.value or 0)
        raw_data = bytes(buf)

        # Cleanup
        gdi32.SelectObject(hdc_mem, old_obj)
        gdi32.DeleteObject(hBitmap)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)
        user32.DestroyIcon(hIcon)
        if small_icons[0]:
            user32.DestroyIcon(small_icons[0])
        # Load into PIL (Windows Bitmap is BGRA)
        img = Image.frombuffer("RGBA", (32, 32), raw_data, "raw", "BGRA", 0, -1)
        return img

    except Exception as e:
        logger.debug(f"Icon extraction failed: {e}")
        return None


class ModConfig(TypedDict, total=False):
    """Defines the structure of a parsed mod config dictionary."""

    Name: str
    Path: str
    Sections: Dict[str, Union[List[str], Dict[str, str]]]
    _deps_errors: List[str]
    _resolved_order_rank: int


def get_mod_name_from_config(mod_config: ModConfig) -> str:
    """Determine mod name. Prefer Metadata 'Name' then folder basename."""
    # Trust the parser to populate 'Name' in the root.
    top_name = mod_config.get("Name")
    if top_name:
        return str(top_name)

    # Fallback to folder name of the mod path
    path = mod_config.get("Path", "") or ""
    return os.path.basename(path) or path


ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
APPDATA_BASE = os.environ.get("APPDATA") or os.path.expanduser(
    os.path.join("~", ".local", "share")
)
LOG_DIR = os.path.join(APPDATA_BASE, "gmos", "logs")
LOCK_PATH = os.path.join(LOG_DIR, "gmos.lock")

logger = logging.getLogger("gmos")
logger.setLevel(logging.WARNING)  # WARNING, INFO, DEBUG

_logger_configured = False
_ensure_log_dir_lock = threading.Lock()


def ensure_log_dir_exists() -> None:
    """Create LOG_DIR if needed."""
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
    global _logger_configured
    if _logger_configured:
        return
    if log_dir:
        globals()["LOG_DIR"] = log_dir
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
    _logger_configured = True


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
        cast(Any, ctypes).windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
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
                    cast(Any, root).iconbitmap(ico)
                except Exception:
                    # alternate signature for some tk versions
                    cast(Any, root).iconbitmap(default=ico)
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
                    root_any = cast(Any, root)
                    root_any.tk.call("wm", "iconphoto", root_any._w, _app_icon_img)
                except Exception:
                    try:
                        cast(Any, root).iconphoto(False, _app_icon_img)
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
                    cast(Any, top).iconbitmap(ico)
                except Exception:
                    cast(Any, top).iconbitmap(default=ico)
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
                    cast(Any, top).iconphoto(False, _app_icon_img)
                except Exception:
                    pass
    except Exception:
        logger.debug("Failed to apply Toplevel icon", exc_info=True)


_active_icon_set: Optional[str] = None


def set_active_icon_set(set_name: str) -> None:
    """Sets the global icon set used by load_icon (e.g. 'Nexus', 'Default')."""
    global _active_icon_set
    if set_name and set_name.lower() != "default":
        _active_icon_set = set_name
    else:
        _active_icon_set = None


def get_available_icon_sets() -> List[str]:
    """Scans assets/icons/themes/ for available icon sets."""
    sets = ["Default"]
    try:
        themes_dir = resource_path(os.path.join("assets", "icons", "themes"))
        if os.path.isdir(themes_dir):
            for name in os.listdir(themes_dir):
                if os.path.isdir(os.path.join(themes_dir, name)):
                    sets.append(name)
    except Exception:
        pass
    return sorted(set(sets))


def detect_icon_theme() -> str:
    """
    Returns 'light' (for dark backgrounds) or 'dark' (for light backgrounds).
    """
    try:
        style = ttk.Style()
        bg = style.lookup("TFrame", "background")
        # If text is White, background is Dark -> We need LIGHT icons
        if get_binary_contrast_color(str(bg)) == "#FFFFFF":
            return "light"
        return "dark"
    except Exception:
        return "dark"


def load_icon(
    name: str,
    theme: Optional[str] = None,
    size: Optional[Tuple[int, int]] = (24, 24),
    force_color_variant: Optional[str] = None,
) -> Optional[Any]:
    """
    Robustly loads an icon from assets/icons/{name}.
    'theme' is the primary theme name (e.g., 'nexus').

    Checks the following paths in order, respecting the light/dark color variant:
    0. Global Active Set (if 'theme' arg is None)
    1. Custom Theme + Color Variant
    2. Custom Theme Default
    3. App Default + Color Variant
    4. Absolute Default
    """
    try:
        if not theme:
            theme = _active_icon_set
        color_variant = force_color_variant or detect_icon_theme()

        if theme:
            path = resource_path(
                os.path.join("assets", "icons", "themes", theme, color_variant, name)
            )
            if os.path.exists(path):
                return _load_image_file(path, size)

        if theme:
            path = resource_path(os.path.join("assets", "icons", "themes", theme, name))
            if os.path.exists(path):
                return _load_image_file(path, size)

        path = resource_path(
            os.path.join("assets", "icons", "default", color_variant, name)
        )
        if os.path.exists(path):
            return _load_image_file(path, size)

        path = resource_path(os.path.join("assets", "icons", "default", name))
        if os.path.exists(path):
            return _load_image_file(path, size)

    except Exception:
        return None
    return None


def _load_image_file(path: str, size: Optional[Tuple[int, int]]) -> Any:
    if Image and ImageTk and size:
        img = (
            cast(Any, Image)
            .open(path)
            .resize(size, cast(Any, Image).Resampling.LANCZOS)
        )
        return cast(Any, ImageTk).PhotoImage(img)
    return tk.PhotoImage(file=path)


# Pre-calculate APCA luminance coefficients
# Coefficients: Rec. 709 (0.2126729, 0.7151522, 0.0721750)
_APCA_R = tuple(0.2126729 * ((i / 255.0) ** 2.4) for i in range(256))
_APCA_G = tuple(0.7151522 * ((i / 255.0) ** 2.4) for i in range(256))
_APCA_B = tuple(0.0721750 * ((i / 255.0) ** 2.4) for i in range(256))

# APCA "Soft Clamp" threshold
_BLK_THRS = 0.022
_BLK_CLAMP = 1.414


def get_binary_contrast_color(hex_color: str) -> str:
    """
    Determines the best text color (Black or White) using the full
    WCAG 3.0 APCA (SAPC-717) contrast algorithm.
    """
    if not hex_color:
        return "#000000"

    clean_hex = hex_color[1:] if hex_color.startswith("#") else hex_color

    try:
        rgb_int = int(clean_hex, 16)

        # Extract RGB components
        if len(clean_hex) == 6:
            r = (rgb_int >> 16) & 0xFF
            g = (rgb_int >> 8) & 0xFF
            b = rgb_int & 0xFF
        elif len(clean_hex) == 3:
            r = ((rgb_int >> 8) & 0xF) * 17
            g = ((rgb_int >> 4) & 0xF) * 17
            b = (rgb_int & 0xF) * 17
        else:
            return "#000000"

        # Calculate Estimated Screen Luminance (Ys)
        y_bg = _APCA_R[r] + _APCA_G[g] + _APCA_B[b]

        # Luminance for Black (0.0) and White (1.0)
        y_black = 0.0
        y_white = 1.0

        # Calculate Contrast Score (Lc) for Black Text
        y_black_clamped = y_black + (_BLK_THRS - y_black) ** _BLK_CLAMP

        y_bg_clamped_for_black = y_bg
        if y_bg < _BLK_THRS:
            y_bg_clamped_for_black += (_BLK_THRS - y_bg) ** _BLK_CLAMP

        lc_black = (y_bg_clamped_for_black**0.56) - (y_black_clamped**0.57)

        # Calculate Contrast Score (Lc) for White Text
        y_bg_clamped_for_white = y_bg
        if y_bg < _BLK_THRS:
            y_bg_clamped_for_white += (_BLK_THRS - y_bg) ** _BLK_CLAMP

        lc_white = (y_white**0.62) - (y_bg_clamped_for_white**0.65)

        return "#000000" if lc_black > lc_white else "#FFFFFF"

    except (ValueError, IndexError):
        return "#000000"


def get_adaptive_color_variant(
    bg_hex: str, light_variant: str, dark_variant: str
) -> str:
    """
    Returns the light_variant if the background is DARK, and dark_variant if the background is LIGHT.
    Used for keeping colored text (Green/Red) readable on any theme.

    Args:
        bg_hex: The background color.
        light_variant: Bright color for dark backgrounds (e.g. Neon Green #00e676)
        dark_variant: Dark color for light backgrounds (e.g. Forest Green #2e7d32)
    """
    # If the background needs WHITE text, it is Dark -> Use the Bright/Light Variant
    if get_binary_contrast_color(bg_hex) == "#FFFFFF":
        return light_variant
    return dark_variant


def get_dynamic_text_color(bg_hex: Optional[str] = None) -> str:
    """
    Calculates a dynamic grayscale text color based on the background's luminance,
    scaling between black and white for optimal contrast.
    """
    if not bg_hex:
        try:
            style = ttk.Style()
            bg_hex = str(style.lookup("TFrame", "background") or "#333333")
        except Exception:
            bg_hex = "#333333"

    clean_hex = bg_hex[1:] if bg_hex.startswith("#") else bg_hex
    try:
        rgb_int = int(clean_hex, 16)
        if len(clean_hex) == 6:
            r = (rgb_int >> 16) & 0xFF
            g = (rgb_int >> 8) & 0xFF
            b = rgb_int & 0xFF
        elif len(clean_hex) == 3:
            r = ((rgb_int >> 8) & 0xF) * 17
            g = ((rgb_int >> 4) & 0xF) * 17
            b = (rgb_int & 0xF) * 17
        else:
            return "#000000"

        y_bg = _APCA_R[r] + _APCA_G[g] + _APCA_B[b]

        val = int(230 - (y_bg * 205))
        val = max(25, min(230, val))

        return f"#{val:02x}{val:02x}{val:02x}"
    except (ValueError, IndexError):
        return "#000000"


def apply_window_theme(window: tk.Wm) -> None:
    """
    Forces the Windows 10/11 title bar to use the immersive dark mode
    and matches the exact background color of the ttkbootstrap theme.
    """
    if sys.platform == "win32":
        try:
            # Get Theme Colors
            style = ttk.Style()
            bg_hex = str(style.lookup("TFrame", "background"))
            fg_hex = str(style.lookup("TLabel", "foreground"))
            # Check if current UI theme is dark
            # (If icons are 'light', the background is dark)
            is_dark_mode = detect_icon_theme() == "light"
            # Setup DWM Constants
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_CAPTION_COLOR = 35
            DWMWA_TEXT_COLOR = 36
            set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
            get_parent = ctypes.windll.user32.GetParent

            hwnd = get_parent(cast(Any, window).winfo_id())
            if hwnd == 0:
                hwnd = cast(Any, window).winfo_id()

            # Apply Dark Mode Preference
            mode_val = ctypes.c_int(1 if is_dark_mode else 0)
            if (
                set_window_attribute(
                    hwnd,
                    DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(mode_val),
                    ctypes.sizeof(mode_val),
                )
                != 0
            ):
                # Fallback for older Win10 builds (Attribute 19)
                set_window_attribute(
                    hwnd, 19, ctypes.byref(mode_val), ctypes.sizeof(mode_val)
                )

            # Apply Exact Background Color (Windows 11+)
            # Windows expects COLORREF (0x00BBGGRR), not RGB. We must swap R and B.
            def hex_to_colorref(h: str) -> int:
                h = h.lstrip("#")
                r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
                return (b << 16) | (g << 8) | r

            if bg_hex and len(bg_hex) == 7:
                color_val = ctypes.c_int(hex_to_colorref(bg_hex))
                set_window_attribute(
                    hwnd,
                    DWMWA_CAPTION_COLOR,
                    ctypes.byref(color_val),
                    ctypes.sizeof(color_val),
                )

            # Match Title Text Color
            if fg_hex and len(fg_hex) == 7:
                text_val = ctypes.c_int(hex_to_colorref(fg_hex))
                set_window_attribute(
                    hwnd,
                    DWMWA_TEXT_COLOR,
                    ctypes.byref(text_val),
                    ctypes.sizeof(text_val),
                )
        except Exception:
            pass


def setup_child_window(
    window: tk.Toplevel, parent: tk.Misc, width: int, height: int, modal: bool = True
) -> None:
    """
    Configures a child window with consistent styling and behavior.
    """
    # Apply Theme
    window.after(100, lambda: apply_window_theme(window))

    # Calculate Centered Geometry
    root = parent.winfo_toplevel()
    root.update_idletasks()  # Ensure geometry is up to date

    x = root.winfo_rootx() + (root.winfo_width() - width) // 2
    y = root.winfo_rooty() + (root.winfo_height() - height) // 2

    window.geometry(f"{width}x{height}+{x}+{y}")

    # Modality
    if modal:
        window.transient(root)  # Keep on top of root
        window.grab_set()  # Capture all events (freeze others)
        window.focus_force()


def excepthook(
    exc_type: type[BaseException],
    exc: BaseException,
    tb: Optional[types.TracebackType],
) -> None:
    """Custom exception hook to log unhandled exceptions."""
    logger.error("Unhandled exception: %s", exc, exc_info=(exc_type, exc, tb))

    # Fallback to default excepthook behavior in addition to logging
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = excepthook


def safe_spawn(
    command: Union[str, Sequence[str]],
    cwd: Optional[str] = None,
    timeout: float = 30.0,
    capture_output: bool = False,
    **popen_kwargs: Any,
) -> Union[subprocess.Popen[Any], Dict[str, Any]]:
    """Safe spawn wrapper."""
    logger.debug(
        "safe_spawn: command=%r cwd=%r capture_output=%s", command, cwd, capture_output
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
            logger.error("safe_spawn timeout: %s", te)
            return {"returncode": 124, "stdout": None, "stderr": str(te)}
        except Exception as e:
            logger.exception("safe_spawn failed (capture): %s", e)
            return {"returncode": 1, "stdout": None, "stderr": str(e)}
    else:
        # return Popen so caller can wait/interact (Async mode)
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
            logger.exception("safe_spawn failed (popen): %s", e)
            raise


def check_write_permission(path: str) -> tuple[bool, Optional[str]]:
    """
    Check whether we can write to `path`.
    """
    try:
        if not path:
            return False, "empty path"
        if os.path.isdir(path):
            parent = path
        else:
            parent = os.path.dirname(path) or "."

        if not os.access(parent, os.W_OK):
            err_msg = f"No write permission to '{parent}'"
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


T = TypeVar("T")


def retry_on_permission(
    op: Callable[[], T],
    parent: Optional[tk.Widget] = None,
    path: Optional[str] = None,
    path_updater: Optional[Callable[[str], None]] = None,
    max_attempts: int = 5,
) -> T:
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
                from gmos.ui.widgets import PermissionErrorDialog
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
                # retry the op after updating path
                continue

            # abort chosen or unexpected value: re-raise original exception
            raise last_exc from None


def fast_tempfile(parent: str, prefix: str = ".gmos_tmp_") -> Tuple[int, str]:
    """
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
        for raw in proc.stdout:
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


def sanitize_filename(name: str) -> str:
    """Return a filename safe version of the string."""
    keep = (" ", ".", "_", "-")
    return "".join(c for c in name if c.isalnum() or c in keep).strip()


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
    "safe_spawn",
    "check_write_permission",
    "handle_permission_error",
    "retry_on_permission",
    "safe_norm",
    "sanitize_filename",
    "resource_path",
    "ModConfig",
    "get_mod_name_from_config",
    "detect_icon_theme",
    "Image",
    "ImageTk",
    "get_dynamic_text_color",
]
