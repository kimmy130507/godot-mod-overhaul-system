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
Tooling Manager
Handles the acquisition and management of external binaries (GDRE Tools, etc.).
"""

import json
import os
import platform
import stat
import urllib.request
import zipfile
from typing import Callable, Dict, Optional, TypedDict, cast

from gmos.state.config import get_app_data_path
from gmos.utils import logger


class ToolDefinition(TypedDict):
    name: str
    version: str
    urls: Dict[str, str]
    bin_name: Dict[str, str]


TOOLS_MANIFEST: Dict[str, ToolDefinition] = {
    "gdre_tools": {
        "name": "GDRE Tools (Godot Reverse Engineering)",
        "version": "v2.2.0",
        "urls": {
            "Windows": "https://github.com/GDRETools/gdsdecomp/releases/download/v2.2.0/GDRE_Tools-v2.2.0-windows.zip",
            "Linux": "https://github.com/GDRETools/gdsdecomp/releases/download/v2.2.0/GDRE_Tools-v2.2.0-linux.zip",
            "Darwin": "https://github.com/GDRETools/gdsdecomp/releases/download/v2.2.0/GDRE_Tools-v2.2.0-macos.zip",
        },
        "bin_name": {
            "Windows": "gdre_tools.exe",
            "Linux": "gdre_tools.x86_64",
            "Darwin": "gdre_tools",
        },
    }
}


class ToolManager:
    """
    Manages installation and path resolution for external tools.
    Stores tools in global app data: %APPDATA%/gmos/tools/
    """

    def __init__(self) -> None:
        self.base_dir = os.path.join(get_app_data_path(), "tools")

    def _get_system_key(self) -> str:
        s = platform.system()
        if s == "Windows":
            return "Windows"
        if s == "Linux":
            return "Linux"
        if s == "Darwin":
            return "Darwin"
        return "Windows"  # Fallback

    def _get_latest_gdre_url(self, sys_key: str) -> str:
        """Queries GitHub API for the latest release asset URL matching the platform."""
        api_url = "https://api.github.com/repos/GDRETools/gdsdecomp/releases/latest"

        keywords = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}

        search_term = keywords.get(sys_key)
        if not search_term:
            raise ValueError(f"Unsupported platform for auto-update: {sys_key}")

        with urllib.request.urlopen(api_url) as response:  # nosec B310
            if response.status != 200:
                raise RuntimeError(f"GitHub API status: {response.status}")

            data = json.loads(response.read().decode())

            for asset in data.get("assets", []):
                name = asset.get("name", "").lower()
                if search_term in name and name.endswith(".zip"):
                    return cast(str, asset["browser_download_url"])

            raise RuntimeError(f"No asset found containing '{search_term}'")

    def get_tool_path(self, tool_id: str) -> Optional[str]:
        """Returns the absolute path to the executable if installed, else None."""
        if tool_id not in TOOLS_MANIFEST:
            return None

        manifest = TOOLS_MANIFEST[tool_id]
        sys_key = self._get_system_key()
        bin_name = manifest["bin_name"].get(sys_key, "game.exe")

        install_dir = os.path.join(self.base_dir, tool_id)
        if not os.path.exists(install_dir):
            return None
        exe_path = os.path.join(install_dir, bin_name)

        if os.path.exists(exe_path):
            return exe_path
        search_suffix = bin_name.replace("\\", "/").lower().replace(" ", "_")
        for root, _, files in os.walk(install_dir):
            for file in files:
                full_path = (
                    os.path.join(root, file)
                    .replace("\\", "/")
                    .lower()
                    .replace(" ", "_")
                )
                if full_path.endswith(search_suffix):
                    return os.path.normpath(os.path.join(root, file))
        return None

    def is_installed(self, tool_id: str) -> bool:
        return self.get_tool_path(tool_id) is not None

    def install_tool(
        self, tool_id: str, progress_callback: Optional[Callable[[float], None]] = None
    ) -> str:
        """
        Downloads and extracts the tool.
        Returns the path to the executable.
        """
        if tool_id not in TOOLS_MANIFEST:
            raise ValueError(f"Unknown tool: {tool_id}")

        manifest = TOOLS_MANIFEST[tool_id]
        sys_key = self._get_system_key()
        url = None
        if tool_id == "gdre_tools":
            try:
                url = self._get_latest_gdre_url(sys_key)
                logger.info(f"Resolved latest {tool_id} URL: {url}")
            except Exception as e:
                logger.warning(f"Failed to fetch latest version (using fallback): {e}")

        if not url:
            url = manifest["urls"].get(sys_key)

        if not url:
            raise RuntimeError(f"No URL defined for {tool_id} on {sys_key}")
        install_dir = os.path.join(self.base_dir, tool_id)
        os.makedirs(install_dir, exist_ok=True)
        zip_path = os.path.join(self.base_dir, f"{tool_id}_install.zip")
        logger.info("Downloading %s from %s...", tool_id, url)
        try:

            def _report(block_num: int, block_size: int, total_size: int) -> None:
                if progress_callback and total_size > 0:
                    percent = (block_num * block_size) / total_size
                    progress_callback(min(percent, 1.0))

            urllib.request.urlretrieve(url, zip_path, reporthook=_report)  # nosec B310

            logger.info("Extracting %s...", tool_id)
            if progress_callback:
                progress_callback(1.0)  # Extraction starting

            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(install_dir)

            os.remove(zip_path)

            exe_path = self.get_tool_path(tool_id)
            if not exe_path:
                raise FileNotFoundError(f"Binary not found after extraction: {tool_id}")

            if sys_key != "Windows":
                st = os.stat(exe_path)
                os.chmod(exe_path, st.st_mode | stat.S_IEXEC)

            logger.info("Tool %s installed to %s", tool_id, exe_path)
            return exe_path

        except Exception as e:
            logger.error("Failed to install tool %s: %s", tool_id, e)
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            raise e
