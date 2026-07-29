# GMOS Data Schemas

This document defines the JSON structures used by GMOS for configuration, state tracking, and data exchange.

# 1. Configuration

GMOS uses a **Two-Tier Configuration System**:

### Tier 1: Global Registry (`global_registry.db`)

**Location:** User Data folder (e.g., `%APPDATA%/gmos/global_registry.db`).
**Format:** SQLite Database.
**Purpose:** Tracks known game instances, the active theme, and the last accessed instance ID.

### Tier 2: Instance Configuration (`instance.json`)
**Location:** Inside each game's data folder: `<GameDir>/gmos_data/instance.json`.
**Purpose:** Settings specific to that installation.

```json
{
  "game_dir": "C:/Games/Brotato",
  "mods_dir": "C:/Games/Brotato/mods", 
  "game_executable": "Brotato.exe",
  "launch_override": "",
  "last_played": "2025-11-23 12:00",
  "mod_website": "",
  "active_profile": "",
  "executables": [],
  "is_packed": false
}
```

# 2. Policy (`user_load_order.json`)

**Location:** Same folder as `instance.json`.
**Purpose:** Persists the user's load order, enabled mods, and conflict resolution rules for a specific instance.

```json
{
  "version": 1,
  "load_order": [
    {
      "name": "CoreMod",
      "enabled": true
    },
    {
      "name": "TexturePack",
      "enabled": false
    }
  ],
  "file_rules": {
    "res://scripts/player.gd": "CoreMod",
    "res://icon.png": "TexturePack"
  }
}
```

  * **`load_order`**: Defines the explicit priority list. Mods listed later override earlier ones.
  * **`file_rules`**: A dictionary mapping a resource path (`res://...`) to the name of the "Winner" mod. This allows granular conflict resolution (e.g., Mod A wins for script X, but Mod B wins for texture Y).

# 3. Runtime Manifest (`runtime_manifest.json`)

**Location:** `<GameDir>/gmos_data/runtime_manifest.json`.
**Purpose:** Tracks the current state of the game directory to enable accurate rollbacks.

```json
{
  "timestamp": "2025-11-23T12:00:00",
  "game_dir": "C:/Games/Brotato",
  "target_pck": "gmos_override.pck",
  "modified_files": [
    "scripts/player.gd",
    "gmos_sandbox.gd",
    "project.godot"
  ],
  "applied_ops_count": 1,
  "applied_ops": [
    {
      "mod": "CoreMod",
      "op": "VariablePatch",
      "target": "res://scripts/player.gd::health",
      "source": "patches/stats.gd::health",
      "mode": "replace"
    }
  ]
}
```

  * **`modified_files`**: A flat list of all files GMOS created or modified. The Revert logic iterates this list to restore `.bak` files.
  * **`applied_ops_count`**: An integer tracking the total number of operations applied during the patch run.
  * **`applied_ops`**: An audit log of operations for debugging.

# 4. Profile (`gmos_profile.json`)

**Location:** User-defined export path.
**Purpose:** Shareable mod lists for community distribution.

```json
{
  "format_version": "1.0",
  "gmos_version": "1.0.0",
  "timestamp_utc": "2025-11-23T12:00:00Z",
  "game_executable": "game.exe",
  "description": "",
  "isolation": {
    "isolate_data": false
  },
  "mods": [
    {
      "name": "ModA",
      "enabled": true,
      "version": "1.2.0"
    }
  ]
}
```