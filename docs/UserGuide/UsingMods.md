# Using Mods

This guide explains how to install, enable, reorder, and remove mods.

## Mod folder layout

Each mod must be a subfolder inside `<GameDir>/mods/`. Example:

```

\<GameDir\>/mods/
              ExampleMod/
                    mod.mos
                    patches/
                    assets/

```

`mod.mos` is the manifest describing the mod.

## Installing Mods

1. Extract or place the mod folder into `<GameDir>/mods/`.
2. In GMOS, click **Refresh Mods**. The app scans for manifests and validates mods.

*Do not* place mods outside `<GameDir>/mods/` — runtime asset lookups expect this location.

## Enabling / Disabling

- Check a mod to enable it; uncheck to disable.
- GMOS uses deterministic load ordering; changes require **Apply Patch** to take effect.

## Ordering and Priority

- GMOS resolves conflicts deterministically using dependencies and priority fields.
- When two mods alter the same target, the mod loaded last (by resolved order) wins unless you resolve conflicts manually.

## Conflict Resolution

When conflicts occur, GMOS opens the **Merge Studio** (see [Conflict Resolution & Debugging](docs/ModAuthorGuide/ConflictResolution.md)). In this window you can:

- Visualize exactly where changes conflict in the file (highlighted in blue).
- Choose a "Winner" (Vanilla, Mod A, Mod B).
- Write a **Custom Patch** to manually merge logic.

All decisions are saved to the Policy and are automatically reapplied on future patch runs.

## Supported Patch Types (high-level)
For detailed usage and syntax, see the [Patch Types Guide](docs/ModAuthorGuide/PatchTypes.md).

- **FileReplace** — replace an entire resource (binary or script).
- **VariablePatch** — change top-level variables or constants in a script.
- **FunctionPatch** — prefix/postfix/replace functions using name-based wrappers.
- **SmartPatch** — inject code into specific locations using token anchors.
- **BinaryPatch** — apply binary deltas to non-text assets.

*Note: To add new files (textures, sounds), simply include them in your mod folder.*

## Safe Script Rewriting & Sandbox

GMOS scans script code for specific remote code execution risks (specifically `OS.execute`, `OS.shell_open`, and dynamic reflection) and rewrites them to call the injected singleton `GMOS_Sandbox` secure wrappers. This prevents direct execution of unsafe OS commands while preserving mod intent where possible. Additionally, the sandbox autoload handles mounting the `gmos_override.pck` file at runtime, seamlessly loading your merged mod files without destructively altering the base game archives. 

See [Script Sanitization](docs/Security/ScriptSanitization.md) for further technical details.

## Uninstalling Mods

1. Disable the mod in GMOS.
2. Click **Apply Patch** to rebuild the patched runtime without the mod.
3. (Optional) Delete the mod folder from `<GameDir>/mods/`.

No leftover artifacts remain in the original game files; backups are cleaned per retention policy.

## CLI Mode

GMOS provides a command-line interface for managing mods, applying patches, and hosting peer-to-peer sync sessions.

```sh
gmos --game-dir "<path>" --mods-dir "<path>" [command]
```

**Supported Commands:**
- `list`: List all installed mods, their state, and version.
- `patch [--conflict overwrite|fail]`: Apply enabled mods to the game using the specified conflict resolution strategy.
- `restore`: Restore game to vanilla state.
- `mod enable <mod_name>`: Enable a specific mod.
- `mod disable <mod_name>`: Disable a specific mod.
- `p2p host`: Host a peer-to-peer mod sync lobby.
- `p2p join <ip>`: Join a peer-to-peer mod sync lobby.