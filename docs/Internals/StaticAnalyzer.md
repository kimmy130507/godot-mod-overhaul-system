# Static Analysis Engine

This document details the architecture of the `gmos.core.security` module, which performs advanced auditing of GDScript code.


---

# 1. Why a Custom Lexer?

Regex is insufficient for robust security scanning because it cannot easily handle:
* Varying whitespace (`OS . execute`)
* Comments (`OS.execute # harmless comment`)
* String formatting

To solve this, GMOS implements a custom `GDScriptLexer` that tokenizes the source code.

### Token Types
The lexer breaks code into:
* `IDENTIFIER` (e.g., `OS`, `DirAccess`, `var_name`)
* `STRING` (e.g., `"cmd.exe"`)
* `DOT` (`.`)
* `LPAREN` / `RPAREN`
* `NEWLINE` / `SKIP` (Whitespace)

---

# 2. The Analyzer

The `SecurityAnalyzer` consumes the stream of tokens to detect dangerous semantic patterns, regardless of formatting.

### Detection Heuristics

#### A. Remote Code Execution (RCE)
* **Pattern:** `IDENTIFIER(OS)` $\to$ `DOT` $\to$ `IDENTIFIER(execute)`
* **Severity:** **HIGH**
* **Reason:** Allows arbitrary command execution.

#### B. File Deletion
* **Pattern:** `IDENTIFIER(DirAccess)` $\to$ `DOT` $\to$ `IDENTIFIER(remove_absolute)`
* **Severity:** **HIGH**
* **Reason:** Malicious mods could delete system files.

#### C. Binary Loading
* **Pattern:** `IDENTIFIER(load)` $\to$ `LPAREN` $\to$ `STRING(*.dll | *.so)`
* **Severity:** **HIGH**
* **Reason:** Loading native binaries bypasses the GDScript sandbox entirely.

---

# 3. Analyzer vs. Sanitizer

GMOS uses a "Two-Pass" security model:

| Feature | **Static Analyzer** | **Runtime Sanitizer** |
| :--- | :--- | :--- |
| **Module** | `gmos.core.security` | `gmos.core.patcher` |
| **Method** | Token Stream Analysis | Regex Substitution |
| **Action** | **Warns** the user in UI | **Rewrites** code on disk |
| **Scope** | Broad (Filesystem, Network, RCE) | Narrow (RCE: `OS.execute`) |
| **Goal** | Audit & Awareness | Active Defense |

The Analyzer is the "Auditor" that flags suspicious behavior for review. The Sanitizer is the "Enforcer" that strictly neutralizes the most dangerous calls before the game runs.
