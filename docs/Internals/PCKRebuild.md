# PCK Manipulation Internals

This document describes how GMOS handles Godot PCK (resource package) files using its native, pure-Python parser.

# 1. Purpose

Some Godot games do not load loose files from the file system, or prioritize the internal `.pck` content over them. In these cases, GMOS must inject modded files directly into the game's main archive (e.g., `Brotato.pck` or `data.pck`).

This functionality is toggled via **Force PCK Patching** in the UI.

# 2. The Safe-Append Strategy

GMOS does **not** repack the entire PCK from scratch (which would be slow and risky). Instead, it uses a **Safe Append** strategy implemented in `gmos.io.pck`.

### The Process (`append_file_to_pck`)

1.  **Parse Header**: Reads the PCK header (Magic `GDPC`) and the existing file index.
2.  **Calculate Offsets**: Determines the end of the current data section.
3.  **Append Data**: writes the new file data (the modded content) to the end of the PCK file.
4.  **Update Index**: Rewrites the PCK header and file index to point the resource path (e.g., `res://player.gd`) to the *new* data offset and size.
5.  **Padding**: Ensures correct byte alignment (usually 16 bytes) as expected by the Godot engine.

### Advantages
* **Speed**: Only writes the new data, not the whole game.
* **Safety**: If the header write fails, the original data remains untouched (though the file grows). GMOS uses atomic file swaps to prevent corruption.

# 3. Supported Formats

The `gmos.io.pck` module supports:
* **PCK Version 1**: Godot 3.x
* **PCK Version 2**: Godot 4.x (Includes flags and file base offset)

It automatically detects the format version from the magic header.

# 4. Integration in Patch Pipeline

When **Force PCK** is enabled:
1.  The Patcher builds modded files in memory (VFS).
2.  It creates a backup of `main.pck` -> `main.pck.bak`.
3.  It iterates through the VFS and calls `append_file_to_pck` for each file.
4.  The modified PCK replaces the original.

# 5. External Tools

While GMOS handles **writing/appending** natively, it relies on **GDRE Tools** (external) if a full *decompilation* of the PCK is required for creating a workspace. GMOS does not contain a decompiler, only a structure parser.