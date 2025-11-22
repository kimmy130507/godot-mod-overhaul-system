# Injection System Internals

This document explains how GMOS modifies the game’s engine configuration to install the security sandbox.

---

# 1. Autoload Injection

GMOS modifies `project.godot` by adding a single entry to the `[autoload]` section. This registers the sandbox as a Global Singleton (Autoload), ensuring it is loaded before any other game scripts.

### The Change
GMOS appends or updates the following key:

```ini
[autoload]
GMOS_Sandbox="*res://gmos_sandbox.tscn"
```

  * **`GMOS_Sandbox`**: The global variable name available in GDScript.
  * **`*`**: Tells Godot to instantiate this node automatically at startup.
  * **`res://gmos_sandbox.tscn`**: Points to the payload scene injected by GMOS.

This single line is sufficient to activate the security layer globally.

-----

# 2. Payload Files

The injection system writes two essential files to the game root:

1.  **`gmos_sandbox.gd`**: The script containing the security logic (wrappers like `secure_execute`).
2.  **`gmos_sandbox.tscn`**: A minimal scene file that attaches the script to a Node.

These files are written atomically using GMOS's IO safety wrappers.

-----

# 3. Injection Process

The `SandboxInjector` class (`gmos/core/injection.py`) handles the process:

1.  **Check**: Verifies if `project.godot` exists.
2.  **Load**: Parses `project.godot` into memory using `GodotProjectFile`.
3.  **Verify**: Checks if `GMOS_Sandbox` is already present.
4.  **Write Payload**: Creates the `.gd` and `.tscn` files.
5.  **Update Config**: Inserts the autoload key and saves `project.godot`.

-----

# 4. Removal (Uninstall)

GMOS can cleanly remove the sandbox:

1.  Removes the `GMOS_Sandbox` key from `project.godot`.
2.  Saves the config.