# Security & Sandbox Architecture

This document details the two-pronged security approach used by GMOS: **Static Analysis** (auditing files before use) and **Runtime Sandboxing** (rewriting scripts to intercept calls).

---

# 1. Static Analysis (Scanner)

The `gmos.core.security` module performs static analysis on GDScript files to detect potentially dangerous operations. This is a **passive** check that generates warnings/errors but does not modify the code.

### The Engine
- **Lexer**: A custom `GDScriptLexer` tokenizes the source code, making the analysis resilient to whitespace and formatting differences.
- **Analyzer**: The `SecurityAnalyzer` scans the token stream for specific dangerous patterns.

### Detected Risks
The analyzer flags the following sequences:
- **RCE Risk**: `OS.execute`, `OS.create_process`, `OS.shell_open`
- **File Deletion**: `DirAccess.remove_absolute` (Godot 4), `Directory.new().remove` (Godot 3)
- **Network**: `HTTPClient.new`, `HTTPRequest.new`
- **Binary Loading**: `load(...)` calls containing `.dll`, `.so`, or `.dylib` extensions.

---

# 2. Runtime Sandboxing (Sanitization)

The **Active Sanitization** process occurs during the patching phase (`gmos.core.patcher`). Unlike the static analyzer, this step **modifies** the GDScript source code before writing it to the game directory.

### Sanitization Scope
Currently, GMOS sanitizes a strict subset of the risks detected by the analyzer. It targets the most immediate RCE (Remote Code Execution) vectors:

1. **OS.execute** $\rightarrow$ Rewritten to `GMOS_Sandbox.secure_execute`
2. **OS.shell_open** $\rightarrow$ Rewritten to `GMOS_Sandbox.secure_shell_open`

*Note: File system calls (like `DirAccess`) are currently detected by the scanner but NOT automatically rewritten by the sanitizer.*

### Rewrite Mechanism
Sanitization uses fast **Regex Substitution**. It does not parse the code into an AST.
- **Input**: `OS.execute("cmd.exe", [])`
- **Output**: `GMOS_Sandbox.secure_execute("cmd.exe", [])`

---

# 3. The Sandbox Singleton

To support the rewritten calls, GMOS injects a global Autoload named `GMOS_Sandbox`.

### Policy
The current sandbox policy is **Strict Blocking**.
- **secure_execute**: Logs the attempt and returns `-1` (Error). It does not execute the command.
- **secure_shell_open**: Logs the attempt and returns `2` (ERR_UNAVAILABLE).

### Future Roadmap
- Implementation of a `whitelist.json` to allow specific, safe commands.
- Expansion of the sanitizer to wrap `FileAccess` and `DirAccess` calls.