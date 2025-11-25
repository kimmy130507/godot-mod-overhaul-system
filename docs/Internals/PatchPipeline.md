# Patch Pipeline Internals

This document describes the deterministic patching process used by GMOS to apply mods.

# 1. Pipeline Overview

GMOS uses this pipeline for every patch run:

1.  **Revert**: Restore vanilla state from backups.
2.  **Load**: Parse manifests and resolve dependencies.
3.  **Plan**: Generate a list of patch instructions.
4.  **Conflict Check**: Analyze overlaps and policy rules.
5.  **Apply**: Execute patches (FileReplace, VariablePatch, FunctionPatch).
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

## 2.4. Conflict Analysis
Instructions are grouped by target file.
- **Policy Check**: If `policy.json` dictates a "Winner" for a specific file, conflicting instructions from other mods are dropped.
- **Merge Strategy**: If no policy exists, GMOS may attempt a text merge or prompt the user (via `ConflictDelegate`).

## 2.5. Execution & Sanitization (Security Phase)
As files are processed in memory:
1.  **Patch**: The content is modified (variables replaced, functions wrapped).
2.  **Sanitize**: The resulting string is passed to `sanitize_script_content`.
    * **Mechanism**: Regex substitution (`re.sub`).
    * **Target**: `OS.execute(...)` -> `GMOS_Sandbox.secure_execute(...)`.
    * **Target**: `OS.shell_open(...)` -> `GMOS_Sandbox.secure_shell_open(...)`.

This ensures that even if a modder writes malicious code, the final script written to the game directory calls the safe sandbox wrapper.

## 2.6. Atomic Write
The final content is written using a robust Write-Replace strategy:
1.  Stream: Data is written to a temporary file (target.tmp) to prevent partial writes.
2.  Backup: If a backup doesn't exist, the original is moved to target.bak
3.  Swap: The temporary file is atomically renamed to target.

**For PCK Mode:**
GMOS performs a Safe Rebuild. It streams the original PCK content into a new temporary file, injecting modded assets during the stream, and then atomically swaps the new PCK into place. This guarantees the main game archive is never corrupted.

## 2.7. Sandbox Injection
Finally, the `SandboxInjector` checks `project.godot`. If the `GMOS_Sandbox` autoload is missing, it is injected, and the payload files (`gmos_sandbox.gd/tscn`) are written to the root.

# 3. Determinism Guarantee

All operations depend **only** on:
- Sorted mod order.
- Mod manifests.
- Target game files.
- User conflict policies.

No randomness is used in the patching logic. This ensures reproducible builds for debugging.