# Workspace SDK (GodotBridge)

GMOS includes a Python-based SDK for advanced mod authors who want to automate the decompilation, editing, and packaging workflow.

---

## Core Class: `GodotBridge`

The `GodotBridge` class (`gmos.core.sdk`) coordinates the lifecycle of a modding workspace. It handles decompilation via GDRE Tools, manages the Godot Editor process, and performs smart diffing to generate patches.

### 1. Initialization

Instantiate the bridge with the game's install directory and your desired workspace location.

```python
from gmos.core.sdk import GodotBridge

# Initialize the bridge
bridge = GodotBridge(
    game_dir="C:/Games/MyGodotGame",
    workspace_dir="C:/Games/MyGodotGame/gmos_workspace"
)

# IMPORTANT: You must configure the path to GDRE Tools for decompilation to work
bridge.set_gdre_tools_path("C:/Tools/gdre_tools.exe")
```

### 2. Decompiling (`init_workspace`)

This method uses the external **GDRE Tools** binary to recover a fully editable project from the game's shipped `data.pck` or executable.

  * **Function:** `bridge.init_workspace()`
  * **What it does:**
      * Locates the main PCK file in `game_dir`.
      * Runs `gdre_tools --headless --recover ...` to decompile assets.
      * Converts binary formats (`.gdc` -\> `.gd`, `.stex` -\> `.png`) back to editable source.
      * Outputs the project into `workspace_dir`.

```python
try:
    logs = bridge.init_workspace()
    print("Decompilation complete!")
except Exception as e:
    print(f"Failed to decompile: {e}")
```

### 3. Editing (`launch_editor`)

Once the workspace is ready, use this method to open it in the Godot Editor.

  * **Function:** `bridge.launch_editor(editor_exe)`
  * **What it does:** Spawns the Godot Editor process, pointing it directly at the `project.godot` inside your workspace.

```python
bridge.launch_editor("C:/Godot/godot_v4.0.exe")
```

### 4. Exporting (`generate_mod_patch`)

This is the "Brain" of the SDK. It compares your workspace against the original vanilla PCK to detect changes and generates a GMOS-compatible mod package.

  * **Function:** `bridge.generate_mod_patch(output_dir, mod_name, author)`
  * **Process:**
    1.  **Scan:** Walks the workspace and compares every file against the vanilla PCK (using size and MD5 hashes).
    2.  **Smart Diffing:**
          * If a `.gd` script is modified, it attempts to detect if *only* a variable was changed.
          * **VariablePatch:** Generated if the diff isolates to a specific variable block.
          * **FileReplace:** Generated if complex logic changes are detected or heuristics fail.
    3.  **Packaging:** Copies modified files to `output_dir` and writes the `mod.mos` manifest.

```python
# Creates "MyCoolMod" inside "C:/Mods"
manifest_path = bridge.generate_mod_patch(
    output_dir="C:/Mods/MyCoolMod",
    mod_name="My Cool Mod",
    author="ModderName"
)
print(f"Mod exported to: {manifest_path}")
```
