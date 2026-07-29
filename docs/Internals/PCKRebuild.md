# PCK Override Internals

This document describes how GMOS handles Godot PCK files using an external override approach rather than modifying original game archives.

# 1. Purpose

Some Godot games do not load loose files from the file system, or prioritize the internal `main.pck` content over them. Instead of permanently appending to or rebuilding massive `.pck` files (which wastes GBs of disk space for backups and runs into memory limits during patching), GMOS dynamically mounts a secondary override package.

# 2. The Override Strategy

GMOS uses `ProjectSettings.load_resource_pack()` injected via the Sandbox Autoload.

### The Process
1.  **VFS Compilation**: During the patch run, all files (whether copied directly or merged in memory) are gathered.
2.  **Pack Generation**: `gmos/io/pck.py` compiles these files into a standalone `gmos_override.pck` file in the game root.
3.  **Runtime Mount**: When the game launches, `gmos_sandbox.gd` (the first script to execute) mounts `gmos_override.pck` with the `replace_files=true` flag.
4.  **Virtual Overwrite**: The Godot engine natively intercepts requests for vanilla files and serves the modified versions from the override package.

### Advantages
* **Zero Disk Bloat**: No need to backup a 10GB `main.pck` just to edit a 5KB text file.
* **Memory Safe**: Creating a small override PCK takes milliseconds and negligible RAM.
* **Clean Uninstall**: Reverting to vanilla simply requires deleting `gmos_override.pck` and removing the sandbox autoload from `project.godot`.