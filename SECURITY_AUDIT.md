# Security Audit Checklist

## Path Safety
- [ ] `ensure_within` rejects absolute and parent-traversal paths.
- [ ] All file joins use `os.path.join` or `Path` followed by `resolve()` and verification.
- [ ] Mods cannot write outside the work root.

## Manifest Validation
- [ ] `mod.mos` parsing rejects shell metacharacters.
- [ ] Absolute or UNC paths forbidden in `[FileReplace]`.

## Code Execution
- [ ] No `eval`, `exec`, or `subprocess` with untrusted strings.
- [ ] Script patching only manipulates text; no runtime import of mod code.

## Permissions
- [ ] On startup, loader checks write permission for `work_root`.
- [ ] Fails with clear message if permission denied.

## External Calls
- [ ] Network access disabled or explicit.
- [ ] Paths and environment sanitized before invoking external tools.

## CI
- [ ] Bandit runs on each push.
- [ ] High-severity findings block merge.
