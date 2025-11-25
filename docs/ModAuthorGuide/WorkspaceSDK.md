# Workspace SDK (GodotBridge)

GMOS includes a Python-based SDK for advanced mod authors who want to automate the decompilation, editing, and packaging workflow.

## Core Class: `GodotBridge`

The `GodotBridge` class (`gmos.core.sdk`) coordinates the lifecycle of a modding workspace. It handles extraction, decompilation via GDRE Tools, and smart diffing to generate patches.

### 1. Initialization

Instantiate the bridge with the game's install directory and your desired workspace location.

```python
from gmos.core.sdk import GodotBridge

# Initialize the bridge
bridge = GodotBridge(
    game_dir="C:/Games/MyGodotGame",
    workspace_dir="C:/Games/MyGodotGame/gmos_workspace"
)

# IMPORTANT: You must configure the path to GDRE Tools for the decompilation step
bridge.set_gdre_tools_path("C:/Tools/gdre_tools.exe")
```

### 2. Extraction (`init_workspace`)

This method uses GMOS's **internal pure-Python parser** to extract raw files from the game's `.pck` archive.

  * **Function:** `bridge.init_workspace()`
  * **What it does:**
      * Locates the main PCK file in `game_dir`.
      * Extracts all archived files to `workspace_dir` preserving directory structure.
      * **Note:** This produces *raw* assets (e.g., `.gdc` scripts, `.stex` textures). These are not yet editable.

<!-- end list -->

```python
# Extract raw files
count = bridge.init_workspace()
print(f"Extracted {count} files.")
```

### 3. Decompilation (`recover_project`)

This method invokes the external **GDRE Tools** binary to convert the raw extracted assets back into an editable Godot project.

  * **Function:** `bridge.recover_project()`
  * **What it does:**
      * Runs `gdre_tools --headless --recover ...` on the extracted data.
      * Converts binary scripts (`.gdc`) back to source (`.gd`).
      * Converts texture assets (`.stex`) back to standard images (`.png`).
      * Reconstructs the `project.godot` file.

<!-- end list -->

```python
try:
    logs = bridge.recover_project()
    print("Decompilation complete! Project is now editable.")
except Exception as e:
    print(f"Failed to decompile: {e}")
```

### 4. Editing (`launch_editor`)

Once the workspace is recovered, use this method to open it in the Godot Editor.

  * **Function:** `bridge.launch_editor(editor_exe)`
  * **What it does:** Spawns the Godot Editor process, pointing it directly at the `project.godot` inside your workspace.

```python
bridge.launch_editor("C:/Godot/godot_v4.0.exe")
```

### 5. Exporting (`generate_mod_patch`)

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
