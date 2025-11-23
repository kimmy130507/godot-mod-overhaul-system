# File Diffing & Merge Engine

GMOS uses an internal diff/patch engine optimized for text-based modding.

# 1. Hash-Based Change Detection

GMOS detects changed files using a two-pass system:

1.  **File Size**: Fast rejection of identical files.
2.  **MD5 Hash**: Used to verify content identity before diffing.
3.  **Text Comparison**: If hashes differ, the file is loaded for line-by-line comparison.

# 2. Textual Diff (Unified Format)

GMOS uses the standard Unified Diff format (`difflib`) for internal operations.

```

@@ -10,6 +10,9 @@

```

These diffs are generated dynamically during the **Simulation** phase to provide a preview of changes without modifying the disk.

# 3. Conflict Resolution

If two mods modify the same file, GMOS flags a conflict.

1.  **Detection**: `analyze_mods_for_conflicts` groups patches by target file.
2.  **Resolution**:
    * **Policy Check**: GMOS checks `policy.json` for a pre-existing rule (e.g., "Mod A wins").
    * **User Prompt**: If no policy exists, the **Resolve Dialog** is shown.
3.  **Persistence**: The user's choice is saved to `policy.json`, ensuring the decision is remembered for future patch runs.

# 4. Limitations

* **Text-Based**: The engine operates on lines of text, not an Abstract Syntax Tree (AST).
* **Formatting**: Changes to indentation or whitespace may generate conflicts even if the logic is semantically identical.
