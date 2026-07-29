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
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Optional, cast

from gmos import utils
from gmos.core import patcher, security
from gmos.core.injection import SandboxInjector
from gmos.core.patcher import (
    ConflictDelegate,
    apply_dependency_resolution,
    parse_mod_config,
    validate_mod_config,
)
from gmos.io import atomic_replace, safe_rmtree
from gmos.net.downloader import DownloadManager
from gmos.net.providers import NexusProvider
from gmos.state import policy
from gmos.state.config import load_global_config
from gmos.utils import ModConfig, logger, safe_norm


@dataclass
class RuntimeMod:
    """
    Represents a loaded mod with its runtime state (validity, errors, security).
    Decouples UI representation from configuration data.
    """

    config: ModConfig
    is_enabled: bool = True
    is_valid: bool = True
    errors: List[str] = field(default_factory=lambda: [])
    security_risks: List[security.SecurityRisk] = field(default_factory=lambda: [])

    @property
    def name(self) -> str:
        """Returns Config Name or falls back to folder name."""
        name = self.config.get("Name")
        if name:
            return str(name)
        # Fallback to folder name
        path = self.path
        if path:
            return os.path.basename(path)
        return "Unknown Mod"

    @property
    def path(self) -> str:
        return str(self.config.get("Path", ""))


class SecurityScanError(Exception):
    """Raised when a downloaded mod fails the security scan."""

    def __init__(self, message: str, risks: List[security.SecurityRisk]):
        super().__init__(message)
        self.risks = risks


