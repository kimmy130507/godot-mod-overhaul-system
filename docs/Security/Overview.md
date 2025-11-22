# GMOS Security Overview

GMOS enforces a multi-layered security model to ensure that:
- Mods cannot execute arbitrary system commands.
- Mod scripts are audited for dangerous patterns before use.
- The original game remains recoverable after any failed patch.
- Dependency chains cannot introduce malicious behavior.

This document summarizes GMOS’ defense layers and how they interact.

---

# 1. Security Design Principles

## Least Privilege
Mods are discouraged from accessing files outside the game directory. The Static Analyzer warns users if a mod attempts to read/write absolute system paths.

## Deterministic Behavior
Every security decision produces the same result across runs, preventing inconsistent behavior that could be exploited.

## Complete Transparency
All injected files, sandbox wrappers, and rewritten lines of code are visible to the user and never obfuscated.

## User Recovery First
All potentially destructive actions are guarded by:
- backups (`.bak` files)
- atomic writes
- rollback policies

---

# 2. Security Layers (Summary)

1. **Manifest Validation**
   Ensures mods conform to safe structure, correct paths, and dependency rules.

2. **Patch Scope Enforcement**
   Patches may only modify files inside the game directory.

3. **Script Sanitization (Active)**
   Rewrites unsafe `OS.execute` calls to `GMOS_Sandbox` secure wrappers.

4. **Static Analysis (Passive)**
   Scans for and warns about filesystem access, network usage, and binary loading.

5. **Dependency Validation**
   Detects malicious or unexpected dependency chains.

6. **Rollback System**
   Immediately restores backups on failure.

These layers work together to form GMOS’s comprehensive security architecture.
