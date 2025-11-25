# PCK Manipulation Internals

This document describes how GMOS handles Godot PCK (resource package) files using its native, pure-Python parser.

# 1. Purpose

Some Godot games do not load loose files from the file system, or prioritize the internal `.pck` content over them. In these cases, GMOS must inject modded files directly into the game's main archive (e.g., `Brotato.pck` or `data.pck`).

This functionality is toggled via **Force PCK Patching** in the UI.

# 2. The Safe-Append Strategy

GMOS uses a **Safe Rebuild** strategy implemented in `gmos.io.pck` to ensure atomicity. It does not modify the original file in-place.

### The Process (`append_file_to_pck`)

1.  **Stream Copy**: Creates a temporary file (`.tmp_rebuild`).
2.  **Header Rewrite**: Writes a new header with the updated file index pointing to new offsets.
3.  **Data Injection**: Streams original data blocks from the source PCK to the temp file.
4.  **Patch Injection**: Inserts modded file data where appropriate (appending or replacing).
5.  **Atomic Swap**: Once the rebuild is complete and verified, the temporary file atomically replaces the original `.pck`.

### Advantages
* **Zero Corruption Risk**: The original file is never touched until the new version is fully written.
* **Defragmentation**: Rebuilding the archive naturally removes gaps from previous modifications.

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