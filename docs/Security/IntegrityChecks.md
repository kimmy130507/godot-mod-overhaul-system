# Integrity Checks

GMOS uses hashing and structural checks to ensure the patched runtime is safe and uncorrupted.

---

# 1. Hash Strategy

GMOS uses:
- **MD5 hashing** to detect if a file has changed on disk, avoiding unnecessary merges or overwrites.
- **Atomic Writes** to ensure no file is partially written or corrupted during a crash.

---

# 2. Script Integrity

After sanitization:
1. GMOS processes the file content in memory.
2. It applies `sanitize_script_content` (Regex-based) to neutralize RCE vectors.
3. The file is written atomically (`.tmp` -> target).

---

# 3. Manifest Integrity

Each `mod.mos`:
- requires a valid schema (INI-style).
- must not contain references to files outside the mod folder.

Invalid manifests cause the mod to be marked **Invalid** in the UI and skipped during patching.

---

# 4. Asset Integrity

GMOS validates that source files referenced in `[FileReplace]` or other patch sections actually exist on disk. If a source file is missing, the patch operation aborts for that specific mod to prevent runtime crashes.
