# Mod Author Guide — Introduction

This guide provides the complete technical documentation required to create high-quality, conflict-safe, and secure mods for **GMOS (Godot Mod Overhaul System)**.

It is written for:
- mod authors,
- technical modders,
- total conversion teams,
- contributors using the SDK (GodotBridge).

GMOS is not a simple loose-file replacer. It is a **deterministic patcher**, capable of:
- merging multiple script modifications,
- generating patch hunks,
- sanitizing unsafe GDScript,
- producing reproducible patched runtimes.

## Key Concepts

### 1. Single Game Directory
All mods must live here:

```

\<GameDir\>/mods/\<YourMod\>/

```

This is required because Godot resolves assets relative to the game root. Many mods reference assets inside their own mod directory using `res://mods/...` or relative paths. External mod folders would break runtime loading.

## 2. Manifest-Based Modding

Each mod must include a `mod.mos` INI-style manifest:

```ini
[ModInfo]
Name = Cool Mod
Version = 1.0.0
Author = Example

[FileReplace]
res://scripts/player.gd = patches/player_override.gd
```

Manifests describe how GMOS should apply the mod safely and predictably.

## 3. Deterministic Patching

GMOS applies mods using:

  - load-order resolution,
  - dependency graphing,
  - automatic conflict detection,
  - deterministic merge pipeline.

This ensures reproducible behavior even with many mods modifying the same files.

## 4. Sandbox & Security System

GMOS includes a GDScript **Sandbox Autoload Singleton**:

```gdscript
GMOS_Sandbox.secure_execute(...)
```

Dangerous APIs (like `OS.execute`) are rewritten automatically:

  - mods cannot silently run OS commands,
  - all calls are intercepted and validated by the sandbox payload.

## 5. The Workspace SDK

GMOS includes a Python-based development SDK (`GodotBridge`) for:

  - automatic decompilation using GDRE Tools,
  - generating editable workspaces,
  - diff analysis,
  - automatic manifest generation,
  - auto-detection of variable/function patches vs full replacements.

This is the fastest and safest way to create mods.

-----

Continue reading the next sections for details on manifests, patching rules, function wrapping, and SDK workflows.
