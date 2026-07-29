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

```

### 2. Extraction & Decompilation (`init_workspace` / `recover_project`)

These methods invoke the external **GDRE Tools** binary to extract and convert the raw assets back into an editable Godot project.

* **Functions:** `bridge.init_workspace()` / `bridge.recover_project()`
* **What they do:**
    * Locates the main PCK file or executable in `game_dir`.
    * Runs `gdre_tools --headless --recover` to extract and decompile scripts/textures.
    * Reconstructs the `project.godot` file in the `workspace_dir`.

```python
try:
    # Executes extraction and decompilation simultaneously
    log_count = bridge.init_workspace()
    print(f"Decompilation complete! Generated {log_count} log entries. Project is now editable.")
except Exception as e:
    print(f"Failed to initialize workspace: {e}")

```

### 3. Editing (`launch_editor`)

Once the workspace is recovered, use this method to open it in the Godot Editor.

  * **Function:** `bridge.launch_editor(editor_exe)`
  * **What it does:** Spawns the Godot Editor process, pointing it directly at the `project.godot` inside your workspace.

```python
bridge.launch_editor("C:/Godot/godot_v4.0.exe")
```

### 4. Exporting (Build Preview)

This is the "Brain" of the SDK. It compares your workspace against the original vanilla PCK to detect changes and generates a GMOS-compatible mod package.

  * **Function:** `bridge.generate_mod_patch(output_dir, mod_name, author)`
  * **Process:**
    1.  **Scan:** Walks the workspace and compares every file against the vanilla PCK (using size and MD5 hashes).
    2.  **Smart Diffing:**
          * If a `.gd` script is modified, it attempts to detect if *only* a variable was changed.
          * **VariablePatch:** Generated if the diff isolates to a specific variable block.
          * **FunctionPatch:** Generated if the diff isolates to a specific function block (uses CST analysis).
          * **BinaryPatch:** Generated if a non-text file (e.g., texture, audio) has changed, creating a compact `bsdiff` delta instead of a full file copy.
          * **FileReplace:** Generated if complex logic changes are detected or heuristics fail.
    3.  **Packaging:** Copies modified files to `output_dir` and writes the `mod.mos` manifest.

Once you click "Build Mod Package" in the Dev Tools UI, GMOS presents a Build Preview dialog, allowing you to review all patch instructions before exporting the final `.mos` manifest.