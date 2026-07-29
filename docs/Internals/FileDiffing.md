# File Diffing & Merge Engine

GMOS uses an internal diff/patch engine optimized for text-based modding.

# 1. Hash-Based Change Detection

GMOS detects changed files using a two-pass system:

1.  **Filecmp (Size + Byte-by-byte)**: Fast rejection of identical files without loading fully into memory.
2.  **Text Comparison**: If bytes differ and the file size is under the `GMOS_SMALL_FILE_LIMIT`, it is loaded for line-by-line comparison. Large files skip text merge and are flagged as binary conflicts.

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
    * **Policy Check**: GMOS checks `user_load_order.json` for a pre-existing rule (e.g., "Mod A wins").
    * **Merge Studio**: If no policy exists, the user opens the **Merge Studio** to visually resolve conflicts.
        * Users can select a winner (A vs B) or write a **Custom Patch**.
        * Custom patches are compiled into a generated `GMOS_Unified_Patch` mod.
3.  **Persistence**: The user's choice is saved to `user_load_order.json`, ensuring the decision is remembered for future patch runs.

# 4. Limitations

* **Hybrid Parsing**: GMOS uses a `CSTParser` (Concrete Syntax Tree) to structurally identify functions and variables for targeting. However, the *content* of these blocks is often compared textually during the merge phase.
* **Formatting**: While the CST helps find code blocks regardless of their position, changes to indentation or whitespace within a block may still generate textual conflicts.
