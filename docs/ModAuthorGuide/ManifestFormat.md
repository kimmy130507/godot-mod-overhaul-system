# Manifest Format (`mod.mos`)

GMOS mod manifests use a strict INI-style format. This file defines metadata, dependencies, and patch instructions.

---

## 1. Required Section: `[General]`

The core metadata section must be named `[General]`.

```ini
[ModInfo]
Name = My Cool Mod
Version = 1.2.0
Author = Jane Doe
Description = Changes balance and adds content.
```

| Field         | Required | Description        |
| ------------- | -------- | ------------------ |
| `Name`        | Yes      | Display name (Case Sensitive Key) |
| `Version`     | Yes      | SemVer recommended |
| `Author`      | No       | Name or team       |
| `Description` | No       | UI description     |

-----

## 2. Dependencies

Dependencies ensure your mod loads after its requirements.

```ini
[Dependencies]
requires = CoreLib, SharedAssets
```

GMOS resolves dependencies via topological sort **before** applying any patches.

-----

## 3. Patch Sections

Supported sections:

  * `[FileReplace]`
  * `[VariablePatch]`
  * `[FunctionPatch]`
  * `[DataAdd]` (Convenience alias for VariablePatch `mode=create`)
  * `[DataPatch]` (Convenience alias for VariablePatch `mode=create`)

They may appear in any order.

-----

## 4. Path Rules

All paths must be:

  * **relative to the mod folder**,
  * forward-slash formatted (`/`),
  * **not** allowed to escape via `../` traversal.

Example:

```ini
[FileReplace]
res://scripts/player.gd = patches/player_override.gd
```

**Left side:** target game file (res://)
**Right side:** file inside your mod folder

-----

## 5. Validation

Common validation errors:

  * Missing file on right side → mod is rejected
  * Left side path doesn’t exist in game → warning
  * Circular dependencies → error
  * Duplicate mod names → error

GMOS provides validation logs in the UI if a mod fails to load.