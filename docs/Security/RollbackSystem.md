# Rollback System

GMOS ensures that the game installation remains recoverable, even after a failed patch.

# 1. Backup Policy (Atomic Strategy)

GMOS never modifies a file in-place without a backup.

**The "Write-Replace" Cycle:**
1.  Read original file `game.gd`.
2.  Write modified content to `game.gd.tmp`.
3.  If `game.gd.bak` does not exist, rename `game.gd` $\to$ `game.gd.bak`.
4.  Rename `game.gd.tmp` $\to$ `game.gd`.

This ensures that at any given moment, either the original or the new file is valid. There is no state where the file is half-written or corrupted.

# 2. Recovery Mechanism

GMOS does not perform an "automatic undo" immediately upon error (to preserve logs and state for debugging). Instead, it relies on **Revert-on-Start**.

**How it works:**
1.  Every patch run begins by reading `runtime_manifest.json`.
2.  It identifies all files modified in the previous run.
3.  It restores those files from their `.bak` counterparts.

This guarantees that every patch operation starts from a clean, vanilla state, preventing "mod residue" from accumulating over time.

# 3. Manual Rollback

Users can trigger a manual cleanup via the **"Revert Game Files"** button in the UI. This:
1.  Scans the game directory for `.bak` files.
2.  Restores them to their original names.
3.  Removes the GMOS temporary files.

# 4. Scope of Protection

The system protects:
- Game scripts (`.gd`)
- Project settings (`project.godot`)
- Binary assets replaced by mods

*Note: Save files and user config in `%APPDATA%` are not touched by GMOS.*
