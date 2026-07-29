# GMOS Security Overview

GMOS enforces a multi-layered security model to ensure that:
- Mods cannot execute arbitrary system commands.
- Mod scripts are audited for dangerous patterns before use.
- The original game remains recoverable after any failed patch.
- Dependency chains cannot introduce malicious behavior.

This document summarizes GMOS’ defense layers and how they interact.

# 1. Security Design Principles

## Least Privilege
Mods are discouraged from performing high-risk operations. The Static Analyzer warns users if a mod attempts to:
- Read/write absolute system paths.
- Open network sockets.
- Load binary extensions.

## Deterministic Behavior
Every security decision produces the same result across runs, preventing inconsistent behavior that could be exploited.

## Complete Transparency
All injected files, sandbox wrappers, and rewritten lines of code are visible to the user and never obfuscated.

## User Recovery First
All potentially destructive actions are guarded by:
- backups (`.bak` files)
- atomic writes
- rollback policies

# 2. Security Layers (Summary)

1. **Manifest Validation**
   Ensures mods conform to safe structure, correct paths, and dependency rules. See [Manifest Format](docs/ModAuthorGuide/ManifestFormat.md).

2. **Patch Scope Enforcement**
   Patches may only modify files inside the game directory.

3. **Script Sanitization (Active)**
   Rewrites `OS.execute`, `OS.shell_open`, dynamic reflection calls, and `load()` calls to `GMOS_Sandbox` secure wrappers. See [Script Sanitization](docs/Security/ScriptSanitization.md).

4. **Static Analysis (Passive)**
   Scans for and warns about:
   * **Filesystem Access:** `FileAccess`, `DirAccess` (absolute paths).
   * **Network Usage:** `HTTPClient`, `HTTPRequest`.
   * **Binary Loading:** `load()` calls targeting `.dll`, `.so`, or `.dylib`.

See [Static Analyzer](docs/Internals/StaticAnalyzer.md).

5. **Dependency Validation**
   Detects malicious or unexpected dependency chains. See [Dependency Validation](docs/Security/DependencyValidation.md).

6. **Rollback System**
   Immediately restores backups on failure. See [Rollback System](docs/Security/RollbackSystem.md).

These layers work together to form GMOS’s comprehensive security architecture.