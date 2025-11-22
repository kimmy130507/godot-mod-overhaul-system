# GMOS Threat Model

This document defines the specific threat surfaces GMOS defends against and the assumptions underlying the system.

---

# 1. Out-of-Scope Threats

GMOS does **not** attempt to protect against:
- malicious game executables (GMOS assumes the base game is trustworthy)
- OS-level malware already present on the user’s machine
- mods that use purely in-game logic to grief the player (e.g., deleting save data through Godot APIs if the game itself allows it)

---

# 2. In-Scope Threats

GMOS protects against:

## 2.1. Arbitrary Code Execution (Active Protection)
GMOS actively **rewrites** scripts to prevent:
- `OS.execute` (running external commands)
- `OS.shell_open` (opening malicious URLs/files)

These calls are routed to `GMOS_Sandbox`, which blocks them by default.

---

## 2.2. Arbitrary File Access (Passive Protection)
Attempted access to system files (e.g., `C:/Windows`) is detected by the **Static Analyzer**.
- **Mitigation:** GMOS displays high-severity warnings for `FileAccess` and `DirAccess` usage on absolute paths.
- **Status:** User auditing required (Automated blocking is planned for v2.0).

---

## 2.3. Path Traversal (Active Protection)
Examples:
- `../` sequences trying to escape the game directory during installation.

**Mitigation:** The patcher uses `ensure_within()` to strictly enforce that all file operations occur inside the game root.

---

## 2.4. Malicious Dependencies

GMOS validates:
- dependency chains
- cycles
- impersonation (two mods with same name)

---

## 2.5. Cross-Mod Interference
Patches cannot overwrite files unless they explicitly target them. The deterministic load order ensures predictable conflict resolution.

---

## 2.6. Broken/Malformed Patches
GMOS ensures:
- invalid patches do not corrupt game files
- conflicting hunks require user approval
- broken patches trigger rollback

---

# 3. Threat Sources

| Threat Source | Description | Mitigation |
|---------------|-------------|------------|
| Accidental mistakes | Syntax errors, invalid paths | Manifest validation, dry-run, rollback |
| Malicious mod authors | Intentional RCE code | Script sanitization (Rewriting) |
| Spyware / Data theft | Reading sensitive files | Static Analysis (Scanning) |
| Supply-chain attacks | Mods depending on compromised modules | Dependency validation |
| File corruption | Unexpected interruption | atomic writes, backups |
