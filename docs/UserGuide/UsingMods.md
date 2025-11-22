# Using Mods

This guide explains how to install, enable, reorder, and remove mods.

---

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

---

## Installing Mods

1. Extract or place the mod folder into `<GameDir>/mods/`.
2. In GMOS, click **Refresh Mods**. The app scans for manifests and validates mods.

*Do not* place mods outside `<GameDir>/mods/` — runtime asset lookups expect this location.

---

## Enabling / Disabling

- Check a mod to enable it; uncheck to disable.
- GMOS uses deterministic load ordering; changes require **Apply Patch** to take effect.

---

## Ordering and Priority

- GMOS resolves conflicts deterministically using dependencies and priority fields.
- When two mods alter the same target, the mod loaded last (by resolved order) wins unless you resolve conflicts manually.

---

## Conflict Resolution

When conflicts occur, GMOS opens the **Hunk Viewer**. In the viewer you can:

- Choose one mod's change (accept A or accept B)
- Merge hunks manually when possible
- Abort the patch and fix the manifests

All user decisions are stored and reapplied on future patch runs.

---

## Supported Patch Types (high-level)

- **FileReplace** — replace an entire resource (binary or script)
- **VariablePatch** — change top-level variables or constants in a script
- **FunctionPatch** — prefix/postfix/replace functions using name-based wrappers

*Note: To add new files (textures, sounds), simply include them in your mod folder.*

---

## Safe Script Rewriting & Sandbox

GMOS scans script code for specific remote code execution risks (specifically `OS.execute` and `OS.shell_open`) and rewrites them to call the injected singleton `GMOS_Sandbox.secure_execute(...)`. This prevents direct execution of unsafe OS commands while preserving mod intent where possible.

---

## Uninstalling Mods

1. Disable the mod in GMOS.
2. Click **Apply Patch** to rebuild the patched runtime without the mod.
3. (Optional) Delete the mod folder from `<GameDir>/mods/`.

No leftover artifacts remain in the original game files; backups are cleaned per retention policy.

---

## CLI examples

- Refresh mods:
```sh
gmos --refresh
```

  - Apply patch:

```sh
gmos --apply
```

  - Enable a mod:


```sh
gmos --enable "ExampleMod"
```