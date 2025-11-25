# File Access Security Model

This document describes GMOS' rules for file access and how mods are audited.

# 1. Allowed Access

Mods are expected to:
- read files within the **game directory** (`res://`)
- read **their own assets** within the `mods/<mod>` folder

# 2. Discouraged Access (Warnings)

The GMOS **Static Analyzer** (`gmos.core.security`) scans mod code before patching. It flags the following patterns as potential security risks:

- Deleting files (`DirAccess.remove` / `DirAccess.remove_absolute`)
- Escaping game root via `../`

**Note:** Currently, GMOS produces **warnings** for these operations but does not automatically rewrite or block them at the engine level. Users should review mods that trigger these warnings carefully.

# 3. Path Enforcement

During the patching process (e.g., `FileReplace`), GMOS strictly enforces that patch targets must reside within the game directory. It uses `ensure_within(game_dir, path)` to prevent Zip Slip or path traversal attacks during installation.

# 4. Sandbox Handling

The sandbox autoload (`GMOS_Sandbox`) currently focuses on **Remote Code Execution (RCE)**.

- `OS.execute` calls are rewritten to `secure_execute`.
- `OS.shell_open` calls are rewritten to `secure_shell_open`.

File system calls are currently **not** routed through the sandbox wrapper in this version.