class GmosSession:
    """
    The 'Brain' of the application.
    Encapsulates state and business logic, independent of the GUI.
    """

    def __init__(self, game_dir: str, mods_dir: str):
        self.game_dir = safe_norm(game_dir)
        self.mods_dir = safe_norm(mods_dir)
        self.mods: List[RuntimeMod] = []

        self.downloader = DownloadManager()
        self._downloads_file = os.path.join(utils.LOG_DIR, "gmos_downloads.json")
        self._active_tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="gmos_install"
        )
        self._load_persisted_downloads()

    def _load_persisted_downloads(self) -> None:
        """Loads download state from disk to survive restarts."""
        if not os.path.exists(self._downloads_file):
            return
        try:
            with open(self._downloads_file, "r") as f:
                self._active_tasks = json.load(f)
        except Exception as e:
            logger.error(f"Failed to load download state: {e}")

    def _save_persisted_downloads(self) -> None:
        """Saves current download state."""
        try:
            atomic_replace(
                self._downloads_file, json.dumps(self._active_tasks, indent=2)
            )
        except Exception as e:
            logger.error(f"Failed to save download state: {e}")

    def restore_tasks(
        self, callback: Callable[[str, int, int, str, str], None]
    ) -> None:
        """Called by App on startup to re-populate the UI."""
        with self._lock:
            snapshot = dict(self._active_tasks)

        for tid, info in snapshot.items():
            state = info.get("state", "error")
            name = info.get("name", "Unknown Mod")
            # Treat transient states as Interrupted on boot
            if state in (
                "downloading",
                "resolving",
                "extracting",
                "scanning",
                "installing",
            ):
                # Mark as interrupted in memory so if the user clicks resume, it works
                if tid in self._active_tasks:
                    self._active_tasks[tid]["state"] = "interrupted"
                callback(tid, 0, 0, "Interrupted", name)

            elif state == "installed":
                callback(tid, 100, 100, "Installed 📂", name)
            elif state == "risk":
                callback(tid, 0, 0, "Dangerous ⚠️", name)
            elif state == "error":
                err = info.get("error", "Unknown error")
                callback(tid, 0, 0, f"Error: {err}", name)
            else:
                callback(tid, 0, 0, state.title(), name)

    def get_active_tasks(self) -> Dict[str, Dict[str, Any]]:
        """Returns a thread-safe copy of all tracked tasks."""
        with self._lock:
            return dict(self._active_tasks)

    @property
    def active_tasks(self) -> Dict[str, Dict[str, Any]]:
        """Direct access to active tasks dict (required by UI)."""
        return self._active_tasks

    def remove_task(self, task_id: str) -> None:
        """Removes a task from tracking, deletes partial files, and saves state."""
        with self._lock:
            if task_id in self._active_tasks:
                # Cleanup temporary file if it exists
                info = self._active_tasks[task_id]
                temp_path = info.get("temp_path")
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError as e:
                        logger.warning(
                            f"Failed to delete partial file {temp_path}: {e}"
                        )

                del self._active_tasks[task_id]
                self._save_persisted_downloads()

    def resume_task(
        self, task_id: str, callback: Callable[[str, int, int, str, str], None]
    ) -> None:
        """Resumes a task by restarting the pipeline with the stored URL."""
        with self._lock:
            info = self._active_tasks.get(task_id)

        if info and "url" in info:
            # Re-queue the pipeline
            self.handle_nxm_link(info["url"], task_id, callback)

    def refresh_mods(self) -> Generator[str, None, None]:
        """
        Scans disk, parses configs, applies policy, and resolves dependencies for UI feedback.
        Yields status strings for UI feedback.
        """
        yield "Scanning mods directory..."

        new_mods: List[RuntimeMod] = []

        if not os.path.isdir(self.mods_dir):
            try:
                os.makedirs(self.mods_dir, exist_ok=True)
            except OSError:
                logger.warning("Mods directory not found/creatable: %s", self.mods_dir)
                self.mods = []
                return

        # Discovery & Parsing
        with os.scandir(self.mods_dir) as it:
            for entry in it:
                if entry.is_dir() and not entry.name.startswith("_"):
                    mod_path = entry.path
                    try:
                        raw_cfg = parse_mod_config(mod_path)
                        if raw_cfg:
                            rmod = RuntimeMod(config=raw_cfg)
                            rmod.config["Path"] = mod_path

                            # Validation
                            valid, err_obj = validate_mod_config(
                                cast(Dict[str, Any], raw_cfg)
                            )
                            rmod.is_valid = bool(valid)

                            if not valid:
                                if isinstance(err_obj, list):
                                    err_list = cast(List[object], err_obj)
                                    rmod.errors.extend([str(e) for e in err_list])
                                else:
                                    rmod.errors.append(str(err_obj))
                            else:
                                # Security Scan (only if valid structure)
                                rmod.security_risks = security.scan_mod(mod_path)

                            new_mods.append(rmod)
                    except Exception as e:
                        logger.error("Failed to load mod at %s: %s", mod_path, e)

        yield f"Found {len(new_mods)} mods. Applying policy..."

        # Apply Load Order Policy
        temp_configs = [m.config for m in new_mods]

        try:
            sorted_configs = policy.load_and_apply_policy(
                cast(List[Dict[str, Any]], temp_configs), game_dir=self.game_dir
            )
        except TypeError:
            sorted_configs = policy.load_and_apply_policy(
                cast(List[Dict[str, Any]], temp_configs)
            )

        mod_map = {m.config["Path"]: m for m in new_mods if "Path" in m.config}
        ordered_mods: List[RuntimeMod] = []

        for cfg in sorted_configs:
            path = cfg.get("Path")
            if path and path in mod_map:
                rmod = mod_map[path]
                rmod.is_enabled = bool(cfg.get("Enabled", True))
                rmod.config = cast(ModConfig, cfg)
                ordered_mods.append(rmod)

        yield "Resolving dependencies..."

        # Dependency Resolution
        resolve_input = [m.config for m in ordered_mods]
        final_order_configs, dep_errors = apply_dependency_resolution(resolve_input)

        final_mods: List[RuntimeMod] = []
        for f_cfg in final_order_configs:
            path = f_cfg.get("Path")
            if path and path in mod_map:
                rmod = mod_map[path]
                final_mods.append(rmod)

                name = rmod.name
                if name in dep_errors:
                    rmod.is_valid = False
                    rmod.errors.extend(dep_errors[name])

        self.mods = final_mods
        yield f"Refresh complete. {len(self.mods)} mods loaded."

    def apply_changes(
        self,
        game_executable: str = "game.exe",
        is_packed: bool = False,
        conflict_delegate: Optional[ConflictDelegate] = None,
    ) -> Generator[str, None, None]:
        """
        Calculates patch plan and executes the patcher.
        """
        yield "Calculating patch plan..."
        enabled_configs = [m.config for m in self.mods if m.is_enabled]

        # Generate plan to check for early errors
        try:
            _ = patcher.analyze_mods_for_conflicts(enabled_configs)
        except Exception:
            pass

        final_plan: List[Any] = []
        for mod in self.mods:
            if mod.is_enabled:
                try:
                    plan = patcher.generate_patch_plan(mod.path, mod.config)
                    # Filter plan based on conflict winners (File Rules)
                    plan = patcher.apply_policy_to_plan(plan, game_dir=self.game_dir)
                    final_plan.extend(plan)
                except Exception as e:
                    logger.error(f"Plan gen failed for {mod.name}: {e}")

        yield f"Executing patch plan ({len(final_plan)} operations)..."

        # Patcher now handles threading internally if supported
        log_lines = patcher.run_patcher(
            self.game_dir,
            final_plan,
            conflict_delegate=conflict_delegate,
            game_executable=game_executable,
            is_packed=is_packed,
        )

        for line in log_lines:
            yield line

        yield "Patching complete."

    def check_sandbox_status(self) -> bool:
        """Returns True if the sandbox is currently active in project.godot."""
        injector = SandboxInjector(self.game_dir)
        return injector.is_injected()

    def toggle_sandbox(self) -> bool:
        """
        Toggles the sandbox state. Returns the new state (True=Injected).
        """
        injector = SandboxInjector(self.game_dir)
        if injector.is_injected():
            injector.remove()
            return False
        else:
            if not injector.inject():
                raise RuntimeError(
                    f"Failed to inject sandbox into '{self.game_dir}'.\n"
                    "Please ensure 'project.godot' exists and that GMOS has write permissions for this folder."
                )
            return True

    def install_mod_from_archive(
        self,
        archive_path: str,
        author_hint: str = "",
        meta_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Installs a mod via Quarantine -> Scan -> Install pipeline.

        Args:
            meta_data: Optional dictionary containing 'name', 'modId', 'version', etc.
                       If provided, relaxes strict 'mod.mos' check for generic mods.
        """
        if not os.path.exists(archive_path):
            raise FileNotFoundError(f"Archive not found: {archive_path}")

        # Prepare Quarantine
        quarantine_dir = os.path.join(self.mods_dir, "_quarantine")
        os.makedirs(quarantine_dir, exist_ok=True)

        # Unique extract folder
        extract_name = f"install_{uuid.uuid4().hex[:8]}"
        extract_path = os.path.join(quarantine_dir, extract_name)

        try:
            logger.info("Extracting %s to quarantine...", archive_path)

            # Prevent Zip Slip: Validate extraction paths
            # Ensure extract_path is absolute for robust prefix checking
            abs_extract_path = os.path.abspath(extract_path)

            try:
                with zipfile.ZipFile(archive_path, "r") as zf:
                    for member in zf.infolist():
                        # Resolve the full target path
                        target_path = os.path.abspath(
                            os.path.join(abs_extract_path, member.filename)
                        )

                        # Verify the target is still within the extraction root
                        if not target_path.startswith(abs_extract_path):
                            logger.warning(
                                f"Security: Zip Slip attempt detected in {member.filename}"
                            )
                            raise SecurityScanError(
                                f"Security Violation: Archive contains file '{member.filename}' which attempts to write outside the extraction directory.",
                                [],
                            )

                        zf.extract(member, abs_extract_path)
            except zipfile.BadZipFile:
                raise SecurityScanError(
                    "Corrupted Archive: File is not a valid zip.", []
                ) from None

            # Security Scan
            logger.info("Scanning for security risks...")
            risks = security.scan_mod(extract_path)

            # Filter for HIGH severity
            high_risks = [r for r in risks if r.severity == "HIGH"]

            if high_risks:
                logger.warning(
                    "Security risks found in %s: %s", archive_path, high_risks
                )
                raise SecurityScanError(
                    "High severity security risks detected.", high_risks
                )

            # Install (Move to mods_dir)
            items = os.listdir(extract_path)
            has_native_manifest = "mod.mos" in items

            # Smart Flattening
            if (
                not has_native_manifest
                and len(items) == 1
                and os.path.isdir(os.path.join(extract_path, items[0]))
            ):
                final_source = os.path.join(extract_path, items[0])
                native_name = items[0]
            else:
                final_source = extract_path
                native_name = os.path.splitext(os.path.basename(archive_path))[0]

            # Determine Target Folder Name
            if meta_data and "name" in meta_data:
                # Use the clean name from API (e.g. "SMAPI")
                mod_folder_name = (
                    str(meta_data["name"])
                    .replace(":", "")
                    .replace("/", "_")
                    .replace("\\", "_")
                )
            else:
                mod_folder_name = native_name

            # Auto-Convert Generic Mods (Feature creep)
            final_manifest_path = os.path.join(final_source, "mod.mos")
            if not os.path.exists(final_manifest_path):
                if meta_data:
                    # Generic Mod from Nexus: Generate a basic mod.mos so GMOS can load it
                    logger.info(
                        "Generating mod.mos for generic mod: %s", mod_folder_name
                    )
                    basic_config: Dict[str, Any] = {
                        "ModInfo": {
                            "Name": meta_data.get("name", mod_folder_name),
                            "Version": meta_data.get("version", "1.0.0"),
                            "Author": meta_data.get("author", "Unknown"),
                            "Description": f"Imported from {meta_data.get('source', 'Archive')}",
                        },
                        "FileReplace": {},  # Empty rules, assumes manual file placement or simple replacements
                    }
                    # We write a simple INI-style config
                    with open(final_manifest_path, "w") as f:
                        mi = cast(Dict[str, str], basic_config["ModInfo"])
                        f.write(f"[ModInfo]\nName={mi['Name']}\n")
                        f.write(f"Version={mi['Version']}\n")
                        f.write(f"Author={mi['Author']}\n")
                        f.write(f"Description={mi['Description']}\n")
                else:
                    raise FileNotFoundError(
                        "Invalid Mod: 'mod.mos' manifest not found and no metadata provided."
                    )

            # Ensure Unique Destination Name
            dest_path = os.path.join(self.mods_dir, mod_folder_name)
            counter = 1
            while os.path.exists(dest_path):
                dest_path = os.path.join(
                    self.mods_dir, f"{mod_folder_name} ({counter})"
                )
                counter += 1

            shutil.move(final_source, dest_path)

            # Write Extended Metadata
            if meta_data:
                # Add install time and archive name
                meta_data["installed_at"] = time.time()
                meta_data["archive_name"] = os.path.basename(archive_path)
                meta_data["install_path"] = dest_path

                try:
                    atomic_replace(
                        os.path.join(dest_path, "gmos_meta.json"),
                        json.dumps(meta_data, indent=2),
                    )
                except Exception as e:
                    logger.warning(f"Failed to write gmos_meta.json: {e}")

            logger.info("Mod installed successfully to %s", dest_path)
            return mod_folder_name

        except Exception as e:
            # Cleanup quarantine on error
            if os.path.exists(extract_path):
                try:
                    safe_rmtree(extract_path)
                except OSError:
                    pass
            raise e
        finally:
            # Cleanup empty quarantine dir
            try:
                os.rmdir(quarantine_dir)
            except Exception:
                pass

    def handle_nxm_link(
        self,
        link: str,
        task_id: str,
        callback: Callable[[str, int, int, str, str], None],
    ) -> None:
        """Registers task and spawns the auto-install pipeline."""
        # Register Task immediately
        with self._lock:
            self._active_tasks[task_id] = {
                "id": task_id,
                "url": link,
                "name": "Resolving...",
                "state": "resolving",
                "game_domain": "unknown",
                "started_at": time.time(),
            }
            self._save_persisted_downloads()

        # Start Async Pipeline
        self._executor.submit(self._pipeline_worker, link, task_id, callback)

    def _check_abort(self, task_id: str) -> bool:
        """Helper to check if task was paused or deleted during execution."""
        with self._lock:
            if task_id not in self._active_tasks:
                return True  # Deleted
            if self._active_tasks[task_id]["state"] == "interrupted":
                return True  # Paused
        return False

    def _pipeline_worker(
        self,
        link: str,
        task_id: str,
        callback: Callable[[str, int, int, str, str], None],
    ) -> None:
        """Background worker that runs the entire Download -> Install chain."""
        try:
            if self._check_abort(task_id):
                return

            # Parse Metadata
            callback(task_id, 0, 0, "Resolving...", "Resolving...")

            # Basic Parse
            match = re.search(r"nxm://([^/]+)/mods/(\d+)/files/(\d+)", link)
            if not match:
                raise ValueError("Invalid NXM link format.")

            game_domain, mod_id, file_id = match.groups()
            query = link.split("?")[1] if "?" in link else ""

            # Check abort before network calls
            if self._check_abort(task_id):
                return

            # Load Config/Provider
            global_cfg = load_global_config()
            provider = NexusProvider(
                api_key=global_cfg.nexus_api_key or "", game_domain=game_domain
            )

            # Fetch Real Name (API)
            meta = provider.get_metadata(mod_id)
            if not meta:
                meta = provider.fetch_mod_details(mod_id)
                pass
            real_name = meta.name if meta else f"Nexus Mod {mod_id}"

            # Resolve Download URL
            callback(task_id, 0, 0, "Getting Link...", real_name)
            cdn_url = provider.get_file_download_url_from_nxm(mod_id, file_id, query)
            if not cdn_url:
                raise ValueError("Could not resolve CDN URL.")

            if self._check_abort(task_id):
                return

            # Update Task State
            filename = f"{mod_id}_{file_id}.zip"
            dest_path = os.path.join(self.mods_dir, "_downloads", filename)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            with self._lock:
                # Ensure task still exists before writing
                if task_id in self._active_tasks:
                    self._active_tasks[task_id]["name"] = real_name
                    self._active_tasks[task_id]["game_domain"] = game_domain
                    self._active_tasks[task_id]["state"] = "downloading"
                    self._active_tasks[task_id][
                        "temp_path"
                    ] = dest_path  # Store path for deletion
                    self._save_persisted_downloads()
                else:
                    return  # Task was deleted while we were fetching metadata

            # Download
            def _prog(c: int, t: int) -> None:
                # Abort check inside the download loop
                if self._check_abort(task_id):
                    # Raise exception to break the downloader loop
                    raise InterruptedError("Task Paused/Deleted")
                callback(task_id, c, t, "Downloading", real_name)

            self.downloader.download_file(
                cdn_url, dest_path, progress_callback=_prog
            ).result()

            if self._check_abort(task_id):
                return

            # Verify Archive
            if not zipfile.is_zipfile(dest_path):
                raise SecurityScanError(
                    "Corrupted Archive: File is not a valid zip.", []
                )

            # Scan
            if self._check_abort(task_id):
                return
            callback(task_id, 0, 100, "Scanning 🛡️", real_name)

            # Install & Tag
            install_meta: Dict[str, Any] = {
                "name": real_name,
                "version": meta.version if meta else "?.?.?",
                "author": meta.author if meta else "Unknown",
                "modId": mod_id,
                "fileId": file_id,
                "source": "Nexus",
            }

            # Run Installer
            self.install_mod_from_archive(dest_path, meta_data=install_meta)

            # Cleanup
            try:
                os.remove(dest_path)
            except Exception:
                pass

            if self._check_abort(task_id):
                return

            # Complete - Remove from active list immediately
            self.remove_task(task_id)
            callback(task_id, 100, 100, "Installed 📂", real_name)

        except InterruptedError:
            # Task was manually paused or deleted.
            # If deleted, it's gone from self._active_tasks.
            # If paused, state is already 'interrupted'.
            # We just exit the worker silently.
            return

        except SecurityScanError as se:
            if self._check_abort(task_id):
                return
            logger.warning(f"Pipeline Security/Validation failed for {task_id}: {se}")
            with self._lock:
                if task_id in self._active_tasks:
                    self._active_tasks[task_id]["state"] = "risk"
                    self._save_persisted_downloads()
            callback(task_id, 0, 0, "Dangerous ⚠️", "Risk")

        except Exception as e:
            if self._check_abort(task_id):
                return
            logger.exception(f"Pipeline failed for {task_id}")
            with self._lock:
                if task_id in self._active_tasks:
                    self._active_tasks[task_id]["state"] = "error"
                    self._save_persisted_downloads()
            callback(task_id, 0, 0, f"Error ❌: {str(e)}", "Error")
