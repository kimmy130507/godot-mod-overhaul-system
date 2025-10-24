# Godot Mod Overhaul System (GMOS)

Small launcher-time tool. Produces a patched working copy of a Godot game so mods can overlay files and patches without modifying originals.

## Install
From repo root:
```bash
pip install -e .
````

## Quick start

1. Place the loader next to the game or run the packaged executable.
2. Configure in UI:

   * Original Game Dir: clean game files
   * Work Root Dir: patched runtime (default `./game_runtime`)
   * Mods Dir: folder containing one subfolder per mod
   * Game Executable: executable name or Launch Override
3. Click `Refresh Mods` → `Apply Patch` → `Start Game`.

## Behavior highlights

* Loader never mutates original game files.
* Work root contains patched runtime and `.bak` backups.
* Last mod in the enabled list wins on conflicting full-file replacements.
* Use `Simulate & Diff` to preview changes before applying.
* See `Mod Author Guide` for full manifest syntax and advanced patch modes.

## Troubleshooting

If a mod is invalid the loader marks it `[INVALID]` and skips it. Use `Simulate & Diff` to inspect errors.

## License
GMOS (Godot Mod Overhaul System) is licensed under the GNU General Public License v3.
This license applies only to the GMOS tool and its source code.
Mods and content created for use with GMOS may use any license chosen by their authors.