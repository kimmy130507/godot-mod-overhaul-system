# Security & Sandbox Architecture

This document details the two-pronged security approach used by GMOS: **Static Analysis** (auditing files before use) and **Runtime Sandboxing** (rewriting scripts to intercept calls).

# 1. Static Analysis (Scanner)

The `gmos.core.security` module performs static analysis on GDScript files to detect potentially dangerous operations. This is a **passive** check that generates warnings/errors but does not modify the code. For details on how the parser builds abstract syntax trees for these scans, see the [Static Analysis Engine](docs/Internals/StaticAnalyzer.md).

### The Engine
- **Lexer**: A custom `GDScriptLexer` tokenizes the source code, making the analysis resilient to whitespace and formatting differences.
- **Analyzer**: The `SecurityAnalyzer` scans the token stream for specific dangerous patterns.

### Detected Risks
The analyzer flags the following sequences:
- **RCE Risk**: `OS.execute`, `OS.create_process`, `OS.shell_open`, `ClassDB.instantiate`, `Engine.get_singleton`
- **Dynamic Reflection**: `call`, `callv`, `call_deferred`
- **File Deletion**: `DirAccess.remove_absolute` (Godot 4), `Directory.new().remove` (Godot 3)
- **Network**: `HTTPClient.new`, `HTTPRequest.new`
- **Binary Loading**: `load(...)` calls containing `.dll`, `.so`, or `.dylib` extensions.

# 2. Runtime Sandboxing (Sanitization)

The **Active Sanitization** process occurs during the patching phase (`gmos.core.patcher`). Unlike the static analyzer, this step **modifies** the GDScript source code before writing it to the game directory.

### Sanitization Scope
Currently, GMOS sanitizes a strict subset of the risks detected by the analyzer. It targets the most immediate RCE (Remote Code Execution) vectors:

1. **OS.execute / create_process / instantiate** $\rightarrow$ Rewritten to `GMOS_Sandbox.secure_execute`
2. **OS.shell_open** $\rightarrow$ Rewritten to `GMOS_Sandbox.secure_shell_open`
3. **call / callv / call_deferred / Engine.get_singleton** $\rightarrow$ Rewritten to `GMOS_Sandbox.secure_call`
4. **load / preload** $\rightarrow$ Rewritten to `GMOS_Sandbox.secure_load`

*Note: File system calls (like `DirAccess`) are currently detected by the scanner but NOT automatically rewritten by the sanitizer.*

### Rewrite Mechanism
Sanitization uses **Token Stream Rewriting** via the `GDScriptLexer`. It iterates through the tokens to identify dangerous function calls and replaces them with their safe counterparts while preserving original whitespace and comments.

### Transformation Examples
- `OS.execute(...)` $\rightarrow$ `GMOS_Sandbox.secure_execute(...)`
- `OS.shell_open(...)` $\rightarrow$ `GMOS_Sandbox.secure_shell_open(...)`
- `load(...)` $\rightarrow$ `GMOS_Sandbox.secure_load(...)`

# 3. The Sandbox Singleton

To support the rewritten calls, GMOS injects a global Autoload named `GMOS_Sandbox`.

### Policy
The current sandbox policy is **Strict Blocking**.
- **secure_execute**: Logs the attempt and returns `-1` (Error). It does not execute the command.
- **secure_shell_open**: Logs the attempt and returns `2` (ERR_UNAVAILABLE).
- **secure_load**: Returns `null` if the path contains a binary extension (`.dll`, `.so`, `.dylib`), otherwise executes the standard `load(path)`.