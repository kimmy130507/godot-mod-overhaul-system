# Script Sanitization

Script sanitization is the process by which GMOS rewrites GDScript source code to prevent unsafe operations.

# 1. The Mechanism: Source Code Rewriting

GMOS does not "hook" engine functions via memory manipulation (like a DLL injector). Instead, it uses **Transpilation** (Source Code Rewriting) to force mods to use safe alternatives.

### How It Works
1.  **Payload**: The safe library `gmos_sandbox.gd` is injected into the game as a global singleton named `GMOS_Sandbox`.
2.  **Interception**: During the patch process, GMOS reads the mod's source code before writing it to the game folder.
3.  **Rewrite**: It uses Regex to detect calls to unsafe APIs and redirects them to the singleton.

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

  * `OS.execute(...)` $\rightarrow$ `GMOS_Sandbox.secure_execute(...)`
  * `OS.shell_open(...)` $\rightarrow$ `GMOS_Sandbox.secure_shell_open(...)`

# 3. Sanitization Process

1.  The file content is loaded into memory.
2.  `sanitize_script_content` (in `gmos/core/patcher.py`) performs the regex substitution.
3.  The modified content is written to the game directory.

This ensures that the unsafe version of the code never exists on the game's filesystem.
