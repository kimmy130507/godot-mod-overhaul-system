# Script Sanitization

Script sanitization is the process by which GMOS rewrites GDScript source code to prevent unsafe operations.

# 1. The Mechanism: Source Code Rewriting

GMOS does not "hook" engine functions via memory manipulation (like a DLL injector). Instead, it uses **Transpilation** (Source Code Rewriting) to force mods to use safe alternatives.

### How It Works
1.  **Payload**: The safe library `gmos_sandbox.gd` is injected into the game as a global singleton named `GMOS_Sandbox`.
2.  **Interception**: During the patch process, GMOS reads the mod's source code before writing it to the game folder.
3.  **Rewrite**: It uses a **Token Stream Rewriter** (`GDScriptLexer`) to safely identify and redirect unsafe calls without breaking code structure.

### Example

**Original Mod Code:**
```gdscript
# Malicious attempt to delete system files
OS.execute("cmd.exe", ["/c", "del system32"])
```

**Rewritten Code (Written to Disk):**

```gdscript
# Redirected to GMOS proxy
GMOS_Sandbox.secure_execute("cmd.exe", ["/c", "del system32"])
```

Because `GMOS_Sandbox` is a global singleton, this code is valid anywhere in the game. The mod "thinks" it is calling the OS, but it is actually executing the GMOS proxy function, which blocks the command and logs a security warning.

# 2. Rewrite Targets

Currently, GMOS targets specific Remote Code Execution (RCE) vectors:

  * `OS.execute(...)`, `OS.create_process(...)` $\rightarrow$ `GMOS_Sandbox.secure_execute(...)`
  * `OS.shell_open(...)` $\rightarrow$ `GMOS_Sandbox.secure_shell_open(...)`
  * `ClassDB.instantiate(...)`, `ClassDB.create_instance(...)` $\rightarrow$ `GMOS_Sandbox.secure_execute(...)`
  * `call(...)`, `callv(...)`, `call_deferred(...)`, `execute(...)`, `Engine.get_singleton(...)` $\rightarrow$ `GMOS_Sandbox.secure_call(...)`
  * `load(...)`, `preload(...)` $\rightarrow$ `GMOS_Sandbox.secure_load(...)`

# 3. Sanitization Process

1.  The file content is loaded into memory.
2.  `sanitize_script_content` (in `gmos/core/patcher.py`) tokenizes the script and rewrites the stream.
3.  The modified content is written to the game directory.

This ensures that the unsafe version of the code never exists on the game's filesystem.
