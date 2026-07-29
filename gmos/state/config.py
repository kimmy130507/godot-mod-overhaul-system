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
Configuration Management
1. Global Registry (global_config.json) in User Data.
2. Instance Configuration (instance.json) in Game Directory.
"""

import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, cast

from gmos.io import atomic_replace
from gmos.utils import logger

GLOBAL_DB_FILENAME = "global_registry.db"
LEGACY_CONFIG_FILENAME = "global_config.json"
INSTANCE_CONFIG_FILENAME = "instance.json"


@dataclass
class InstanceMetadata:
    """Metadata for the Game Selector UI."""

    id: str
    name: str
    path: str
    godot_version: int = 0
    custom_name: Optional[str] = None
    icon_path: Optional[str] = None


@dataclass
class GlobalConfig:
    """Application-wide registry."""

    schema_version: str = "2.0"
    default_instance_id: Optional[str] = None
    theme_preference: str = "darkly"
    nexus_api_key: str = ""
    legal_accepted: bool = False
    sandbox_enabled: bool = True
    icon_set: str = "Default"
    godot_editor_path: str = ""
    instances: Dict[str, InstanceMetadata] = field(default_factory=lambda: {})


@dataclass
class InstanceConfig:
    """Game-specific configuration."""

    game_dir: str
    mods_dir: str
    game_executable: str = "game.exe"
    launch_override: str = ""
    last_played: str = ""  # ISO format date
    mod_website: str = ""
    active_profile: str = ""
    executables: List[Dict[str, Any]] = field(default_factory=lambda: [])
    is_packed: bool = False


def get_app_data_path() -> str:
    """Resolves the OS-specific application data directory."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        # Linux / Unix: XDG support
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))

    return os.path.join(base, "gmos")


def get_global_config_path() -> str:
    """Returns the path to the global registry in AppData."""
    return os.path.join(get_app_data_path(), GLOBAL_DB_FILENAME)


def _get_db_connection() -> sqlite3.Connection:
    """Establishes and returns a connection to the SQLite database."""
    path = get_global_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    """Creates the database schema if it doesn't exist."""
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS instances (
                id TEXT PRIMARY KEY,
                name TEXT,
                path TEXT,
                godot_version INTEGER DEFAULT 0,
                custom_name TEXT,
                icon_path TEXT
            );
        """)


def _migrate_legacy_json(conn: sqlite3.Connection) -> None:
    """Migrates legacy global_config.json to SQLite."""
    legacy_path = os.path.join(get_app_data_path(), LEGACY_CONFIG_FILENAME)
    if not os.path.exists(legacy_path):
        return

    # Skip if DB already populated
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM instances")
    if cur.fetchone()[0] > 0:
        return

    logger.info("Migrating legacy global_config.json to SQLite...")
    try:
        with open(legacy_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        settings = cast(
            Dict[str, Any],
            {
                "schema_version": data.get("schema_version", "2.0"),
                "default_instance_id": data.get("default_instance_id"),
                "theme_preference": data.get("theme_preference", "darkly"),
                "nexus_api_key": data.get("nexus_api_key", ""),
                "legal_accepted": str(data.get("legal_accepted", False)).lower(),
                "icon_set": data.get("icon_set", "Default"),
                "godot_editor_path": data.get("godot_editor_path", ""),
            },
        )

        with conn:
            for k, v in settings.items():
                if v is not None:
                    conn.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                        (k, str(v) if v is not None else ""),
                    )

            inst_data = cast(Dict[str, Any], data.get("instances", {}))
            for k, v in inst_data.items():
                conn.execute(
                    """
                    INSERT INTO instances (id, name, path, godot_version, custom_name, icon_path)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        v.get("id", k),
                        v.get("name", "Unknown"),
                        v.get("path", ""),
                        v.get("godot_version", 0),
                        v.get("custom_name"),
                        v.get("icon_path"),
                    ),
                )

        # Rename legacy file to .bak
        os.replace(legacy_path, legacy_path + ".bak")
        logger.info("Migration complete.")
    except Exception as e:
        logger.error(f"Migration failed: {e}")


def load_global_config() -> GlobalConfig:
    """Loads the global registry from SQLite."""
    try:
        conn = _get_db_connection()
        _init_db(conn)
        _migrate_legacy_json(conn)

        cur = conn.cursor()
        cur.execute("SELECT key, value FROM settings")
        settings = {row["key"]: row["value"] for row in cur.fetchall()}

        cur.execute("SELECT * FROM instances")
        instances: Dict[str, InstanceMetadata] = {}
        for row in cur.fetchall():
            meta = InstanceMetadata(
                id=row["id"],
                name=row["name"],
                path=row["path"],
                godot_version=int(row["godot_version"]),
                custom_name=row["custom_name"],
                icon_path=row["icon_path"],
            )
            instances[meta.id] = meta

        conn.close()

        return GlobalConfig(
            schema_version=settings.get("schema_version", "2.0"),
            default_instance_id=settings.get("default_instance_id"),
            theme_preference=settings.get("theme_preference", "darkly"),
            nexus_api_key=settings.get("nexus_api_key", ""),
            legal_accepted=(settings.get("legal_accepted", "false") == "true"),
            icon_set=settings.get("icon_set", "Default"),
            godot_editor_path=settings.get("godot_editor_path", ""),
            instances=instances,
        )
    except Exception as e:
        logger.error(f"Failed to load global db: {e}")
        return GlobalConfig()


def save_global_config(cfg: GlobalConfig) -> None:
    """Saves the global registry to SQLite."""
    try:
        conn = _get_db_connection()
        _init_db(conn)
        with conn:
            settings_map: Dict[str, Any] = {
                "schema_version": cfg.schema_version,
                "default_instance_id": cfg.default_instance_id,
                "theme_preference": cfg.theme_preference,
                "nexus_api_key": cfg.nexus_api_key,
                "legal_accepted": str(cfg.legal_accepted).lower(),
                "icon_set": cfg.icon_set,
                "godot_editor_path": cfg.godot_editor_path,
            }
            for k, v in settings_map.items():
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (k, str(v) if v is not None else ""),
                )

            conn.execute("DELETE FROM instances")

            if cfg.instances:
                params = [
                    (
                        meta.id,
                        meta.name,
                        meta.path,
                        meta.godot_version,
                        meta.custom_name,
                        meta.icon_path,
                    )
                    for meta in cfg.instances.values()
                ]
                conn.executemany(
                    """
                    INSERT INTO instances (id, name, path, godot_version, custom_name, icon_path)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    params,
                )

        conn.close()
    except Exception as e:
        logger.error(f"Failed to save global db: {e}")


def load_instance_config_dict(path: str) -> Dict[str, Any]:
    """Loads an instance config as a dictionary for UI consumption."""
    defaults = asdict(InstanceConfig(game_dir="", mods_dir=""))
    if not os.path.exists(path):
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        defaults.update(data)
        return defaults
    except Exception as e:
        logger.error(f"Failed to load instance config {path}: {e}")
        return defaults


def save_instance_config_dict(data: Dict[str, Any], path: str) -> None:
    """Saves the UI configuration dictionary to the instance file."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_replace(path, json.dumps(data, indent=2))
    except Exception as e:
        logger.error(f"Failed to save config {path}: {e}")
