# Patch Pipeline Internals

This document describes the deterministic patching process used by GMOS to apply mods.

# 1. Pipeline Overview

GMOS uses this pipeline for every patch run:

1.  **Revert**: Restore vanilla state from backups.
2.  **Load**: Parse manifests and resolve dependencies.
3.  **Plan**: Generate a list of patch instructions.
4.  **Conflict Check**: Analyze overlaps and policy rules.
5.  **Apply**: Execute patches concurrently using DAG-based Parallel Scheduling.
6.  **Sanitize**: Rewrite scripts to neutralize RCE vectors.
7.  **Commit**: Write files to disk (or PCK).
8.  **Manifest**: Update `runtime_manifest.json`.

Each stage is deterministic: given the same input files and mod configuration, the output is identical.

# 2. Stage Breakdown

## 2.1. Revert (Clean Slate)
Before applying changes, `run_patcher` checks `runtime_manifest.json`. It restores every file listed in `modified_files` from its corresponding `.bak` file. This ensures no "mod residue" accumulates over time.

## 2.2. Dependency Resolution
Uses a topological sort (`resolve_mod_dependencies`).
- **Cycles**: Detected and flagged.
- **Order**: Dependencies load first. Unrelated mods are sorted alphabetically or by folder name to ensure stability.

## 2.3. Patch Instruction Generation
For every enabled mod, GMOS generates instructions:
- `FileReplace`: Maps source path -> target resource.
- `VariablePatch`: Extracts source block -> targets specific variable in destination.
- `FunctionPatch`: Extracts function -> targets function (prefix/postfix/replace).
- `DataAdd`: Treats as a request to create a new top-level variable/table (emitted internally as a VariablePatch with `mode='create'`).
- `SmartPatch`: Token-based injection into existing code blocks (anchors/append).
- `BinaryPatch`: Application of binary deltas (bsdiff4) for non-text assets.

## 2.4. Conflict Analysis
Instructions are grouped by target file.
- **Policy Check**: If `user_load_order.json` dictates a "Winner" for a specific file, conflicting instructions from other mods are dropped.
- **Merge Strategy**: If no policy exists, GMOS attempts a text merge or delegates to `ConflictDelegate` (which invokes the **Merge Studio** in the UI, or applies the `--conflict` strategy in the CLI).

## 2.5. Execution & Sanitization (Security Phase)
Files are grouped by their target resource and processed concurrently in independent threads using a bounded thread pool. As files are processed in memory:
1.  **Patch**: The content is modified (variables replaced, functions wrapped).
2.  **Sanitize**: The resulting string (including SmartPatch injections) is passed to `sanitize_script_content`.
    * **Mechanism**: Token Stream Rewriting (via `GDScriptLexer`).
    * **Target**: `OS.execute(...)` -> `GMOS_Sandbox.secure_execute(...)`.
    * **Target**: `OS.shell_open(...)` -> `GMOS_Sandbox.secure_shell_open(...)`.
    * **Target**: `call(...)`, `ClassDB.instantiate(...)`, `Engine.get_singleton(...)` -> `GMOS_Sandbox.secure_call(...)`.
    * **Target**: `load(...)` -> `GMOS_Sandbox.secure_load(...)`.

This ensures malicious code is neutralized before being written to the game directory.

## 2.6. Cache & Deployment Strategy
Instead of modifying the game's `main.pck`, GMOS deploys modifications based on the instance configuration:

1.  **Cache Write**: Modified file content is processed in memory and written to `gmos_data/cache/merged/`.
2.  **Backup**: Any existing vanilla target file is backed up with a `.bak` extension (e.g., `player.gd.bak`).
3.  **Packed Mode (`is_packed=True`)**: Standard resources in memory are compiled into a standalone `gmos_override.pck` package, which is mounted at launch by `GMOS_Sandbox`.
4.  **Loose Mode / Native Binaries**: Loose files or native binaries (`.dll`, `.so`, `.dylib`, `.gdextension`) bypass PCK packing and are deployed directly to the game root using symbolic links via `SymlinkManager` (falling back to hard copy if privileges fail).

This ensures that the game engine loads the modded content transparently, while the original assets remain safe on disk for the **Revert** phase.

## 2.7. Sandbox & Override Injection
Finally, the `SandboxInjector` checks `project.godot`. If the `GMOS_Sandbox` autoload is missing, it is injected. The payload file (`gmos_sandbox.gd`) handles intercepting dangerous calls AND mounting `gmos_override.pck` at runtime via `ProjectSettings.load_resource_pack()`.

# 3. Determinism Guarantee

All operations depend **only** on:
- Sorted mod order.
- Mod manifests.
- Target game files.
- User conflict policies.

No randomness is used in the patching logic. This ensures reproducible builds for debugging.