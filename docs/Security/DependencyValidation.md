# Dependency Validation

Dependency validation ensures that mods load in a consistent, stable order.

---

# 1. Validation Rules

GMOS enforces the following rules during the **Mod Loading** phase:

### Mod Existence
All dependencies listed in the `[Dependencies]` section of `mod.mos` must exist in the `mods/` folder.

### Cycle Detection
The topological sorter detects circular dependencies (e.g., A to B to A).
- **Action:** The cycle is detected, logged as an error, and the patch process is aborted to prevent undefined behavior.

### Name Collisions
GMOS enforces unique mod names.
- **Action:** If two folders contain mods with the same `Name` in their manifest, both are disabled, and an error is displayed.

---

# 2. Ordering Guarantees

Dependency order is:
1.  **Topologically Sorted**: Dependencies always load before the mods that require them.
2.  **Deterministic**: Unrelated mods are sorted alphabetically or by folder name.

This removes non-deterministic "race conditions" where the load order might change randomly between runs.
