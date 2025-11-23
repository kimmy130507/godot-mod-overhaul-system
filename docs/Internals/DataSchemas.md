# GMOS Data Schemas

This document defines the JSON structures used by GMOS for configuration, state tracking, and data exchange.

# 1. Configuration (`config.json`)

**Location:** OS-specific user data folder (e.g., `%LOCALAPPDATA%/gmos/config.json`).
**Purpose:** Stores global application settings.

```json
{
  "game_dir": "C:/Games/Brotato",
  "mods_dir": "C:/Games/Brotato/mods",
  "game_executable": "Brotato.exe",
  "launch_override": "",
  "legal_accepted": true
}
```

| Key | Type | Description |
| :--- | :--- | :--- |
| `game_dir` | string | Path to the game root directory. |
| `mods_dir` | string | Path to the `mods/` folder (usually inside `game_dir`). |
| `legal_accepted` | bool | `true` if the user accepted the first-run disclaimer. |

# 2. Policy (`user_load_order.json`)

**Location:** Same folder as `config.json`.
**Purpose:** Persists the user's load order, enabled mods, and conflict resolution rules.

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

**Location:** `<GameDir>/runtime_manifest.json`.
**Purpose:** Tracks the current state of the game directory to enable accurate rollbacks.

```json
{
  "timestamp": "2025-11-23T12:00:00",
  "game_dir": "C:/Games/Brotato",
  "modified_files": [
    "scripts/player.gd",
    "gmos_sandbox.gd",
    "project.godot"
  ],
  "applied_ops": [
    {
      "mod": "CoreMod",
      "op": "VariablePatch",
      "target": "res://scripts/player.gd::health"
    }
  ]
}
```

  * **`modified_files`**: A flat list of all files GMOS created or modified. The Revert logic iterates this list to restore `.bak` files.
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
  "mods": [
    {
      "name": "ModA",
      "enabled": true,
      "version": "1.2.0"
    }
  ]
}
```