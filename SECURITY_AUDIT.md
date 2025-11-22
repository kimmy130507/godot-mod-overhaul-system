# Security Audit Checklist

This document tracks the implementation status of critical security controls in GMOS.

## Path Safety
- [x] `ensure_within` rejects absolute and parent-traversal paths.
- [x] All file joins use `os.path.join` or `Path` followed by `resolve()` verification.
- [x] Mods cannot write outside the work root (Enforced by `gmos.core.patcher`).

## Manifest Validation
- [x] `mod.mos` parsing rejects shell metacharacters (INI parser used).
- [x] Absolute or UNC paths forbidden in `[FileReplace]` validation logic.

## Code Execution
- [x] No `eval`, `exec`, or `subprocess` with untrusted strings.
- [x] Script patching uses Regex substitution (`re.sub`); no runtime import of mod code.
- [x] `OS.execute` calls are rewritten to `GMOS_Sandbox.secure_execute`.

## Permissions & Locking
- [x] On startup, `gmos.io.locking` acquires a singleton lock (`gmos.lock`).
- [x] File operations use `gmos.io.base` thread locks to prevent internal races.
- [x] Atomic writes (`.tmp` -> `.bak` -> target) prevent file corruption.

## External Calls
- [x] `GodotBridge` sanitizes paths before passing them to `gdre_tools`.
- [x] No automatic network access is implemented in the core.

## CI/CD Security
- [x] `Bandit` runs on each push (`workflows/ci.yml`).
- [x] `Safety` checks dependencies for CVEs.
- [x] Windows binaries are GPG signed (`workflows/build-windows-gpg.yml`).