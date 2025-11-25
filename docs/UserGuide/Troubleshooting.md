# Troubleshooting

Common problems and how to fix them.

## Logs

GMOS writes logs to platform-appropriate locations.
* **Windows**: `%APPDATA%\gmos\logs\gmos.log` (Roaming Profile)
* **Linux**: `~/.local/share/gmos/logs/gmos.log`
* **macOS**: `~/Library/Logs/gmos/gmos.log`

Please include `gmos.log` when reporting issues.

## Mods missing or not loading

* Ensure each mod is a folder inside `<GameDir>/mods/`.
* Verify that `mod.mos` exists and is syntactically correct (INI format).
* Run **Refresh Mods** after adding or removing a mod.
* Check the "Dependency Errors" section in the UI inspector for the selected mod.

## Broken asset or missing file at runtime

This commonly happens when a mod references an asset path expecting the mod folder to be inside the game root.
* **Fix:** Ensure the mod is installed at `<GameDir>/mods/ModName/`.
* **Check:** If the mod uses absolute paths (e.g., `C:/...`), GMOS security warnings may be flagging it.

## Permission or access denied errors

* Avoid installing the game in `C:\Program Files` or other protected folders.
* If the game is in a protected folder, run GMOS as **Administrator** to allow atomic writes.
* Check antivirus logs — some AVs block the creation of `.tmp` or `.bak` files.

## PCK-related issues

* **Force PCK Patching:** This mode appends mod files directly to the game's `.pck` archive. It is robust but modifies the main game file.
* **Corruption:** If the game fails to start after a PCK patch, click **Revert Game Files** to restore the clean `main.pck.bak`.
* *Note: You do **not** need GDRE Tools to use the "Force PCK Patching" feature; GMOS handles this natively.*

## Sandbox rewrites cause syntax errors

* Rarely, the script sanitizer (which rewrites `OS.execute`) may produce invalid syntax if the mod code uses highly unconventional formatting.
* **Fix:** Inspect the patched `.gd` file in the game directory. If the rewrite is incorrect, contact the mod author or report a bug with the specific code snippet.

## The game crashes after applying mods

1.  **Disable all mods** and click **Apply Patch**. Verify the vanilla game launches.
2.  Enable mods one-by-one to identify the culprit.
3.  Check `gmos.log` and the game's own crash logs (often in `%APPDATA%/Godot/app_userdata/`).

## Still stuck?

When opening an issue, please include:
1. `gmos.log` (full content).
2. Your OS and GMOS version.
3. A copy of the `mod.mos` manifest for the problematic mod.
4. Steps to reproduce the problem